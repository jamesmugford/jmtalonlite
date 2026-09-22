import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if __package__:
    from .wayland_fakes import FakeRegistry, ImmediateConnection, interface
else:
    from wayland_fakes import FakeRegistry, ImmediateConnection, interface

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
sys.path.insert(0, str(PLUGINS))
try:
    from wayland_backend.errors import CapabilityUnavailable
    from wayland_backend.pointer import (
        INT32_MAX,
        POINTER_EXTENT,
        WAYLAND_FIXED_MAX,
        VirtualPointer,
        linux_button_code,
        normalized_to_extent,
        validate_int32,
        validate_wayland_fixed,
    )
    from wayland_backend.outputs import OutputRegistry, OutputTarget
    from wayland_backend.seats import SeatRegistry
finally:
    sys.path.remove(str(PLUGINS))


def scroll_frames(calls):
    """Interpret requests with Hyprland's persistent, per-axis source state."""
    # VirtualPointer.cpp selects an axis before axis_source updates it; frame
    # clears pending events but retains sources. axis resets its event, whereas
    # axis_discrete retains the source. Keep this model limited to that contract.
    current_axis = 0
    sources = [0, 0]
    pending = {}
    frames = []
    for request, *args in calls:
        if request == "axis":
            _timestamp, current_axis, distance = args
            sources[current_axis] = 0
            pending[current_axis] = (distance, 0)
        elif request == "axis_discrete":
            _timestamp, current_axis, distance, steps = args
            pending[current_axis] = (distance, steps)
        elif request == "axis_source":
            sources[current_axis] = args[0]
        elif request == "frame":
            frames.append(
                tuple(
                    (axis, sources[axis], *pending[axis]) for axis in sorted(pending)
                )
            )
            pending.clear()
    return frames


class PointerValueTests(unittest.TestCase):
    def test_normalized_coordinates_are_clamped_to_inclusive_extent(self):
        self.assertEqual(normalized_to_extent(0.0, 0.0), (0, 0))
        self.assertEqual(normalized_to_extent(0.5, 0.5), (32768, 32768))
        self.assertEqual(
            normalized_to_extent(-1.0, 2.0),
            (0, POINTER_EXTENT),
        )

    def test_invalid_coordinates_and_wire_values_are_rejected(self):
        for value in (math.inf, -math.inf, math.nan):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalized_to_extent(value, 0.5)
        with self.assertRaises(TypeError):
            normalized_to_extent(True, 0.5)
        self.assertEqual(
            validate_wayland_fixed(WAYLAND_FIXED_MAX, "value"), WAYLAND_FIXED_MAX
        )
        self.assertEqual(validate_int32(INT32_MAX, "value"), INT32_MAX)
        with self.assertRaises(ValueError):
            validate_wayland_fixed(WAYLAND_FIXED_MAX + 1, "value")

    def test_talon_buttons_map_to_linux_codes(self):
        self.assertEqual(
            [linux_button_code(index) for index in range(3)], [0x110, 0x111, 0x112]
        )
        with self.assertRaises(TypeError):
            linux_button_code(True)
        with self.assertRaises(ValueError):
            linux_button_code(3)


