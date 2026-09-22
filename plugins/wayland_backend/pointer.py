"""Virtual-pointer values, lifecycle, and protocol operations."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from typing import Any

from .connection import WaylandConnection, monotonic_timestamp_ms, run_cleanup_steps
from .errors import CapabilityUnavailable
from .outputs import OutputRegistry, OutputTarget
from .seats import SeatRegistry

POINTER_EXTENT = 65535
WAYLAND_FIXED_MIN = -(1 << 23)
WAYLAND_FIXED_MAX = ((1 << 31) - 1) / 256
INT32_MIN = -(1 << 31)
INT32_MAX = (1 << 31) - 1
BUTTON_CODES = {
    0: 0x110,  # BTN_LEFT
    1: 0x111,  # BTN_RIGHT
    2: 0x112,  # BTN_MIDDLE
}
_AXIS_VERTICAL = 0
_AXIS_HORIZONTAL = 1
_AXIS_SOURCE_WHEEL = 0
_AXIS_SOURCE_CONTINUOUS = 2
_BUTTON_RELEASED = 0
_BUTTON_PRESSED = 1
_SCROLL_DISTANCE_PER_STEP = 15.0


def validate_wayland_fixed(value: float, field: str) -> float:
    """Return a finite value representable as Wayland signed 24.8 fixed."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if not WAYLAND_FIXED_MIN <= value <= WAYLAND_FIXED_MAX:
        raise ValueError(f"{field} is outside the Wayland fixed-point range")
    return float(value)


def validate_int32(value: int, field: str) -> int:
    """Return a value representable as a signed Wayland protocol int."""
    if type(value) is not int:
        raise TypeError(f"{field} must be an integer")
    if not INT32_MIN <= value <= INT32_MAX:
        raise ValueError(f"{field} is outside the signed 32-bit range")
    return value


def normalized_to_extent(
    x: float, y: float, extent: int = POINTER_EXTENT
) -> tuple[int, int]:
    """Convert normalized coordinates to an inclusive integer extent."""
    if type(extent) is not int:
        raise TypeError("Pointer extent must be an integer")
    if not 0 < extent < (1 << 32):
        raise ValueError("Pointer extent must fit an unsigned 32-bit integer")
    if (
        isinstance(x, bool)
        or not isinstance(x, (int, float))
        or isinstance(y, bool)
        or not isinstance(y, (int, float))
    ):
        raise TypeError("Pointer coordinates must be numbers")
    if (isinstance(x, float) and not math.isfinite(x)) or (
        isinstance(y, float) and not math.isfinite(y)
    ):
        raise ValueError("Pointer coordinates must be finite")
    x = min(1.0, max(0.0, x))
    y = min(1.0, max(0.0, y))
    return (round(x * extent), round(y * extent))


def linux_button_code(button: int) -> int:
    """Return the Linux input code for a supported Talon mouse button."""
    if type(button) is not int:
        raise TypeError("Mouse button must be an integer")
    try:
        return BUTTON_CODES[button]
    except KeyError as exc:
        raise ValueError(f"Unsupported mouse button: {button}") from exc


