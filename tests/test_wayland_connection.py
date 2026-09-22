import errno
import math
import socket
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
sys.path.insert(0, str(PLUGINS))
try:
    from wayland_backend.bindings import WaylandBindings
    from wayland_backend.connection import (
        WaylandConnection,
        run_cleanup_steps,
        validate_timeout,
    )
    from wayland_backend.errors import CapabilityUnavailable
finally:
    sys.path.remove(str(PLUGINS))


class FakeAdapter:
    def __init__(self, interface_name: str, log=None, *, multiple: bool = False):
        self.interface_name = interface_name
        self.multiple = multiple
        self.log = log if log is not None else []
        self.bound = []
        self.removed = []

    def bind(self, registry, global_id, version, interface):
        negotiated = min(version, interface.version)
        self.bound.append((global_id, negotiated))
        self.log.append((self.interface_name, "bind", global_id))
        return negotiated

    def remove(self, global_id):
        self.removed.append(global_id)
        self.log.append((self.interface_name, "remove", global_id))

    def ready(self):
        self.log.append((self.interface_name, "ready"))

    def close(self):
        self.log.append((self.interface_name, "close"))


class FakeDisplay:
    def __init__(self, log):
        self.log = log

    def flush(self):
        self.log.append(("display", "flush"))
        return 0

    def disconnect(self):
        self.log.append(("display", "disconnect"))


class EventDisplay:
    def __init__(self, fd: int, flush_results=None):
        self._ptr = object()
        self.fd = fd
        self.flush_results = list(flush_results or [0])
        self.dispatch_count = 0
        self.flush_count = 0

    def get_fd(self):
        return self.fd

    def flush(self):
        self.flush_count += 1
        if len(self.flush_results) > 1:
            return self.flush_results.pop(0)
        return self.flush_results[0]

    def dispatch(self, *, block=False):
        self.dispatch_count += 1


class FakeLib:
    def __init__(self, connection, *, prepare_results=None, stop_on_cancel=False):
        self.connection = connection
        self.prepare_results = list(prepare_results or [0])
        self.stop_on_cancel = stop_on_cancel
        self.cancel_count = 0

    def wl_display_prepare_read(self, _display):
        if len(self.prepare_results) > 1:
            return self.prepare_results.pop(0)
        return self.prepare_results[0]

    def wl_display_cancel_read(self, _display):
        self.cancel_count += 1
        if self.stop_on_cancel:
            self.connection._stopping.set()

    def wl_display_read_events(self, _display):
        return 0

    def wl_display_get_error(self, _display):
        return 5


class ConnectionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.connection = WaylandConnection()
        self.first = FakeAdapter("first")
        self.multiple = FakeAdapter("multiple", multiple=True)
        self.connection.register(self.first)
        self.connection.register(self.multiple)
        self.connection._bindings = WaylandBindings(
            Display=object,
            ffi=SimpleNamespace(errno=0),
            lib=SimpleNamespace(),
            interfaces={
                "first": SimpleNamespace(version=2),
                "multiple": SimpleNamespace(version=3),
            },
        )

    def test_singletons_bind_one_candidate_and_rebind_after_removal(self):
        registry = object()
        self.connection._on_global(registry, 10, "first", 3)
        self.connection._on_global(registry, 11, "first", 1)
        self.assertEqual(self.first.bound, [(10, 2)])
        self.assertEqual(self.connection.protocols(), (("first", 2),))

        self.connection._on_global_remove(registry, 10)
        self.assertEqual(self.first.removed, [10])
        self.assertEqual(self.first.bound, [(10, 2), (11, 1)])
        self.assertEqual(self.connection.protocols(), (("first", 1),))

    def test_multiple_globals_are_all_bound_and_report_highest_version(self):
        registry = object()
        self.connection._on_global(registry, 20, "multiple", 1)
        self.connection._on_global(registry, 21, "multiple", 5)
        self.assertEqual(self.multiple.bound, [(20, 1), (21, 3)])
        self.assertEqual(self.connection.protocols(), (("multiple", 3),))
        self.connection._on_global_remove(registry, 21)
        self.assertEqual(self.connection.protocols(), (("multiple", 1),))

    def test_registration_rejects_duplicate_protocols(self):
        with self.assertRaises(ValueError):
            self.connection.register(FakeAdapter("first"))


