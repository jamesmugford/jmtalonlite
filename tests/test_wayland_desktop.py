import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
sys.path.insert(0, str(PLUGINS))
try:
    from wayland_backend.desktop import WaylandDesktop
    from wayland_backend.errors import CapabilityUnavailable
    from wayland_backend.keyboard import VirtualKeyboard
    from wayland_backend.outputs import OutputRegistry
    from wayland_backend.pointer import VirtualPointer
    from wayland_backend.seats import SeatRegistry
    from wayland_backend.windows import ForeignToplevels
finally:
    sys.path.remove(str(PLUGINS))


class WaylandDesktopTests(unittest.TestCase):
    def test_composes_semantic_capabilities_without_constructor_io(self):
        desktop = WaylandDesktop()
        self.assertIsInstance(desktop._seats, SeatRegistry)
        self.assertIsInstance(desktop._outputs, OutputRegistry)
        self.assertIsInstance(desktop._keyboard, VirtualKeyboard)
        self.assertIsInstance(desktop._pointer, VirtualPointer)
        self.assertIsInstance(desktop._windows, ForeignToplevels)
        status = desktop.status()
        self.assertFalse(status.running)
        self.assertFalse(status.keyboard_available)
        self.assertFalse(status.pointer_available)
        self.assertEqual(status.protocols, ())
        self.assertEqual(status.outputs, ())

    def test_modified_click_preflights_then_orders_both_capabilities(self):
        desktop = WaylandDesktop()
        desktop._connection._running.set()
        desktop._connection._owner_thread_id = threading.get_ident()
        calls = []
        pressed = (object(), object())
        with (
            patch.object(
                desktop._keyboard,
                "_require_keyboard",
                side_effect=lambda: calls.append("keyboard-ready"),
            ),
            patch.object(
                desktop._pointer,
                "_require_clickable",
                side_effect=lambda _code: calls.append("pointer-ready"),
            ),
            patch.object(
                desktop._keyboard,
                "_emit_strokes",
                side_effect=lambda strokes: (
                    calls.append(strokes[0].action.value),
                    pressed,
                )[1],
            ),
            patch.object(
                desktop._keyboard,
                "_release_presses",
                side_effect=lambda events: calls.append(("release", events)),
            ),
            patch.object(
                desktop._pointer,
                "_click_code",
                side_effect=lambda _code: calls.append("click"),
            ),
        ):
            desktop.modified_click("ctrl-shift", 0)
        self.assertEqual(
            calls,
            [
                "keyboard-ready",
                "pointer-ready",
                "down",
                "click",
                ("release", pressed),
            ],
        )

    def test_failed_preflight_emits_no_keyboard_state(self):
        desktop = WaylandDesktop()
        desktop._connection._running.set()
        desktop._connection._owner_thread_id = threading.get_ident()
        with (
            patch.object(desktop._keyboard, "_require_keyboard"),
            patch.object(
                desktop._pointer,
                "_require_clickable",
                side_effect=CapabilityUnavailable("missing pointer"),
            ),
            patch.object(desktop._keyboard, "_emit_strokes") as emit,
        ):
            with self.assertRaisesRegex(CapabilityUnavailable, "missing pointer"):
                desktop.modified_click("ctrl", 0)
        emit.assert_not_called()

    def test_modified_click_preserves_click_error_when_release_also_fails(self):
        desktop = WaylandDesktop()
        desktop._connection._running.set()
        desktop._connection._owner_thread_id = threading.get_ident()
        pressed = (object(),)
        with (
            patch.object(desktop._keyboard, "_require_keyboard"),
            patch.object(desktop._pointer, "_require_clickable"),
            patch.object(desktop._keyboard, "_emit_strokes", return_value=pressed),
            patch.object(
                desktop._pointer,
                "_click_code",
                side_effect=RuntimeError("click failed"),
            ),
            patch.object(
                desktop._keyboard,
                "_release_presses",
                side_effect=RuntimeError("release failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "click failed") as raised:
                desktop.modified_click("ctrl", 0)

        self.assertTrue(
            any("release failed" in note for note in raised.exception.__notes__)
        )

    def test_stale_modifier_release_is_safe_after_connection_teardown(self):
        desktop = WaylandDesktop()
        desktop.release_temporary_modifiers((object(),))

    def test_start_and_stop_delegate_lifecycle(self):
        desktop = WaylandDesktop()
        with (
            patch.object(desktop._connection, "start") as start,
            patch.object(desktop._connection, "stop") as stop,
        ):
            desktop.start(2)
            desktop.stop(3)
        start.assert_called_once_with(2)
        stop.assert_called_once_with(3)

    def test_continuous_scroll_delegates_to_pointer(self):
        desktop = WaylandDesktop()
        with patch.object(desktop._pointer, "scroll_continuous") as scroll:
            desktop.scroll_pointer_continuous(0.25, -0.5, timeout=2)

        scroll.assert_called_once_with(0.25, -0.5, timeout=2)


if __name__ == "__main__":
    unittest.main()
