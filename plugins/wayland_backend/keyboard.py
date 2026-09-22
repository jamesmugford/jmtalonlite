"""Virtual-keyboard protocol lifecycle and key-event execution."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import Any

from .connection import WaylandConnection, monotonic_timestamp_ms, run_cleanup_steps
from .errors import CapabilityUnavailable
from .key_spec import (
    KeyEvent,
    KeyPress,
    KeyStroke,
    ResolvedStroke,
    parse_key_spec,
    plan_key_events,
)
from .seats import SeatCapability, SeatRegistry
from .xkb import (
    KEYMAP_FORMAT_XKB_V1,
    XkbKeymap,
    create_keymap_fd,
    read_keymap_fd,
    validate_keycode,
)

_KEY_RELEASED = 0
_KEY_PRESSED = 1


class VirtualKeyboard:
    """Own zwp_virtual_keyboard_manager_v1 and mirrored XKB state."""

    interface_name = "zwp_virtual_keyboard_manager_v1"
    multiple = False

    def __init__(
        self,
        connection: WaylandConnection,
        seats: SeatRegistry,
        timestamp_ms: Callable[[], int] = monotonic_timestamp_ms,
    ) -> None:
        """Create an unavailable keyboard without performing protocol I/O."""
        self._connection = connection
        self._seats = seats
        self._timestamp_ms = timestamp_ms
        self._lock = threading.Lock()
        self._manager_name: int | None = None
        self._manager: Any = None
        self._source_seat_id: int | None = None
        self._source_seat_version = 0
        self._source_keyboard: Any = None
        self._source_keymap: bytes | None = None
        self._locked_modifiers = 0
        self._group = 0
        self._keyboard: Any = None
        self._keyboard_keymap: bytes | None = None
        self._xkb: XkbKeymap | None = None
        self._held_keys: dict[int, KeyPress] = {}
        self._unsubscribe_seats = seats.subscribe(self._on_seat_changed)

    def bind(self, registry: Any, name: int, version: int, interface: type) -> int:
        """Bind the virtual-keyboard manager and create its child when ready."""
        negotiated = min(version, interface.version)
        manager = registry.bind(name, interface, negotiated)
        with self._lock:
            self._manager_name = name
            self._manager = manager
        self._maybe_create_virtual()
        return negotiated

    def remove(self, name: int) -> None:
        """Destroy the virtual keyboard before releasing its removed manager."""
        with self._lock:
            if name != self._manager_name:
                return
            manager = self._manager
            self._manager_name = None
            self._manager = None
        run_cleanup_steps(
            (
                ("virtual keyboard", self._destroy_virtual),
                ("virtual keyboard manager", lambda: self._destroy_manager(manager)),
            )
        )

    def ready(self) -> None:
        """Attach to the selected source keyboard after initial synchronization."""
        self._sync_source_keyboard()
        self._maybe_create_virtual()

    def close(self) -> None:
        """Release virtual and source keyboards before destroying the manager."""
        with self._lock:
            manager = self._manager
            self._manager_name = None
            self._manager = None
        run_cleanup_steps(
            (
                ("virtual keyboard", self._destroy_virtual),
                ("source keyboard", self._release_source_keyboard),
                ("virtual keyboard manager", lambda: self._destroy_manager(manager)),
            )
        )

    def available(self) -> bool:
        """Return whether a virtual keyboard with an active keymap exists."""
        with self._lock:
            return (
                self._keyboard is not None
                and not self._keyboard.destroyed
                and self._keyboard_keymap is not None
                and self._xkb is not None
            )

    def send(self, key_spec: str, *, timeout: float = 1.0) -> None:
        """Parse and send a Talon key specification through the owner thread."""
        strokes = parse_key_spec(key_spec)
        if strokes:
            self._connection.execute(lambda: self._emit_strokes(strokes), timeout)

    def _on_seat_changed(self) -> None:
        """Synchronize source and virtual keyboards after selected-seat changes."""
        self._sync_source_keyboard()
        self._maybe_create_virtual()

    def _sync_source_keyboard(self) -> None:
        """Attach to the selected seat's keyboard capability when available."""
        seat = self._seats.selected()
        usable = seat is not None and bool(seat.capabilities & SeatCapability.KEYBOARD)
        target_id = seat.id if usable else None
        with self._lock:
            current_id = self._source_seat_id
            source = self._source_keyboard
        if target_id == current_id and source is not None:
            return
        run_cleanup_steps(
            (
                ("virtual keyboard", self._destroy_virtual),
                ("source keyboard", self._release_source_keyboard),
            )
        )
        if not usable or seat is None or not self._connection.initialized:
            return

        source = seat.proxy.get_keyboard()
        with self._lock:
            self._source_seat_id = seat.id
            self._source_seat_version = seat.version
            self._source_keyboard = source
            self._source_keymap = None
            self._locked_modifiers = 0
            self._group = 0
        source.dispatcher["keymap"] = self._connection.guard(self._on_keymap)
        source.dispatcher["modifiers"] = self._connection.guard(self._on_modifiers)

    def _release_source_keyboard(self) -> None:
        """Release the selected seat's source keyboard idempotently."""
        with self._lock:
            source = self._source_keyboard
            version = self._source_seat_version
            self._source_seat_id = None
            self._source_seat_version = 0
            self._source_keyboard = None
            self._source_keymap = None
            self._locked_modifiers = 0
            self._group = 0
        if source is None or source.destroyed:
            return
        if version >= 3:
            source.release()
        else:
            source._destroy()

    @staticmethod
    def _destroy_manager(manager: Any) -> None:
        """Destroy one virtual-keyboard manager when it remains live."""
        if manager is not None and not manager.destroyed:
            manager.destroy()

    def _on_keymap(
        self,
        source: Any,
        format: int,
        fd: int,
        size: int,
    ) -> None:
        """Copy a selected source keymap and apply it to the virtual keyboard."""
        with self._lock:
            current = source is self._source_keyboard
        if not current or self._connection.stopping:
            if fd >= 0:
                os.close(fd)
            return
        if format != KEYMAP_FORMAT_XKB_V1:
            if fd >= 0:
                os.close(fd)
            with self._lock:
                if source is self._source_keyboard:
                    self._source_keymap = None
            self._destroy_virtual()
            return

        keymap = read_keymap_fd(fd, size)
        with self._lock:
            if source is not self._source_keyboard:
                return
            unchanged = keymap == self._source_keymap
            self._source_keymap = keymap
            keyboard = self._keyboard
        if unchanged:
            self._maybe_create_virtual()
            return
        if keyboard is None:
            self._maybe_create_virtual()
            return
        self._apply_keymap(keyboard)

    def _on_modifiers(
        self,
        source: Any,
        _serial: int,
        _depressed: int,
        _latched: int,
        locked: int,
        group: int,
    ) -> None:
        """Mirror source lock and layout state while excluding depressed keys."""
        with self._lock:
            if source is not self._source_keyboard:
                return
            self._locked_modifiers = locked
            self._group = group
            keyboard = self._keyboard
            xkb = self._xkb
        if keyboard is None or xkb is None:
            self._maybe_create_virtual()
            return
        modifiers = xkb.set_external_state(locked, group)
        if modifiers is not None:
            keyboard.modifiers(*modifiers)

    def _maybe_create_virtual(self) -> None:
        """Create a virtual keyboard once manager, source, and keymap are ready."""
        if not self._connection.initialized or self._connection.stopping:
            return
        seat = self._seats.selected()
        with self._lock:
            manager = self._manager
            source = self._source_keyboard
            keymap = self._source_keymap
            keyboard = self._keyboard
            source_seat_id = self._source_seat_id
        if (
            manager is None
            or source is None
            or source.destroyed
            or keymap is None
            or keyboard is not None
            or seat is None
            or seat.id != source_seat_id
        ):
            return
        keyboard = manager.create_virtual_keyboard(seat.proxy)
        with self._lock:
            self._keyboard = keyboard
            self._keyboard_keymap = None
        try:
            self._apply_keymap(keyboard)
        except Exception as exc:
            with self._lock:
                if self._keyboard is keyboard:
                    self._keyboard = None
                    self._keyboard_keymap = None
            try:
                if not keyboard.destroyed:
                    keyboard.destroy()
            except Exception as cleanup_error:
                exc.add_note(
                    "Virtual keyboard rollback also failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            raise

    def _apply_keymap(self, keyboard: Any) -> None:
        """Replace XKB state and send the selected keymap to a virtual keyboard."""
        with self._lock:
            keymap = self._source_keymap
            locked = self._locked_modifiers
            group = self._group
            if self._keyboard is not keyboard or self._keyboard_keymap == keymap:
                return
            self._keyboard_keymap = None
        if keymap is None:
            raise RuntimeError("Selected Wayland seat has no XKB-v1 keymap")
        xkb = XkbKeymap(keymap, locked_modifiers=locked, group=group)
        try:
            self._release_held_keys(keyboard)
            fd = create_keymap_fd(keymap)
            try:
                keyboard.keymap(KEYMAP_FORMAT_XKB_V1, fd, len(keymap))
            finally:
                os.close(fd)
            modifiers = xkb.modifiers()
            if any(modifiers):
                keyboard.modifiers(*modifiers)
        except Exception:
            xkb.close()
            raise
        with self._lock:
            old_xkb = self._xkb
            self._xkb = xkb
            if self._keyboard is keyboard:
                self._keyboard_keymap = keymap
        if old_xkb is not None:
            old_xkb.close()

    def _destroy_virtual(self) -> None:
        """Release held keys and destroy virtual keyboard state idempotently."""
        with self._lock:
            keyboard = self._keyboard
            xkb = self._xkb
            self._keyboard = None
            self._keyboard_keymap = None
        if keyboard is None:
            self._held_keys.clear()
            if xkb is not None:
                xkb.close()
                with self._lock:
                    if self._xkb is xkb:
                        self._xkb = None
            return
        try:
            try:
                if not keyboard.destroyed:
                    self._release_held_keys(keyboard)
            except Exception as release_error:
                try:
                    if not keyboard.destroyed:
                        keyboard.destroy()
                except Exception as destroy_error:
                    release_error.add_note(
                        "Virtual keyboard destroy also failed: "
                        f"{type(destroy_error).__name__}: {destroy_error}"
                    )
                raise
            if not keyboard.destroyed:
                keyboard.destroy()
        finally:
            self._held_keys.clear()
            if xkb is not None:
                xkb.close()
            with self._lock:
                if self._xkb is xkb:
                    self._xkb = None

    def _require_keyboard(self) -> tuple[Any, XkbKeymap]:
        """Return active protocol and XKB objects or raise a capability error."""
        with self._lock:
            keyboard = self._keyboard
            keymap = self._keyboard_keymap
            xkb = self._xkb
        if keyboard is None or keyboard.destroyed or keymap is None or xkb is None:
            raise CapabilityUnavailable("Wayland virtual keyboard is not available")
        return keyboard, xkb

    def _resolve_strokes(
        self,
        strokes: tuple[KeyStroke, ...],
        xkb: XkbKeymap,
    ) -> tuple[ResolvedStroke, ...]:
        """Resolve every stroke before allowing any keyboard side effect."""
        resolved = []
        for stroke in strokes:
            modifiers = [xkb.resolve_modifier(name) for name in stroke.modifiers]
            keycode = None
            if stroke.key is not None:
                keycode, implicit = xkb.resolve_key(stroke.key)
                keycode = validate_keycode(keycode)
                modifiers.extend(implicit)
            resolved.append(
                ResolvedStroke(
                    tuple(dict.fromkeys(validate_keycode(code) for code in modifiers)),
                    keycode,
                    stroke.action,
                    stroke.repeat,
                )
            )
        return tuple(resolved)

    def _emit_strokes(self, strokes: tuple[KeyStroke, ...]) -> tuple[KeyPress, ...]:
        """Resolve and emit strokes, returning identities of newly emitted presses."""
        keyboard, xkb = self._require_keyboard()
        resolved = self._resolve_strokes(strokes, xkb)
        plan = plan_key_events(resolved, frozenset(self._held_keys))
        presses = []
        for event in plan.events:
            self._send_event(keyboard, event)
            if event.pressed:
                presses.append(self._held_keys[event.keycode])
        return tuple(presses)

    def _release_presses(self, presses: tuple[KeyPress, ...]) -> None:
        """Release only recorded presses still held, on the owner thread."""
        keyboard, _xkb = self._require_keyboard()
        run_cleanup_steps(
            (
                f"keycode {press.keycode}",
                lambda press=press: self._send_event(
                    keyboard, KeyEvent(press.keycode, False)
                ),
            )
            for press in reversed(presses)
            if self._held_keys.get(press.keycode) is press
        )

    def _send_event(self, keyboard: Any, event: KeyEvent) -> None:
        """Emit one key transition and update actual held and modifier state."""
        keycode = validate_keycode(event.keycode)
        if (keycode in self._held_keys) == event.pressed:
            return
        try:
            keyboard.key(
                self._timestamp_ms(),
                keycode,
                _KEY_PRESSED if event.pressed else _KEY_RELEASED,
            )
            if event.pressed:
                self._held_keys[keycode] = KeyPress(keycode)
            else:
                del self._held_keys[keycode]
            with self._lock:
                xkb = self._xkb
            if xkb is not None:
                modifiers = xkb.update_key(keycode, event.pressed)
                if modifiers is not None:
                    keyboard.modifiers(*modifiers)
        except Exception as exc:
            self._connection.fail(exc)
            raise

    def _release_held_keys(self, keyboard: Any) -> None:
        """Release held keys in reverse press order."""
        run_cleanup_steps(
            (
                (
                    f"keycode {keycode}",
                    lambda keycode=keycode: self._send_event(
                        keyboard,
                        KeyEvent(keycode, False),
                    ),
                )
                for keycode in reversed(tuple(self._held_keys))
            )
        )
