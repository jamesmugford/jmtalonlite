"""Talon app/window scope providers backed by immutable Wayland window values."""

from typing import Any

from talon import registry, scope

from .wayland_backend.windows import Window


class WaylandScopes:
    """Own scope providers and app aliases; called from the Talon-side bridge."""

    def __init__(self) -> None:
        """Construct inactive state without capturing or replacing Talon scopes."""
        self._originals: tuple[Any, Any] | None = None
        self._app_decl: Any = None
        self._win_decl: Any = None
        self._app_update_pending = False
        self._win_update_pending = False
        self._app_provider = self._app_scope
        self._win_provider = self._win_scope
        self._installed = False
        self._active_window: Window | None = None
        self._active_apps: set[str] = set()

    def initialize(self) -> None:
        """Capture current providers after the preceding bridge has retired."""
        self._app_decl = scope.scopes["app"]
        self._win_decl = scope.scopes["win"]
        self._originals = (self._app_decl.func, self._win_decl.func)

    def clear(self) -> None:
        """Clear published values without installing or restoring providers."""
        self._active_window = None
        self._active_apps = set()

    def available(self) -> bool:
        """Return whether native Wayland scope providers are installed."""
        return self._installed

    def app_name(self) -> str:
        """Return a display name derived from the active Wayland app ID."""
        app_id = "" if self._active_window is None else self._active_window.app_id
        if not app_id:
            return ""
        return app_id[0].upper() + app_id[1:]

    def window_title(self) -> str:
        """Return the active Wayland window title or an empty string."""
        return "" if self._active_window is None else self._active_window.title

    def install(self) -> None:
        """Install native app and window providers once."""
        if self._installed or self._originals is None:
            return
        self._installed = True
        try:
            self._app_decl.func = self._app_provider
            self._win_decl.func = self._win_provider
            self._app_decl.update()
            self._win_decl.update()
        except Exception as exc:
            try:
                self.restore()
            except Exception as cleanup_error:
                exc.add_note(
                    "Scope rollback also failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            raise

    def restore(self) -> None:
        """Restore only providers still owned here, retrying pending updates."""
        if self._originals is None:
            return
        original_app, original_win = self._originals
        if self._app_decl.func is self._app_provider:
            self._app_decl.func = original_app
            self._app_update_pending = True
        if self._win_decl.func is self._win_provider:
            self._win_decl.func = original_win
            self._win_update_pending = True
        self._installed = False
        first_error = None
        for pending_attr, declaration in (
            ("_app_update_pending", self._app_decl),
            ("_win_update_pending", self._win_decl),
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

    def refresh_app_scope(self) -> None:
        """Update app IDs and preserve matching declared Talon applications."""
        app_id = "" if self._active_window is None else self._active_window.app_id
        self._active_apps = {app_id, app_id.casefold()} if app_id else set()
        self._app_decl.update()
        matched_apps = {
            name
            for name, declarations in registry.decls.apps.items()
            if any(declaration.is_active() for declaration in declarations)
        }
        if not matched_apps.issubset(self._active_apps):
            self._active_apps |= matched_apps
            self._app_decl.update()

    def update(self, window: Window | None, *, available: bool) -> None:
        """Publish a window, or restore the fallback when context is unavailable."""
        previous = self._active_window
        self._active_window = window
        if not available:
            self.restore()
            return
        self.install()
        self._win_decl.update()
        self.refresh_app_scope()
        previous_key = None if previous is None else (previous.app_id, previous.title)
        window_key = None if window is None else (window.app_id, window.title)
        if window_key != previous_key:
            app_id, title = ("", "") if window is None else window_key
            print(f"Wayland context: app={app_id!r} title={title!r}", flush=True)

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
