"""Per-screen debug overlays for the current Control Mouse gaze point."""

import sys

from talon import Context, Module, actions, app, tracking_system, ui

_RELOAD_STATE_KEY = "_jm_talon_lite_control1_overlay_state"
_previous_enabled, _previous_callbacks, _previous_entries = getattr(
    sys, _RELOAD_STATE_KEY, None
) or (False, (), ())

if _previous_callbacks or _previous_entries:
    _previous_failures = []
    _previous_failed_callbacks = []
    _previous_remaining = []
    for _previous_callback in _previous_callbacks:
        try:
            tracking_system.unregister("gaze", _previous_callback)
        except Exception as exc:
            _previous_failures.append(("gaze callback", exc))
            _previous_failed_callbacks.append(_previous_callback)
    for _previous_canvas, _previous_draw in _previous_entries:
        try:
            _previous_canvas.unregister("draw", _previous_draw)
        except Exception as exc:
            _previous_failures.append(("canvas callback", exc))
        try:
            _previous_canvas.close()
        except Exception as exc:
            _previous_remaining.append((_previous_canvas, _previous_draw))
            _previous_failures.append(("canvas", exc))
    if _previous_failures:
        setattr(
            sys,
            _RELOAD_STATE_KEY,
            (
                _previous_enabled,
                tuple(_previous_failed_callbacks),
                tuple(_previous_remaining),
            ),
        )
        _label, _previous_error = _previous_failures[0]
        for _label, _secondary in _previous_failures[1:]:
            _previous_error.add_note(
                f"{_label} also failed: {type(_secondary).__name__}: {_secondary}"
            )
        raise _previous_error.with_traceback(_previous_error.__traceback__)
setattr(sys, _RELOAD_STATE_KEY, (_previous_enabled, (), ()))

from talon.canvas import Canvas  # noqa: E402
from talon.plugins import eye_mouse  # noqa: E402

from ..wayland_backend.connection import run_cleanup_steps  # noqa: E402
from ..wayland_backend.geometry import local_point  # noqa: E402

ctx = Context()
mod = Module()

_overlay_enabled = _previous_enabled
_gaze_registered = False
_dot_pos = None
_canvas_entries = []


def _make_draw(rect):
    """Return a canvas callback that draws the current gaze point."""

    def _draw(c):
        """Draw the gaze marker when it falls inside this screen."""
        if _dot_pos is None:
            return

        x, y = _dot_pos
        point = local_point(rect, x, y)
        if point is None:
            return

        lx, ly = point

        c.paint.style = c.paint.Style.STROKE
        c.paint.color = "00ff00cc"
        c.paint.stroke_width = 2
        c.draw_circle(lx, ly, 18)

        c.paint.style = c.paint.Style.FILL
        c.paint.color = "00ff00ff"
        c.draw_circle(lx, ly, 4)

    return _draw


def _publish_reload_state() -> None:
    """Retain exact callback and canvas identities across Talon reloads."""
    callbacks = (_on_gaze,) if _gaze_registered else ()
    setattr(
        sys,
        _RELOAD_STATE_KEY,
        (_overlay_enabled, callbacks, tuple(_canvas_entries)),
    )


def _close_canvases() -> None:
    """Close every overlay canvas while preserving the first failure."""
    global _canvas_entries
    entries = _canvas_entries
    remaining = []
    first_error = None
    for canvas, draw_cb in entries:
        try:
            canvas.unregister("draw", draw_cb)
        except Exception as exc:
            if first_error is None:
                first_error = exc
            else:
                first_error.add_note(
                    f"Canvas unregister also failed: {type(exc).__name__}: {exc}"
                )
        try:
            canvas.close()
        except Exception as exc:
            remaining.append((canvas, draw_cb))
            if first_error is None:
                first_error = exc
            else:
                first_error.add_note(
                    f"Canvas close also failed: {type(exc).__name__}: {exc}"
                )
    _canvas_entries = remaining
    _publish_reload_state()
    if first_error is not None:
        raise first_error.with_traceback(first_error.__traceback__)