class VirtualPointer:
    """Own desktop-wide and output-bound pointers for the selected seat."""

    interface_name = "zwlr_virtual_pointer_manager_v1"
    multiple = False

    def __init__(
        self,
        connection: WaylandConnection,
        seats: SeatRegistry,
        outputs: OutputRegistry,
        timestamp_ms: Callable[[], int] = monotonic_timestamp_ms,
    ) -> None:
        """Create an unavailable pointer without performing protocol I/O."""
        self._connection = connection
        self._seats = seats
        self._outputs = outputs
        self._timestamp_ms = timestamp_ms
        self._lock = threading.Lock()
        self._manager_id: int | None = None
        self._manager_version = 0
        self._manager: Any = None
        self._pointer: Any = None
        self._output_pointer: Any = None
        self._output_id: int | None = None
        self._output_target: OutputTarget | None = None
        self._seat_id: int | None = None
        self._held_buttons: set[int] = set()
        self._unsubscribe_seats = seats.subscribe(self._on_seat_changed)
        self._unsubscribe_outputs = outputs.subscribe(self._on_outputs_changed)

    def bind(
        self, registry: Any, global_id: int, version: int, interface: type
    ) -> int:
        """Bind the pointer manager and create a pointer when a seat exists."""
        negotiated = min(version, interface.version)
        manager = registry.bind(global_id, interface, negotiated)
        with self._lock:
            self._manager_id = global_id
            self._manager_version = negotiated
            self._manager = manager
        self._maybe_create()
        return negotiated

    def remove(self, global_id: int) -> None:
        """Destroy the pointer before releasing its removed manager."""
        with self._lock:
            if global_id != self._manager_id:
                return
            manager = self._manager
            self._manager_id = None
            self._manager_version = 0
            self._manager = None
        run_cleanup_steps(
            (
                ("output-bound virtual pointer", self._destroy_output_pointer),
                ("virtual pointer", self._destroy_pointer),
                ("virtual pointer manager", lambda: self._destroy_manager(manager)),
            )
        )

    def ready(self) -> None:
        """Create the virtual pointer after initial registry synchronization."""
        self._maybe_create()

    def close(self) -> None:
        """Release held buttons, the virtual pointer, and its manager."""
        with self._lock:
            manager = self._manager
            self._manager_id = None
            self._manager_version = 0
            self._manager = None
        run_cleanup_steps(
            (
                ("output-bound virtual pointer", self._destroy_output_pointer),
                ("virtual pointer", self._destroy_pointer),
                ("virtual pointer manager", lambda: self._destroy_manager(manager)),
            )
        )

    def available(self) -> bool:
        """Return whether a usable virtual pointer currently exists."""
        with self._lock:
            return self._pointer is not None and not self._pointer.destroyed

    def move_absolute(
        self,
        x: float,
        y: float,
        *,
        refresh_hover: bool = False,
        timeout: float = 1.0,
    ) -> None:
        """Move to normalized desktop coordinates through the owner thread."""
        if type(refresh_hover) is not bool:
            raise TypeError("Pointer hover refresh must be a boolean")
        x_value, y_value = normalized_to_extent(x, y)
        self._connection.execute(
            lambda: self._emit_absolute(x_value, y_value, refresh_hover),
            timeout,
        )

    def move_output_absolute(
        self,
        target: OutputTarget,
        x: float,
        y: float,
        *,
        refresh_hover: bool = False,
        timeout: float = 1.0,
    ) -> None:
        """Move to normalized coordinates within one matched output."""
        if type(refresh_hover) is not bool:
            raise TypeError("Pointer hover refresh must be a boolean")
        x_value, y_value = normalized_to_extent(x, y)
        self._connection.execute(
            lambda: self._emit_output_absolute(
                target,
                x_value,
                y_value,
                refresh_hover,
            ),
            timeout,
        )

    def move_relative(self, dx: float, dy: float, *, timeout: float = 1.0) -> None:
        """Move by a relative compositor-space delta."""
        x_value = validate_wayland_fixed(dx, "Pointer x delta")
        y_value = validate_wayland_fixed(dy, "Pointer y delta")
        self._connection.execute(lambda: self._emit_relative(x_value, y_value), timeout)

    def set_button(self, button: int, pressed: bool, *, timeout: float = 1.0) -> None:
        """Establish one Talon button's requested pressed state idempotently."""
        if type(pressed) is not bool:
            raise TypeError("Pointer button state must be a boolean")
        code = linux_button_code(button)
        self._connection.execute(lambda: self._emit_button(code, pressed), timeout)

    def toggle_button(self, button: int, *, timeout: float = 1.0) -> bool:
        """Toggle one Talon button and return its new pressed state."""
        code = linux_button_code(button)
        return self._connection.execute(lambda: self._toggle_code(code), timeout)

    def click(self, button: int = 0, *, timeout: float = 1.0) -> None:
        """Press and release one Talon button as an intentional repeated effect."""
        code = linux_button_code(button)
        self._connection.execute(lambda: self._click_code(code), timeout)

    def release_all(self, *, timeout: float = 1.0) -> bool:
        """Release all held buttons and report whether state changed."""
        return self._connection.execute(self._release_all_codes, timeout)

    def scroll(
        self,
        vertical: int = 0,
        horizontal: int = 0,
        *,
        timeout: float = 1.0,
    ) -> None:
        """Emit discrete wheel steps, with positive vertical values moving down."""
        vertical = validate_int32(vertical, "Vertical scroll steps")
        horizontal = validate_int32(horizontal, "Horizontal scroll steps")
        validate_wayland_fixed(
            vertical * _SCROLL_DISTANCE_PER_STEP,
            "Vertical scroll distance",
        )
        validate_wayland_fixed(
            horizontal * _SCROLL_DISTANCE_PER_STEP,
            "Horizontal scroll distance",
        )
        self._connection.execute(
            lambda: self._emit_scroll(vertical, horizontal), timeout
        )

    def scroll_continuous(
        self,
        vertical_lines: float = 0.0,
        horizontal_lines: float = 0.0,
        *,
        timeout: float = 1.0,
    ) -> None:
        """Emit continuous fractional-line scrolling through the owner thread."""
        vertical_lines = validate_wayland_fixed(
            vertical_lines,
            "Vertical scroll lines",
        )
        horizontal_lines = validate_wayland_fixed(
            horizontal_lines,
            "Horizontal scroll lines",
        )
        vertical_distance = validate_wayland_fixed(
            vertical_lines * _SCROLL_DISTANCE_PER_STEP,
            "Vertical scroll distance",
        )
        horizontal_distance = validate_wayland_fixed(
            horizontal_lines * _SCROLL_DISTANCE_PER_STEP,
            "Horizontal scroll distance",
        )
        self._connection.execute(
            lambda: self._emit_continuous_scroll(
                vertical_distance,
                horizontal_distance,
            ),
            timeout,
        )

    def _on_seat_changed(self) -> None:
        """Recreate the pointer when selected-seat identity changes."""
        seat = self._seats.selected()
        seat_id = None if seat is None else seat.id
        with self._lock:
            old_seat_id = self._seat_id
        if old_seat_id != seat_id:
            run_cleanup_steps(
                (
                    ("output-bound virtual pointer", self._destroy_output_pointer),
                    ("virtual pointer", self._destroy_pointer),
                )
            )
        self._maybe_create()

    def _on_outputs_changed(self) -> None:
        """Drop a bound pointer when its target resolves to another output."""
        with self._lock:
            target = self._output_target
            output_id = self._output_id
        if target is None:
            return
        selected = self._outputs.match(target)
        if selected is None or selected.id != output_id:
            self._destroy_output_pointer()

    def _maybe_create(self) -> None:
        """Create a virtual pointer when manager, seat, and startup are ready."""
        if not self._connection.initialized or self._connection.stopping:
            return
        seat = self._seats.selected()
        with self._lock:
            manager = self._manager
            pointer = self._pointer
        if manager is None or seat is None or pointer is not None:
            return
        pointer = manager.create_virtual_pointer(seat.proxy)
        with self._lock:
            self._pointer = pointer
            self._seat_id = seat.id

    def _destroy_pointer(self) -> None:
        """Release held buttons and destroy the current pointer idempotently."""
        with self._lock:
            pointer = self._pointer
            self._pointer = None
            self._seat_id = None
        if pointer is None:
            self._held_buttons.clear()
            return
        try:
            try:
                if not pointer.destroyed and self._held_buttons:
                    timestamp = self._timestamp_ms()
                    run_cleanup_steps(
                        (
                            *(
                                (
                                    f"button {code}",
                                    lambda code=code: pointer.button(
                                        timestamp,
                                        code,
                                        _BUTTON_RELEASED,
                                    ),
                                )
                                for code in sorted(self._held_buttons)
                            ),
                            ("button release frame", pointer.frame),
                        )
                    )
            except Exception as release_error:
                try:
                    if not pointer.destroyed:
                        pointer.destroy()
                except Exception as destroy_error:
                    release_error.add_note(
                        "Virtual pointer destroy also failed: "
                        f"{type(destroy_error).__name__}: {destroy_error}"
                    )
                raise
            if not pointer.destroyed:
                pointer.destroy()
        finally:
            self._held_buttons.clear()

    def _destroy_output_pointer(self) -> None:
        """Destroy the output-bound motion pointer idempotently."""
        with self._lock:
            pointer = self._output_pointer
            self._output_pointer = None
            self._output_id = None
            self._output_target = None
        if pointer is not None and not pointer.destroyed:
            pointer.destroy()

    @staticmethod
    def _destroy_manager(manager: Any) -> None:
        """Destroy one virtual-pointer manager when it remains live."""
        if manager is not None and not manager.destroyed:
            manager.destroy()

    def _require_pointer(self) -> Any:
        """Return the active pointer or raise a capability error."""
        with self._lock:
            pointer = self._pointer
        if pointer is None or pointer.destroyed:
            raise CapabilityUnavailable("Wayland virtual pointer is not available")
        return pointer

    def _require_output_pointer(self, target: OutputTarget) -> Any:
        """Return a pointer bound to the output matching the supplied target."""
        selected = self._outputs.match(target)
        if selected is None:
            raise CapabilityUnavailable(
                f"No unique Wayland output matches Talon screen {target.name!r}"
            )
        seat = self._seats.selected()
        with self._lock:
            manager = self._manager
            manager_version = self._manager_version
            pointer = self._output_pointer
            output_id = self._output_id
        if manager is None or manager_version < 2 or seat is None:
            raise CapabilityUnavailable(
                "Output-bound Wayland virtual pointer is not available"
            )
        if pointer is not None and not pointer.destroyed and output_id == selected.id:
            with self._lock:
                self._output_target = target
            return pointer
        self._destroy_output_pointer()
        pointer = manager.create_virtual_pointer_with_output(
            seat.proxy,
            selected.proxy,
        )
        with self._lock:
            self._output_pointer = pointer
            self._output_id = selected.id
            self._output_target = target
        return pointer

    def _emit_absolute(self, x: int, y: int, refresh_hover: bool) -> None:
        """Emit one absolute motion transaction on the owner thread."""
        pointer = self._require_pointer()
        try:
            self._emit_absolute_events(pointer, x, y, refresh_hover)
        except Exception as exc:
            self._connection.fail(exc)
            raise

    def _emit_output_absolute(
        self,
        target: OutputTarget,
        x: int,
        y: int,
        refresh_hover: bool,
    ) -> None:
        """Emit absolute motion in one matched output's coordinate frame."""
        try:
            pointer = self._require_output_pointer(target)
        except CapabilityUnavailable:
            raise
        except Exception as exc:
            self._connection.fail(exc)
            raise
        try:
            self._emit_absolute_events(pointer, x, y, refresh_hover)
        except Exception as exc:
            self._connection.fail(exc)
            raise

    def _emit_absolute_events(
        self,
        pointer: Any,
        x: int,
        y: int,
        refresh_hover: bool,
    ) -> None:
        """Send one absolute motion transaction through a supplied pointer."""
        pointer.motion_absolute(
            self._timestamp_ms(),
            x,
            y,
            POINTER_EXTENT,
            POINTER_EXTENT,
        )
        pointer.frame()
        if refresh_hover:
            nudge = -1.0 if x == POINTER_EXTENT else 1.0
            pointer.motion(self._timestamp_ms(), nudge, 0.0)
            pointer.frame()
            pointer.motion(self._timestamp_ms(), -nudge, 0.0)
            pointer.frame()

    def _emit_relative(self, dx: float, dy: float) -> None:
        """Emit one relative motion transaction on the owner thread."""
        pointer = self._require_pointer()
        try:
            pointer.motion(self._timestamp_ms(), dx, dy)
            pointer.frame()
        except Exception as exc:
            self._connection.fail(exc)
            raise

    def _emit_button(self, code: int, pressed: bool) -> None:
        """Emit a button transition only when its state changes."""
        if (code in self._held_buttons) == pressed:
            self._require_pointer()
            return
        pointer = self._require_pointer()
        try:
            pointer.button(
                self._timestamp_ms(),
                code,
                _BUTTON_PRESSED if pressed else _BUTTON_RELEASED,
            )
            if pressed:
                self._held_buttons.add(code)
            pointer.frame()
            if not pressed:
                self._held_buttons.remove(code)
        except Exception as exc:
            self._connection.fail(exc)
            raise

    def _click_code(self, code: int) -> None:
        """Emit one ordered press-and-release transaction for a Linux button."""
        pointer = self._require_clickable(code)
        try:
            timestamp = self._timestamp_ms()
            pointer.button(timestamp, code, _BUTTON_PRESSED)
            self._held_buttons.add(code)
            pointer.frame()
            pointer.button(timestamp, code, _BUTTON_RELEASED)
            pointer.frame()
            self._held_buttons.remove(code)
        except Exception as exc:
            self._connection.fail(exc)
            raise

    def _require_clickable(self, code: int) -> Any:
        """Return the pointer after confirming a button can be clicked."""
        pointer = self._require_pointer()
        if code in self._held_buttons:
            raise RuntimeError("Cannot click a pointer button that is already held")
        return pointer

    def _toggle_code(self, code: int) -> bool:
        """Toggle one Linux button code on the owner thread."""
        pressed = code not in self._held_buttons
        self._emit_button(code, pressed)
        return pressed

    def _release_all_codes(self) -> bool:
        """Release every held Linux button code on the owner thread."""
        self._require_pointer()
        held = tuple(sorted(self._held_buttons))
        run_cleanup_steps(
            (
                (
                    f"button {code}",
                    lambda code=code: self._emit_button(code, False),
                )
                for code in held
            )
        )
        return bool(held)

    def _emit_scroll(self, vertical: int, horizontal: int) -> None:
        """Emit one wheel transaction containing both requested axes."""
        pointer = self._require_pointer()
        if vertical == 0 and horizontal == 0:
            return
        vertical_value = validate_wayland_fixed(
            vertical * _SCROLL_DISTANCE_PER_STEP,
            "Vertical scroll distance",
        )
        horizontal_value = validate_wayland_fixed(
            horizontal * _SCROLL_DISTANCE_PER_STEP,
            "Horizontal scroll distance",
        )
        try:
            timestamp = self._timestamp_ms()
            # Set the source after selecting each axis; compositors can retain it
            # per axis across frames, including after continuous scrolling.
            if vertical:
                pointer.axis_discrete(
                    timestamp,
                    _AXIS_VERTICAL,
                    vertical_value,
                    vertical,
                )
                pointer.axis_source(_AXIS_SOURCE_WHEEL)
            if horizontal:
                pointer.axis_discrete(
                    timestamp,
                    _AXIS_HORIZONTAL,
                    horizontal_value,
                    horizontal,
                )
                pointer.axis_source(_AXIS_SOURCE_WHEEL)
            pointer.frame()
        except Exception as exc:
            self._connection.fail(exc)
            raise

    def _emit_continuous_scroll(
        self,
        vertical_distance: float,
        horizontal_distance: float,
    ) -> None:
        """Emit each continuous axis as one independently sourced frame."""
        pointer = self._require_pointer()
        try:
            for axis, distance in (
                (_AXIS_VERTICAL, vertical_distance),
                (_AXIS_HORIZONTAL, horizontal_distance),
            ):
                if distance == 0.0:
                    continue
                pointer.axis(self._timestamp_ms(), axis, distance)
                # Virtual-pointer compositors attach the source to the current axis.
                pointer.axis_source(_AXIS_SOURCE_CONTINUOUS)
                pointer.frame()
        except Exception as exc:
            self._connection.fail(exc)
            raise
