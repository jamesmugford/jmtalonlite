"""Single-owner-thread Wayland transport and protocol registry."""

from __future__ import annotations

import errno
import math
import selectors
import socket
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Protocol

from .bindings import WaylandBindings, load_wayland_bindings
from .errors import CapabilityUnavailable

_DISCONNECT_FLUSH_TIMEOUT = 0.25


class ProtocolAdapter(Protocol):
    """The lifecycle contract for one Wayland global interface."""

    interface_name: str
    multiple: bool

    def bind(
        self, registry: Any, global_id: int, version: int, interface: type
    ) -> int:
        """Bind one announced global and return its negotiated version."""
        ...

    def remove(self, global_id: int) -> None:
        """Release one removed global owned by this adapter."""
        ...

    def ready(self) -> None:
        """Finish initialization after the initial registry round trips."""
        ...

    def close(self) -> None:
        """Release every protocol resource owned by this adapter."""
        ...


class CommandState(Enum):
    """The owner-thread mailbox lifecycle of one submitted command."""

    QUEUED = auto()
    CLAIMED = auto()
    CANCELLED = auto()
    DONE = auto()


@dataclass(slots=True)
class _Command:
    """A synchronous command awaiting execution on the Wayland owner thread."""

    callback: Callable[[], Any]
    done: threading.Event = field(default_factory=threading.Event)
    state: CommandState = CommandState.QUEUED
    result: Any = None
    error: Exception | None = None


