"""Talon lifecycle and scope bridge for the internal Wayland desktop facade."""

import os
import sys
import threading
from collections.abc import Callable
from typing import Any

from talon import Context, Module, actions, app, cron, registry, scope, ui
from talon.plugins import eye_mouse

from .wayland_backend.desktop import WaylandDesktop
from .wayland_backend.errors import CapabilityUnavailable
from .wayland_backend.geometry import normalize_point
from .wayland_backend.key_spec import KeyAction, KeyEvent, parse_key_spec
from .wayland_backend.outputs import OutputTarget
from .wayland_backend.session import is_wayland_session
from .wayland_backend.windows import Window

mod = Module()
ctx = Context()
ctx.matches = "os: linux"

_BRIDGE_KEY = "_jm_talon_lite_wayland_bridge"
_LEGACY_RUNTIME_KEY = "_jm_talon_lite_wayland_runtime"
_LEGACY_CONTEXT_JOB_KEY = "_jm_talon_lite_wayland_context_job"
_LEGACY_SCOPE_ORIGINALS_KEY = "_jm_talon_lite_wayland_scope_originals"
_FALLBACK_KEYS_KEY = "_jm_talon_lite_fallback_keyboard_keys"
_fallback_held_keys = set(
    getattr(
        sys,
        _FALLBACK_KEYS_KEY,
        globals().get("_fallback_held_keys", ()),
    )
)
_PROTOCOL_FEATURES = {
    "wl_output": "output-bound gaze forwarding",
    "zwp_virtual_keyboard_manager_v1": "keyboard forwarding",
    "zwlr_virtual_pointer_manager_v1": "pointer, gaze, and hiss forwarding",
    "zwlr_foreign_toplevel_manager_v1": "application and window contexts",
}


def _is_wayland() -> bool:
    """Return whether Talon is running in a Wayland session."""
    return is_wayland_session(os.environ)


def _publish_fallback_keys() -> None:
    """Retain fallback key ownership across Talon script reloads."""
    setattr(sys, _FALLBACK_KEYS_KEY, tuple(sorted(_fallback_held_keys)))


def _record_fallback_key_spec(key_spec: str) -> None:
    """Track explicit key holds routed through Talon's next implementation."""
    try:
        strokes = parse_key_spec(key_spec)
    except (TypeError, ValueError):
        return
    for stroke in strokes:
        identities = {f"modifier:{name}" for name in stroke.modifiers}
        if stroke.key is not None:
            identities.add(f"key:{stroke.key}")
        if stroke.action is KeyAction.DOWN:
            _fallback_held_keys.update(identities)
        elif stroke.action is KeyAction.UP:
            _fallback_held_keys.difference_update(identities)
    _publish_fallback_keys()


