"""Forward control1 gaze samples through the native Wayland pointer."""

import os
import sys

from talon import Module, actions, app, settings, tracking_system

_CALLBACK_KEY = "_jm_talon_lite_control1_pointer_callback"
_STATE_KEY = "_jm_talon_lite_control1_pointer_enabled"
# Talon does not automatically remove tracking callbacks on script reload.
_retained_callback = getattr(sys, _CALLBACK_KEY, None)
if _retained_callback is not None:
    tracking_system.unregister("gaze", _retained_callback)
    if getattr(sys, _CALLBACK_KEY, None) is _retained_callback:
        delattr(sys, _CALLBACK_KEY)

from talon.plugins import eye_mouse  # noqa: E402

from ..wayland_backend.errors import CapabilityUnavailable  # noqa: E402
from ..wayland_backend.session import is_wayland_session  # noqa: E402

mod = Module()
mod.setting(
    "control1_pointer_forwarder_autostart",
    type=bool,
    default=False,
    desc="Auto-start native control1 pointer forwarding at Talon startup.",
)

_registered = False


def _is_wayland() -> bool:
    """Return whether Talon is running in a Wayland session."""
    return is_wayland_session(os.environ)


def _native_pointer_available() -> bool:
    """Return whether this session currently has native pointer output."""
    if not _is_wayland():
        return False
    try:
        return actions.user.mouse_forwarder_native_pointer_selected()
    except Exception:
        return False


def _register_gaze() -> None:
    """Register exactly one process-retained gaze callback."""
    global _registered
    setattr(sys, _STATE_KEY, True)
    if _registered:
        return
    tracking_system.register("gaze", _on_gaze)
    setattr(sys, _CALLBACK_KEY, _on_gaze)
    _registered = True


def _unregister_gaze() -> None:
    """Remove this module generation's gaze callback idempotently."""
    global _registered
    setattr(sys, _STATE_KEY, False)
    if not _registered:
        return
    tracking_system.unregister("gaze", _on_gaze)
    if getattr(sys, _CALLBACK_KEY, None) is _on_gaze:
        delattr(sys, _CALLBACK_KEY)
    _registered = False


def _on_gaze(*_args) -> None:
    """Forward the latest enabled Control Mouse point to the pointer."""
    if not actions.tracking.control1_enabled():
        return
    hist = eye_mouse.mouse.xy_hist
    if not hist:
        return
    point = hist[-1]
    if not _native_pointer_available():
        actions.mouse_move(point.x, point.y)
        return
    try:
        actions.user.wayland_pointer_move_main_screen(
            point.x,
            point.y,
            refresh_hover=True,
        )
    except CapabilityUnavailable:
        actions.mouse_move(point.x, point.y)


@mod.action_class
class Actions:
    def control1_pointer_forwarder_start() -> None:
        """Start control1 forwarding through the native Wayland pointer."""
        _register_gaze()

    def control1_pointer_forwarder_stop() -> None:
        """Stop control1 pointer forwarding."""
        _unregister_gaze()

    def control1_pointer_forwarder_toggle(state: bool | None = None) -> None:
        """Enable, disable, or toggle control1 pointer forwarding."""
        target = not _registered if state is None else bool(state)
        if target:
            actions.user.control1_pointer_forwarder_start()
        else:
            actions.user.control1_pointer_forwarder_stop()


def _on_ready() -> None:
    """Resolve startup intent once, then reconcile callback ownership."""
    enabled = getattr(sys, _STATE_KEY, None)
    if enabled is None:
        enabled = bool(settings.get("user.control1_pointer_forwarder_autostart"))
        setattr(sys, _STATE_KEY, enabled)
    if enabled and not _registered:
        actions.user.control1_pointer_forwarder_start()
    elif not enabled and _registered:
        _unregister_gaze()


def _on_quit() -> None:
    """Release gaze subscriptions before Talon exits."""
    _unregister_gaze()


if getattr(sys, _STATE_KEY, False):
    _register_gaze()

app.register("ready", _on_ready)
app.register("quit", _on_quit)
