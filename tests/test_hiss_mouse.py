import sys
import types
import unittest
from pathlib import Path


if __package__:
    from .talon_fakes import FakeApp, FakeContext, FakeModule, load_talon_module
else:
    from talon_fakes import FakeApp, FakeContext, FakeModule, load_talon_module


class FakeSettings:
    def __init__(self, autostart):
        self.autostart = autostart

    def get(self, _name):
        return self.autostart


class FakeActions:
    def __init__(self):
        self.control1_enabled = False
        self.control1_toggles = []
        self.drag_active = False
        self.drag_end_count = 0
        self.click_calls = []
        self.tracking = types.SimpleNamespace(
            control1_enabled=lambda: self.control1_enabled,
            control1_toggle=self._toggle_control1,
        )
        self.user = types.SimpleNamespace(
            mouse_drag_end=self._drag_end,
        )
        self.mouse_click = self.click_calls.append

    def _toggle_control1(self, enabled):
        self.control1_enabled = enabled
        self.control1_toggles.append(enabled)

    def _drag_end(self):
        self.drag_end_count += 1
        active = self.drag_active
        self.drag_active = False
        return active


def load_hiss_module(*, clear_state, autostart=False):
    root = Path(__file__).resolve().parents[1]
    talon = types.ModuleType("talon")
    talon.Context = FakeContext
    talon.Module = FakeModule
    talon.actions = FakeActions()
    talon.app = FakeApp()
    talon.settings = FakeSettings(autostart)
    path = root / "plugins" / "hiss_mouse.py"
    key = "_jm_talon_lite_hiss_mouse_enabled"
    old_state = getattr(sys, key, None)
    if clear_state and hasattr(sys, key):
        delattr(sys, key)
    module = load_talon_module("plugins.hiss_mouse_under_test", path, {"talon": talon})
    talon.actions.user.hiss_mouse_enable = module.Actions.hiss_mouse_enable
    return module, talon, old_state


class HissMouseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module, cls.talon, cls.old_state = load_hiss_module(clear_state=True)

    @classmethod
    def tearDownClass(cls):
        key = cls.module._STATE_KEY
        if hasattr(sys, key):
            delattr(sys, key)
        if cls.old_state is not None:
            setattr(sys, key, cls.old_state)

    def setUp(self):
        self.module._set_hiss_mouse_enabled(False)
        self.talon.actions.control1_enabled = False
        self.talon.actions.control1_toggles.clear()
        self.talon.actions.drag_active = False
        self.talon.actions.drag_end_count = 0
        self.talon.actions.click_calls.clear()

    def test_pop_releases_a_drag_or_clicks_once(self):
        self.module.Actions.hiss_mouse_enable()

        self.talon.actions.drag_active = True
        self.module.UserActions.noise_trigger_pop()
        self.assertEqual(self.talon.actions.drag_end_count, 1)
        self.assertEqual(self.talon.actions.click_calls, [])

        self.module.UserActions.noise_trigger_pop()
        self.assertEqual(self.talon.actions.drag_end_count, 2)
        self.assertEqual(self.talon.actions.click_calls, [0])

    def test_manual_enabled_state_survives_script_reload(self):
        self.module.Actions.hiss_mouse_enable()

        reloaded, _talon, _old_state = load_hiss_module(clear_state=False)

        self.assertTrue(reloaded.Actions.hiss_mouse_enabled())

    def test_reload_does_not_reapply_autostart_after_explicit_disable(self):
        self.module.Actions.hiss_mouse_disable()

        reloaded, _talon, _old_state = load_hiss_module(
            clear_state=False,
            autostart=True,
        )
        reloaded._on_ready()

        self.assertFalse(reloaded.Actions.hiss_mouse_enabled())

    def test_reload_before_ready_still_applies_autostart(self):
        first, _talon, _old_state = load_hiss_module(clear_state=True, autostart=True)
        self.assertFalse(hasattr(sys, first._STATE_KEY))
        reloaded, _talon, _old_state = load_hiss_module(
            clear_state=False, autostart=True
        )
        reloaded._on_ready()
        self.assertTrue(reloaded.Actions.hiss_mouse_enabled())

    def test_explicit_disable_before_ready_is_preserved(self):
        loaded, _talon, _old_state = load_hiss_module(clear_state=True, autostart=True)
        loaded.Actions.hiss_mouse_disable()
        loaded._on_ready()
        self.assertFalse(loaded.Actions.hiss_mouse_enabled())

    def test_disabled_startup_does_not_toggle_control_mouse(self):
        loaded, talon, _old_state = load_hiss_module(clear_state=True, autostart=False)
        talon.actions.control1_enabled = True
        loaded._on_ready()
        talon.settings.autostart = True
        loaded._on_ready()
        self.assertFalse(loaded.Actions.hiss_mouse_enabled())
        self.assertTrue(talon.actions.control1_enabled)
        self.assertEqual(talon.actions.control1_toggles, [])


if __name__ == "__main__":
    unittest.main()