class ConnectionMailboxTests(unittest.TestCase):
    def test_owner_thread_executes_directly(self):
        connection = WaylandConnection()
        connection._running.set()
        connection._owner_thread_id = threading.get_ident()
        self.assertEqual(connection.execute(lambda: "result"), "result")

    def test_queued_timeout_prevents_late_execution(self):
        connection = WaylandConnection()
        connection._running.set()
        executed = []
        with self.assertRaisesRegex(TimeoutError, "cancelled before execution"):
            connection.execute(lambda: executed.append(True), timeout=0.01)
        connection._drain_commands()
        self.assertEqual(executed, [])

    def test_failure_releases_unexecuted_command_as_unavailable(self):
        connection = WaylandConnection()
        connection._running.set()
        queued = threading.Event()
        errors = []

        def submit():
            try:
                connection.execute(lambda: None)
            except Exception as exc:
                errors.append(exc)

        failure = ValueError("bad event")
        with patch.object(connection, "_wake", queued.set):
            thread = threading.Thread(target=submit)
            thread.start()
            self.assertTrue(queued.wait(1))
            connection.fail(failure)
            thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], CapabilityUnavailable)
        self.assertIs(errors[0].__cause__, failure)

    def test_stopped_connection_is_a_preflight_capability_failure(self):
        connection = WaylandConnection()

        with self.assertRaises(CapabilityUnavailable):
            connection.execute(lambda: None)

    def test_timeout_validation_is_side_effect_free(self):
        for value in (0, -1, math.inf, math.nan, threading.TIMEOUT_MAX * 2):
            with self.assertRaises(ValueError):
                validate_timeout(value)
        for value in (True, "1"):
            with self.assertRaises(TypeError):
                validate_timeout(value)


