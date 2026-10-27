"""Bounded nonblocking pipe I/O, owned by the Wayland selector thread."""

from __future__ import annotations

import os
import selectors
import time
from concurrent.futures import Future
from dataclasses import dataclass, field

MAX_BYTES = 64 * 1024 * 1024
MAX_TRANSFERS = 128
_CHUNK = 64 * 1024


@dataclass(slots=True)
class ReadBudget:
    """A shared byte allowance for all formats in one clipboard snapshot."""

    remaining: int = MAX_BYTES


@dataclass(eq=False, slots=True)
class PipeTransfer:
    """One owned descriptor; its future is safe to await from another thread."""

    fd: int
    owner: object
    deadline: float
    payload: bytes | None
    budget: ReadBudget
    future: Future[bytes] = field(default_factory=Future)
    buffer: bytearray = field(default_factory=bytearray)
    offset: int = 0


class PipeTransfers:
    """Service a bounded amount of I/O per turn without delaying input dispatch.

    All methods run on the connection owner thread. Registration consumes the fd,
    including on failure. Errors complete only the affected transfer's future.
    """

    def __init__(self, selector: selectors.BaseSelector) -> None:
        self._selector = selector
        self._active: dict[int, PipeTransfer] = {}

    def start(
        self,
        fd: int,
        owner: object,
        *,
        timeout: float,
        payload: bytes | None = None,
        budget: ReadBudget | None = None,
    ) -> PipeTransfer:
        """Take an fd for reading (payload=None) or writing immutable bytes."""
        transfer = PipeTransfer(
            fd, owner, time.monotonic() + timeout, payload, budget or ReadBudget()
        )
        try:
            if len(self._active) >= MAX_TRANSFERS:
                raise OSError("Too many pending clipboard transfers")
            if payload is not None and len(payload) > MAX_BYTES:
                raise ValueError("Clipboard payload exceeds byte limit")
            os.set_blocking(fd, False)
            event = selectors.EVENT_READ if payload is None else selectors.EVENT_WRITE
            self._selector.register(fd, event, transfer)
        except Exception as exc:
            try:
                os.close(fd)
            except OSError as cleanup_error:
                exc.add_note(f"Closing transfer fd also failed: {cleanup_error}")
            transfer.future.set_exception(exc)
            return transfer
        self._active[fd] = transfer
        return transfer

    def service(self, transfer: PipeTransfer) -> None:
        """Handle one readiness notification, including partial I/O and EOF."""
        if self._active.get(transfer.fd) is not transfer:
            return
        try:
            if transfer.payload is None:
                chunk = os.read(transfer.fd, min(_CHUNK, transfer.budget.remaining + 1))
                if not chunk:
                    self._finish(transfer)
                    return
                transfer.budget.remaining -= len(chunk)
                if transfer.budget.remaining < 0:
                    raise ValueError("Clipboard snapshot exceeds byte limit")
                transfer.buffer.extend(chunk)
            else:
                payload = memoryview(transfer.payload)
                count = os.write(
                    transfer.fd, payload[transfer.offset : transfer.offset + _CHUNK]
                )
                transfer.offset += count
                if transfer.offset == len(payload):
                    self._finish(transfer)
        except BlockingIOError:
            return
        except (OSError, ValueError) as exc:
            self._finish(transfer, exc)

    def expire(self, now: float | None = None) -> None:
        """Fail stalled transfers without failing the Wayland connection."""
        now = time.monotonic() if now is None else now
        for transfer in tuple(self._active.values()):
            if transfer.deadline <= now:
                self._finish(transfer, TimeoutError("Clipboard transfer timed out"))

    def timeout(self) -> float | None:
        """Return the delay until the next transfer deadline."""
        if not self._active:
            return None
        deadline = min(t.deadline for t in self._active.values())
        return max(0.0, deadline - time.monotonic())

    def cancel(self, owner: object) -> None:
        """Retire all transfers belonging to one removed offer or device."""
        for transfer in tuple(self._active.values()):
            if transfer.owner is owner:
                self._finish(transfer, RuntimeError("Clipboard transfer cancelled"))

    def close(self) -> None:
        """Close every descriptor and wake all waiting callers."""
        for transfer in tuple(self._active.values()):
            self._finish(transfer, RuntimeError("Wayland connection stopped"))

    def _finish(self, transfer: PipeTransfer, error: Exception | None = None) -> None:
        if self._active.get(transfer.fd) is not transfer:
            return
        del self._active[transfer.fd]
        try:
            self._selector.unregister(transfer.fd)
        except Exception as exc:
            if error is None:
                error = exc
            else:
                error.add_note(f"Unregistering transfer fd also failed: {exc}")
        try:
            os.close(transfer.fd)
        except OSError as exc:
            if error is None:
                error = exc
            else:
                error.add_note(f"Closing transfer fd also failed: {exc}")
        if error is None:
            transfer.future.set_result(bytes(transfer.buffer))
        else:
            transfer.future.set_exception(error)
