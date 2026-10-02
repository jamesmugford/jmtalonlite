import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__ and __package__.startswith("user."):
    from ..plugins.tracking_forwarder.gaze_dispatch import GazeDispatch
else:
    PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
    sys.path.insert(0, str(PLUGINS))
    try:
        from tracking_forwarder.gaze_dispatch import GazeDispatch
    finally:
        sys.path.remove(str(PLUGINS))

if __package__:
    from .talon_fakes import FakeCron
else:
    from talon_fakes import FakeCron


class GazeDispatchTests(unittest.TestCase):
    def test_worker_burst_delivers_latest_sample_once_on_scheduler_thread(self):
        cron = FakeCron()
        seen = []
        sample = [0]
        delivery = GazeDispatch(
            lambda: seen.append((threading.get_ident(), sample[0])), cron
        )
        delivery.start()
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda _: delivery(), range(100)))
        self.assertEqual(seen, [])
        self.assertEqual(len(cron.jobs), 1)
        sample[0] = 123
        cron.jobs[0].callback()
        self.assertEqual(seen, [(threading.get_ident(), 123)])
        delivery()
        self.assertEqual(len(cron.jobs), 2)

    def test_stop_and_restart_invalidate_pending_delivery(self):
        cron = FakeCron()
        seen = []
        delivery = GazeDispatch(lambda: seen.append(True), cron)
        delivery.start()
        delivery()
        stale = cron.jobs[-1]
        delivery.stop()
        delivery.stop()
        self.assertTrue(stale.cancelled)
        delivery()
        self.assertEqual(len(cron.jobs), 1)
        delivery.start()
        delivery()
        stale.callback()
        self.assertEqual(seen, [])
        cron.jobs[-1].callback()
        self.assertEqual(seen, [True])

    def test_stop_while_scheduler_creates_job_cancels_it(self):
        cron = FakeCron()
        original = cron.after
        entered = threading.Event()
        release = threading.Event()

        def after(delay, callback):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Test scheduler was not released")
            return original(delay, callback)

        cron.after = after
        seen = []
        delivery = GazeDispatch(lambda: seen.append(True), cron)
        delivery.start()
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(delivery)
            try:
                self.assertTrue(entered.wait(5))
                delivery.stop()
            finally:
                release.set()
            future.result(timeout=5)
        self.assertTrue(cron.jobs[0].cancelled)
        cron.jobs[0].callback()
        self.assertEqual(seen, [])

    def test_synchronous_delivery_does_not_leave_a_pending_job(self):
        cron = FakeCron()
        original = cron.after

        def after(delay, callback):
            job = original(delay, callback)
            callback()
            return job

        cron.after = after
        seen = []
        delivery = GazeDispatch(lambda: seen.append(True), cron)
        delivery.start()
        delivery()
        delivery()
        self.assertEqual(seen, [True, True])

    def test_scheduler_failure_allows_retry(self):
        cron = FakeCron()
        original = cron.after

        def fail(*_args):
            raise RuntimeError("scheduler unavailable")

        delivery = GazeDispatch(lambda: None, cron)
        delivery.start()
        cron.after = fail
        with self.assertRaisesRegex(RuntimeError, "scheduler unavailable"):
            delivery()
        cron.after = original
        delivery()
        self.assertEqual(len(cron.jobs), 1)

    def test_events_during_delivery_cannot_schedule_an_overlapping_callback(self):
        cron = FakeCron()
        entered = threading.Event()
        release = threading.Event()
        seen = []

        def callback():
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Delivery was not released")
            seen.append(True)

        delivery = GazeDispatch(callback, cron)
        delivery.start()
        delivery()
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(cron.jobs[0].callback)
            try:
                self.assertTrue(entered.wait(5))
                delivery()
                delivery.stop()
                delivery.start()
                delivery()
                self.assertEqual(len(cron.jobs), 1)
            finally:
                release.set()
            future.result(timeout=5)
        self.assertEqual(seen, [True])
        delivery()
        self.assertEqual(len(cron.jobs), 2)
