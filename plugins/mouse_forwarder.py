"""Forward Talon mouse actions through the native Wayland runtime."""

import os
import sys
import threading

from talon import Context, Module, actions, app, settings, ui

from .wayland_backend.errors import CapabilityUnavailable
from .wayland_backend.geometry import desktop_bounds, normalize_point
from .wayland_backend.scroll import accumulate_steps
from .wayland_backend.session import is_wayland_session

mod = Module()
mod.tag(
    "wayland_mouse_forwarder",
    desc="Enable Wayland mouse forwarder command overrides.",
)

ctx = Context()
ctx.matches = r"""
os: linux
"""


@mod.action_class
class Actions:
    def mouse_forwarder_touch():
        """Click normally, or release an active drag."""

    def mouse_forwarder_modified_click(modifiers: str, button: int = 0):
        """Click while holding modifiers via native Wayland input."""

    def mouse_forwarder_native_pointer_selected() -> bool:
        """Return whether new pointer effects should use native Wayland."""


_vertical_scroll_remainder = 0.0
_horizontal_scroll_remainder = 0.0
_scroll_lock = threading.Lock()
_FALLBACK_BUTTONS_KEY = "_jm_talon_lite_fallback_mouse_buttons"
_fallback_held_buttons = set(getattr(sys, _FALLBACK_BUTTONS_KEY, ()))


def _publish_fallback_buttons() -> None:
    """Retain fallback drag ownership across Talon script reloads."""
    setattr(sys, _FALLBACK_BUTTONS_KEY, tuple(sorted(_fallback_held_buttons)))


def _record_fallback_button(button: int, pressed: bool) -> None:
    """Update the buttons whose drag lifecycle belongs to Talon's fallback."""
    if pressed:
        _fallback_held_buttons.add(button)
    else:
        _fallback_held_buttons.discard(button)
    _publish_fallback_buttons()


def _is_wayland() -> bool:
    """Return whether Talon is running in a Wayland session."""
    return is_wayland_session(os.environ)


def _native_pointer_available() -> bool:
    """Return whether this session currently has native pointer output."""
    if not _is_wayland():
        return False
    try:
        return actions.user.wayland_pointer_available()
    except Exception:
        return False


def _native_keyboard_available() -> bool:
    """Return whether this session currently has native keyboard output."""
    if not _is_wayland():
        return False
    try:
        return actions.user.wayland_keyboard_available()
    except Exception:
        return False


def _use_native_pointer() -> bool:
    """Return whether new pointer effects should use the native backend."""
    return not _fallback_held_buttons and _native_pointer_available()


def _fallback_modified_click(modifiers: str, button: int) -> None:
    """Perform a modified click through the next Talon implementations."""
    if _native_keyboard_available():
        try:
            token = actions.user.wayland_keyboard_modifiers_begin(modifiers)
        except CapabilityUnavailable:
            pass
        else:
            try:
                actions.mouse_click(button)
            finally:
                actions.user.wayland_keyboard_modifiers_end(token)
            return
    actions.key(f"{modifiers}:down")
    try:
        actions.mouse_click(button)
    finally:
        actions.key(f"{modifiers}:up")


def _scaled_scroll_delta(delta: float, setting_name: str) -> float:
    """Scale one Talon wheel delta into native discrete-step units."""
    unit = settings.get(setting_name)
    if unit == 0:
        return delta
    return delta / unit


def _is_fractional(delta: float) -> bool:
    """Return whether a Talon float contains a fractional component."""
    return isinstance(delta, float) and not delta.is_integer()


def _forward_scroll(
    vertical_delta: float = 0.0,
    horizontal_delta: float = 0.0,
    *,
    by_lines: bool = False,
) -> None:
    """Forward fractional lines continuously and accumulate other wheel deltas."""
    global _horizontal_scroll_remainder, _vertical_scroll_remainder

    with _scroll_lock:
        if by_lines and (
            _is_fractional(vertical_delta) or _is_fractional(horizontal_delta)
        ):
            actions.user.wayland_pointer_scroll_continuous(
                vertical_lines=vertical_delta,
                horizontal_lines=horizontal_delta,
            )
            return

        next_vertical_remainder = _vertical_scroll_remainder
        next_horizontal_remainder = _horizontal_scroll_remainder
        vertical_steps = 0
        horizontal_steps = 0

        if vertical_delta:
            scaled = (
                vertical_delta
                if by_lines
                else _scaled_scroll_delta(
                    vertical_delta,
                    "user.mouse_wheel_down_amount",
                )
            )
            vertical_steps, next_vertical_remainder = accumulate_steps(
                scaled,
                _vertical_scroll_remainder,
            )
        if horizontal_delta:
            scaled = (
                horizontal_delta
                if by_lines
                else _scaled_scroll_delta(
                    horizontal_delta,
                    "user.mouse_wheel_horizontal_amount",
                )
            )
            horizontal_steps, next_horizontal_remainder = accumulate_steps(
                scaled,
                _horizontal_scroll_remainder,
            )

        if vertical_steps or horizontal_steps:
            # CapabilityUnavailable is preflight-safe; other failures may emit.
            try:
                actions.user.wayland_pointer_scroll(
                    vertical_steps=vertical_steps,
                    horizontal_steps=horizontal_steps,
                )
            except CapabilityUnavailable:
                raise
            except Exception:
                _vertical_scroll_remainder = next_vertical_remainder
                _horizontal_scroll_remainder = next_horizontal_remainder
                raise

        _vertical_scroll_remainder = next_vertical_remainder
        _horizontal_scroll_remainder = next_horizontal_remainder


