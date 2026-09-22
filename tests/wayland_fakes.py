"""Small protocol fakes shared by native Wayland unit tests."""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from typing import Any


class ImmediateConnection:
    """Execute capability commands inline without a Wayland event loop."""

    def __init__(self, *, initialized: bool = True) -> None:
        """Create an available fake connection with no recorded failures."""
        self.initialized = initialized
        self.stopping = False
        self.failures: list[Exception] = []
        self.deactivated: list[tuple[str, int]] = []

    def execute(self, callback, timeout: float = 1.0):
        """Execute and return one callback immediately."""
        return callback()

    def guard(self, callback):
        """Return a callback wrapper that records and suppresses failures."""

        def guarded(*args):
            """Invoke the wrapped callback."""
            try:
                return callback(*args)
            except Exception as exc:
                self.fail(exc)

        return guarded

    def fail(self, error: Exception) -> None:
        """Record one fatal capability error."""
        self.failures.append(error)
        self.stopping = True

    def deactivate(self, interface_name: str, global_id: int) -> None:
        """Record one server-retired global."""
        self.deactivated.append((interface_name, global_id))


class FakeProxy:
    """Record common requests made to generated PyWayland proxies."""

    def __init__(self, event_log: list[tuple[Any, str]] | None = None) -> None:
        """Create a live proxy with empty dispatchers and request history."""
        self.destroyed = False
        self.dispatcher: dict[str, Any] = {}
        self.calls: list[tuple] = []
        self.created_pointers: list[FakeProxy] = []
        self.created_keyboards: list[FakeProxy] = []
        self.created_virtual_keyboards: list[FakeProxy] = []
        self.event_log = event_log

    def _record(self, call: tuple) -> None:
        """Append one request locally and to the shared order log."""
        self.calls.append(call)
        if self.event_log is not None:
            self.event_log.append((self, call[0]))

    def destroy(self) -> None:
        """Record a protocol destroy request."""
        self._record(("destroy",))
        self.destroyed = True

    def _destroy(self) -> None:
        """Record local proxy destruction without a protocol request."""
        self._record(("_destroy",))
        self.destroyed = True

    def release(self) -> None:
        """Record a protocol release request."""
        self._record(("release",))
        self.destroyed = True

    def stop(self) -> None:
        """Record a foreign-toplevel manager stop request."""
        self._record(("stop",))

    def create_virtual_pointer(self, seat) -> "FakeProxy":
        """Create and record a virtual-pointer child."""
        pointer = FakeProxy(self.event_log)
        self._record(("create_virtual_pointer", seat))
        self.created_pointers.append(pointer)
        return pointer

    def create_virtual_pointer_with_output(self, seat, output) -> "FakeProxy":
        """Create and record an output-bound virtual-pointer child."""
        pointer = FakeProxy(self.event_log)
        self._record(("create_virtual_pointer_with_output", seat, output))
        self.created_pointers.append(pointer)
        return pointer

    def get_keyboard(self) -> "FakeProxy":
        """Create and record a source wl_keyboard child."""
        keyboard = FakeProxy(self.event_log)
        self._record(("get_keyboard",))
        self.created_keyboards.append(keyboard)
        return keyboard

    def create_virtual_keyboard(self, seat) -> "FakeProxy":
        """Create and record a virtual-keyboard child."""
        keyboard = FakeProxy(self.event_log)
        self._record(("create_virtual_keyboard", seat))
        self.created_virtual_keyboards.append(keyboard)
        return keyboard

    def keymap(self, format: int, fd: int, size: int) -> None:
        """Record exact bytes sent through a keymap descriptor."""
        self._record(("keymap", format, os.pread(fd, size, 0), size))

    def key(self, timestamp: int, keycode: int, state: int) -> None:
        """Record one virtual-keyboard key request."""
        self._record(("key", timestamp, keycode, state))

    def modifiers(self, depressed: int, latched: int, locked: int, group: int) -> None:
        """Record one virtual-keyboard modifier request."""
        self._record(("modifiers", depressed, latched, locked, group))

    def motion_absolute(
        self,
        timestamp: int,
        x: int,
        y: int,
        x_extent: int,
        y_extent: int,
    ) -> None:
        """Record one absolute pointer motion request."""
        self._record(("motion_absolute", timestamp, x, y, x_extent, y_extent))

    def motion(self, timestamp: int, dx: float, dy: float) -> None:
        """Record one relative pointer motion request."""
        self._record(("motion", timestamp, dx, dy))

    def button(self, timestamp: int, button: int, state: int) -> None:
        """Record one pointer button request."""
        self._record(("button", timestamp, button, state))

    def axis_source(self, source: int) -> None:
        """Record one pointer axis-source request."""
        self._record(("axis_source", source))

    def axis(self, timestamp: int, axis: int, value: float) -> None:
        """Record one continuous pointer axis request."""
        self._record(("axis", timestamp, axis, value))

    def axis_discrete(
        self,
        timestamp: int,
        axis: int,
        value: float,
        discrete: int,
    ) -> None:
        """Record one discrete pointer axis request."""
        self._record(("axis_discrete", timestamp, axis, value, discrete))

    def frame(self) -> None:
        """Record one pointer frame request."""
        self._record(("frame",))