class ConnectionLifecycleTests(unittest.TestCase):
    def test_cleanup_steps_continue_and_preserve_all_failures(self):
        calls = []

        def fail_first():
            calls.append("first")
            raise ValueError("first failed")

        def fail_second():
            calls.append("second")
            raise RuntimeError("second failed")

        with self.assertRaisesRegex(ValueError, "first failed") as raised:
            run_cleanup_steps((("first", fail_first), ("second", fail_second)))
        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(
            raised.exception.__notes__,
            ["second also failed: RuntimeError: second failed"],
        )

    def test_disconnect_closes_adapters_in_reverse_order_before_display(self):
        log = []
        connection = WaylandConnection()
        connection.register(FakeAdapter("first", log))
        connection.register(FakeAdapter("second", log))
        connection._display = FakeDisplay(log)
        connection._disconnect()
        self.assertEqual(
            log,
            [
                ("second", "close"),
                ("first", "close"),
                ("display", "flush"),
                ("display", "disconnect"),
            ],
        )

    def test_binding_load_failure_is_reported_and_resources_are_closed(self):
        connection = WaylandConnection(
            lambda: (_ for _ in ()).throw(ValueError("broken bindings"))
        )
        with patch("wayland_backend.connection.traceback.print_exc"):
            with self.assertRaisesRegex(RuntimeError, "broken bindings"):
                connection.start(timeout=1)
        self.assertFalse(connection.running())
        self.assertIsNone(connection._thread)
        self.assertIsNone(connection._wake_read)
        self.assertIsNone(connection._wake_write)

    def test_thread_start_failure_rolls_back_wakeup_resources(self):
        connection = WaylandConnection()
        with patch.object(
            threading.Thread,
            "start",
            side_effect=RuntimeError("thread unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "thread unavailable"):
                connection.start(timeout=1)
        self.assertIsNone(connection._thread)
        self.assertIsNone(connection._wake_read)
        self.assertIsNone(connection._wake_write)

    def test_owner_thread_stop_does_not_publish_a_restartable_generation(self):
        connection = WaylandConnection()
        thread = threading.current_thread()
        connection._thread = thread
        connection._running.set()
        connection._owner_thread_id = threading.get_ident()

        connection.stop()

        self.assertIs(connection._thread, thread)
        self.assertTrue(connection.stopping)
        with self.assertRaisesRegex(RuntimeError, "stopping"):
            connection.start()
        connection._thread = None

    def test_stale_stop_request_does_not_stop_a_newer_generation(self):
        connection = WaylandConnection()
        old_thread = object()
        new_thread = object()
        connection._thread = new_thread
        connection._running.set()

        connection._stop_thread(
            old_thread,
            1.0,
            report_shutdown_error=False,
        )

        self.assertIs(connection._thread, new_thread)
        self.assertFalse(connection.stopping)

    def test_start_reset_replaces_generation_ready_event(self):
        connection = WaylandConnection()
        previous_ready = connection._ready
        connection._reset_for_start()
        self.assertIsNot(connection._ready, previous_ready)

    def test_stop_surfaces_shutdown_failure_once(self):
        connection = WaylandConnection()
        connection._shutdown_error = ValueError("cleanup failed")
        with self.assertRaisesRegex(ValueError, "cleanup failed"):
            connection.stop()
        connection.stop()

    def test_guard_converts_callback_failure_to_connection_failure(self):
        connection = WaylandConnection()

        def fail():
            raise ValueError("bad callback")

        with patch("wayland_backend.connection.traceback.print_exc"):
            connection.guard(fail)()
        self.assertEqual(connection.error(), "ValueError: bad callback")
        self.assertTrue(connection.stopping)

    def test_pending_events_are_dispatched_before_preparing_a_read(self):
        connection = WaylandConnection()
        wake_read, wake_write = socket.socketpair()
        display_read, display_write = socket.socketpair()
        try:
            wake_read.setblocking(False)
            connection._wake_read = wake_read
            connection._wake_write = wake_write
            display = EventDisplay(display_read.fileno())
            connection._display = display
            lib = FakeLib(connection, prepare_results=[-1, 0], stop_on_cancel=True)
            connection._bindings = SimpleNamespace(
                ffi=SimpleNamespace(errno=0),
                lib=lib,
            )
            wake_write.send(b"wake")
            connection._event_loop()
            self.assertEqual(display.dispatch_count, 1)
            self.assertEqual(lib.cancel_count, 1)
        finally:
            wake_read.close()
            wake_write.close()
            display_read.close()
            display_write.close()

    def test_wakeup_cancels_a_prepared_read(self):
        connection = WaylandConnection()
        wake_read, wake_write = socket.socketpair()
        display_read, display_write = socket.socketpair()
        try:
            wake_read.setblocking(False)
            connection._wake_read = wake_read
            connection._wake_write = wake_write
            connection._display = EventDisplay(display_read.fileno())
            lib = FakeLib(connection, stop_on_cancel=True)
            connection._bindings = SimpleNamespace(
                ffi=SimpleNamespace(errno=0),
                lib=lib,
            )
            wake_write.send(b"wake")
            connection._event_loop()
            self.assertEqual(lib.cancel_count, 1)
        finally:
            wake_read.close()
            wake_write.close()
            display_read.close()
            display_write.close()

    def test_disconnect_flush_retries_after_eagain(self):
        connection = WaylandConnection()
        display_socket, peer_socket = socket.socketpair()
        try:
            display = EventDisplay(
                display_socket.fileno(),
                flush_results=[-1, 0],
            )
            connection._display = display
            connection._bindings = SimpleNamespace(
                ffi=SimpleNamespace(errno=errno.EAGAIN)
            )
            connection._flush_for_disconnect()
            self.assertEqual(display.flush_count, 2)
        finally:
            display_socket.close()
            peer_socket.close()


if __name__ == "__main__":
    unittest.main()
