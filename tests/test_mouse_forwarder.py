import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class FakeContext:
    def __init__(self):
        self.matches = ""
        self.tags = []

    def action_class(self, _action_namespace):
        return lambda cls: cls


class FakeModule:
    def tag(self, _name, *, desc):
        pass

    def action_class(self, cls):
        return cls


class FakeApp:
    def register(self, _event, _callback):
        pass


class FakeSettings:
    _values = {
        "user.mouse_wheel_down_amount": 1.0,
        "user.mouse_wheel_horizontal_amount": 1.0,
    }

    def get(self, name):
        return self._values[name]


class FakeActions:
    def __init__(self):
        self.next_calls = []
        self.scroll_attempts = []
        self.scroll_emissions = []
        self.fail_scroll_call = None
        self.scroll_failure = None
        self.continuous_scroll_attempts = []
        self.continuous_scroll_emissions = []
        self.continuous_scroll_failure = None
        self.key_calls = []
        self.click_calls = []
        self.modifier_starts = []
        self.modifier_ends = []
        self.pointer_available = True
        self.native_button_down = []
        self.native_button_up = []
        self.user = types.SimpleNamespace(
            wayland_pointer_available=lambda: self.pointer_available,
            wayland_keyboard_available=lambda: True,
            wayland_keyboard_modifiers_begin=self._begin_modifiers,
            wayland_keyboard_modifiers_end=self.modifier_ends.append,
            wayland_pointer_button_down=self.native_button_down.append,
            wayland_pointer_button_up=self.native_button_up.append,
            wayland_pointer_scroll=self._scroll,
            wayland_pointer_scroll_continuous=self._scroll_continuous,
        )

    def next(self, *args):
        self.next_calls.append(args)

    def key(self, key_spec):
        self.key_calls.append(key_spec)

    def mouse_click(self, button):
        self.click_calls.append(button)

    def _begin_modifiers(self, modifiers):
        self.modifier_starts.append(modifiers)
        return 7

    def _scroll(self, vertical_steps=0, horizontal_steps=0):
        scroll = (vertical_steps, horizontal_steps)
        self.scroll_attempts.append(scroll)
        if len(self.scroll_attempts) == self.fail_scroll_call:
            raise self.scroll_failure
        self.scroll_emissions.append(scroll)

    def _scroll_continuous(self, vertical_lines=0.0, horizontal_lines=0.0):
        scroll = (vertical_lines, horizontal_lines)
        self.continuous_scroll_attempts.append(scroll)
        if self.continuous_scroll_failure is not None:
            raise self.continuous_scroll_failure
        self.continuous_scroll_emissions.append(scroll)


def load_mouse_forwarder_module():
    root = Path(__file__).resolve().parents[1]
    talon = types.ModuleType("talon")
    talon.Context = FakeContext
    talon.Module = FakeModule
    talon.actions = FakeActions()
    talon.app = FakeApp()
    talon.settings = FakeSettings()
    talon.ui = types.SimpleNamespace(screens=lambda: ())
    path = root / "plugins" / "mouse_forwarder.py"
    spec = importlib.util.spec_from_file_location(
        "plugins.mouse_forwarder_under_test",
        path,
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"talon": talon}):
        spec.loader.exec_module(module)
    return module, talon


class MouseForwarderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(
            patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}, clear=True)
        )
        cls.module, cls.talon = load_mouse_forwarder_module()

    def setUp(self):
        self.module._vertical_scroll_remainder = 0.0
        self.module._horizontal_scroll_remainder = 0.0
        self.module._fallback_held_buttons.clear()
        self.module._publish_fallback_buttons()
        self.talon.settings._values = {
            "user.mouse_wheel_down_amount": 1.0,
            "user.mouse_wheel_horizontal_amount": 1.0,
        }
        self.talon.actions.pointer_available = True
        self.talon.actions.next_calls.clear()
        self.talon.actions.scroll_attempts.clear()
        self.talon.actions.scroll_emissions.clear()
        self.talon.actions.fail_scroll_call = None
        self.talon.actions.scroll_failure = None
        self.talon.actions.continuous_scroll_attempts.clear()
        self.talon.actions.continuous_scroll_emissions.clear()
        self.talon.actions.continuous_scroll_failure = None
        self.talon.actions.key_calls.clear()
        self.talon.actions.click_calls.clear()
        self.talon.actions.modifier_starts.clear()
        self.talon.actions.modifier_ends.clear()
        self.talon.actions.native_button_down.clear()
        self.talon.actions.native_button_up.clear()

    def test_two_axis_scroll_is_one_native_transaction(self):
        self.module._vertical_scroll_remainder = 0.4
        self.module._horizontal_scroll_remainder = 0.3
        self.talon.actions.fail_scroll_call = 2
        self.talon.actions.scroll_failure = self.module.CapabilityUnavailable("lost")

        self.module.MainActions.mouse_scroll(0.8, 0.9)

        self.assertEqual(self.talon.actions.scroll_attempts, [(1, 1)])
        self.assertEqual(self.talon.actions.scroll_emissions, [(1, 1)])
        self.assertAlmostEqual(self.module._vertical_scroll_remainder, 0.2)
        self.assertAlmostEqual(self.module._horizontal_scroll_remainder, 0.2)
        self.assertEqual(self.talon.actions.next_calls, [])

    def test_unavailable_scroll_preserves_accumulated_remainders(self):
        self.module._vertical_scroll_remainder = 0.4
        self.module._horizontal_scroll_remainder = 0.3
        self.talon.actions.fail_scroll_call = 1
        self.talon.actions.scroll_failure = self.module.CapabilityUnavailable("lost")

        self.module.MainActions.mouse_scroll(0.8, 0.9)

        self.assertAlmostEqual(self.module._vertical_scroll_remainder, 0.4)
        self.assertAlmostEqual(self.module._horizontal_scroll_remainder, 0.3)
        self.assertEqual(self.talon.actions.scroll_attempts, [(1, 1)])
        self.assertEqual(self.talon.actions.scroll_emissions, [])
        self.assertEqual(self.talon.actions.next_calls, [(0.8, 0.9, False)])

    def test_modified_click_uses_native_temporary_modifier_token(self):
        self.module._fallback_modified_click("ctrl", 1)

        self.assertEqual(self.talon.actions.modifier_starts, ["ctrl"])
        self.assertEqual(self.talon.actions.click_calls, [1])
        self.assertEqual(self.talon.actions.modifier_ends, [7])
        self.assertEqual(self.talon.actions.key_calls, [])

    def test_line_scroll_values_are_already_native_steps(self):
        self.talon.settings._values = {
            "user.mouse_wheel_down_amount": 120.0,
            "user.mouse_wheel_horizontal_amount": 80.0,
        }

        self.module.MainActions.mouse_scroll(1.0, -2.0, by_lines=True)

        self.assertEqual(self.talon.actions.scroll_emissions, [(1, -2)])
        self.assertEqual(self.module._vertical_scroll_remainder, 0.0)
        self.assertEqual(self.module._horizontal_scroll_remainder, 0.0)

    def test_standard_wheel_units_use_the_shared_scroll_boundary(self):
        self.talon.settings._values = {
            "user.mouse_wheel_down_amount": 120,
            "user.mouse_wheel_horizontal_amount": 40,
        }
        for y, x, expected in (
            (120, 0, (1, 0)),
            (-120, 0, (-1, 0)),
            (0, 40, (0, 1)),
            (0, -40, (0, -1)),
        ):
            with self.subTest(y=y, x=x):
                self.talon.actions.scroll_emissions.clear()
                self.module.MainActions.mouse_scroll(y, x)
                self.assertEqual(self.talon.actions.scroll_emissions, [expected])

        self.assertEqual(self.talon.actions.continuous_scroll_attempts, [])

    def test_tiny_standard_wheel_amounts_accumulate_as_discrete_steps(self):
        self.talon.settings._values = {
            "user.mouse_wheel_down_amount": 120,
            "user.mouse_wheel_horizontal_amount": 40,
        }
        for _ in range(5):
            self.module.MainActions.mouse_scroll(24)
        for _ in range(2):
            self.module.MainActions.mouse_scroll(0, -20)

        self.assertEqual(self.talon.actions.scroll_emissions, [(1, 0), (0, -1)])
        self.assertEqual(self.talon.actions.continuous_scroll_attempts, [])
        self.assertAlmostEqual(self.module._vertical_scroll_remainder, 0.0)
        self.assertAlmostEqual(self.module._horizontal_scroll_remainder, 0.0)

    def test_fractional_line_scroll_bypasses_accumulation(self):
        self.module._vertical_scroll_remainder = 0.4
        self.module._horizontal_scroll_remainder = 0.3

        self.module.MainActions.mouse_scroll(0.25, -1.0, by_lines=True)

        self.assertEqual(
            self.talon.actions.continuous_scroll_emissions,
            [(0.25, -1.0)],
        )
        self.assertEqual(self.talon.actions.scroll_attempts, [])
        self.assertAlmostEqual(self.module._vertical_scroll_remainder, 0.4)
        self.assertAlmostEqual(self.module._horizontal_scroll_remainder, 0.3)
        self.assertEqual(self.talon.actions.next_calls, [])

    def test_unavailable_fractional_line_scroll_falls_back_unchanged(self):
        self.module._vertical_scroll_remainder = 0.4
        self.module._horizontal_scroll_remainder = 0.3
        self.talon.actions.continuous_scroll_failure = (
            self.module.CapabilityUnavailable("lost")
        )

        self.module.MainActions.mouse_scroll(0.25, -0.5, by_lines=True)

        self.assertEqual(
            self.talon.actions.continuous_scroll_attempts,
            [(0.25, -0.5)],
        )
        self.assertEqual(self.talon.actions.next_calls, [(0.25, -0.5, True)])
        self.assertAlmostEqual(self.module._vertical_scroll_remainder, 0.4)
        self.assertAlmostEqual(self.module._horizontal_scroll_remainder, 0.3)

    def test_failed_fractional_line_scroll_is_not_replayed(self):
        self.talon.actions.continuous_scroll_failure = RuntimeError("failed")

        with self.assertRaisesRegex(RuntimeError, "failed"):
            self.module.MainActions.mouse_scroll(0.25, by_lines=True)

        self.assertEqual(self.talon.actions.next_calls, [])
        self.assertEqual(self.module._vertical_scroll_remainder, 0.0)
        self.assertEqual(self.module._horizontal_scroll_remainder, 0.0)

    def test_failed_discrete_scroll_is_not_replayed(self):
        self.talon.actions.fail_scroll_call = 1
        self.talon.actions.scroll_failure = RuntimeError("frame failed")

        with self.assertRaisesRegex(RuntimeError, "frame failed"):
            self.module.MainActions.mouse_scroll(2, -1, by_lines=True)

        self.assertEqual(self.talon.actions.scroll_attempts, [(2, -1)])
        self.assertEqual(self.talon.actions.next_calls, [])

    def test_non_wayland_scroll_uses_fallback_despite_native_capability(self):
        for environment in ({"XDG_SESSION_TYPE": "x11"}, {}):
            with (
                self.subTest(environment=environment),
                patch.dict(os.environ, environment, clear=True),
            ):
                self.talon.actions.next_calls.clear()
                self.module.MainActions.mouse_scroll(0.25, -0.5, by_lines=True)

                self.assertEqual(self.talon.actions.next_calls, [(0.25, -0.5, True)])
                self.assertEqual(self.talon.actions.scroll_attempts, [])
                self.assertEqual(self.talon.actions.continuous_scroll_attempts, [])
                self.assertEqual(self.module._vertical_scroll_remainder, 0.0)
                self.assertEqual(self.module._horizontal_scroll_remainder, 0.0)

    def test_fallback_drag_release_stays_with_fallback_after_capability_appears(self):
        self.talon.actions.pointer_available = False
        self.module.MainActions.mouse_drag(0)
        self.talon.actions.pointer_available = True
        self.module.MainActions.mouse_release(0)

        self.assertEqual(self.talon.actions.next_calls, [(0,), (0,)])
        self.assertEqual(self.talon.actions.native_button_down, [])
        self.assertEqual(self.talon.actions.native_button_up, [])
        self.assertEqual(self.module._fallback_held_buttons, set())

        self.module.MainActions.mouse_drag(0)

        self.assertEqual(self.talon.actions.native_button_down, [0])
        self.assertEqual(self.talon.actions.next_calls, [(0,), (0,)])

    def _community_drag_toggle(self, held, button):
        """Follow Community's toggle through main actions to a recording fallback."""
        if button in held:
            with patch.object(self.talon.actions, "next", held.remove):
                self.module.MainActions.mouse_release(button)
        else:
            with patch.object(self.talon.actions, "next", held.add):
                self.module.MainActions.mouse_drag(button)

    def test_nested_fallback_toggle_keeps_release_with_its_owner(self):
        held = set()
        self.talon.actions.pointer_available = False
        with patch.object(
            self.talon.actions,
            "next",
            lambda button: self._community_drag_toggle(held, button),
        ):
            self.module.UserActions.mouse_drag_toggle(0)
            self.assertEqual(held, {0})
            self.assertEqual(self.module._fallback_held_buttons, {0})

            self.talon.actions.pointer_available = True
            self.module.UserActions.mouse_drag_toggle(0)

        self.assertEqual(held, set())
        self.assertEqual(self.module._fallback_held_buttons, set())
        self.assertEqual(self.talon.actions.native_button_down, [])
        self.assertEqual(self.talon.actions.native_button_up, [])

    def test_nested_toggle_can_select_native_when_capability_appears(self):
        held = set()
        self.talon.actions.pointer_available = False

        def delegate(button):
            self.talon.actions.pointer_available = True
            self._community_drag_toggle(held, button)

        with patch.object(self.talon.actions, "next", delegate):
            self.module.UserActions.mouse_drag_toggle(0)

        self.assertEqual(held, set())
        self.assertEqual(self.module._fallback_held_buttons, set())
        self.assertEqual(self.talon.actions.native_button_down, [0])
        self.module.MainActions.mouse_release(0)
        self.assertEqual(self.talon.actions.native_button_up, [0])

    def test_toggle_preflight_loss_records_nested_fallback_hold(self):
        held = set()

        def unavailable(button):
            self.talon.actions.pointer_available = False
            raise self.module.CapabilityUnavailable("pointer lost")

        with (
            patch.object(
                self.talon.actions.user,
                "wayland_pointer_button_toggle",
                unavailable,
                create=True,
            ),
            patch.object(
                self.talon.actions,
                "next",
                lambda button: self._community_drag_toggle(held, button),
            ),
        ):
            self.module.UserActions.mouse_drag_toggle(0)

        self.assertEqual(held, {0})
        self.assertEqual(self.module._fallback_held_buttons, {0})

    def test_failed_native_toggle_is_not_replayed_through_fallback(self):
        with patch.object(
            self.talon.actions.user,
            "wayland_pointer_button_toggle",
            side_effect=RuntimeError("frame failed"),
            create=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "frame failed"):
                self.module.UserActions.mouse_drag_toggle(0)

        self.assertEqual(self.talon.actions.next_calls, [])
        self.assertEqual(self.module._fallback_held_buttons, set())


if __name__ == "__main__":
    unittest.main()
