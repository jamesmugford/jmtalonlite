"""Native clipboard and primary selection using ext-data-control-v1."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .connection import WaylandConnection, run_cleanup_steps, validate_timeout
from .errors import CapabilityUnavailable
from .seats import SeatRegistry
from .transfers import MAX_BYTES, PipeTransfer, ReadBudget

MimePayload = tuple[tuple[str, bytes], ...]
MAX_FORMATS = 64
TRANSFER_TIMEOUT = 2.0


def validate_payload(payload: MimePayload) -> MimePayload:
    """Validate and freeze outgoing data before emitting any protocol requests."""
    result = tuple((name, data) for name, data in payload)
    if len(result) > MAX_FORMATS:
        raise ValueError("Too many clipboard formats")
    names = set()
    total = 0
    for name, data in result:
        if not isinstance(name, str) or not name or "\0" in name or len(name) > 1024:
            raise ValueError("Invalid clipboard MIME name")
        if name in names:
            raise ValueError("Duplicate clipboard MIME name")
        if not isinstance(data, bytes):
            raise TypeError("Clipboard payloads must be immutable bytes")
        names.add(name)
        total += len(data)
    if total > MAX_BYTES:
        raise ValueError("Clipboard snapshot exceeds byte limit")
    return result


@dataclass(eq=False, slots=True)
class _Offer:
    proxy: Any
    formats: list[str] = field(default_factory=list)


class Clipboard:
    """Own all clipboard proxies on the connection thread.

    Public operations expose immutable payloads, revisions and availability.
    Synchronous reads wait on pipe futures outside the owner thread; completion
    never requires Talon's scheduler. Selection events, including identical
    copies, advance the revision; synchronized writes prevent a late clear echo
    confusing capture.
    """

    interface_name = "ext_data_control_manager_v1"
    multiple = False

    def __init__(self, connection: WaylandConnection, seats: SeatRegistry) -> None:
        self._connection = connection
        self._seats = seats
        self._unsubscribe: Callable[[], None] | None = None
        self._lock = threading.Lock()
        self._available = {"main": False, "select": False}
        self._revisions = {"main": 0, "select": 0}
        self._manager: Any = None
        self._manager_id: int | None = None
        self._device: Any = None
        self._seat_id: int | None = None
        self._finished_seat: int | None = None
        self._offers: dict[Any, _Offer] = {}
        self._selections: dict[str, _Offer | None] = {"main": None, "select": None}
        self._sources: dict[Any, dict[str, bytes]] = {}

    def bind(self, registry: Any, global_id: int, version: int, interface: type) -> int:
        """Bind the manager and subscribe to owner-thread seat changes."""
        negotiated = min(version, interface.version)
        self._manager = registry.bind(global_id, interface, negotiated)
        self._manager_id = global_id
        self._finished_seat = None
        if self._unsubscribe is None:
            self._unsubscribe = self._seats.subscribe(self._refresh_device)
        self._refresh_device()
        return negotiated

    def remove(self, global_id: int) -> None:
        """Release a removed manager after all of its child objects."""
        if global_id != self._manager_id:
            return
        manager, self._manager = self._manager, None
        self._manager_id = None
        run_cleanup_steps(
            (
                ("clipboard device", self._retire_device),
                ("clipboard manager", lambda: self._destroy(manager)),
            )
        )

    def ready(self) -> None:
        """Ensure discovery has produced a device for the selected seat."""
        self._refresh_device()

    def close(self) -> None:
        """Symmetrically retire the seat listener, device, offers and sources."""
        unsubscribe, self._unsubscribe = self._unsubscribe, None
        manager, self._manager = self._manager, None
        self._manager_id = None
        self._finished_seat = None
        run_cleanup_steps(
            (
                ("clipboard seat subscription", unsubscribe or (lambda: None)),
                ("clipboard device", self._retire_device),
                ("clipboard manager", lambda: self._destroy(manager)),
            )
        )

    def available(self, mode: str = "main") -> bool:
        """Read availability without touching protocol proxies off-thread."""
        with self._lock:
            return self._available.get(mode, False)

    def serial(self, mode: str = "main") -> int:
        """Return the selection-event revision, never a hash of clipboard data."""
        with self._lock:
            if not self._available.get(mode, False):
                raise CapabilityUnavailable(
                    f"Wayland clipboard mode unavailable: {mode}"
                )
            return self._revisions[mode]

    def read(
        self,
        mode: str = "main",
        formats: tuple[str, ...] | None = None,
        *,
        timeout: float = TRANSFER_TIMEOUT,
    ) -> MimePayload:
        """Read all requested MIME representations or raise without partial data."""
        timeout = validate_timeout(timeout)
        transfers = self._connection.execute(
            lambda: self._begin_read(mode, formats, timeout), timeout
        )
        try:
            return tuple(
                (name, transfer.future.result(timeout + 1))
                for name, transfer in transfers
            )
        finally:
            pending = [
                transfer for _, transfer in transfers if not transfer.future.done()
            ]
            if pending:

                def cancel_pending() -> None:
                    for transfer in pending:
                        self._connection.cancel_transfers(transfer.owner)

                try:
                    self._connection.execute(cancel_pending)
                except CapabilityUnavailable:
                    pass  # Connection teardown owns their descriptors.

    def write(
        self,
        payload: MimePayload,
        mode: str = "main",
        *,
        timeout: float = TRANSFER_TIMEOUT,
    ) -> None:
        """Publish a fresh source (or clear) and wait for the compositor's reply."""
        payload = validate_payload(payload)
        self._connection.synchronize(
            lambda: self._set_selection(mode, payload), timeout
        )

    def _require_device(self, mode: str) -> Any:
        if not self.available(mode) or self._device is None:
            raise CapabilityUnavailable(f"Wayland clipboard mode unavailable: {mode}")
        return self._device

    def _refresh_device(self) -> None:
        seat = self._seats.selected()
        seat_id = None if seat is None else seat.id
        if self._device is not None and seat_id == self._seat_id:
            return
        self._retire_device()
        if (
            self._manager is None
            or seat is None
            or self._connection.stopping
            or seat_id == self._finished_seat
        ):
            return
        device = self._manager.get_data_device(seat.proxy)
        self._device = device
        self._seat_id = seat.id
        device.dispatcher["data_offer"] = self._connection.guard(self._on_offer)
        device.dispatcher["selection"] = self._connection.guard(
            lambda proxy, offer: self._on_selection(proxy, offer, "main")
        )
        device.dispatcher["primary_selection"] = self._connection.guard(
            lambda proxy, offer: self._on_selection(proxy, offer, "select")
        )
        device.dispatcher["finished"] = self._connection.guard(self._on_finished)

    def _on_offer(self, device: Any, proxy: Any) -> None:
        if device is not self._device:
            self._destroy(proxy)
            return
        offer = _Offer(proxy)
        self._offers[proxy] = offer

        def advertised(_proxy: Any, name: str) -> None:
            if name not in offer.formats and len(offer.formats) <= MAX_FORMATS:
                offer.formats.append(name)

        proxy.dispatcher["offer"] = self._connection.guard(advertised)

    def _on_selection(self, device: Any, proxy: Any, mode: str) -> None:
        if device is not self._device:
            return
        previous = self._selections[mode]
        current = None if proxy is None else self._offers[proxy]
        self._selections[mode] = current
        with self._lock:
            self._available[mode] = True
            self._revisions[mode] += 1
        if previous is not None and previous is not current:
            self._retire_offer(previous)

    def _on_finished(self, device: Any) -> None:
        if device is self._device:
            self._finished_seat = self._seat_id
            self._retire_device()

    def _begin_read(
        self, mode: str, formats: tuple[str, ...] | None, timeout: float
    ) -> tuple[tuple[str, PipeTransfer], ...]:
        self._require_device(mode)
        offer = self._selections[mode]
        if offer is None:
            return ()
        if len(offer.formats) > MAX_FORMATS:
            raise ValueError("Too many clipboard formats")
        names = (
            offer.formats
            if formats is None
            else [name for name in formats if name in offer.formats]
        )
        budget = ReadBudget()
        service = self._connection.transfers
        transfers = []
        try:
            for name in dict.fromkeys(names):
                reader, writer = os.pipe2(os.O_CLOEXEC)
                try:
                    transfer = service.start(
                        reader, offer, timeout=timeout, budget=budget
                    )
                    transfers.append((name, transfer))
                    if transfer.future.done():
                        transfer.future.result()
                    offer.proxy.receive(name, writer)
                finally:
                    os.close(writer)
        except Exception:
            self._connection.cancel_transfers(offer)
            raise
        return tuple(transfers)

    def _set_selection(self, mode: str, payload: MimePayload) -> None:
        device = self._require_device(mode)
        source = None
        try:
            if payload:
                source = self._manager.create_data_source()
                self._sources[source] = dict(payload)
                source.dispatcher["send"] = self._connection.guard(self._on_send)
                source.dispatcher["cancelled"] = self._connection.guard(
                    self._on_cancelled
                )
                for name, _data in payload:
                    source.offer(name)
            if mode == "main":
                device.set_selection(source)
            else:
                device.set_primary_selection(source)
            # Some compositors omit the selection event for our own clear.
            # Retire the stale offer before capture starts waiting for a copy;
            # receiving from it can otherwise return empty MIME items instantly.
            if source is None and self._selections[mode] is not None:
                self._on_selection(device, None, mode)
        except Exception as exc:
            if source is not None:
                try:
                    self._on_cancelled(source)
                except Exception as cleanup_error:
                    exc.add_note(
                        f"Clipboard source cleanup also failed: {cleanup_error}"
                    )
            raise

    def _on_send(self, source: Any, name: str, fd: int) -> None:
        payload = self._sources.get(source, {}).get(name)
        if payload is None or self._device is None:
            os.close(fd)
            return
        # An already requested paste must finish even if capture/revert replaces
        # its source in the meantime. These writes belong to the device instead.
        self._connection.transfers.start(
            fd, self._device, timeout=TRANSFER_TIMEOUT, payload=payload
        )

    def _on_cancelled(self, source: Any) -> None:
        self._sources.pop(source, None)
        self._destroy(source)

    def _retire_offer(self, offer: _Offer) -> None:
        self._offers.pop(offer.proxy, None)
        run_cleanup_steps(
            (
                ("clipboard reads", lambda: self._connection.cancel_transfers(offer)),
                ("clipboard offer", lambda: self._destroy(offer.proxy)),
            )
        )

    def _retire_device(self) -> None:
        device, self._device = self._device, None
        self._seat_id = None
        with self._lock:
            for mode in self._available:
                if self._available[mode]:
                    self._revisions[mode] += 1
                self._available[mode] = False
        self._selections = {"main": None, "select": None}
        sources, self._sources = self._sources, {}
        steps = [
            ("clipboard writes", lambda: self._connection.cancel_transfers(device))
        ]
        steps.extend(
            ("clipboard offer", lambda offer=offer: self._retire_offer(offer))
            for offer in tuple(self._offers.values())
        )
        steps.extend(
            ("clipboard source", lambda source=source: self._destroy(source))
            for source in sources
        )
        steps.append(("clipboard device", lambda: self._destroy(device)))
        run_cleanup_steps(steps)

    @staticmethod
    def _destroy(proxy: Any) -> None:
        if proxy is not None and not proxy.destroyed:
            proxy.destroy()