class WaylandConnection:
    """Own the Wayland display, registry, mailbox, and worker thread."""

    def __init__(
        self,
        load_bindings: Callable[[], WaylandBindings] | None = None,
    ) -> None:
        """Create a disconnected transport without performing I/O."""
        self._load_bindings = load_bindings or load_wayland_bindings
        self._lifecycle_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._command_lock = threading.Lock()
        self._ready = threading.Event()
        self._running = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._owner_thread_id: int | None = None
        self._wake_read: socket.socket | None = None
        self._wake_write: socket.socket | None = None
        self._commands: deque[_Command] = deque()
        self._error: str | None = None
        self._shutdown_error: Exception | None = None
        self._bindings: WaylandBindings | None = None
        self._display: Any = None
        self._registry: Any = None
        self._sync_callback: Any = None
        self._initialized = False
        self._adapters: list[ProtocolAdapter] = []
        self._adapter_by_interface: dict[str, ProtocolAdapter] = {}
        self._announced_globals: dict[int, tuple[str, int]] = {}
        self._active_globals: dict[str, dict[int, int]] = {}

    @property
    def initialized(self) -> bool:
        """Return whether initial registry synchronization has completed."""
        return self._initialized

    @property
    def stopping(self) -> bool:
        """Return whether shutdown has begun."""
        return self._stopping.is_set()

    def register(self, adapter: ProtocolAdapter) -> None:
        """Register one protocol adapter before the connection starts."""
        if self._thread is not None or self._running.is_set():
            raise RuntimeError("Cannot register protocols after Wayland startup")
        if adapter.interface_name in self._adapter_by_interface:
            raise ValueError(
                f"Wayland protocol already registered: {adapter.interface_name}"
            )
        self._adapters.append(adapter)
        self._adapter_by_interface[adapter.interface_name] = adapter

    def start(self, timeout: float = 5.0) -> None:
        """Start the owner thread and wait for registry initialization."""
        timeout = validate_timeout(timeout)
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                if self._stopping.is_set():
                    raise RuntimeError("Wayland connection is stopping")
                thread = self._thread
                ready = self._ready
            else:
                self._reset_for_start()
                self._wake_read, self._wake_write = socket.socketpair()
                self._wake_read.setblocking(False)
                self._wake_write.setblocking(False)
                thread = threading.Thread(
                    target=self._run,
                    name="talon-wayland-owner",
                    daemon=True,
                )
                self._thread = thread
                try:
                    thread.start()
                except Exception:
                    self._thread = None
                    self._close_wakeup()
                    raise
                ready = self._ready

        if not ready.wait(timeout):
            self._stop_thread(thread, timeout, report_shutdown_error=False)
            raise TimeoutError("Timed out starting the Wayland connection")
        with self._state_lock:
            error = self._error
        if error is not None:
            self._stop_thread(thread, timeout, report_shutdown_error=False)
            raise RuntimeError(error)
        with self._lifecycle_lock:
            if (
                self._thread is not thread
                or not thread.is_alive()
                or not self._running.is_set()
                or self._stopping.is_set()
            ):
                raise RuntimeError("Wayland connection stopped during startup")

    def stop(self, timeout: float = 5.0) -> None:
        """Stop and join the owner thread, safely allowing repeated calls."""
        timeout = validate_timeout(timeout)
        self._stop_thread(None, timeout, report_shutdown_error=True)

    def _stop_thread(
        self,
        expected_thread: threading.Thread | None,
        timeout: float,
        *,
        report_shutdown_error: bool,
    ) -> None:
        """Stop only the expected connection generation and join its thread."""
        shutdown_error = None
        with self._lifecycle_lock:
            thread = self._thread
            if expected_thread is not None and thread is not expected_thread:
                return
            if thread is None:
                self._close_wakeup()
            else:
                self._stopping.set()
                self._fail_pending_commands(
                    CapabilityUnavailable("Wayland connection is stopping")
                )
                self._wake()
                if thread is threading.current_thread():
                    return
                thread.join(timeout)
                if thread.is_alive():
                    raise TimeoutError("Timed out stopping the Wayland connection")
                if self._thread is thread:
                    self._thread = None
                self._close_wakeup()
            if report_shutdown_error:
                with self._state_lock:
                    shutdown_error = self._shutdown_error
                    self._shutdown_error = None
        if shutdown_error is not None:
            raise shutdown_error.with_traceback(shutdown_error.__traceback__)

    def execute(self, callback: Callable[[], Any], timeout: float = 1.0) -> Any:
        """Run on the owner thread, timing out only while still queued.

        Once claimed, wait for the outcome to avoid ambiguous input replay.
        """
        timeout = validate_timeout(timeout)
        if not self._running.is_set() or self._stopping.is_set():
            raise CapabilityUnavailable("Wayland connection is not running")
        if threading.get_ident() == self._owner_thread_id:
            return callback()

        command = _Command(callback)
        with self._command_lock:
            if not self._running.is_set() or self._stopping.is_set():
                raise CapabilityUnavailable("Wayland connection is not running")
            self._commands.append(command)
        self._wake()

        if not command.done.wait(timeout):
            with self._command_lock:
                if command.state is CommandState.QUEUED:
                    command.state = CommandState.CANCELLED
                    cancelled = True
                else:
                    cancelled = False
            if cancelled:
                raise TimeoutError("Wayland command was cancelled before execution")
            command.done.wait()
        if command.error is not None:
            raise command.error
        return command.result

    def fail(self, error: Exception) -> None:
        """Record a fatal protocol failure and begin connection shutdown."""
        with self._state_lock:
            if self._error is None:
                self._error = f"{type(error).__name__}: {error}"
        self._stopping.set()
        self._ready.set()
        unavailable = CapabilityUnavailable(
            "Wayland connection failed before command execution"
        )
        unavailable.__cause__ = error
        self._fail_pending_commands(unavailable)
        self._wake()

    def guard(self, callback: Callable[..., None]) -> Callable[..., None]:
        """Wrap a protocol callback so failures stop the connection."""

        def guarded(*args: Any) -> None:
            """Invoke the callback and convert failures to connection failure."""
            try:
                callback(*args)
            except Exception as exc:
                self.fail(exc)
                traceback.print_exc()

        return guarded

    def protocols(self) -> tuple[tuple[str, int], ...]:
        """Return active protocol names and their highest negotiated versions."""
        with self._state_lock:
            return tuple(
                sorted(
                    (interface, max(versions.values()))
                    for interface, versions in self._active_globals.items()
                    if versions
                )
            )

    def error(self) -> str | None:
        """Return the first fatal connection error, if any."""
        with self._state_lock:
            return self._error

    def running(self) -> bool:
        """Return whether the owner thread is processing Wayland events."""
        return self._running.is_set()

    def deactivate(self, interface_name: str, global_id: int) -> None:
        """Forget a server-retired global and activate its next candidate."""
        self._announced_globals.pop(global_id, None)
        with self._state_lock:
            active = self._active_globals.get(interface_name)
            if active is not None:
                active.pop(global_id, None)
        self._activate_next(interface_name)

    def _reset_for_start(self) -> None:
        """Reset connection-owned state before creating a new owner thread."""
        self._close_wakeup()
        self._ready = threading.Event()
        self._running.clear()
        self._stopping.clear()
        with self._state_lock:
            self._error = None
            self._shutdown_error = None
            self._active_globals.clear()
        self._announced_globals.clear()
        with self._command_lock:
            self._commands.clear()
        self._bindings = None
        self._display = None
        self._registry = None
        self._sync_callback = None
        self._initialized = False

    def _run(self) -> None:
        """Connect, dispatch events, and clean up on the owner thread."""
        self._owner_thread_id = threading.get_ident()
        try:
            self._bindings = self._load_bindings()
            self._connect()
            self._running.set()
            self._event_loop()
        except Exception as exc:
            self.fail(exc)
            traceback.print_exc()
        finally:
            self._running.clear()
            self._ready.set()
            self._fail_pending_commands(
                CapabilityUnavailable("Wayland connection stopped")
            )
            try:
                self._disconnect()
            except Exception as exc:
                with self._state_lock:
                    if self._shutdown_error is None:
                        self._shutdown_error = exc
                self.fail(exc)
                traceback.print_exc()
            finally:
                self._owner_thread_id = None
                self._close_wakeup()

    def _connect(self) -> None:
        """Connect the display and request two initial registry round trips."""
        assert self._bindings is not None
        display = self._bindings.Display()
        display.connect()
        self._display = display
        registry = display.get_registry()
        registry.dispatcher["global"] = self.guard(self._on_global)
        registry.dispatcher["global_remove"] = self.guard(self._on_global_remove)
        self._registry = registry
        callback = display.sync()
        callback.dispatcher["done"] = self.guard(self._on_registry_sync)
        self._sync_callback = callback

    def _on_registry_sync(self, callback: Any, _callback_data: int) -> None:
        """Finish discovery and request a round trip for bound-object events."""
        callback._destroy()
        if self._stopping.is_set():
            return
        callback = self._display.sync()
        callback.dispatcher["done"] = self.guard(self._on_bindings_sync)
        self._sync_callback = callback

    def _on_bindings_sync(self, callback: Any, _callback_data: int) -> None:
        """Mark initialization complete and notify every protocol adapter."""
        callback._destroy()
        self._sync_callback = None
        self._initialized = True
        for adapter in self._adapters:
            adapter.ready()
        self._ready.set()

    def _on_global(
        self, registry: Any, global_id: int, interface_name: str, version: int
    ) -> None:
        """Record a supported global and bind it when its adapter is available."""
        adapter = self._adapter_by_interface.get(interface_name)
        if adapter is None:
            return
        self._announced_globals[global_id] = (interface_name, version)
        with self._state_lock:
            active = self._active_globals.setdefault(interface_name, {})
            should_bind = adapter.multiple or not active
        if should_bind:
            self._bind(adapter, registry, global_id, version)

    def _bind(
        self,
        adapter: ProtocolAdapter,
        registry: Any,
        global_id: int,
        version: int,
    ) -> None:
        """Bind one global through its adapter and publish its version."""
        assert self._bindings is not None
        interface = self._bindings.interfaces[adapter.interface_name]
        negotiated = adapter.bind(registry, global_id, version, interface)
        with self._state_lock:
            self._active_globals.setdefault(adapter.interface_name, {})[global_id] = (
                negotiated
            )

    def _on_global_remove(self, registry: Any, global_id: int) -> None:
        """Release a removed global and bind the next matching announcement."""
        announcement = self._announced_globals.pop(global_id, None)
        if announcement is None:
            return
        interface_name, _version = announcement
        adapter = self._adapter_by_interface[interface_name]
        with self._state_lock:
            active = self._active_globals.get(interface_name, {})
            was_active = global_id in active
        if not was_active:
            return
        adapter.remove(global_id)
        with self._state_lock:
            self._active_globals[interface_name].pop(global_id, None)
        if not adapter.multiple:
            self._activate_next(interface_name, registry)

    def _activate_next(self, interface_name: str, registry: Any = None) -> None:
        """Bind the next announced singleton global for an interface."""
        adapter = self._adapter_by_interface[interface_name]
        if adapter.multiple:
            return
        with self._state_lock:
            if self._active_globals.get(interface_name):
                return
        registry = registry or self._registry
        if registry is None:
            return
        for global_id, (candidate_interface, version) in (
            self._announced_globals.items()
        ):
            if candidate_interface == interface_name:
                self._bind(adapter, registry, global_id, version)
                return

    def _event_loop(self) -> None:
        """Dispatch Wayland and mailbox events until shutdown is requested."""
        assert self._bindings is not None
        assert self._display is not None
        assert self._wake_read is not None
        ffi = self._bindings.ffi
        lib = self._bindings.lib
        display = self._display
        display_fd = display.get_fd()

        with selectors.DefaultSelector() as selector:
            selector.register(self._wake_read, selectors.EVENT_READ, "wake")
            selector.register(display_fd, selectors.EVENT_READ, "display")
            while not self._stopping.is_set():
                self._drain_commands()
                if self._stopping.is_set():
                    break
                while lib.wl_display_prepare_read(display._ptr) != 0:
                    display.dispatch(block=False)

                prepared = True
                try:
                    events = selectors.EVENT_READ
                    flush_result = display.flush()
                    if flush_result == -1:
                        if ffi.errno != errno.EAGAIN:
                            raise OSError(ffi.errno, "wl_display_flush failed")
                        events |= selectors.EVENT_WRITE
                    selector.modify(display_fd, events, "display")
                    selected = selector.select()
                    display_events = 0
                    for key, mask in selected:
                        if key.data == "wake":
                            self._drain_wakeup()
                        else:
                            display_events |= mask

                    if self._stopping.is_set():
                        lib.wl_display_cancel_read(display._ptr)
                        prepared = False
                        break
                    if display_events & selectors.EVENT_READ:
                        read_result = lib.wl_display_read_events(display._ptr)
                        prepared = False
                        if read_result == -1:
                            error = lib.wl_display_get_error(display._ptr)
                            raise RuntimeError(
                                f"Wayland read failed with error {error}"
                            )
                        display.dispatch(block=False)
                    else:
                        lib.wl_display_cancel_read(display._ptr)
                        prepared = False
                finally:
                    if prepared:
                        lib.wl_display_cancel_read(display._ptr)

    def _drain_commands(self) -> None:
        """Execute every queued command that has not been cancelled."""
        while True:
            with self._command_lock:
                if not self._commands:
                    return
                command = self._commands.popleft()
                if command.state is CommandState.CANCELLED:
                    cancelled = True
                else:
                    command.state = CommandState.CLAIMED
                    cancelled = False
            if cancelled:
                command.done.set()
                continue
            if self._stopping.is_set():
                command.error = CapabilityUnavailable("Wayland connection is stopping")
            else:
                try:
                    command.result = command.callback()
                except Exception as exc:
                    command.error = exc
            with self._command_lock:
                command.state = CommandState.DONE
            command.done.set()

    def _fail_pending_commands(self, error: Exception) -> None:
        """Complete every queued command with a shared terminal error."""
        with self._command_lock:
            commands = tuple(self._commands)
            self._commands.clear()
            for command in commands:
                if command.state is CommandState.QUEUED:
                    command.error = error
                command.state = CommandState.DONE
        for command in commands:
            command.done.set()

    def _wake(self) -> None:
        """Wake the selector after a command or shutdown request."""
        writer = self._wake_write
        if writer is None:
            return
        try:
            writer.send(b"\0")
        except (BlockingIOError, OSError):
            pass

    def _drain_wakeup(self) -> None:
        """Consume all bytes currently queued on the wake socket."""
        assert self._wake_read is not None
        while True:
            try:
                if not self._wake_read.recv(4096):
                    return
            except BlockingIOError:
                return

    def _close_wakeup(self) -> None:
        """Close and clear both mailbox wake sockets idempotently."""
        for sock in (self._wake_read, self._wake_write):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._wake_read = None
        self._wake_write = None

    def _flush_for_disconnect(self) -> None:
        """Flush queued destructor requests before disconnecting the display."""
        assert self._display is not None
        display = self._display
        bindings = self._bindings
        deadline = time.monotonic() + _DISCONNECT_FLUSH_TIMEOUT
        while display.flush() == -1:
            if bindings is None:
                raise RuntimeError("Wayland bindings unavailable during shutdown")
            if bindings.ffi.errno != errno.EAGAIN:
                raise OSError(
                    bindings.ffi.errno,
                    "wl_display_flush failed during shutdown",
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out flushing Wayland shutdown requests")
            with selectors.DefaultSelector() as selector:
                selector.register(display.get_fd(), selectors.EVENT_WRITE)
                if not selector.select(remaining):
                    raise TimeoutError("Timed out flushing Wayland shutdown requests")

    def _disconnect(self) -> None:
        """Release adapters and disconnect while preserving cleanup failures."""
        display = self._display
        if display is None:
            return
        failures: list[tuple[str, Exception]] = []

        def attempt(label: str, operation: Callable[[], Any]) -> None:
            """Record a cleanup failure while allowing later cleanup to run."""
            try:
                operation()
            except Exception as exc:
                failures.append((label, exc))

        try:
            for adapter in reversed(self._adapters):
                attempt(f"{adapter.interface_name} cleanup", adapter.close)
            attempt("shutdown flush", self._flush_for_disconnect)
        finally:
            try:
                display.disconnect()
            except Exception as exc:
                failures.append(("display disconnect", exc))
            finally:
                self._display = None
                self._registry = None
                self._sync_callback = None
                self._bindings = None
                self._initialized = False
                self._announced_globals.clear()
                with self._state_lock:
                    self._active_globals.clear()

        if failures:
            _label, error = failures[0]
            for label, secondary in failures[1:]:
                error.add_note(
                    f"{label} also failed: {type(secondary).__name__}: {secondary}"
                )
            raise error.with_traceback(error.__traceback__)


def validate_timeout(timeout: float) -> float:
    """Return a finite positive timeout accepted by threading primitives."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError("Timeout must be a number")
    if isinstance(timeout, float) and not math.isfinite(timeout):
        raise ValueError("Timeout must be finite")
    if timeout <= 0 or timeout > threading.TIMEOUT_MAX:
        raise ValueError("Timeout must be positive and within threading.TIMEOUT_MAX")
    return float(timeout)


def monotonic_timestamp_ms() -> int:
    """Return monotonic milliseconds wrapped to the Wayland uint32 range."""
    return (time.monotonic_ns() // 1_000_000) & 0xFFFFFFFF


def run_cleanup_steps(
    steps: Iterable[tuple[str, Callable[[], Any]]],
) -> None:
    """Run every cleanup step and raise the first failure with later notes."""
    failures: list[tuple[str, Exception]] = []
    for label, operation in steps:
        try:
            operation()
        except Exception as exc:
            failures.append((label, exc))
    if not failures:
        return
    _label, error = failures[0]
    for label, secondary in failures[1:]:
        error.add_note(f"{label} also failed: {type(secondary).__name__}: {secondary}")
    raise error.with_traceback(error.__traceback__)
