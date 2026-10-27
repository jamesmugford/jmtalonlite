import json
import os
import subprocess
import sys
import sysconfig
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NativeClipboardTests(unittest.TestCase):
    def test_socket_pair_clipboard_without_user_compositor(self):
        manifest = json.loads((ROOT / "third_party/manifest.json").read_text())
        if sysconfig.get_config_var("SOABI") not in manifest["bundle"]["targets"]:
            self.skipTest("Native clipboard test requires a supported Talon Python ABI")
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                "import runpy; "
                "runpy.run_path('tests/wayland_clipboard_peer.py', run_name='__main__')",
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Native clipboard wire contract passed", result.stdout)