class VirtualPointerTests(unittest.TestCase):
    def setUp(self):
        self.connection = ImmediateConnection()
        self.seats = SeatRegistry(self.connection)
        self.outputs = OutputRegistry(self.connection)
        self.pointer_adapter = VirtualPointer(
            self.connection,
            self.seats,
            self.outputs,
            timestamp_ms=lambda: 1234,
        )
        self.registry = FakeRegistry()
        self.seats.bind(self.registry, 20, 11, interface(11))
        self.pointer_adapter.bind(self.registry, 10, 2, interface(2))
        self.manager = self.registry.bound[1][3]
        self.pointer = self.manager.created_pointers[0]
        self.pointer.calls.clear()

    def _bind_output(self, output_id=30, name="HDMI-A-1"):
        self.outputs.bind(self.registry, output_id, 4, interface(4))
        output = self.registry.bound[-1][3]
        output.dispatcher["geometry"](
            output, 0, 0, 530, 300, 0, "Wacom Tech", "CintiqPro24PT", 0
        )
        output.dispatcher["mode"](output, 1, 3840, 2160, 60000)
        output.dispatcher["scale"](output, 2)
        output.dispatcher["name"](output, name)
        output.dispatcher["description"](output, "Wacom Tech CintiqPro24PT")
        output.dispatcher["done"](output)
        return output

    @staticmethod
    def _target(name="HDMI-A-1"):
        return OutputTarget(
            name,
            "Wacom Tech",
            "CintiqPro24PT",
            530,
            300,
            3840,
            2160,
            60000,
        )

    def test_emits_expected_motion_button_and_scroll_order(self):
        self.pointer_adapter.move_absolute(-1.0, 2.0, refresh_hover=True)
        self.pointer_adapter.move_relative(2.5, -3.5)
        self.pointer_adapter.set_button(0, True)
        self.pointer_adapter.set_button(0, True)
        self.pointer_adapter.set_button(0, False)
        self.pointer_adapter.click(1)
        self.pointer_adapter.scroll(2, -1)

        self.assertEqual(
            self.pointer.calls,
            [
                ("motion_absolute", 1234, 0, 65535, 65535, 65535),
                ("frame",),
                ("motion", 1234, 1.0, 0.0),
                ("frame",),
                ("motion", 1234, -1.0, 0.0),
                ("frame",),
                ("motion", 1234, 2.5, -3.5),
                ("frame",),
                ("button", 1234, 0x110, 1),
                ("frame",),
                ("button", 1234, 0x110, 0),
                ("frame",),
                ("button", 1234, 0x111, 1),
                ("frame",),
                ("button", 1234, 0x111, 0),
                ("frame",),
                ("axis_discrete", 1234, 0, 30.0, 2),
                ("axis_source", 0),
                ("axis_discrete", 1234, 1, -15.0, -1),
                ("axis_source", 0),
                ("frame",),
            ],
        )

    def test_continuous_scroll_emits_each_axis_as_one_sourced_frame(self):
        self.pointer_adapter.scroll_continuous(0.25, -0.5)

        self.assertEqual(
            self.pointer.calls,
            [
                ("axis", 1234, 0, 3.75),
                ("axis_source", 2),
                ("frame",),
                ("axis", 1234, 1, -7.5),
                ("axis_source", 2),
                ("frame",),
            ],
        )

    def test_continuous_scroll_validates_all_values_before_emitting(self):
        with self.assertRaises(TypeError):
            self.pointer_adapter.scroll_continuous(True)
        with self.assertRaises(ValueError):
            self.pointer_adapter.scroll_continuous(math.nan)
        with self.assertRaises(ValueError):
            self.pointer_adapter.scroll_continuous(WAYLAND_FIXED_MAX)

        self.assertEqual(self.pointer.calls, [])

    def test_scroll_mode_transitions_set_each_axis_source_and_preserve_frames(self):
        continuous_cases = (
            ((0.25, 0), [((0, 2, 3.75, 0),)]),
            ((0, -0.5), [((1, 2, -7.5, 0),)]),
            ((0.25, -0.5), [((0, 2, 3.75, 0),), ((1, 2, -7.5, 0),)]),
        )
        wheel_cases = (
            ((2, 0), ((0, 0, 30.0, 2),)),
            ((0, -1), ((1, 0, -15.0, -1),)),
            ((2, -1), ((0, 0, 30.0, 2), (1, 0, -15.0, -1))),
        )
        for continuous, continuous_frames in continuous_cases:
            for wheel, wheel_frame in wheel_cases:
                with self.subTest(continuous=continuous, wheel=wheel):
                    self.pointer.calls.clear()
                    self.pointer_adapter.scroll_continuous(*continuous)
                    self.pointer_adapter.scroll(*wheel)
                    self.pointer_adapter.scroll_continuous(*continuous)

                    self.assertEqual(
                        scroll_frames(self.pointer.calls),
                        [*continuous_frames, wheel_frame, *continuous_frames],
                    )

    def test_discrete_scroll_validates_both_axes_before_emitting(self):
        for horizontal, error in ((True, TypeError), (INT32_MAX, ValueError)):
            with self.subTest(horizontal=horizontal):
                with self.assertRaises(error):
                    self.pointer_adapter.scroll(1, horizontal)

                self.assertEqual(self.pointer.calls, [])

    def test_discrete_scroll_source_failure_stops_after_first_axis(self):
        error = RuntimeError("source failed")
        with patch.object(self.pointer, "axis_source", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "source failed"):
                self.pointer_adapter.scroll(2, -1)

        self.assertEqual(self.pointer.calls, [("axis_discrete", 1234, 0, 30.0, 2)])
        self.assertTrue(self.connection.stopping)
        self.assertEqual(self.connection.failures, [error])

    def test_output_bound_motion_uses_selected_output_without_desktop_scaling(self):
        output = self._bind_output()

        self.pointer_adapter.move_output_absolute(
            self._target(),
            0.5,
            0.5,
            refresh_hover=True,
        )

        output_pointer = self.manager.created_pointers[-1]
        self.assertEqual(
            self.manager.calls[-1],
            (
                "create_virtual_pointer_with_output",
                self.registry.bound[0][3],
                output,
            ),
        )
        self.assertEqual(
            output_pointer.calls,
            [
                ("motion_absolute", 1234, 32768, 32768, 65535, 65535),
                ("frame",),
                ("motion", 1234, 1.0, 0.0),
                ("frame",),
                ("motion", 1234, -1.0, 0.0),
                ("frame",),
            ],
        )
        self.assertEqual(self.pointer.calls, [])

    def test_output_target_from_an_older_reload_generation_is_accepted(self):
        self._bind_output()
        reloaded_target = SimpleNamespace(
            name="HDMI-A-1",
            make="Wacom Tech",
            model="CintiqPro24PT",
            physical_width=530,
            physical_height=300,
            mode_width=3840,
            mode_height=2160,
            refresh_millihz=60000,
        )

        self.pointer_adapter.move_output_absolute(reloaded_target, 0.5, 0.5)

        output_pointer = self.manager.created_pointers[-1]
        self.assertEqual(
            output_pointer.calls[:2],
            [
                ("motion_absolute", 1234, 32768, 32768, 65535, 65535),
                ("frame",),
            ],
        )

    def test_output_removal_destroys_bound_pointer_before_output_release(self):
        output = self._bind_output()
        self.pointer_adapter.move_output_absolute(self._target(), 0.5, 0.5)
        output_pointer = self.manager.created_pointers[-1]
        self.registry.event_log.clear()

        self.outputs.remove(30)

        self.assertTrue(output_pointer.destroyed)
        self.assertLess(
            self.registry.event_log.index((output_pointer, "destroy")),
            self.registry.event_log.index((output, "release")),
        )

    def test_manager_v1_reports_output_bound_motion_unavailable(self):
        connection = ImmediateConnection()
        seats = SeatRegistry(connection)
        outputs = OutputRegistry(connection)
        pointer_adapter = VirtualPointer(connection, seats, outputs)
        registry = FakeRegistry()
        seats.bind(registry, 20, 11, interface(11))
        pointer_adapter.bind(registry, 10, 1, interface(2))
        outputs.bind(registry, 30, 4, interface(4))
        output = registry.bound[-1][3]
        output.dispatcher["name"](output, "HDMI-A-1")
        output.dispatcher["done"](output)

        with self.assertRaisesRegex(CapabilityUnavailable, "not available"):
            pointer_adapter.move_output_absolute(self._target(), 0.5, 0.5)

    def test_output_pointer_creation_failure_stops_connection(self):
        self._bind_output()
        with patch.object(
            self.manager,
            "create_virtual_pointer_with_output",
            side_effect=RuntimeError("create failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "create failed"):
                self.pointer_adapter.move_output_absolute(
                    self._target(),
                    0.5,
                    0.5,
                )

        self.assertTrue(self.connection.stopping)
        self.assertEqual(str(self.connection.failures[0]), "create failed")

    def test_toggle_release_and_teardown_are_state_safe(self):
        self.assertTrue(self.pointer_adapter.toggle_button(2))
        self.assertFalse(self.pointer_adapter.toggle_button(2))
        self.pointer_adapter.set_button(2, True)
        self.assertTrue(self.pointer_adapter.release_all())
        self.assertFalse(self.pointer_adapter.release_all())
        self.pointer_adapter.set_button(0, True)
        self.pointer_adapter.close()
        self.pointer_adapter.close()
        self.assertFalse(self.pointer_adapter.available())
        self.assertEqual(
            self.pointer.calls[-3:],
            [("button", 1234, 0x110, 0), ("frame",), ("destroy",)],
        )
        self.assertEqual(self.manager.calls.count(("destroy",)), 1)

    def test_seat_replacement_recreates_pointer_before_old_seat_release(self):
        first_pointer = self.pointer
        first_seat = self.registry.bound[0][3]
        self._bind_output()
        self.pointer_adapter.move_output_absolute(self._target(), 0.5, 0.5)
        first_output_pointer = self.manager.created_pointers[-1]
        self.registry.event_log.clear()
        self.seats.bind(self.registry, 21, 11, interface(11))
        second_seat = self.registry.bound[3][3]
        second_seat.dispatcher["name"](second_seat, "seat0")

        self.assertTrue(first_pointer.destroyed)
        self.assertTrue(first_output_pointer.destroyed)
        self.assertLess(
            self.registry.event_log.index((first_pointer, "destroy")),
            self.registry.event_log.index((self.manager, "create_virtual_pointer")),
        )
        self.assertFalse(first_seat.destroyed)

        self.pointer_adapter.move_output_absolute(self._target(), 0.5, 0.5)
        self.assertEqual(
            self.manager.calls[-1][0],
            "create_virtual_pointer_with_output",
        )
        self.assertIs(self.manager.calls[-1][1], second_seat)

    def test_protocol_failure_stops_connection_and_preserves_uncertain_state(self):
        with patch.object(
            self.pointer,
            "frame",
            side_effect=RuntimeError("frame failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "frame failed"):
                self.pointer_adapter.click()

        self.assertTrue(self.connection.stopping)
        self.assertEqual(str(self.connection.failures[0]), "frame failed")
        self.pointer_adapter.close()
        self.assertFalse(self.pointer_adapter.available())

    def test_continuous_scroll_failure_stops_connection_without_replaying(self):
        with patch.object(
            self.pointer,
            "axis_source",
            side_effect=RuntimeError("source failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "source failed"):
                self.pointer_adapter.scroll_continuous(0.25)

        self.assertEqual(self.pointer.calls, [("axis", 1234, 0, 3.75)])
        self.assertTrue(self.connection.stopping)
        self.assertEqual(str(self.connection.failures[0]), "source failed")

    def test_close_releases_manager_after_button_cleanup_failure(self):
        self.pointer_adapter.set_button(0, True)
        with patch.object(
            self.pointer,
            "frame",
            side_effect=RuntimeError("release failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "release failed"):
                self.pointer_adapter.close()
        self.assertTrue(self.pointer.destroyed)
        self.assertTrue(self.manager.destroyed)
        self.assertFalse(self.pointer_adapter.available())

    def test_close_continues_releasing_buttons_after_one_failure(self):
        self.pointer_adapter.set_button(0, True)
        self.pointer_adapter.set_button(1, True)
        original_button = self.pointer.button
        attempted = []

        def release(timestamp, code, state):
            attempted.append(code)
            if code == 0x110:
                raise RuntimeError("left release failed")
            original_button(timestamp, code, state)

        with patch.object(self.pointer, "button", side_effect=release):
            with self.assertRaisesRegex(RuntimeError, "left release failed"):
                self.pointer_adapter.close()

        self.assertEqual(attempted, [0x110, 0x111])
        self.assertIn(("frame",), self.pointer.calls)
        self.assertTrue(self.pointer.destroyed)
        self.assertTrue(self.manager.destroyed)

    def test_release_all_continues_after_one_button_failure(self):
        self.pointer_adapter.set_button(0, True)
        self.pointer_adapter.set_button(1, True)
        original_button = self.pointer.button
        attempted = []

        def release(timestamp, code, state):
            attempted.append(code)
            if code == 0x110:
                raise RuntimeError("left release failed")
            original_button(timestamp, code, state)

        with patch.object(self.pointer, "button", side_effect=release):
            with self.assertRaisesRegex(RuntimeError, "left release failed"):
                self.pointer_adapter.release_all()

        self.assertEqual(attempted, [0x110, 0x111])
        self.assertEqual(self.pointer_adapter._held_buttons, {0x110})

    def test_zero_scroll_validates_availability_without_emitting(self):
        self.pointer_adapter.scroll()
        self.pointer_adapter.scroll_continuous()
        self.assertEqual(self.pointer.calls, [])
        self.pointer_adapter.close()
        with self.assertRaisesRegex(RuntimeError, "not available"):
            self.pointer_adapter.scroll()
        with self.assertRaisesRegex(RuntimeError, "not available"):
            self.pointer_adapter.scroll_continuous()


if __name__ == "__main__":
    unittest.main()
