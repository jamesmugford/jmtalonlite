import sys
import unittest
from pathlib import Path

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
sys.path.insert(0, str(PLUGINS))
try:
    from wayland_backend.session import is_wayland_session
finally:
    sys.path.remove(str(PLUGINS))


class SessionTests(unittest.TestCase):
    def test_explicit_native_socket_with_xwayland_ui(self):
        self.assertTrue(
            is_wayland_session(
                {"WAYLAND_DISPLAY": "", "JMTALONLITE_WAYLAND_DISPLAY": "wayland-1"}
            )
        )
        self.assertFalse(is_wayland_session({"JMTALONLITE_WAYLAND_DISPLAY": ""}))

    def test_detects_standard_wayland_environment_values(self):
        self.assertTrue(is_wayland_session({"WAYLAND_DISPLAY": "wayland-1"}))
        self.assertTrue(is_wayland_session({"SWAYSOCK": "/run/sway.sock"}))
        self.assertTrue(is_wayland_session({"XDG_SESSION_TYPE": "Wayland"}))
        self.assertFalse(is_wayland_session({"XDG_SESSION_TYPE": "x11"}))
        self.assertFalse(is_wayland_session({}))


if __name__ == "__main__":
    unittest.main()