class FakeRegistry:
    """Create recording proxies for registry bind requests."""

    def __init__(self) -> None:
        """Create an empty registry and shared request-order log."""
        self.bound: list[tuple[int, Any, int, FakeProxy]] = []
        self.event_log: list[tuple[Any, str]] = []

    def bind(self, global_id: int, interface: Any, version: int) -> FakeProxy:
        """Return and record a new proxy for one global bind."""
        proxy = FakeProxy(self.event_log)
        self.bound.append((global_id, interface, version, proxy))
        return proxy


class FakeXkbKeymap:
    """Resolve a small deterministic keymap and track modifier state."""

    instances: list["FakeXkbKeymap"] = []
    keys = {
        "a": (30, ()),
        "A": (30, (42,)),
        "b": (48, ()),
        "!": (2, (42,)),
    }
    modifiers_by_name = {"ctrl": 29, "alt": 56, "shift": 42, "super": 125}
    modifier_masks = {29: 4, 42: 1, 56: 8, 125: 64}

    def __init__(self, data: bytes, locked_modifiers: int = 0, group: int = 0):
        """Store source state and register this test instance."""
        self.data = data
        self.pressed: set[int] = set()
        self.locked_modifiers = locked_modifiers
        self.group = group
        self.closed = False
        self.instances.append(self)

    def close(self) -> None:
        """Mark this fake XKB resource closed."""
        self.closed = True

    def resolve_key(self, name: str) -> tuple[int, tuple[int, ...]]:
        """Resolve one configured fake key."""
        try:
            return self.keys[name]
        except KeyError as exc:
            raise ValueError(f"Unknown test key: {name}") from exc

    def resolve_modifier(self, name: str) -> int:
        """Resolve one configured fake modifier."""
        return self.modifiers_by_name[name]

    def modifiers(self) -> tuple[int, int, int, int]:
        """Return fake protocol modifier state."""
        return (0, 0, self.locked_modifiers, self.group)

    def set_external_state(
        self, locked_modifiers: int, group: int
    ) -> tuple[int, int, int, int] | None:
        """Apply external state and return modifiers only when it changes."""
        if locked_modifiers == self.locked_modifiers and group == self.group:
            return None
        self.locked_modifiers = locked_modifiers
        self.group = group
        return (self._depressed(), 0, locked_modifiers, group)

    def update_key(
        self, keycode: int, pressed: bool
    ) -> tuple[int, int, int, int] | None:
        """Apply a key transition and report modifier changes."""
        if pressed:
            self.pressed.add(keycode)
        else:
            self.pressed.discard(keycode)
        if keycode not in self.modifier_masks:
            return None
        return (self._depressed(), 0, self.locked_modifiers, self.group)

    def _depressed(self) -> int:
        """Return the union of held fake modifier masks."""
        value = 0
        for keycode in self.pressed:
            value |= self.modifier_masks.get(keycode, 0)
        return value


def interface(version: int) -> SimpleNamespace:
    """Return a minimal generated-interface stand-in."""
    return SimpleNamespace(version=version)


def send_keymap(
    source: FakeProxy,
    data: bytes = b"xkb_keymap {}\n\0",
    *,
    format: int = 1,
    modifiers: tuple[int, int] | None = (0, 0),
) -> int:
    """Dispatch a source-keyboard keymap and optional modifier event."""
    with tempfile.TemporaryFile() as keymap_file:
        keymap_file.write(data)
        keymap_file.flush()
        fd = os.dup(keymap_file.fileno())
    source.dispatcher["keymap"](source, format, fd, len(data))
    if modifiers is not None:
        locked, group = modifiers
        source.dispatcher["modifiers"](source, 0, 0, 0, locked, group)
    return fd
