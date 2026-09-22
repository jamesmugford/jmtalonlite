"""Foreign-toplevel tracking and active-window events."""

from __future__ import annotations

import struct
import threading
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .connection import WaylandConnection, run_cleanup_steps

_STATE_NAMES = {
    0: "maximized",
    1: "minimized",
    2: "activated",
    3: "fullscreen",
}


@dataclass(frozen=True, slots=True)
class Window:
    """An immutable foreign-toplevel snapshot."""

    id: int
    title: str
    app_id: str
    states: tuple[str, ...]


@dataclass(slots=True)
class _PendingWindow:
    """Mutable fields accumulated until a toplevel done event."""

    id: int
    title: str = ""
    app_id: str = ""
    states: tuple[str, ...] = ()


def decode_window_states(payload: bytes) -> tuple[str, ...]:
    """Decode the native-endian uint32 state array used by wlr-protocols."""
    if len(payload) % 4:
        raise ValueError("Toplevel state payload is not uint32-aligned")
    values = struct.unpack(f"={len(payload) // 4}I", payload)
    return tuple(_STATE_NAMES.get(value, f"unknown:{value}") for value in values)


def choose_active_window(
    windows: Iterable[Window],
    current_id: int | None,
    preferred_id: int | None = None,
) -> Window | None:
    """Choose an active window, preferring a newly activated candidate."""
    values = tuple(windows)
    by_id = {window.id: window for window in values}
    preferred = by_id.get(preferred_id)
    if preferred is not None and "activated" in preferred.states:
        return preferred
    current = by_id.get(current_id)
    if current is not None and "activated" in current.states:
        return current
    return next(
        (window for window in values if "activated" in window.states),
        None,
    )