def _create_canvases() -> None:
    """Recreate one gaze overlay canvas for every Talon screen."""
    global _canvas_entries
    _close_canvases()

    entries = []
    try:
        for screen in ui.screens():
            rect = screen.rect
            draw_cb = _make_draw((rect.x, rect.y, rect.width, rect.height))
            canvas = Canvas.from_screen(screen)
            entries.append((canvas, draw_cb))
            canvas.register("draw", draw_cb)
    except Exception as exc:
        _canvas_entries = entries
        try:
            _close_canvases()
        except Exception as cleanup_error:
            exc.add_note(
                "Partial canvas cleanup also failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
        raise

    _canvas_entries = entries
    _publish_reload_state()


def _register_gaze() -> None:
    """Register this generation's gaze callback exactly once."""
    global _gaze_registered
    if _gaze_registered:
        return
    tracking_system.register("gaze", _on_gaze)
    _gaze_registered = True
    _publish_reload_state()


def _clear_overlay() -> None:
    """Clear the marker from every open overlay canvas."""
    global _dot_pos
    _dot_pos = None
    for canvas, _draw_cb in _canvas_entries:
        canvas.freeze()


def _unregister_gaze() -> None:
    """Unregister this generation's gaze callback idempotently."""
    global _gaze_registered
    if not _gaze_registered:
        return
    tracking_system.unregister("gaze", _on_gaze)
    _gaze_registered = False
    _publish_reload_state()


def _teardown_overlay() -> None:
    """Release gaze and canvas resources while preserving all failures."""
    run_cleanup_steps(
        (("gaze callback", _unregister_gaze), ("canvases", _close_canvases))
    )


def _sync_overlay() -> None:
    """Match gaze subscription state to overlay and Control Mouse state."""
    if not _overlay_enabled:
        _unregister_gaze()
        _clear_overlay()
        return

    if not actions.tracking.control1_enabled():
        _unregister_gaze()
        _clear_overlay()
        return

    _register_gaze()


def _on_gaze(*_args) -> None:
    """Update every canvas from the latest enabled Control Mouse sample."""
    global _dot_pos
    if not _overlay_enabled:
        return

    if not actions.tracking.control1_enabled():
        return

    hist = eye_mouse.mouse.xy_hist
    if not hist:
        return

    point = hist[-1]
    _dot_pos = (point.x, point.y)

    for canvas, _draw_cb in _canvas_entries:
        canvas.freeze()


def _on_screen_change(_screens) -> None:
    """Rebuild enabled overlays after Talon's screen layout changes."""
    global _overlay_enabled
    if not _overlay_enabled:
        return
    try:
        _create_canvases()
    except Exception as exc:
        _overlay_enabled = False
        try:
            _teardown_overlay()
        except Exception as cleanup_error:
            exc.add_note(
                "Overlay rebuild rollback also failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
        raise


@ctx.action_class("user")
class UserActions:
    @staticmethod
    def control1_state_changed(enabled: bool) -> None:
        """Synchronize the overlay after Control Mouse changes state."""
        actions.next(enabled)
        _sync_overlay()


@mod.action_class
class Actions:
    @staticmethod
    def control1_debug_overlay_start() -> None:
        """Start control1 debug overlay."""
        global _overlay_enabled
        if _overlay_enabled:
            print("control1_debug_overlay already running")
            return
        _overlay_enabled = True
        try:
            _create_canvases()
            _sync_overlay()
        except Exception as exc:
            _overlay_enabled = False
            try:
                _teardown_overlay()
            except Exception as cleanup_error:
                exc.add_note(
                    "Overlay rollback also failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            raise
        print("control1_debug_overlay started")

    @staticmethod
    def control1_debug_overlay_stop() -> None:
        """Stop control1 debug overlay."""
        global _overlay_enabled
        _overlay_enabled = False
        _teardown_overlay()
        print("control1_debug_overlay stopped")

    @staticmethod
    def control1_debug_overlay_toggle(state: bool | None = None) -> None:
        """Toggle control1 debug overlay."""
        target = (not _overlay_enabled) if state is None else bool(state)
        if not target:
            actions.user.control1_debug_overlay_stop()
            return
        actions.user.control1_debug_overlay_start()

    @staticmethod
    def control1_debug_overlay_running() -> bool:
        """Return whether control1 debug overlay is enabled."""
        return _overlay_enabled


def _on_ready() -> None:
    """Subscribe to Talon screen-layout changes."""
    ui.register("screen_change", _on_screen_change)


def _on_quit() -> None:
    """Release every tracking callback and canvas before Talon exits."""
    global _overlay_enabled
    _overlay_enabled = False
    _teardown_overlay()
    if not _gaze_registered and not _canvas_entries and hasattr(sys, _RELOAD_STATE_KEY):
        delattr(sys, _RELOAD_STATE_KEY)


if _overlay_enabled:
    try:
        _create_canvases()
        _sync_overlay()
    except Exception as exc:
        _overlay_enabled = False
        try:
            _teardown_overlay()
        except Exception as cleanup_error:
            exc.add_note(
                "Overlay reload rollback also failed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
        raise
else:
    _publish_reload_state()
app.register("ready", _on_ready)
app.register("quit", _on_quit)
