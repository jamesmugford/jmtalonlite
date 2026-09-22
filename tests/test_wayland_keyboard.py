import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if __package__:
    from .wayland_fakes import (
        FakeRegistry,
        FakeXkbKeymap,
        ImmediateConnection,
        interface,
        send_keymap,
    )
else:
    from wayland_fakes import (
        FakeRegistry,
        FakeXkbKeymap,
        ImmediateConnection,
        interface,
        send_keymap,
    )

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
sys.path.insert(0, str(PLUGINS))
try:
    from wayland_backend.key_spec import modifier_chord
    from wayland_backend.keyboard import VirtualKeyboard
    from wayland_backend.seats import SeatRegistry
    from wayland_backend.xkb import (
        KEY_MAX,
        XkbKeymap,
        create_keymap_fd,
        read_keymap_fd,
        validate_keycode,
    )
finally:
    sys.path.remove(str(PLUGINS))


US_KEYMAP = b"""xkb_keymap {
    xkb_keycodes { include "evdev+aliases(qwerty)" };
    xkb_types { include "complete" };
    xkb_compatibility { include "complete" };
    xkb_symbols { include "pc+us" };
};
\0"""

US_GB_KEYMAP = b"""xkb_keymap {
    xkb_keycodes { include "evdev+aliases(qwerty)" };
    xkb_types { include "complete" };
    xkb_compatibility { include "complete" };
    xkb_symbols { include "pc+us+gb:2+inet(evdev)" };
};
\0"""


class XkbKeymapTests(unittest.TestCase):
    def setUp(self):
        self.keymap = XkbKeymap(US_KEYMAP)

    def tearDown(self):
        self.keymap.close()

    def test_resolves_characters_named_keys_and_modifiers(self):
        self.assertEqual(self.keymap.resolve_key("a"), (30, ()))
        self.assertEqual(self.keymap.resolve_key("A"), (30, (42,)))
        self.assertEqual(self.keymap.resolve_key("!"), (2, (42,)))
        self.assertEqual(self.keymap.resolve_key("pageup"), (104, ()))
        self.assertEqual(self.keymap.resolve_key("keypad_1"), (79, ()))
        self.assertEqual(self.keymap.resolve_modifier("ctrl"), 29)

    def test_tracks_external_lock_and_layout_state(self):
        self.assertEqual(self.keymap.set_external_state(2, 0), (0, 0, 2, 0))
        self.assertEqual(self.keymap.resolve_key("a"), (30, (42,)))
        self.assertEqual(self.keymap.resolve_key("A"), (30, ()))

        alternate = XkbKeymap(US_GB_KEYMAP)
        try:
            with self.assertRaisesRegex(ValueError, "not available"):
                alternate.resolve_key("£")
            alternate.set_external_state(0, 1)
            self.assertEqual(alternate.resolve_key("£"), (4, (42,)))
        finally:
            alternate.close()

    def test_modifier_key_updates_return_protocol_state(self):
        depressed = self.keymap.update_key(42, True)
        released = self.keymap.update_key(42, False)
        self.assertIsNotNone(depressed)
        self.assertNotEqual(depressed[0], 0)
        self.assertEqual(released, (0, 0, 0, 0))


class KeymapFileTests(unittest.TestCase):
    @staticmethod
    def keymap_fd(data: bytes) -> int:
        with tempfile.TemporaryFile() as keymap_file:
            keymap_file.write(data)
            keymap_file.flush()
            return os.dup(keymap_file.fileno())

    def test_keycodes_and_keymap_descriptors_are_validated(self):
        self.assertEqual(validate_keycode(1), 1)
        self.assertEqual(validate_keycode(KEY_MAX), KEY_MAX)
        for value in (0, KEY_MAX + 1):
            with self.assertRaises(ValueError):
                validate_keycode(value)
        with self.assertRaises(TypeError):
            validate_keycode(True)

        data = b"xkb_keymap {}\n\0"
        incoming = self.keymap_fd(data)
        self.assertEqual(read_keymap_fd(incoming, len(data)), data)
        with self.assertRaises(OSError):
            os.fstat(incoming)

        outgoing = create_keymap_fd(data)
        try:
            self.assertEqual(os.pread(outgoing, len(data), 0), data)
        finally:
            os.close(outgoing)

    def test_invalid_incoming_keymap_still_closes_descriptor(self):
        data = b"not-null-terminated"
        fd = self.keymap_fd(data)
        with self.assertRaisesRegex(ValueError, "null-terminated"):
            read_keymap_fd(fd, len(data))
        with self.assertRaises(OSError):
            os.fstat(fd)