class ForeignToplevels:
    """Own zwlr_foreign_toplevel_manager_v1 and publish active windows."""

    interface_name = "zwlr_foreign_toplevel_manager_v1"
    multiple = False

    def __init__(self, connection: WaylandConnection) -> None:
        """Create an unavailable window tracker without protocol I/O."""
        self._connection = connection
        self._lock = threading.Lock()
        self._listener_lock = threading.Lock()
        self._manager_id: int | None = None
        self._manager: Any = None
        self._handles: dict[Any, _PendingWindow] = {}
        self._windows: dict[int, Window] = {}
        self._active: Window | None = None
        self._listeners: list[Callable[[Window | None], None]] = []
        self._next_id = 1

    def bind(
        self, registry: Any, global_id: int, version: int, interface: type
    ) -> int:
        """Bind a toplevel manager and subscribe to manager events."""
        negotiated = min(version, interface.version)
        manager = registry.bind(global_id, interface, negotiated)
        with self._lock:
            self._manager_id = global_id
            self._manager = manager
        manager.dispatcher["toplevel"] = self._connection.guard(self._on_toplevel)
        manager.dispatcher["finished"] = self._connection.guard(
            self._on_manager_finished
        )
        return negotiated

    def remove(self, global_id: int) -> None:
        """Release all toplevels before destroying a removed manager."""
        with self._lock:
            if global_id != self._manager_id:
                return
            manager = self._manager
            self._manager_id = None
            self._manager = None
        run_cleanup_steps(
            (
                ("toplevel handles", lambda: self._clear_windows(notify=True)),
                ("toplevel manager", lambda: self._destroy_manager(manager)),
            )
        )

    def ready(self) -> None:
        """Complete initialization without additional protocol requests."""

    def close(self) -> None:
        """Destroy tracked handles and ask the manager to stop sending events."""
        with self._lock:
            manager = self._manager
            self._manager_id = None
            self._manager = None
        run_cleanup_steps(
            (
                ("toplevel handles", lambda: self._clear_windows(notify=True)),
                ("toplevel manager", lambda: self._stop_manager(manager)),
            )
        )

    def available(self) -> bool:
        """Return whether a foreign-toplevel manager is active."""
        with self._lock:
            return self._manager is not None and not self._manager.destroyed

    def active(self) -> Window | None:
        """Return the current immutable active-window snapshot."""
        with self._lock:
            return self._active

    def snapshots(self) -> tuple[Window, ...]:
        """Return every window snapshot ordered by local ID."""
        with self._lock:
            return tuple(self._windows[key] for key in sorted(self._windows))

    def on_active_changed(
        self, callback: Callable[[Window | None], None]
    ) -> Callable[[], None]:
        """Subscribe to active-window changes and return an unsubscribe callback."""
        with self._listener_lock:
            self._listeners.append(callback)

        def unsubscribe() -> None:
            """Remove this listener if it remains subscribed."""
            with self._listener_lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return unsubscribe

    def _on_toplevel(self, _manager: Any, handle: Any) -> None:
        """Track one newly announced toplevel handle."""
        with self._lock:
            pending = _PendingWindow(self._next_id)
            self._next_id += 1
            self._handles[handle] = pending
        handle.dispatcher["title"] = self._connection.guard(self._on_title)
        handle.dispatcher["app_id"] = self._connection.guard(self._on_app_id)
        handle.dispatcher["state"] = self._connection.guard(self._on_state)
        handle.dispatcher["done"] = self._connection.guard(self._on_done)
        handle.dispatcher["closed"] = self._connection.guard(self._on_closed)

    def _on_title(self, handle: Any, title: str) -> None:
        """Apply a title event to a pending toplevel."""
        with self._lock:
            pending = self._handles.get(handle)
            if pending is not None:
                pending.title = title

    def _on_app_id(self, handle: Any, app_id: str) -> None:
        """Apply an app-ID event to a pending toplevel."""
        with self._lock:
            pending = self._handles.get(handle)
            if pending is not None:
                pending.app_id = app_id

    def _on_state(self, handle: Any, payload: bytes) -> None:
        """Decode and apply a state event to a pending toplevel."""
        states = decode_window_states(payload)
        with self._lock:
            pending = self._handles.get(handle)
            if pending is not None:
                pending.states = states

    def _on_done(self, handle: Any) -> None:
        """Commit pending fields and publish a changed active window."""
        with self._lock:
            pending = self._handles.get(handle)
            if pending is None:
                return
            previous = self._windows.get(pending.id)
            window = Window(
                pending.id,
                pending.title,
                pending.app_id,
                pending.states,
            )
            self._windows[pending.id] = window
            newly_activated = "activated" in window.states and (
                previous is None or "activated" not in previous.states
            )
        self._select_and_publish(window.id if newly_activated else None)

    def _on_closed(self, handle: Any) -> None:
        """Remove a closed toplevel and destroy its handle."""
        with self._lock:
            pending = self._handles.pop(handle, None)
            if pending is not None:
                self._windows.pop(pending.id, None)
        if pending is not None:
            self._select_and_publish()
        if not handle.destroyed:
            handle.destroy()

    def _on_manager_finished(self, manager: Any) -> None:
        """Retire a finished manager and make its protocol unavailable."""
        with self._lock:
            if manager is not self._manager:
                if not manager.destroyed:
                    manager._destroy()
                return
            global_id = self._manager_id
            self._manager_id = None
            self._manager = None
        steps = [
            ("toplevel handles", lambda: self._clear_windows(notify=True)),
            ("toplevel manager", lambda: self._destroy_manager(manager)),
        ]
        if global_id is not None:
            steps.append(
                (
                    "toplevel protocol",
                    lambda: self._connection.deactivate(self.interface_name, global_id),
                )
            )
        run_cleanup_steps(steps)

    def _select_and_publish(self, preferred_id: int | None = None) -> None:
        """Select an active window and notify listeners only when identity changes."""
        with self._lock:
            previous = self._active
            active = choose_active_window(
                self._windows.values(),
                None if previous is None else previous.id,
                preferred_id,
            )
            self._active = active
        previous_key = (
            None if previous is None else (previous.id, previous.app_id, previous.title)
        )
        active_key = (
            None if active is None else (active.id, active.app_id, active.title)
        )
        if active_key != previous_key:
            self._notify(active)

    def _clear_windows(self, *, notify: bool = False) -> None:
        """Destroy every handle and publish an empty active-window state."""
        with self._lock:
            handles = tuple(self._handles)
            self._handles.clear()
            self._windows.clear()
            previous = self._active
            self._active = None
            self._next_id = 1
        if notify or previous is not None:
            self._notify(None)
        run_cleanup_steps(
            (
                (
                    f"toplevel handle {index}",
                    lambda handle=handle: self._destroy_handle(handle),
                )
                for index, handle in enumerate(handles, start=1)
            )
        )

    @staticmethod
    def _destroy_handle(handle: Any) -> None:
        """Destroy one toplevel handle when it remains live."""
        if not handle.destroyed:
            handle.destroy()

    @staticmethod
    def _destroy_manager(manager: Any) -> None:
        """Destroy a removed toplevel manager locally."""
        if manager is not None and not manager.destroyed:
            manager._destroy()

    @staticmethod
    def _stop_manager(manager: Any) -> None:
        """Ask a live toplevel manager to stop sending new events."""
        if manager is not None and not manager.destroyed:
            manager.stop()

    def _notify(self, window: Window | None) -> None:
        """Notify a stable listener snapshot without failing the connection."""
        with self._listener_lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(window)
            except Exception:
                traceback.print_exc()
