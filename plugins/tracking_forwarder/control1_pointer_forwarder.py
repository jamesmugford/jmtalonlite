"""Forward control1 gaze samples through the native Wayland pointer."""

import os
import sys

import talon
from talon import Module, actions, app, settings

_CALLBACK_KEY = "_jm_talon_lite_control1_pointer_callback"
_STATE_KEY = "_jm_talon_lite_control1_pointer_enabled"
# Talon does not automatically remove tracking callbacks on script reload.
_retained_callback = getattr(sys, _CALLBACK_KEY, None)
if _retained_callback is not None:
    # The currently loaded generation may predate queued gaze delivery.
    if hasattr(_retained_callback, "stop"):
        _retained_callback.stop()
    with talon.scripting.rctx.main.enter():
        talon.tracking_system.unregister("gaze", _retained_callback)
    if getattr(sys, _CALLBACK_KEY, None) is _retained_callback:
        delattr(sys, _CALLBACK_KEY)

from ..wayland_backend.errors import CapabilityUnavailable  # noqa: E402
from ..wayland_backend.session import is_wayland_session  # noqa: E402
from .gaze_dispatch import GazeDispatch  # noqa: E402

mod = Module()
mod.setting(
    "control1_pointer_forwarder_autostart",
    type=bool,
    default=False,
    desc="Auto-start native control1 pointer forwarding at Talon startup.",
)

_registered = False
eye_mouse = None


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
    global _registered, eye_mouse
    setattr(sys, _STATE_KEY, True)
    if _registered:
        return
    # Talon 1.0 initializes tracking/plugins after loading user scripts.
    eye_mouse = talon.plugins.eye_mouse
    # Talon 1.0 filters gaze subscriptions owned by user.* resource contexts.
    # Own this subscription for the process and retire its exact callback on
    # stop, script reload, and quit. Raw events are coalesced onto Talon's scheduler.
    with talon.scripting.rctx.main.enter():
        talon.tracking_system.register("gaze", _on_gaze)
    _on_gaze.start()
    setattr(sys, _CALLBACK_KEY, _on_gaze)
    _registered = True


def _unregister_gaze() -> None:
    """Remove this module generation's gaze callback idempotently."""
    global _registered
    setattr(sys, _STATE_KEY, False)
    if not _registered:
        return
    _on_gaze.stop()
    with talon.scripting.rctx.main.enter():
        talon.tracking_system.unregister("gaze", _on_gaze)
    if getattr(sys, _CALLBACK_KEY, None) is _on_gaze:
        delattr(sys, _CALLBACK_KEY)
    _registered = False


def _forward_gaze() -> None:
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


_on_gaze = GazeDispatch(_forward_gaze, talon.cron)


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
