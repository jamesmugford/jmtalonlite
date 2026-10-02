import hashlib
import json
import os
import subprocess
import sys
import sysconfig
import textwrap
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
sys.path.insert(0, str(PLUGINS))
try:
    from wayland_backend import vendor
finally:
    sys.path.remove(str(PLUGINS))

ROOT = Path(__file__).resolve().parents[1]


class WaylandVendorTests(unittest.TestCase):
    def test_protocol_inputs_match_manifest(self):
        manifest = json.loads((ROOT / "third_party" / "manifest.json").read_text())

        for filename, expected_hash in manifest["protocols"].items():
            with self.subTest(filename=filename):
                payload = (ROOT / "third_party" / "protocols" / filename).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), expected_hash)

    def test_bundle_has_patched_extension_and_generated_protocols(self):
        manifest = json.loads((ROOT / "third_party" / "manifest.json").read_text())
        for soabi, target in manifest["bundle"]["targets"].items():
            with self.subTest(soabi=soabi):
                site = ROOT / ".vendor" / "pywayland" / target["directory"]
                self.check_bundle(site, soabi, manifest)

    def check_bundle(self, site, soabi, manifest):
        extension_path = site / "pywayland" / f"_ffi.{soabi}.so"
        self.assertTrue(extension_path.is_file())
        self.assertEqual(list((site / "pywayland").glob("_ffi*.so")), [extension_path])
        extension = extension_path.read_bytes()
        self.assertIn(b"wl_display_cancel_read", extension)
        self.assertIn(b"$ORIGIN/../pywayland.libs", extension)
        ffi_stub = (site / "pywayland" / "_ffi" / "lib.pyi").read_text()
        self.assertIn("def wl_display_cancel_read", ffi_stub)
        expected_protocols = {
            "wayland.py",
            "virtual_keyboard_unstable_v1.py",
            "wlr_foreign_toplevel_management_unstable_v1.py",
            "wlr_virtual_pointer_unstable_v1.py",
        }
        actual_protocols = {
            path.name for path in (site / "pywayland" / "protocol").glob("*.py")
        }
        self.assertEqual(actual_protocols, expected_protocols)
        for module, expected_hash in manifest["generated_protocols"].items():
            with self.subTest(generated_module=module):
                payload = (site / "pywayland" / "protocol" / module).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), expected_hash)

    def test_bundle_provenance_and_private_libraries(self):
        manifest_path = ROOT / "third_party" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())

        for target in manifest["bundle"]["targets"].values():
            with self.subTest(target=target):
                site = ROOT / ".vendor" / "pywayland" / target["directory"]
                self.check_provenance(site, manifest_path, manifest)

    def check_provenance(self, site, manifest_path, manifest):

        self.assertEqual(
            (site / "VENDOR.json").read_bytes(), manifest_path.read_bytes()
        )
        self.assertFalse(any(site.glob("*.dist-info")))
        self.assertFalse((site / "pywayland" / "ffi_build.py").exists())
        for library in manifest["bundle"]["private_libraries"]:
            with self.subTest(library=library):
                self.assertTrue((site / "pywayland.libs" / library).is_file())

        server = (
            site / "pywayland.libs" / "libwayland-server-2d5f7739.so.0.26.0"
        ).read_bytes()
        self.assertIn(b"GLIBC_2.34", server)

    def test_loader_selects_exact_abi_and_preserves_import_path_priority(self):
        manifest = json.loads((ROOT / "third_party" / "manifest.json").read_text())
        for soabi, target in manifest["bundle"]["targets"].items():
            with (
                self.subTest(soabi=soabi),
                patch.object(vendor.sysconfig, "get_config_var", return_value=soabi),
                patch.object(vendor.platform, "machine", return_value="x86_64"),
                patch.object(vendor.platform, "system", return_value="Linux"),
                patch.object(vendor.platform, "libc_ver", return_value=("glibc", "2.34")),
                patch.object(sys, "path", sys.path.copy()),
                patch.dict(sys.modules),
            ):
                sys.modules.pop("pywayland", None)
                expected = ROOT / ".vendor" / "pywayland" / target["directory"]
                self.assertEqual(vendor.activate(), expected)
                self.assertEqual(vendor.activate(), expected)
                self.assertEqual(sys.path[0], str(expected))
                self.assertEqual(sys.path.count(str(expected)), 1)

    def test_loader_rejects_other_abis_without_changing_import_path(self):
        for soabi in (
            "cpython-313-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "cpython-313t-x86_64-linux-gnu",
            "cpython-312-x86_64-linux-gnu",
        ):
            with (
                self.subTest(soabi=soabi),
                patch.object(vendor.sysconfig, "get_config_var", return_value=soabi),
            ):
                before = sys.path.copy()
                with self.assertRaisesRegex(RuntimeError, "requires CPython"):
                    vendor.activate()
                self.assertEqual(sys.path, before)

    def test_loader_rejects_a_preloaded_different_bundle(self):
        other = types.SimpleNamespace(__file__="/elsewhere/pywayland/__init__.py")
        with (
            patch.object(
                vendor.sysconfig,
                "get_config_var",
                return_value="cpython-314t-x86_64-linux-gnu",
            ),
            patch.object(vendor.platform, "machine", return_value="x86_64"),
            patch.object(vendor.platform, "system", return_value="Linux"),
            patch.object(vendor.platform, "libc_ver", return_value=("glibc", "2.34")),
            patch.dict(sys.modules, {"pywayland": other}),
        ):
            with self.assertRaisesRegex(RuntimeError, "already loaded"):
                vendor.activate()

    def test_native_callback_on_owner_thread_without_a_compositor(self):
        manifest = json.loads((ROOT / "third_party" / "manifest.json").read_text())
        if sysconfig.get_config_var("SOABI") not in manifest["bundle"]["targets"]:
            self.skipTest("Native smoke test requires a supported Talon Python ABI")
        code = textwrap.dedent("""
            import socket
            import sys
            import threading
            from concurrent.futures import ThreadPoolExecutor
            gil_before = getattr(sys, '_is_gil_enabled', lambda: True)()
            from plugins.wayland_backend.bindings import load_wayland_bindings
            bindings = load_wayland_bindings()
            assert hasattr(bindings.lib, 'wl_display_cancel_read')
            from pywayland.server import EventLoop
            def exercise():
                owner = threading.get_ident()
                seen = []
                loop = EventLoop()
                try:
                    reader, writer = socket.socketpair()
                    with reader, writer:
                        def receive(fd, mask, data):
                            seen.append((threading.get_ident(), reader.recv(1), data))
                            return 0
                        source = loop.add_fd(reader.fileno(), receive,
                                             EventLoop.FdMask.WL_EVENT_READABLE, 'marker')
                        writer.sendall(b'x')
                        loop.dispatch(0)
                        source.remove()
                    assert seen == [(owner, b'x', 'marker')], seen
                finally:
                    loop.destroy()
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(exercise).result(timeout=5)
            assert getattr(sys, '_is_gil_enabled', lambda: True)() == gil_before
        """)
        result = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=ROOT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
