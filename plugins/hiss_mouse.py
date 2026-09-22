"""Drive Control Mouse and clicking from Talon hiss and pop events."""

import sys

from talon import Context, Module, actions, app, settings

ctx = Context()
mod = Module()

mod.setting(
    "hiss_mouse_autostart",
    type=bool,
    default=False,
    desc="Enable hiss mouse on Talon startup.",
)

_STATE_KEY = "_jm_talon_lite_hiss_mouse_enabled"
_hiss_mouse_enabled = bool(getattr(sys, _STATE_KEY, False))


def _publish_state() -> None:
    """Retain manual Hiss Mouse state across Talon script reloads."""
    setattr(sys, _STATE_KEY, _hiss_mouse_enabled)


def _forward_left_click() -> None:
    """Click normally, or release an active drag without another click."""
    if actions.user.mouse_drag_end():
        return
    actions.mouse_click(0)


def _enable_hiss_mouse() -> None:
    """Establish the enabled Hiss Mouse state."""
    global _hiss_mouse_enabled
    _hiss_mouse_enabled = True
    _publish_state()


def _disable_hiss_mouse() -> None:
    """Disable Hiss Mouse and stop Control Mouse when active."""
    global _hiss_mouse_enabled
    _hiss_mouse_enabled = False
    _publish_state()
    if not actions.tracking.control1_enabled():
        return
    actions.tracking.control1_toggle(False)


def _set_hiss_mouse_enabled(enabled: bool) -> None:
    """Establish the requested Hiss Mouse state idempotently."""
    if enabled:
        _enable_hiss_mouse()
        return
    _disable_hiss_mouse()


@ctx.action_class("user")
class UserActions:
    @staticmethod
    def noise_trigger_hiss(active: bool):
        """Start or stop Control Mouse while an enabled hiss is active."""
        if not _hiss_mouse_enabled:
            return
        if active:
            actions.tracking.control1_toggle(True)
            return
        actions.tracking.control1_toggle(False)

    @staticmethod
    def noise_trigger_pop():
        """Click once for an enabled Talon pop event."""
        if not _hiss_mouse_enabled:
            return
        _forward_left_click()


@mod.action_class
class Actions:
    @staticmethod
    def hiss_mouse_enable() -> None:
        """Enable hiss mouse behavior."""
        _set_hiss_mouse_enabled(True)

    @staticmethod
    def hiss_mouse_disable() -> None:
        """Disable hiss mouse behavior."""
        _set_hiss_mouse_enabled(False)

    @staticmethod
    def hiss_mouse_toggle(state: bool | None = None) -> None:
        """Toggle hiss mouse behavior."""
        target = not _hiss_mouse_enabled if state is None else bool(state)
        _set_hiss_mouse_enabled(target)

    @staticmethod
    def hiss_mouse_enabled() -> bool:
        """Return whether hiss mouse behavior is enabled."""
        return _hiss_mouse_enabled


def _on_ready() -> None:
    """Enable Hiss Mouse when its Talon autostart setting is set."""
    if hasattr(sys, _STATE_KEY):
        return
    if settings.get("user.hiss_mouse_autostart"):
        actions.user.hiss_mouse_enable()
    else:
        _publish_state()


app.register("ready", _on_ready)
