"""Event-driven diagnostic logging for Control Mouse gaze samples."""

import sys

from talon import Module, actions, app, settings, tracking_system

_CALLBACK_KEY = "_jm_talon_lite_control1_gaze_logger_callback"
_STATE_KEY = "_jm_talon_lite_control1_gaze_logger_enabled"

# Retire the exact previous callback before importing optional dependencies.
_retained_callback = getattr(sys, _CALLBACK_KEY, None)
if _retained_callback is not None:
    tracking_system.unregister("gaze", _retained_callback)
    if getattr(sys, _CALLBACK_KEY, None) is _retained_callback:
        delattr(sys, _CALLBACK_KEY)

from talon.plugins import eye_mouse  # noqa: E402

from .gaze_sample import GazeSample, format_gaze_sample  # noqa: E402

mod = Module()
mod.setting(
    "control1_gaze_logger_autostart",
    type=bool,
    default=False,
    desc="Auto-start control1 gaze logger at Talon startup.",
)
_registered = False


def _control1_sample_line() -> str:
    """Return a formatted line for the latest Control Mouse sample."""
    mouse = eye_mouse.mouse
    if not mouse.xy_hist or not mouse.eye_hist:
        return format_gaze_sample(None)

    position = mouse.xy_hist[-1]
    delta = mouse.delta_hist[-1] if mouse.delta_hist else None
    eye_sample = mouse.eye_hist[-1]
    return format_gaze_sample(
        GazeSample(
            timestamp=eye_sample.ts,
            x=position.x,
            y=position.y,
            gaze_x=eye_sample.gaze.x,
            gaze_y=eye_sample.gaze.y,
            delta_x=None if delta is None else delta.x,
            delta_y=None if delta is None else delta.y,
        )
    )


def _on_gaze(*_args) -> None:
    """Log the latest sample while Control Mouse is enabled."""
    if not actions.tracking.control1_enabled():
        return
    print(_control1_sample_line())


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


@mod.action_class
class Actions:
    @staticmethod
    def control1_gaze_logger_start() -> None:
        """Enable control1 gaze logger (gaze-event driven)."""
        _register_gaze()
        print(
            "control1_gaze_logger started mode=gaze "
            f"enabled={actions.tracking.control1_enabled()}"
        )

    @staticmethod
    def control1_gaze_logger_stop() -> None:
        """Disable control1 gaze logger."""
        _unregister_gaze()
        print("control1_gaze_logger stopped")

    @staticmethod
    def control1_gaze_logger_once() -> None:
        """Log one control1 eye tracking sample."""
        print(_control1_sample_line())


def _on_ready() -> None:
    """Resolve startup intent once, then reconcile callback ownership."""
    enabled = getattr(sys, _STATE_KEY, None)
    if enabled is None:
        enabled = bool(settings.get("user.control1_gaze_logger_autostart"))
        setattr(sys, _STATE_KEY, enabled)
    if enabled and not _registered:
        actions.user.control1_gaze_logger_start()
    elif not enabled and _registered:
        _unregister_gaze()


def _on_quit() -> None:
    """Release gaze subscriptions before Talon exits."""
    _unregister_gaze()


if getattr(sys, _STATE_KEY, False):
    _register_gaze()

app.register("ready", _on_ready)
app.register("quit", _on_quit)
