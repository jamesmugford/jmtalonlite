import sys
import unittest
from pathlib import Path

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
sys.path.insert(0, str(PLUGINS))
try:
    from wayland_backend.outputs import OutputRegistry
    from wayland_backend.seats import SeatRegistry
    from wayland_backend.windows import ForeignToplevels
finally:
    sys.path.remove(str(PLUGINS))

if __package__:
    from .wayland_fakes import ImmediateConnection
else:
    from wayland_fakes import ImmediateConnection


class SubscriptionTests(unittest.TestCase):
    def test_duplicate_callbacks_have_independent_idempotent_handles(self):
        for adapter_type in (SeatRegistry, OutputRegistry, ForeignToplevels):
            with self.subTest(adapter=adapter_type.__name__):
                adapter = adapter_type(ImmediateConnection())
                seen = []

                def callback(*args):
                    seen.append(args)

                if isinstance(adapter, ForeignToplevels):
                    subscribe = adapter.on_active_changed

                    def notify():
                        adapter._notify(None)
                else:
                    subscribe = adapter.subscribe
                    notify = adapter._notify
                first = subscribe(callback)
                second = subscribe(callback)
                first()
                first()
                notify()
                self.assertEqual(len(seen), 1)
                second()
                second()
                notify()
                self.assertEqual(len(seen), 1)
