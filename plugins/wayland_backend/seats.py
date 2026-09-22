"""Wayland seat discovery, selection, and immutable status values."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import IntFlag
from typing import Any

from .connection import WaylandConnection, run_cleanup_steps


class SeatCapability(IntFlag):
    """Input capabilities advertised by a Wayland seat."""

    POINTER = 1
    KEYBOARD = 2
    TOUCH = 4


@dataclass(frozen=True, slots=True)
class SeatSnapshot:
    """Public immutable state for one advertised Wayland seat."""

    id: int
    name: str
    capabilities: frozenset[SeatCapability]
    selected: bool = False


@dataclass(frozen=True, slots=True)
class SelectedSeat:
    """An owner-thread borrow of the selected wl_seat and immutable metadata."""

    id: int
    version: int
    proxy: Any
    capabilities: SeatCapability


@dataclass(slots=True)
class _Seat:
    """One bound wl_seat proxy and its latest advertised state."""

    id: int
    version: int
    proxy: Any
    name: str = ""
    capabilities: SeatCapability = SeatCapability(0)

    def snapshot(self, selected_id: int | None) -> SeatSnapshot:
        """Return an immutable view of this seat."""
        capabilities = frozenset(
            capability
            for capability in SeatCapability
            if self.capabilities & capability
        )
        return SeatSnapshot(
            id=self.id,
            name=self.name or f"global-{self.id}",
            capabilities=capabilities,
            selected=self.id == selected_id,
        )


def choose_seat(seats: Iterable[SeatSnapshot]) -> int | None:
    """Return the preferred seat ID, favoring seat0 then registry order."""
    values = tuple(seats)
    if not values:
        return None
    return min(values, key=lambda seat: (seat.name != "seat0", seat.id)).id


class SeatRegistry:
    """Own every wl_seat and publish changes to the selected seat."""

    interface_name = "wl_seat"
    multiple = True

    def __init__(self, connection: WaylandConnection) -> None:
        """Create an empty seat registry attached to a connection."""
        self._connection = connection
        self._lock = threading.Lock()
        self._listener_lock = threading.Lock()
        self._seats: dict[int, _Seat] = {}
        self._selected_id: int | None = None
        self._listeners: list[Callable[[], None]] = []

    def bind(
        self, registry: Any, global_id: int, version: int, interface: type
    ) -> int:
        """Bind one wl_seat and subscribe to name and capability events."""
        negotiated = min(version, interface.version)
        proxy = registry.bind(global_id, interface, negotiated)
        seat = _Seat(global_id, negotiated, proxy)
        with self._lock:
            self._seats[global_id] = seat
        proxy.dispatcher["name"] = self._connection.guard(
            lambda _proxy, value: self._set_name(global_id, value)
        )
        proxy.dispatcher["capabilities"] = self._connection.guard(
            lambda _proxy, value: self._set_capabilities(global_id, value)
        )
        self._publish_change()
        return negotiated

    def remove(self, global_id: int) -> None:
        """Publish a seat removal before releasing its proxy."""
        with self._lock:
            seat = self._seats.pop(global_id, None)
        if seat is None:
            return
        run_cleanup_steps(
            (
                ("selected seat listeners", lambda: self._publish_change(force=True)),
                (f"seat {seat.id}", lambda: self._release(seat)),
            )
        )

    def ready(self) -> None:
        """Publish the selected seat after initial registry synchronization."""
        self._publish_change(force=True)

    def close(self) -> None:
        """Release all seats and publish that no seat remains selected."""
        with self._lock:
            seats = tuple(self._seats.values())
            self._seats.clear()
            had_selection = self._selected_id is not None
            self._selected_id = None
        steps = []
        if had_selection:
            steps.append(("selected seat listeners", self._notify))
        steps.extend(
            (
                (f"seat {seat.id}", lambda seat=seat: self._release(seat))
                for seat in seats
            )
        )
        run_cleanup_steps(steps)

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a selected-seat listener and return an idempotent unsubscribe."""
        with self._listener_lock:
            self._listeners.append(callback)

        def unsubscribe() -> None:
            """Remove this callback if it remains subscribed."""
            with self._listener_lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return unsubscribe

    def selected(self) -> SelectedSeat | None:
        """Borrow the selected seat for immediate owner-thread protocol use."""
        with self._lock:
            seat = self._seats.get(self._selected_id)
            if seat is None:
                return None
            return SelectedSeat(
                seat.id,
                seat.version,
                seat.proxy,
                seat.capabilities,
            )

    def snapshots(self) -> tuple[SeatSnapshot, ...]:
        """Return immutable seat snapshots ordered by registry ID."""
        with self._lock:
            selected_id = self._selected_id
            return tuple(
                self._seats[key].snapshot(selected_id) for key in sorted(self._seats)
            )

    def _set_name(self, seat_id: int, name: str) -> None:
        """Apply a seat name event and recompute seat preference."""
        with self._lock:
            seat = self._seats.get(seat_id)
            if seat is None:
                return
            seat.name = name
        self._publish_change()

    def _set_capabilities(self, seat_id: int, capabilities: int) -> None:
        """Apply seat capabilities and notify selected-seat consumers."""
        with self._lock:
            seat = self._seats.get(seat_id)
            if seat is None:
                return
            seat.capabilities = SeatCapability(capabilities)
            selected = seat_id == self._selected_id
        self._publish_change(force=selected)

    def _publish_change(self, *, force: bool = False) -> None:
        """Recompute the selected seat and notify consumers when relevant."""
        with self._lock:
            snapshots = tuple(
                seat.snapshot(self._selected_id) for seat in self._seats.values()
            )
            selected_id = choose_seat(snapshots)
            changed = selected_id != self._selected_id
            self._selected_id = selected_id
        if changed or force:
            self._notify()

    def _notify(self) -> None:
        """Invoke every listener outside locks and preserve their failures."""
        with self._listener_lock:
            listeners = tuple(self._listeners)
        run_cleanup_steps(
            (f"selected seat listener {index}", listener)
            for index, listener in enumerate(listeners, start=1)
        )

    @staticmethod
    def _release(seat: _Seat) -> None:
        """Release one seat with the request supported by its protocol version."""
        if seat.proxy.destroyed:
            return
        if seat.version >= 5:
            seat.proxy.release()
        else:
            seat.proxy._destroy()
