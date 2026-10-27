import os
import selectors
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
sys.path.insert(0, str(PLUGINS))
try:
    from wayland_backend.clipboard import Clipboard, validate_payload
    from wayland_backend.errors import CapabilityUnavailable
    from wayland_backend.seats import SeatRegistry
    from wayland_backend.transfers import PipeTransfers
finally:
    sys.path.remove(str(PLUGINS))

if __package__:
    from .wayland_fakes import FakeProxy, FakeRegistry, ImmediateConnection, interface
else:
    from wayland_fakes import FakeProxy, FakeRegistry, ImmediateConnection, interface


class DataProxy(FakeProxy):
    def __init__(self):
        super().__init__()
        self.devices = []
        self.sources = []
        self.writers = []

    def get_data_device(self, seat):
        device = DataProxy()
        self.devices.append(device)
        return device

    def create_data_source(self):
        source = DataProxy()
        self.sources.append(source)
        return source

    def offer(self, name):
        self.calls.append(("offer", name))

    def set_selection(self, source):
        self.calls.append(("selection", source))

    def set_primary_selection(self, source):
        self.calls.append(("primary", source))

    def receive(self, name, fd):
        self.writers.append((name, os.dup(fd)))


class ClipboardTests(unittest.TestCase):
    def setUp(self):
        self.connection = ImmediateConnection()
        self.selector = selectors.DefaultSelector()
        self.transfers = PipeTransfers(self.selector)
        self.connection.transfers = self.transfers
        self.connection.cancel_transfers = self.transfers.cancel
        self.connection.synchronize = self.connection.execute
        self.seats = SeatRegistry(self.connection)
        self.registry = FakeRegistry()
        self.seats.bind(self.registry, 1, 9, interface(9))
        self.clipboard = Clipboard(self.connection, self.seats)
        self.manager = DataProxy()
        with patch.object(self.registry, "bind", return_value=self.manager):
            self.clipboard.bind(self.registry, 2, 1, interface(1))
        self.device = self.manager.devices[-1]
        self.select(None)
        self.addCleanup(self.selector.close)
        self.addCleanup(self.transfers.close)
        self.addCleanup(self.clipboard.close)

    def select(self, offer, mode="main", device=None):
        device = device or self.device
        event = "selection" if mode == "main" else "primary_selection"
        device.dispatcher[event](device, offer)

    def offer(self, *formats):
        offer = DataProxy()
        self.device.dispatcher["data_offer"](self.device, offer)
        for fmt in formats:
            offer.dispatcher["offer"](offer, fmt)
        return offer

    def test_selection_modes_are_distinct_and_primary_is_optional(self):
        self.assertTrue(self.clipboard.available())
        self.assertFalse(self.clipboard.available("select"))
        with self.assertRaises(CapabilityUnavailable):
            self.clipboard.write((("text/plain", b"primary"),), "select")
        main = self.offer("text/plain")
        self.select(main)
        revision = self.clipboard.serial()
        primary = self.offer("text/html")
        self.select(primary, "select")
        self.assertTrue(self.clipboard.available("select"))
        self.assertEqual(self.clipboard.serial(), revision)
        self.select(None, "select")
        self.assertTrue(primary.destroyed)
        self.assertFalse(main.destroyed)

    def test_identical_offers_and_clear_each_advance_revision(self):
        old = self.offer("text/plain")
        self.select(old)
        revision = self.clipboard.serial()
        self.select(self.offer("text/plain"))
        self.assertEqual(self.clipboard.serial(), revision + 1)
        self.assertTrue(old.destroyed)
        self.select(None)
        self.assertEqual(self.clipboard.serial(), revision + 2)

    def test_clear_retires_offer_when_compositor_sends_no_selection_event(self):
        offer = self.offer("text/plain", "text/html")
        self.select(offer)
        revision = self.clipboard.serial()
        self.clipboard.write(())
        self.assertEqual(self.clipboard.read(), ())
        self.assertTrue(offer.destroyed)
        self.assertEqual(self.clipboard.serial(), revision + 1)
        self.clipboard.write(())
        self.assertEqual(self.clipboard.serial(), revision + 1)
        self.select(self.offer("text/plain"))
        self.assertEqual(self.clipboard.serial(), revision + 2)

    def test_write_uses_fresh_source_and_advertises_before_selection(self):
        payload = (("text/plain", b"same"), ("application/custom", b"\0\xff"))
        for _ in range(2):
            self.clipboard.write(payload)
            source = self.manager.sources[-1]
            self.assertEqual(source.calls, [("offer", name) for name, _ in payload])
            self.assertIs(self.device.calls[-1][1], source)
        self.assertIsNot(*self.manager.sources)
        self.clipboard.write(())
        self.assertEqual(self.device.calls[-1], ("selection", None))

    def test_reads_preserve_multiple_formats_and_cancel_on_replacement(self):
        offer = self.offer("text/plain", "application/custom")
        self.select(offer)
        transfers = self.clipboard._begin_read("main", None, 2)
        self.assertEqual(
            [name for name, _ in transfers], ["text/plain", "application/custom"]
        )
        for name, fd in offer.writers:
            os.write(fd, name.encode())
            os.close(fd)
        for _ in range(2):
            for key, _ in self.selector.select(0.1):
                self.transfers.service(key.data)
        self.assertEqual(
            [t.future.result() for _, t in transfers],
            [b"text/plain", b"application/custom"],
        )
        pending = self.clipboard._begin_read("main", ("text/plain",), 2)[0][1]
        self.select(None)
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            pending.future.result()
        os.close(offer.writers[-1][1])

    def test_source_cancellation_allows_already_requested_paste_to_finish(self):
        self.clipboard.write((("text/plain", b"paste"),))
        source = self.manager.sources[-1]
        reader, writer = os.pipe()
        self.addCleanup(os.close, reader)
        source.dispatcher["send"](source, "text/plain", writer)
        source.dispatcher["cancelled"](source)
        for key, _ in self.selector.select(0.1):
            self.transfers.service(key.data)
        self.assertEqual(os.read(reader, 100), b"paste")
        self.assertEqual(os.read(reader, 100), b"")
        self.assertTrue(source.destroyed)
        self.assertEqual(self.connection.failures, [])

    def test_unknown_mime_send_closes_fd_without_failing_connection(self):
        self.clipboard.write((("text/plain", b"paste"),))
        source = self.manager.sources[-1]
        reader, writer = os.pipe()
        self.addCleanup(os.close, reader)
        source.dispatcher["send"](source, "unknown", writer)
        self.assertEqual(os.read(reader, 1), b"")
        self.assertEqual(self.connection.failures, [])

    def test_seat_loss_cancels_reads_and_writes_before_releasing_seat(self):
        offer = self.offer("text/plain")
        self.select(offer)
        incoming = self.clipboard._begin_read("main", None, 2)[0][1]
        self.addCleanup(os.close, offer.writers[-1][1])
        self.clipboard.write((("text/plain", b"data"),))
        source = self.manager.sources[-1]
        reader, writer = os.pipe()
        self.addCleanup(os.close, reader)
        source.dispatcher["send"](source, "text/plain", writer)
        self.seats.remove(1)
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            incoming.future.result()
        self.assertEqual(os.read(reader, 1), b"")
        self.assertFalse(self.clipboard.available())
        self.assertTrue(self.device.destroyed)
        self.assertTrue(source.destroyed)
        self.assertEqual(self.connection.failures, [])

    def test_finished_device_does_not_recreate_until_seat_changes(self):
        self.device.dispatcher["finished"](self.device)
        self.clipboard.ready()
        self.assertFalse(self.clipboard.available())
        self.assertEqual(len(self.manager.devices), 1)
        self.seats.bind(self.registry, 3, 9, interface(9))
        self.seats.remove(1)
        self.assertEqual(len(self.manager.devices), 2)
        new_device = self.manager.devices[-1]
        self.select(None, device=new_device)
        self.assertTrue(self.clipboard.available())

    def test_close_continues_after_release_failure_and_unsubscribes(self):
        offer = self.offer("text/plain")
        self.select(offer)
        self.clipboard.write((("text/plain", b"data"),))
        with patch.object(offer, "destroy", side_effect=ValueError("release")):
            with self.assertRaisesRegex(ValueError, "release"):
                self.clipboard.close()
        self.assertTrue(self.device.destroyed)
        self.assertTrue(self.manager.sources[-1].destroyed)
        self.assertTrue(self.manager.destroyed)
        self.assertEqual(len(self.seats._listeners), 0)
        self.clipboard.close()

    def test_validation_rejects_bad_data_before_source_creation(self):
        for payload in (
            (("x", bytearray()),),
            (("x\0y", b""),),
            (("x", b"1"), ("x", b"2")),
        ):
            with (
                self.subTest(payload=payload),
                self.assertRaises((ValueError, TypeError)),
            ):
                self.clipboard.write(payload)
        self.assertEqual(self.manager.sources, [])
        self.assertEqual(validate_payload((("x", b""),)), (("x", b""),))
