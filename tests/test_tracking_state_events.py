import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

if __package__:
    from .test_tracking_overlay import (
        FakeCanvas,
        load_overlay_module,
        make_overlay_environment,
    )
else:
    from test_tracking_overlay import (
        FakeCanvas,
        load_overlay_module,
        make_overlay_environment,
    )


def load_state_events_module(talon, plugins_module):
    path = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "tracking_forwarder"
        / "control1_state_events.py"
    )
    spec = importlib.util.spec_from_file_location(
        "plugins.tracking_forwarder.control1_state_events_under_test", path
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"talon": talon, "talon.plugins": plugins_module}):
        spec.loader.exec_module(module)
    return module


class ControlMouseNotificationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(
            patch.object(sys, "_jm_talon_lite_control1_overlay_state", None, create=True)
        )
        self.enterContext(patch("builtins.print"))
        self.enabled = False
        self.notifications = []
        self.talon, canvas_module, plugins_module = make_overlay_environment()
        self.talon.actions.tracking.control1_enabled = lambda: self.enabled
        self.talon.actions.skip = Mock()
        self.talon.actions.next = self._toggle
        self.talon.actions.user = types.SimpleNamespace(
            control1_state_changed=self._notify
        )
        self.canvas = FakeCanvas()
        canvas_module.Canvas.from_screen = lambda _screen: self.canvas
        self.talon.ui.screens = lambda: (
            types.SimpleNamespace(
                rect=types.SimpleNamespace(x=0, y=0, width=100, height=100)
            ),
        )
        plugins_module.eye_mouse.mouse.xy_hist = [types.SimpleNamespace(x=20, y=30)]
        self.menu_callback = Mock(side_effect=self._menu_toggle)
        self.menu_item = types.SimpleNamespace(attrs={"cb": self.menu_callback})
        self.eye_mouse_2 = types.SimpleNamespace(control1_item=self.menu_item)
        plugins_module.eye_mouse_2 = self.eye_mouse_2
        self.overlay = load_overlay_module(
            environment=(self.talon, canvas_module, plugins_module)
        )[0]
        self.addCleanup(self.overlay.Actions.control1_debug_overlay_stop)
        self.events = load_state_events_module(self.talon, plugins_module)

    def _toggle(self, state=None):
        self.enabled = not self.enabled if state is None else state

    def _menu_toggle(self, _item):
        self._toggle()
        return "menu result"

    def _notify(self, enabled):
        """Wire only the notification chain, including its concrete base action."""
        self.notifications.append(enabled)
        with patch.object(
            self.talon.actions, "next", self.events.Actions.control1_state_changed
        ):
            self.overlay.UserActions.control1_state_changed(enabled)

    def test_default_notification_works_without_an_overlay_consumer(self):
        self.talon.actions.user.control1_state_changed = (
            self.events.Actions.control1_state_changed
        )

        self.events.TrackingActions.control1_toggle(True)

        self.assertTrue(self.enabled)
        self.talon.actions.skip.assert_called_once_with()

    def test_action_changes_sync_overlay_and_ignore_repeated_state(self):
        self.overlay.Actions.control1_debug_overlay_start()

        self.events.TrackingActions.control1_toggle(True)
        self.events.TrackingActions.control1_toggle(True)

        self.assertEqual(self.notifications, [True])
        self.assertEqual(self.talon.tracking_system.callbacks, [self.overlay._on_gaze])
        self.overlay._on_gaze()
        self.assertEqual(self.overlay._dot_pos, (20, 30))
        freezes = self.canvas.freeze_count

        self.events.TrackingActions.control1_toggle(False)
        self.events.TrackingActions.control1_toggle(False)

        self.assertEqual(self.notifications, [True, False])
        self.assertEqual(self.talon.tracking_system.callbacks, [])
        self.assertIsNone(self.overlay._dot_pos)
        self.assertEqual(self.canvas.freeze_count, freezes + 1)
        self.assertTrue(self.overlay.Actions.control1_debug_overlay_running())

    def test_notifications_do_not_enable_a_disabled_overlay(self):
        self.events.TrackingActions.control1_toggle(True)
        self.events.TrackingActions.control1_toggle(False)

        self.assertEqual(self.notifications, [True, False])
        self.assertFalse(self.overlay.Actions.control1_debug_overlay_running())
        self.assertEqual(self.talon.tracking_system.callbacks, [])
        self.assertEqual(self.overlay._canvas_entries, [])

    def test_menu_changes_sync_overlay_without_stacking_wrappers(self):
        self.overlay.Actions.control1_debug_overlay_start()
        self.events._on_ready()
        self.events._on_ready()

        self.assertEqual(self.menu_item.attrs["cb"](self.menu_item), "menu result")
        self.assertEqual(self.talon.tracking_system.callbacks, [self.overlay._on_gaze])
        self.assertEqual(self.menu_item.attrs["cb"](self.menu_item), "menu result")

        self.assertEqual(self.notifications, [True, False])
        self.assertEqual(self.talon.tracking_system.callbacks, [])
        self.assertEqual(self.menu_callback.call_count, 2)
        self.menu_callback.assert_called_with(self.menu_item)

    def test_unchanged_menu_state_does_not_notify(self):
        self.menu_callback.side_effect = None
        self.menu_callback.return_value = "unchanged"
        self.events._on_ready()

        self.assertEqual(self.menu_item.attrs["cb"](self.menu_item), "unchanged")
        self.assertEqual(self.notifications, [])

    def test_missing_menu_does_not_prevent_action_notifications(self):
        self.eye_mouse_2.control1_item = None
        self.events._on_ready()

        self.events.TrackingActions.control1_toggle(True)

        self.assertEqual(self.notifications, [True])

    def test_explicit_notification_reports_current_state_without_toggling(self):
        self.enabled = True

        self.events.Actions.control1_state_emit_now()

        self.assertTrue(self.enabled)
        self.assertEqual(self.notifications, [True])

    def test_failed_toggle_propagates_without_notifying(self):
        with patch.object(
            self.talon.actions, "next", side_effect=RuntimeError("toggle failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "toggle failed"):
                self.events.TrackingActions.control1_toggle(True)

        self.assertFalse(self.enabled)
        self.assertEqual(self.notifications, [])


if __name__ == "__main__":
    unittest.main()
