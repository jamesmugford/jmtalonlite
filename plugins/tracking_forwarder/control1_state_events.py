"""Event-driven hooks for Control Mouse enabled-state transitions."""

from talon import Context, Module, actions, app
from talon.plugins import eye_mouse_2

ctx = Context()
mod = Module()


def _emit_control1_state(enabled: bool) -> None:
    """Publish Control Mouse state through the user notification hook."""
    print(f"control1 enabled={enabled}")
    actions.user.control1_state_changed(enabled)


def _install_menu_hook() -> bool:
    """Wrap Talon's Control Mouse menu callback exactly once."""
    item = getattr(eye_mouse_2, "control1_item", None)
    if item is None:
        return False

    attrs = getattr(item, "attrs", None)
    if attrs is None:
        return False

    cb = attrs.get("cb")
    if cb is None:
        return False

    original = attrs.get("_control1_state_menu_original", cb)

    def wrapped(menu_item):
        """Run Talon's callback and publish a resulting state change."""
        before = actions.tracking.control1_enabled()
        result = original(menu_item)
        after = actions.tracking.control1_enabled()
        if before == after:
            return result
        _emit_control1_state(after)
        return result

    attrs["cb"] = wrapped
    attrs["_control1_state_menu_original"] = original
    return True


@ctx.action_class("tracking")
class TrackingActions:
    @staticmethod
    def control1_toggle(state=None) -> None:
        """Wrap control1 toggle and emit state hooks."""
        before = actions.tracking.control1_enabled()
        actions.next(state)
        after = actions.tracking.control1_enabled()
        if before == after:
            return
        _emit_control1_state(after)


@mod.action_class
class Actions:
    @staticmethod
    def control1_state_changed(enabled: bool) -> None:
        """Hook called when Control Mouse changes state; optional to override."""
        actions.skip()

    @staticmethod
    def control1_state_emit_now() -> None:
        """Emit current control1 state through hook actions."""
        _emit_control1_state(actions.tracking.control1_enabled())


def _on_ready() -> None:
    """Install the Control Mouse menu hook after Talon initialization."""
    _install_menu_hook()


app.register("ready", _on_ready)