@ctx.action_class("main")
class MainActions:
    def mouse_click(button: int = 0):
        """Click through the native pointer or the next implementation."""
        if not _use_native_pointer():
            actions.next(button)
            return
        try:
            actions.user.wayland_pointer_click(button)
        except (CapabilityUnavailable, ValueError):
            actions.next(button)

    def mouse_drag(button: int = 0):
        """Start a native pointer drag or use the next implementation."""
        if not _use_native_pointer():
            actions.next(button)
            _record_fallback_button(button, True)
            return
        try:
            actions.user.wayland_pointer_button_down(button)
        except (CapabilityUnavailable, ValueError):
            actions.next(button)
            _record_fallback_button(button, True)

    def mouse_release(button: int = 0):
        """Release a native pointer button or use the next implementation."""
        if not _use_native_pointer():
            actions.next(button)
            _record_fallback_button(button, False)
            return
        try:
            actions.user.wayland_pointer_button_up(button)
        except (CapabilityUnavailable, ValueError):
            actions.next(button)
            _record_fallback_button(button, False)

    def mouse_move(x: float, y: float):
        """Normalize and forward an absolute Talon pointer position."""
        if not _use_native_pointer():
            actions.next(x, y)
            return
        rects = [
            (screen.rect.x, screen.rect.y, screen.rect.width, screen.rect.height)
            for screen in ui.screens()
        ]
        bounds = desktop_bounds(rects)
        normalized_x, normalized_y = normalize_point(bounds, x, y)
        try:
            actions.user.wayland_pointer_move_absolute(normalized_x, normalized_y)
        except CapabilityUnavailable:
            actions.next(x, y)

    def mouse_scroll(y: float = 0.0, x: float = 0.0, by_lines: bool = False):
        """Forward Talon wheel deltas through the native pointer."""
        if not _use_native_pointer():
            actions.next(y, x, by_lines)
            return
        try:
            _forward_scroll(y, x, by_lines=by_lines)
        except CapabilityUnavailable:
            actions.next(y, x, by_lines)


@ctx.action_class("user")
class UserActions:
    def mouse_forwarder_native_pointer_selected() -> bool:
        """Return whether native Wayland owns the current pointer lifecycle."""
        return _use_native_pointer()

    def mouse_forwarder_touch():
        """End a native drag or perform a normal left click."""
        if actions.user.mouse_drag_end():
            return
        actions.mouse_click(0)

    def mouse_forwarder_modified_click(modifiers: str, button: int = 0):
        """Perform a modified click through native output when available."""
        if not _use_native_pointer():
            _fallback_modified_click(modifiers, button)
            return
        try:
            actions.user.wayland_pointer_modified_click(modifiers, button)
        except CapabilityUnavailable:
            _fallback_modified_click(modifiers, button)

    def mouse_drag_end() -> bool:
        """Release all native buttons and report whether a drag ended."""
        if _fallback_held_buttons:
            actions.next()
            _fallback_held_buttons.clear()
            _publish_fallback_buttons()
            return True
        if not _native_pointer_available():
            return actions.next()
        try:
            return actions.user.wayland_pointer_release_all()
        except CapabilityUnavailable:
            return actions.next()

    def mouse_drag_toggle(button: int):
        """Toggle a native drag button or use the next implementation."""
        if not _use_native_pointer():
            actions.next(button)
            return
        try:
            actions.user.wayland_pointer_button_toggle(button)
        except (CapabilityUnavailable, ValueError):
            actions.next(button)


def _on_ready() -> None:
    """Enable mouse-forwarder command tags for Wayland sessions."""
    if not _is_wayland():
        ctx.tags = []
        return
    ctx.tags = ["user.wayland_mouse_forwarder"]


app.register("ready", _on_ready)
_on_ready()