class _TalonWaylandBridge:
    """Own Talon lifecycle and scope adaptation for one WaylandDesktop."""

    def __init__(self) -> None:
        """Construct an inactive bridge without Talon or Wayland side effects."""
        self.desktop = WaylandDesktop()
        self._lock = threading.Lock()
        self._started = False
        self._cleanup_pending = False
        self._generation = 0
        self._context_revision = 0
        self._scope_originals: tuple[Any, Any] | None = None
        self._app_scope_decl: Any = None
        self._win_scope_decl: Any = None
        self._app_scope_update_pending = False
        self._win_scope_update_pending = False
        self._app_scope_provider = self._app_scope
        self._win_scope_provider = self._win_scope
        self._context_available = False
        self._context_job: Any = None
        self._context_job_clears = False
        self._latest_window: Window | None = None
        self._active_window: Window | None = None
        self._active_apps: set[str] = set()
        self._modifier_tokens: dict[int, tuple[KeyEvent, ...]] = {}
        self._next_modifier_token = 1
        self._registered_declaration_callback = False
        self._unsubscribe_windows: Callable[[], None] | None = None

    def start(self) -> None:
        """Start the bridge transactionally and allow safe repeated calls."""
        with self._lock:
            already_started = self._started
            cleanup_pending = self._cleanup_pending
        if already_started:
            status = self.desktop.status()
            if status.running and status.error is None:
                return
        if already_started or cleanup_pending:
            self.stop()
        with self._lock:
            self._started = True
            self._generation += 1
            generation = self._generation
            self._latest_window = None
            self._active_window = None
            self._active_apps = set()
        try:
            unsubscribe_windows = self.desktop.on_active_window_changed(
                lambda window: self._queue_active_window(generation, window)
            )
            with self._lock:
                self._unsubscribe_windows = unsubscribe_windows
            self._initialize_scopes()
            self.desktop.start()
            self._warn_for_missing_protocols()
            if self.desktop.window_context_available():
                self._install_scope_providers()
            registry.register("update_decls", self._on_declarations_updated)
            self._registered_declaration_callback = True
        except Exception as exc:
            try:
                self.stop()
            except Exception as cleanup_error:
                exc.add_note(
                    "Bridge rollback also failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            raise

    def stop(self) -> None:
        """Release every callback and resource, allowing safe repeated calls."""
        with self._lock:
            self._started = False
            self._generation += 1
            self._context_revision += 1
            job = self._context_job
            unsubscribe_windows = self._unsubscribe_windows
            unregister_declarations = self._registered_declaration_callback
            self._latest_window = None
            self._active_window = None
            self._active_apps = set()
            modifier_tokens = tuple(self._modifier_tokens.items())

        failures: list[tuple[str, Exception]] = []

        def attempt(label: str, operation: Callable[[], None]) -> bool:
            """Record one teardown failure while allowing later steps to run."""
            try:
                operation()
            except Exception as exc:
                failures.append((label, exc))
                return False
            return True

        if job is not None:
            if attempt("context job cancellation", lambda: cron.cancel(job)):
                with self._lock:
                    if self._context_job is job:
                        self._context_job = None
                        self._context_job_clears = False
        if unregister_declarations and attempt(
            "declaration callback",
            self._unregister_declaration_callback,
        ):
            with self._lock:
                self._registered_declaration_callback = False
        if unsubscribe_windows is not None and attempt(
            "window callback",
            unsubscribe_windows,
        ):
            with self._lock:
                if self._unsubscribe_windows is unsubscribe_windows:
                    self._unsubscribe_windows = None
        for token, pressed in modifier_tokens:
            if attempt(
                f"temporary modifier token {token}",
                lambda pressed=pressed: self.desktop.release_temporary_modifiers(
                    pressed
                ),
            ):
                with self._lock:
                    if self._modifier_tokens.get(token) is pressed:
                        self._modifier_tokens.pop(token)
        desktop_stopped = attempt("Wayland desktop", self.desktop.stop)
        if desktop_stopped:
            with self._lock:
                self._modifier_tokens.clear()
        attempt("scope providers", self._restore_scope_providers)
        with self._lock:
            self._cleanup_pending = bool(failures)
        if failures:
            _label, error = failures[0]
            for label, secondary in failures[1:]:
                error.add_note(
                    f"{label} also failed: {type(secondary).__name__}: {secondary}"
                )
            raise error.with_traceback(error.__traceback__)

    def app_name(self) -> str:
        """Return a display name derived from the active Wayland app ID."""
        app_id = "" if self._active_window is None else self._active_window.app_id
        if not app_id:
            return ""
        return app_id[0].upper() + app_id[1:]

    def window_title(self) -> str:
        """Return the active Wayland window title or an empty string."""
        return "" if self._active_window is None else self._active_window.title

    def context_available(self) -> bool:
        """Return whether native Wayland scope providers are installed."""
        return self._context_available

    def begin_temporary_modifiers(self, modifiers: str) -> int:
        """Press unheld modifiers and retain their release plan by token."""
        pressed = self.desktop.press_temporary_modifiers(modifiers)
        with self._lock:
            token = self._next_modifier_token
            self._next_modifier_token += 1
            self._modifier_tokens[token] = pressed
        return token

    def end_temporary_modifiers(self, token: int) -> None:
        """Release and forget one temporary modifier token idempotently."""
        with self._lock:
            pressed = self._modifier_tokens.get(token)
        if pressed is None:
            return
        self.desktop.release_temporary_modifiers(pressed)
        with self._lock:
            if self._modifier_tokens.get(token) is pressed:
                self._modifier_tokens.pop(token)

    def move_pointer_on_main_screen(
        self,
        x: float,
        y: float,
        *,
        refresh_hover: bool = False,
    ) -> None:
        """Move within Talon's current eye-mouse screen through its wl_output."""
        screen = eye_mouse.main_screen or ui.main_screen()
        if screen is None:
            raise CapabilityUnavailable("Talon main screen is not available")
        rect = screen.rect
        if rect.width <= 0 or rect.height <= 0:
            raise CapabilityUnavailable("Talon main screen has invalid dimensions")
        scale = float(getattr(screen, "scale", 1.0) or 1.0)
        target = OutputTarget(
            name=str(getattr(screen, "name", "") or ""),
            make=str(getattr(screen, "manufacturer", "") or ""),
            model=str(getattr(screen, "model", "") or ""),
            physical_width=float(getattr(screen, "mm_x", 0.0) or 0.0),
            physical_height=float(getattr(screen, "mm_y", 0.0) or 0.0),
            mode_width=round(rect.width * scale),
            mode_height=round(rect.height * scale),
            refresh_millihz=round(
                float(getattr(screen, "refresh_rate", 0.0) or 0.0) * 1000
            ),
        )
        normalized_x, normalized_y = normalize_point(
            (rect.x, rect.y, rect.width, rect.height),
            x,
            y,
        )
        self.desktop.move_pointer_output_absolute(
            target,
            normalized_x,
            normalized_y,
            refresh_hover=refresh_hover,
        )

    def _initialize_scopes(self) -> None:
        """Capture Talon's scope declarations and their original providers."""
        self._app_scope_decl = scope.scopes["app"]
        self._win_scope_decl = scope.scopes["win"]
        self._scope_originals = (
            self._app_scope_decl.func,
            self._win_scope_decl.func,
        )

    def _app_scope(self) -> dict[str, Any]:
        """Return Talon app-scope values for the active Wayland window."""
        return {
            "app": set(self._active_apps),
            "name": self.app_name(),
            "bundle": "",
            "path": "",
            "exe": "",
            "exe_path": "",
        }

    def _win_scope(self) -> dict[str, str]:
        """Return Talon window-scope values for the active Wayland window."""
        return {
            "title": self.window_title(),
            "doc": "",
            "filename": "",
            "file_ext": "",
        }

    def _install_scope_providers(self) -> None:
        """Install native app and window providers once."""
        if self._context_available or self._scope_originals is None:
            return
        self._context_available = True
        try:
            self._app_scope_decl.func = self._app_scope_provider
            self._win_scope_decl.func = self._win_scope_provider
            self._app_scope_decl.update()
            self._win_scope_decl.update()
        except Exception as exc:
            try:
                self._restore_scope_providers()
            except Exception as cleanup_error:
                exc.add_note(
                    "Scope rollback also failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            raise

    def _restore_scope_providers(self) -> None:
        """Restore only scope providers still owned by this bridge."""
        if self._scope_originals is None:
            return
        original_app, original_win = self._scope_originals
        if self._app_scope_decl.func is self._app_scope_provider:
            self._app_scope_decl.func = original_app
            self._app_scope_update_pending = True
        if self._win_scope_decl.func is self._win_scope_provider:
            self._win_scope_decl.func = original_win
            self._win_scope_update_pending = True
        self._context_available = False
        first_error = None
        for pending_attr, declaration in (
            ("_app_scope_update_pending", self._app_scope_decl),
            ("_win_scope_update_pending", self._win_scope_decl),
        ):
            if not getattr(self, pending_attr):
                continue
            try:
                declaration.update()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                else:
                    first_error.add_note(
                        f"Another scope update also failed: {type(exc).__name__}: {exc}"
                    )
            else:
                setattr(self, pending_attr, False)
        if first_error is not None:
            raise first_error.with_traceback(first_error.__traceback__)

    def _refresh_app_scope(self) -> None:
        """Update app IDs and preserve matching declared Talon applications."""
        app_id = "" if self._active_window is None else self._active_window.app_id
        self._active_apps = {app_id, app_id.casefold()} if app_id else set()
        self._app_scope_decl.update()
        matched_apps = {
            name
            for name, declarations in registry.decls.apps.items()
            if any(declaration.is_active() for declaration in declarations)
        }
        if not matched_apps.issubset(self._active_apps):
            self._active_apps |= matched_apps
            self._app_scope_decl.update()

    def _queue_active_window(self, generation: int, window: Window | None) -> None:
        """Coalesce owner-thread window events onto Talon's main thread."""
        with self._lock:
            if not self._started or generation != self._generation:
                return
            self._latest_window = window
            if self._context_job is not None:
                if window is not None and not self._context_job_clears:
                    return
                cron.cancel(self._context_job)
            self._context_job_clears = window is None
            self._context_revision += 1
            revision = self._context_revision
            delay = "20ms" if self._context_job_clears else "0ms"
            self._context_job = cron.after(
                delay,
                lambda: self._apply_active_window(generation, revision),
            )

    def _apply_active_window(self, generation: int, revision: int) -> None:
        """Publish the latest active window through Talon's scope system."""
        with self._lock:
            if (
                not self._started
                or generation != self._generation
                or revision != self._context_revision
            ):
                return
            window = self._latest_window
            self._context_job = None
            self._context_job_clears = False
        previous = self._active_window
        self._active_window = window
        if not self.desktop.window_context_available():
            self._restore_scope_providers()
            return
        self._install_scope_providers()
        self._win_scope_decl.update()
        self._refresh_app_scope()
        previous_key = None if previous is None else (previous.app_id, previous.title)
        window_key = None if window is None else (window.app_id, window.title)
        if window_key != previous_key:
            app_id, title = ("", "") if window is None else window_key
            print(f"Wayland context: app={app_id!r} title={title!r}", flush=True)

    def _on_declarations_updated(self, _declarations) -> None:
        """Refresh active application aliases after Talon declaration changes."""
        if self._started and self._context_available:
            self._refresh_app_scope()

    def _unregister_declaration_callback(self) -> None:
        """Remove this bridge's Talon declaration listener if still present."""
        try:
            registry.unregister("update_decls", self._on_declarations_updated)
        except (KeyError, ValueError):
            pass

    def _warn_for_missing_protocols(self) -> None:
        """Print one warning listing unavailable feature protocols."""
        available = dict(self.desktop.status().protocols)
        missing = [
            f"{protocol} ({feature})"
            for protocol, feature in _PROTOCOL_FEATURES.items()
            if protocol not in available
        ]
        pointer_version = available.get("zwlr_virtual_pointer_manager_v1")
        if pointer_version is not None and pointer_version < 2:
            missing.append(
                "zwlr_virtual_pointer_manager_v1 version 2 "
                "(output-bound gaze forwarding)"
            )
        if missing:
            print(
                "Wayland compositor compatibility warning: missing "
                + "; ".join(missing),
                file=sys.stderr,
                flush=True,
            )


@mod.action_class
class Actions:
    """Talon actions exposing native Wayland pointer operations."""

    def wayland_pointer_move_absolute(
        x: float, y: float, refresh_hover: bool = False
    ) -> None:
        """Move the pointer to normalized desktop coordinates."""
        _bridge.desktop.move_pointer_absolute(
            x,
            y,
            refresh_hover=refresh_hover,
        )

    def wayland_pointer_move_main_screen(
        x: float, y: float, refresh_hover: bool = False
    ) -> None:
        """Move the pointer within Talon's current eye-mouse screen."""
        _bridge.move_pointer_on_main_screen(
            x,
            y,
            refresh_hover=refresh_hover,
        )

    def wayland_pointer_available() -> bool:
        """Return whether native pointer emission is currently available."""
        return _bridge.desktop.pointer_available()

    def wayland_keyboard_available() -> bool:
        """Return whether native keyboard emission is currently available."""
        return _bridge.desktop.keyboard_available()

    def wayland_keyboard_modifiers_begin(modifiers: str) -> int:
        """Press unheld native modifiers and return a release token."""
        return _bridge.begin_temporary_modifiers(modifiers)

    def wayland_keyboard_modifiers_end(token: int) -> None:
        """Release modifiers introduced by one native token."""
        _bridge.end_temporary_modifiers(token)

    def wayland_pointer_move_relative(dx: float, dy: float) -> None:
        """Move the pointer by a relative compositor-space delta."""
        _bridge.desktop.move_pointer_relative(dx, dy)

    def wayland_pointer_button_down(button: int = 0) -> None:
        """Establish the pressed state for one pointer button."""
        _bridge.desktop.set_pointer_button(button, True)

    def wayland_pointer_button_up(button: int = 0) -> None:
        """Establish the released state for one pointer button."""
        _bridge.desktop.set_pointer_button(button, False)

    def wayland_pointer_click(button: int = 0) -> None:
        """Click one pointer button."""
        _bridge.desktop.click_pointer(button)

    def wayland_pointer_button_toggle(button: int = 0) -> bool:
        """Toggle one pointer button and return its new state."""
        return _bridge.desktop.toggle_pointer_button(button)

    def wayland_pointer_release_all() -> bool:
        """Release all pointer buttons and report whether any were held."""
        return _bridge.desktop.release_pointer_buttons()

    def wayland_pointer_scroll(
        vertical_steps: int = 0, horizontal_steps: int = 0
    ) -> None:
        """Scroll discrete steps through the native pointer."""
        _bridge.desktop.scroll_pointer(vertical_steps, horizontal_steps)

    def wayland_pointer_scroll_continuous(
        vertical_lines: float = 0.0, horizontal_lines: float = 0.0
    ) -> None:
        """Scroll fractional lines continuously through the native pointer."""
        _bridge.desktop.scroll_pointer_continuous(vertical_lines, horizontal_lines)

    def wayland_pointer_modified_click(modifiers: str, button: int = 0) -> None:
        """Click while holding one Talon modifier chord."""
        _bridge.desktop.modified_click(modifiers, button)


@ctx.action_class("main")
class MainActions:
    """Override Talon's main key action for Wayland sessions."""

    def key(key: str):
        """Send Talon key syntax through the native Wayland keyboard."""
        if (
            not _is_wayland()
            or _fallback_held_keys
            or not _bridge.desktop.keyboard_available()
        ):
            actions.next(key)
            _record_fallback_key_spec(key)
            return
        try:
            _bridge.desktop.send_key(key)
        except CapabilityUnavailable:
            actions.next(key)
            _record_fallback_key_spec(key)


@ctx.action_class("app")
class AppActions:
    """Expose active Wayland application values through Talon actions."""

    def name() -> str:
        """Return the active Wayland application name."""
        if not _is_wayland() or not _bridge.context_available():
            return actions.next()
        return _bridge.app_name()

    def executable() -> str:
        """Return no executable because the protocol exposes only app IDs."""
        if not _is_wayland() or not _bridge.context_available():
            return actions.next()
        return ""

    def bundle() -> str:
        """Return no bundle because the protocol exposes only app IDs."""
        if not _is_wayland() or not _bridge.context_available():
            return actions.next()
        return ""


@ctx.action_class("win")
class WinActions:
    """Expose the active Wayland window title through Talon actions."""

    def title() -> str:
        """Return the active Wayland window title."""
        if not _is_wayland() or not _bridge.context_available():
            return actions.next()
        return _bridge.window_title()


def _on_ready() -> None:
    """Start native capabilities after Talon has initialized its scopes."""
    if not _is_wayland():
        return
    try:
        _bridge.start()
    except Exception as exc:
        print(f"Wayland desktop startup failed: {exc}", file=sys.stderr, flush=True)


def _on_quit() -> None:
    """Stop native capabilities before Talon exits."""
    _bridge.stop()


def _retire_legacy_runtime() -> None:
    """Stop and remove state retained by the previous runtime architecture."""
    failures = []

    def attempt(label: str, operation: Callable[[], None]) -> bool:
        """Run one migration cleanup while preserving later opportunities."""
        try:
            operation()
        except Exception as exc:
            failures.append((label, exc))
            return False
        return True

    legacy_runtime = getattr(sys, _LEGACY_RUNTIME_KEY, None)
    if legacy_runtime is not None:
        if attempt("legacy runtime", legacy_runtime.stop):
            delattr(sys, _LEGACY_RUNTIME_KEY)
    # Stopping the old runtime can enqueue one final delayed context update.
    legacy_job = getattr(sys, _LEGACY_CONTEXT_JOB_KEY, None)
    if legacy_job is not None:
        if attempt("legacy context job", lambda: cron.cancel(legacy_job)):
            delattr(sys, _LEGACY_CONTEXT_JOB_KEY)
    legacy_scopes = getattr(sys, _LEGACY_SCOPE_ORIGINALS_KEY, None)
    if legacy_scopes is not None:
        app_scope_decl = scope.scopes["app"]
        win_scope_decl = scope.scopes["win"]

        def restore_scopes() -> None:
            """Restore both pre-Wayland providers and publish their values."""
            app_scope_decl.func, win_scope_decl.func = legacy_scopes
            scope_failures = []
            for label, declaration in (
                ("app scope", app_scope_decl),
                ("window scope", win_scope_decl),
            ):
                try:
                    declaration.update()
                except Exception as exc:
                    scope_failures.append((label, exc))
            if scope_failures:
                _label, error = scope_failures[0]
                for label, secondary in scope_failures[1:]:
                    error.add_note(
                        f"{label} also failed: {type(secondary).__name__}: {secondary}"
                    )
                raise error.with_traceback(error.__traceback__)

        if attempt("legacy scope providers", restore_scopes):
            delattr(sys, _LEGACY_SCOPE_ORIGINALS_KEY)
    if failures:
        _label, error = failures[0]
        for label, secondary in failures[1:]:
            error.add_note(
                f"{label} also failed: {type(secondary).__name__}: {secondary}"
            )
        raise error.with_traceback(error.__traceback__)


_candidate_bridge = _TalonWaylandBridge()
_previous_bridge = getattr(sys, _BRIDGE_KEY, None)
if _previous_bridge is not None:
    _previous_bridge.stop()
_retire_legacy_runtime()
_bridge = _candidate_bridge
setattr(sys, _BRIDGE_KEY, _bridge)

app.register("ready", _on_ready)
app.register("quit", _on_quit)
