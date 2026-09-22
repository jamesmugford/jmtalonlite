"""Internal semantic facade for native Wayland desktop capabilities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .bindings import WaylandBindings
from .connection import WaylandConnection, monotonic_timestamp_ms
from .errors import CapabilityUnavailable
from .key_spec import KeyPress, modifier_chord
from .keyboard import VirtualKeyboard
from .outputs import OutputRegistry, OutputSnapshot, OutputTarget
from .pointer import VirtualPointer, linux_button_code
from .seats import SeatRegistry, SeatSnapshot
from .windows import ForeignToplevels, Window


@dataclass(frozen=True, slots=True)
class DesktopStatus:
    """An immutable aggregate of connection and capability state."""

    running: bool
    protocols: tuple[tuple[str, int], ...]
    seats: tuple[SeatSnapshot, ...]
    outputs: tuple[OutputSnapshot, ...]
    windows: tuple[Window, ...]
    active_window: Window | None
    keyboard_available: bool
    pointer_available: bool
    error: str | None


class WaylandDesktop:
    """Compose Wayland transport and semantic desktop capabilities."""

    def __init__(
        self,
        *,
        load_bindings: Callable[[], WaylandBindings] | None = None,
        timestamp_ms: Callable[[], int] = monotonic_timestamp_ms,
    ) -> None:
        """Construct every capability without connecting to Wayland."""
        self._connection = WaylandConnection(load_bindings)
        self._seats = SeatRegistry(self._connection)
        self._outputs = OutputRegistry(self._connection)
        self._keyboard = VirtualKeyboard(
            self._connection,
            self._seats,
            timestamp_ms,
        )
        self._pointer = VirtualPointer(
            self._connection,
            self._seats,
            self._outputs,
            timestamp_ms,
        )
        self._windows = ForeignToplevels(self._connection)
        self._connection.register(self._seats)
        self._connection.register(self._outputs)
        self._connection.register(self._keyboard)
        self._connection.register(self._pointer)
        self._connection.register(self._windows)

    def start(self, timeout: float = 5.0) -> None:
        """Connect all desktop capabilities and wait until discovery completes."""
        self._connection.start(timeout)

    def stop(self, timeout: float = 5.0) -> None:
        """Stop every capability and the shared connection idempotently."""
        self._connection.stop(timeout)

    def status(self) -> DesktopStatus:
        """Return one immutable aggregate desktop snapshot."""
        return DesktopStatus(
            running=self._connection.running(),
            protocols=self._connection.protocols(),
            seats=self._seats.snapshots(),
            outputs=self._outputs.snapshots(),
            windows=self._windows.snapshots(),
            active_window=self._windows.active(),
            keyboard_available=self._keyboard.available(),
            pointer_available=self._pointer.available(),
            error=self._connection.error(),
        )

    def on_active_window_changed(
        self, callback: Callable[[Window | None], None]
    ) -> Callable[[], None]:
        """Subscribe to owner-thread active-window changes."""
        return self._windows.on_active_changed(callback)

    def window_context_available(self) -> bool:
        """Return whether native application and window context is available."""
        return self._windows.available()

    def keyboard_available(self) -> bool:
        """Return whether native key emission is currently available."""
        return self._keyboard.available()

    def pointer_available(self) -> bool:
        """Return whether native pointer emission is currently available."""
        return self._pointer.available()

    def send_key(self, key_spec: str, *, timeout: float = 1.0) -> None:
        """Send one Talon key specification through the native keyboard."""
        self._keyboard.send(key_spec, timeout=timeout)

    def press_temporary_modifiers(
        self,
        modifiers: str,
        *,
        timeout: float = 1.0,
    ) -> tuple[KeyPress, ...]:
        """Press unheld modifiers and return their opaque press identities."""
        down = modifier_chord(modifiers)
        return self._connection.execute(
            lambda: self._keyboard._emit_strokes(down),
            timeout,
        )

    def release_temporary_modifiers(
        self,
        pressed: tuple[KeyPress, ...],
        *,
        timeout: float = 1.0,
    ) -> None:
        """Release still-current presses returned by `press_temporary_modifiers`."""
        if not self._connection.running() or not self._keyboard.available():
            return
        try:
            self._connection.execute(
                lambda: self._keyboard._release_presses(pressed),
                timeout,
            )
        except CapabilityUnavailable:
            return
        except RuntimeError:
            if not self._connection.running():
                return
            raise

    def move_pointer_absolute(
        self,
        x: float,
        y: float,
        *,
        refresh_hover: bool = False,
        timeout: float = 1.0,
    ) -> None:
        """Move the native pointer to normalized desktop coordinates."""
        self._pointer.move_absolute(
            x,
            y,
            refresh_hover=refresh_hover,
            timeout=timeout,
        )

    def move_pointer_output_absolute(
        self,
        target: OutputTarget,
        x: float,
        y: float,
        *,
        refresh_hover: bool = False,
        timeout: float = 1.0,
    ) -> None:
        """Move the native pointer to normalized coordinates on one output."""
        self._pointer.move_output_absolute(
            target,
            x,
            y,
            refresh_hover=refresh_hover,
            timeout=timeout,
        )

    def move_pointer_relative(
        self, dx: float, dy: float, *, timeout: float = 1.0
    ) -> None:
        """Move the native pointer by a relative compositor-space delta."""
        self._pointer.move_relative(dx, dy, timeout=timeout)

    def set_pointer_button(
        self,
        button: int,
        pressed: bool,
        *,
        timeout: float = 1.0,
    ) -> None:
        """Establish one native pointer button's pressed state."""
        self._pointer.set_button(button, pressed, timeout=timeout)

    def toggle_pointer_button(self, button: int, *, timeout: float = 1.0) -> bool:
        """Toggle one native pointer button and return its new state."""
        return self._pointer.toggle_button(button, timeout=timeout)

    def click_pointer(self, button: int = 0, *, timeout: float = 1.0) -> None:
        """Click one native pointer button."""
        self._pointer.click(button, timeout=timeout)

    def release_pointer_buttons(self, *, timeout: float = 1.0) -> bool:
        """Release all native pointer buttons and report whether any were held."""
        return self._pointer.release_all(timeout=timeout)

    def scroll_pointer(
        self,
        vertical: int = 0,
        horizontal: int = 0,
        *,
        timeout: float = 1.0,
    ) -> None:
        """Scroll discrete steps through the native pointer."""
        self._pointer.scroll(vertical, horizontal, timeout=timeout)

    def scroll_pointer_continuous(
        self,
        vertical_lines: float = 0.0,
        horizontal_lines: float = 0.0,
        *,
        timeout: float = 1.0,
    ) -> None:
        """Scroll fractional lines continuously through the native pointer."""
        self._pointer.scroll_continuous(
            vertical_lines,
            horizontal_lines,
            timeout=timeout,
        )

    def modified_click(
        self,
        modifiers: str,
        button: int = 0,
        *,
        timeout: float = 1.0,
    ) -> None:
        """Click while holding one modifier chord as one ordered transaction."""
        down = modifier_chord(modifiers)
        code = linux_button_code(button)

        def operation() -> None:
            """Preflight both capabilities before emitting the combined effect."""
            self._keyboard._require_keyboard()
            self._pointer._require_clickable(code)
            pressed = self._keyboard._emit_strokes(down)
            try:
                self._pointer._click_code(code)
            except Exception as exc:
                try:
                    self._keyboard._release_presses(pressed)
                except Exception as cleanup_error:
                    exc.add_note(
                        "Temporary modifier release also failed: "
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
                raise
            else:
                self._keyboard._release_presses(pressed)

        self._connection.execute(operation, timeout)
