import os
import selectors
import sys
import unittest
from pathlib import Path

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
sys.path.insert(0, str(PLUGINS))
try:
    from wayland_backend.transfers import PipeTransfers, ReadBudget
finally:
    sys.path.remove(str(PLUGINS))


class PipeTransferTests(unittest.TestCase):
    def setUp(self):
        self.selector = selectors.DefaultSelector()
        self.transfers = PipeTransfers(self.selector)
        self.addCleanup(self.selector.close)
        self.addCleanup(self.transfers.close)

    def pump(self, *transfers):
        for _ in range(1000):
            if all(t.future.done() for t in transfers):
                return
            for key, _mask in self.selector.select(0.1):
                self.transfers.service(key.data)
        self.fail("Transfer did not finish")

    def assert_closed(self, fd):
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_partial_write_read_and_eof_preserve_large_binary_payload(self):
        reader, writer = os.pipe()
        data = bytes(range(256)) * 2048
        incoming = self.transfers.start(reader, object(), timeout=2)
        outgoing = self.transfers.start(writer, object(), timeout=2, payload=data)
        self.pump(incoming, outgoing)
        self.assertEqual(incoming.future.result(), data)
        self.assertEqual(outgoing.future.result(), b"")
        self.assert_closed(reader)
        self.assert_closed(writer)

    def test_empty_payload_is_not_a_missing_selection(self):
        reader, writer = os.pipe()
        incoming = self.transfers.start(reader, object(), timeout=2)
        outgoing = self.transfers.start(writer, object(), timeout=2, payload=b"")
        self.pump(incoming, outgoing)
        self.assertEqual(incoming.future.result(), b"")

    def test_eagain_and_stalled_transfer_do_not_prevent_other_work(self):
        reader, writer = os.pipe()
        self.addCleanup(os.close, writer)
        stalled = self.transfers.start(reader, object(), timeout=2)
        self.transfers.service(stalled)
        self.assertFalse(stalled.future.done())
        other_reader, other_writer = os.pipe()
        incoming = self.transfers.start(other_reader, object(), timeout=2)
        outgoing = self.transfers.start(
            other_writer, object(), timeout=2, payload=b"ok"
        )
        self.pump(incoming, outgoing)
        self.assertEqual(incoming.future.result(), b"ok")
        self.transfers.expire(stalled.deadline)
        with self.assertRaises(TimeoutError):
            stalled.future.result()
        self.assert_closed(reader)

    def test_shared_read_budget_limits_combined_formats(self):
        budget = ReadBudget(3)
        for data in (b"ab", b"cd"):
            reader, writer = os.pipe()
            incoming = self.transfers.start(reader, object(), timeout=2, budget=budget)
            os.write(writer, data)
            os.close(writer)
            self.pump(incoming)
            if data == b"ab":
                self.assertEqual(incoming.future.result(), data)
            else:
                with self.assertRaisesRegex(ValueError, "byte limit"):
                    incoming.future.result()

    def test_broken_pipe_is_local_and_cancellation_is_idempotent(self):
        reader, writer = os.pipe()
        os.close(reader)
        outgoing = self.transfers.start(writer, object(), timeout=2, payload=b"data")
        self.transfers.service(outgoing)
        with self.assertRaises(BrokenPipeError):
            outgoing.future.result()
        owner = object()
        reader, writer = os.pipe()
        self.addCleanup(os.close, writer)
        incoming = self.transfers.start(reader, owner, timeout=2)
        self.transfers.cancel(owner)
        self.transfers.cancel(owner)
        self.transfers.service(incoming)  # Stale selector result after fd retirement.
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            incoming.future.result()
        self.assert_closed(reader)

    def test_registration_failure_consumes_descriptor(self):
        reader, writer = os.pipe()
        self.addCleanup(os.close, writer)
        self.selector.close()
        incoming = self.transfers.start(reader, object(), timeout=2)
        with self.assertRaises(ValueError):
            incoming.future.result()
        self.assert_closed(reader)

    def test_shutdown_completes_every_waiter_even_after_unregister_failure(self):
        incoming = []
        for _ in range(2):
            reader, writer = os.pipe()
            self.addCleanup(os.close, writer)
            incoming.append(self.transfers.start(reader, object(), timeout=2))
        self.selector.close()
        self.transfers.close()
        self.transfers.close()
        for transfer in incoming:
            with self.assertRaisesRegex(RuntimeError, "connection stopped"):
                transfer.future.result()
            self.assert_closed(transfer.fd)