class VirtualKeyboardTests(unittest.TestCase):
    def setUp(self):
        FakeXkbKeymap.instances.clear()
        self.xkb_patch = patch(
            "wayland_backend.keyboard.XkbKeymap",
            FakeXkbKeymap,
        )
        self.xkb_patch.start()
        self.connection = ImmediateConnection()
        self.seats = SeatRegistry(self.connection)
        self.keyboard_adapter = VirtualKeyboard(
            self.connection,
            self.seats,
            timestamp_ms=lambda: 12,
        )
        self.registry = FakeRegistry()
        self.keyboard_adapter.bind(self.registry, 10, 1, interface(1))
        self.seats.bind(self.registry, 20, 11, interface(11))
        self.seat = self.registry.bound[1][3]
        self.seat.dispatcher["capabilities"](self.seat, 2)
        self.source = self.seat.created_keyboards[0]
        self.manager = self.registry.bound[0][3]

    def tearDown(self):
        self.keyboard_adapter.close()
        self.xkb_patch.stop()

    def make_ready(self):
        send_keymap(self.source)
        keyboard = self.manager.created_virtual_keyboards[0]
        keyboard.calls.clear()
        self.registry.event_log.clear()
        return keyboard

    def test_waits_for_selected_seat_keymap(self):
        self.assertFalse(self.keyboard_adapter.available())
        self.assertEqual(self.manager.created_virtual_keyboards, [])
        fd = send_keymap(self.source, modifiers=None)
        with self.assertRaises(OSError):
            os.fstat(fd)
        self.assertTrue(self.keyboard_adapter.available())
        self.assertEqual(
            self.manager.created_virtual_keyboards[0].calls,
            [("keymap", 1, b"xkb_keymap {}\n\0", 15)],
        )

    def test_sends_resolved_chords_repeats_and_held_state_in_order(self):
        keyboard = self.make_ready()
        self.keyboard_adapter.send("ctrl-A:2")
        self.assertEqual(
            keyboard.calls,
            [
                ("key", 12, 29, 1),
                ("modifiers", 4, 0, 0, 0),
                ("key", 12, 42, 1),
                ("modifiers", 5, 0, 0, 0),
                ("key", 12, 30, 1),
                ("key", 12, 30, 0),
                ("key", 12, 30, 1),
                ("key", 12, 30, 0),
                ("key", 12, 42, 0),
                ("modifiers", 4, 0, 0, 0),
                ("key", 12, 29, 0),
                ("modifiers", 0, 0, 0, 0),
            ],
        )

        self.keyboard_adapter.send("ctrl:down ctrl:down a:down")
        with self.assertRaisesRegex(RuntimeError, "while it is held"):
            self.keyboard_adapter.send("a")
        self.keyboard_adapter.send("a:up ctrl:up")
        self.assertFalse(self.keyboard_adapter._held_keys)

    def test_resolves_an_entire_sequence_before_emitting(self):
        keyboard = self.make_ready()
        with self.assertRaisesRegex(ValueError, "Unknown test key"):
            self.keyboard_adapter.send("ctrl-a unknown")
        self.assertEqual(keyboard.calls, [])

    def test_win_alias_emits_the_same_chord_as_super(self):
        keyboard = self.make_ready()
        self.keyboard_adapter.send("super-a")
        expected = list(keyboard.calls)
        keyboard.calls.clear()

        self.keyboard_adapter.send("win-a")

        self.assertEqual(keyboard.calls, expected)

    def _assert_invalid_resolution_emits_nothing(self, spec, keys, modifiers):
        keyboard = self.make_ready()
        self.keyboard_adapter.send("alt:down")
        keyboard.calls.clear()
        xkb = FakeXkbKeymap.instances[-1]
        with (
            patch.dict(FakeXkbKeymap.keys, keys),
            patch.dict(FakeXkbKeymap.modifiers_by_name, modifiers),
        ):
            with self.assertRaises(ValueError):
                self.keyboard_adapter.send(spec)

        self.assertEqual(keyboard.calls, [])
        self.assertEqual(set(self.keyboard_adapter._held_keys), {56})
        self.assertEqual(xkb.pressed, {56})
        self.assertFalse(self.connection.stopping)

    def test_invalid_primary_keycode_rejects_even_a_valid_prefix(self):
        self._assert_invalid_resolution_emits_nothing(
            "b ctrl-a", {"a": (KEY_MAX + 1, ())}, {}
        )

    def test_invalid_implicit_modifier_rejects_even_a_valid_prefix(self):
        self._assert_invalid_resolution_emits_nothing(
            "b ctrl-a", {"a": (30, (KEY_MAX + 1,))}, {}
        )

    def test_invalid_explicit_modifier_rejects_even_a_valid_prefix(self):
        self._assert_invalid_resolution_emits_nothing(
            "b ctrl-a", {}, {"ctrl": KEY_MAX + 1}
        )

    def test_invalid_keycode_is_rejected_even_for_an_unheld_release(self):
        self._assert_invalid_resolution_emits_nothing(
            "a:up", {"a": (KEY_MAX + 1, ())}, {}
        )

    def test_temporary_modifier_release_preserves_preheld_keys(self):
        self.make_ready()
        self.keyboard_adapter.send("ctrl:down")
        down = modifier_chord("ctrl")

        pressed = self.keyboard_adapter._emit_strokes(down)
        self.keyboard_adapter._release_presses(pressed)

        self.assertEqual(pressed, ())
        self.assertEqual(set(self.keyboard_adapter._held_keys), {29})
        self.keyboard_adapter.send("ctrl:up")

    def _press_temporary(self, modifiers):
        down = modifier_chord(modifiers)
        return self.keyboard_adapter._emit_strokes(down)

    def test_temporary_release_does_not_release_a_repressed_key(self):
        keyboard = self.make_ready()
        pressed = self._press_temporary("ctrl")
        self.keyboard_adapter.send("ctrl:up ctrl:down")
        keyboard.calls.clear()

        self.keyboard_adapter._release_presses(pressed)
        self.keyboard_adapter._release_presses(pressed)

        self.assertEqual(keyboard.calls, [])
        self.assertIn(29, self.keyboard_adapter._held_keys)

    def test_temporary_release_does_not_cross_keymap_replacement(self):
        keyboard = self.make_ready()
        pressed = self._press_temporary("ctrl")
        send_keymap(self.source, b"xkb_keymap { updated };\n\0")
        self.keyboard_adapter.send("ctrl:down")
        keyboard.calls.clear()

        self.keyboard_adapter._release_presses(pressed)

        self.assertEqual(keyboard.calls, [])
        self.assertIn(29, self.keyboard_adapter._held_keys)

    def test_temporary_release_does_not_cross_keyboard_recreation(self):
        old_keyboard = self.make_ready()
        pressed = self._press_temporary("ctrl")
        self.seat.dispatcher["capabilities"](self.seat, 0)
        self.seat.dispatcher["capabilities"](self.seat, 2)
        send_keymap(self.seat.created_keyboards[-1])
        keyboard = self.manager.created_virtual_keyboards[-1]
        self.assertTrue(old_keyboard.destroyed)
        self.keyboard_adapter.send("ctrl:down")
        keyboard.calls.clear()

        self.keyboard_adapter._release_presses(pressed)

        self.assertEqual(keyboard.calls, [])
        self.assertIn(29, self.keyboard_adapter._held_keys)

    def test_unchanged_keymap_keeps_temporary_cleanup_valid_and_idempotent(self):
        keyboard = self.make_ready()
        pressed = self._press_temporary("ctrl")
        send_keymap(self.source)
        keyboard.calls.clear()

        self.keyboard_adapter._release_presses(pressed)
        self.keyboard_adapter._release_presses(pressed)

        self.assertEqual(
            keyboard.calls,
            [("key", 12, 29, 0), ("modifiers", 0, 0, 0, 0)],
        )

    def test_repeated_down_does_not_create_another_temporary_owner(self):
        keyboard = self.make_ready()
        first = self._press_temporary("ctrl")
        second = self._press_temporary("ctrl")
        self.assertEqual(second, ())
        keyboard.calls.clear()

        self.keyboard_adapter._release_presses(second)
        self.assertEqual(keyboard.calls, [])
        self.keyboard_adapter._release_presses(first)
        self.assertNotIn(29, self.keyboard_adapter._held_keys)

    def test_temporary_cleanup_releases_valid_part_of_a_stale_chord(self):
        keyboard = self.make_ready()
        pressed = self._press_temporary("ctrl-shift")
        self.keyboard_adapter.send("ctrl:up ctrl:down")
        keyboard.calls.clear()

        self.keyboard_adapter._release_presses(pressed)

        self.assertEqual(
            keyboard.calls,
            [("key", 12, 42, 0), ("modifiers", 4, 0, 0, 0)],
        )
        self.assertIn(29, self.keyboard_adapter._held_keys)
        self.assertNotIn(42, self.keyboard_adapter._held_keys)

    def test_temporary_cleanup_continues_after_one_release_fails(self):
        keyboard = self.make_ready()
        pressed = self._press_temporary("ctrl-shift")
        original_key = keyboard.key
        attempted = []

        def release(timestamp, keycode, state):
            attempted.append(keycode)
            if keycode == 42:
                raise RuntimeError("shift release failed")
            original_key(timestamp, keycode, state)

        with patch.object(keyboard, "key", side_effect=release):
            with self.assertRaisesRegex(RuntimeError, "shift release failed"):
                self.keyboard_adapter._release_presses(pressed)

        self.assertEqual(attempted, [42, 29])
        self.assertIn(42, self.keyboard_adapter._held_keys)
        self.assertNotIn(29, self.keyboard_adapter._held_keys)
        self.assertTrue(self.connection.stopping)

    def test_keymap_replacement_releases_held_keys_and_closes_old_xkb(self):
        keyboard = self.make_ready()
        self.keyboard_adapter.send("ctrl:down")
        send_keymap(self.source, b"xkb_keymap { updated };\n\0")
        self.assertTrue(FakeXkbKeymap.instances[0].closed)
        self.assertFalse(self.keyboard_adapter._held_keys)
        self.assertEqual(
            keyboard.calls[-3:-1], [("key", 12, 29, 0), ("modifiers", 0, 0, 0, 0)]
        )
        self.assertTrue(self.keyboard_adapter.available())

    def test_capability_loss_destroys_virtual_before_source(self):
        keyboard = self.make_ready()
        self.registry.event_log.clear()
        self.seat.dispatcher["capabilities"](self.seat, 0)
        self.assertLess(
            self.registry.event_log.index((keyboard, "destroy")),
            self.registry.event_log.index((self.source, "release")),
        )
        self.assertFalse(self.keyboard_adapter.available())

    def test_capability_loss_releases_source_after_virtual_cleanup_failure(self):
        keyboard = self.make_ready()
        self.keyboard_adapter.send("a:down")
        with patch.object(
            keyboard,
            "key",
            side_effect=RuntimeError("release failed"),
        ):
            self.seat.dispatcher["capabilities"](self.seat, 0)
        self.assertRegex(str(self.connection.failures[-1]), "release failed")
        self.assertTrue(keyboard.destroyed)
        self.assertTrue(self.source.destroyed)
        self.assertFalse(self.keyboard_adapter.available())

    def test_unsupported_keymap_disables_keyboard_and_closes_descriptor(self):
        keyboard = self.make_ready()
        fd = send_keymap(self.source, format=0)
        with self.assertRaises(OSError):
            os.fstat(fd)
        self.assertTrue(keyboard.destroyed)
        self.assertFalse(self.keyboard_adapter.available())

    def test_close_releases_source_and_manager_after_key_cleanup_failure(self):
        keyboard = self.make_ready()
        self.keyboard_adapter.send("a:down")
        with patch.object(
            keyboard,
            "key",
            side_effect=RuntimeError("release failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "release failed"):
                self.keyboard_adapter.close()
        self.assertTrue(keyboard.destroyed)
        self.assertTrue(self.source.destroyed)
        self.assertTrue(self.manager.destroyed)
        self.assertFalse(self.keyboard_adapter.available())

    def test_close_continues_releasing_keys_after_one_failure(self):
        keyboard = self.make_ready()
        self.keyboard_adapter.send("a:down b:down")
        original_key = keyboard.key
        attempted = []

        def release(timestamp, keycode, state):
            attempted.append(keycode)
            if keycode == 48:
                raise RuntimeError("b release failed")
            original_key(timestamp, keycode, state)

        with patch.object(keyboard, "key", side_effect=release):
            with self.assertRaisesRegex(RuntimeError, "b release failed"):
                self.keyboard_adapter.close()

        self.assertEqual(attempted, [48, 30])
        self.assertTrue(keyboard.destroyed)
        self.assertTrue(self.source.destroyed)
        self.assertTrue(self.manager.destroyed)


if __name__ == "__main__":
    unittest.main()
