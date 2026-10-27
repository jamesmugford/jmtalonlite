"""Run in Talon's REPL to check the private clipboard contract with synthetic data.

from pathlib import Path
path = '/path/to/repo/tools/probe_talon_clipboard.py'
exec(compile(Path(path).read_text(), path, 'exec'),
     {'__file__': path, '__name__': '__main__'})

Only this REPL thread is redirected. No real clipboard contents are read or set.
"""

import importlib
import sys
import threading
import types
from pathlib import Path

from talon import clip, registry


def probe(root):
    package_name = "_jmtalonlite_clipboard_probe"
    package = types.ModuleType(package_name)
    package.__path__ = [str(root / "plugins")]
    sys.modules[package_name] = package
    original = clip._backend
    probe_thread = threading.current_thread()

    class MemoryDesktop:
        def __init__(self):
            self.data = {"main": (), "select": ()}
            self.revisions = {"main": 0, "select": 0}
            self.fail_reads = False

        def clipboard_available(self, mode):
            return mode in self.data

        def clipboard_serial(self, mode):
            return self.revisions[mode]

        def read_clipboard(self, mode, formats):
            if self.fail_reads:
                raise TimeoutError("Synthetic transfer timeout")
            return tuple(
                (name, data)
                for name, data in self.data[mode]
                if formats is None or name in formats
            )

        def write_clipboard(self, payload, mode):
            self.data[mode] = payload
            self.revisions[mode] += 1

    memory = MemoryDesktop()
    proxy = None
    try:
        module = importlib.import_module(package_name + ".wayland_clipboard")
        sandbox = types.SimpleNamespace(
            _backend=original,
            _modes=clip._modes,
            MimeData=clip.MimeData,
        )
        backend = module.WaylandClipboard(memory, sandbox)
        backend.install()

        class ThreadScopedBackend:
            def __getattr__(self, name):
                def invoke(*args, **kwargs):
                    target = (
                        backend
                        if threading.current_thread() is probe_thread
                        else original
                    )
                    return getattr(target, name)(*args, **kwargs)

                return invoke

        proxy = ThreadScopedBackend()
        clip._backend = proxy
        clip.set_text("synthetic α\nsecond line")
        assert clip.text() == "synthetic α\nsecond line"
        with clip.revert():
            clip.set_text("temporary")
        assert clip.text() == "synthetic α\nsecond line"
        with clip.capture(0.02) as captured:
            clip.set_text("captured")
        assert captured.text() == "captured"
        assert clip.text() == "synthetic α\nsecond line"
        with clip.capture(0.02) as captured:
            clip.set_text("synthetic α\nsecond line")
        assert captured.text() == "synthetic α\nsecond line"
        with clip.capture(0.001) as captured:
            pass
        try:
            captured.text()
        except clip.NoChange:
            pass
        else:
            raise AssertionError("Capture did not time out")
        clip.set_text("primary", mode="select")
        assert clip.text(mode="select") == "primary"
        assert clip.text() == "synthetic α\nsecond line"
        assert clip.has_mode("main") and clip.has_mode("select")
        assert not clip.has_mode("find")

        payload = (
            ("text/plain;charset=utf-8", "rich α".encode()),
            ("text/html", b"<b>rich</b>"),
            ("application/x-synthetic", b"\0\xff\x01"),
        )
        memory.write_clipboard(payload, "main")
        with clip.revert():
            clip.set_text("temporary")
        assert dict(memory.data["main"]) == dict(payload)
        assert clip.mime().html == "<b>rich</b>"
        assert clip.mime().get("application/x-synthetic") == b"\0\xff\x01"

        typed = clip.MimeData()
        typed.text = "typed"
        typed.html = "<i>typed</i>"
        typed.rtf = "{typed}"
        typed.urls = ["https://example.com/"]
        typed.color = [1.0, 0.5, 0.0, 1.0]
        clip.set_mime(typed)
        restored = clip.mime()
        assert restored.text == typed.text and restored.html == typed.html
        assert restored.rtf == typed.rtf and restored.urls == typed.urls
        assert abs(restored.color[1] - 0.5) < 0.0001
        restored.urls = ["https://example.com/edited"]
        restored.color = [0.0, 0.0, 1.0, 1.0]
        clip.set_mime(restored)
        edited = clip.mime()
        assert edited.urls == restored.urls and edited.color == restored.color

        memory.write_clipboard(
            (("text/plain;charset=utf-8", b"old"), ("STRING", b"old")), "main"
        )
        edited = clip.mime()
        edited.text = "Unicode edit α"
        clip.set_mime(edited)
        assert clip.text() == edited.text
        assert "STRING" not in dict(memory.data["main"])

        from skia import EncodedImageFormat, Image

        image = Image.from_pixels(bytes([255, 0, 0, 255]), 4, 1, 1)
        clip.set_image(image)
        assert clip.image() is not None
        original_png = dict(memory.data["main"])["image/png"]
        with clip.revert():
            clip.set_text("temporary")
        assert dict(memory.data["main"])["image/png"] == original_png

        jpeg = image.encode(EncodedImageFormat.JPEG).data()
        memory.write_clipboard(
            (("image/png", original_png), ("image/jpeg", jpeg)), "main"
        )
        original_images = memory.data["main"]
        with clip.revert():
            clip.set_text("temporary")
        assert dict(memory.data["main"]) == dict(original_images)
        edited = clip.mime()
        blue = Image.from_pixels(bytes([0, 0, 255, 255]), 4, 1, 1)
        edited.image = blue
        clip.set_mime(edited)
        assert set(dict(memory.data["main"])) == {"image/png"}
        assert clip.image().read_pixels(0, 0, 1, 1) == blue.read_pixels(0, 0, 1, 1)

        for encoded_image in (jpeg, b"invalid image bytes"):
            original_images = (("image/jpeg", encoded_image),)
            memory.write_clipboard(original_images, "main")
            with clip.revert():
                clip.set_text("temporary")
            assert dict(memory.data["main"]) == dict(original_images)
            edited = clip.mime()
            edited.image = blue
            clip.set_mime(edited)
            assert set(dict(memory.data["main"])) == {"image/png"}
            assert clip.image().read_pixels(0, 0, 1, 1) == blue.read_pixels(0, 0, 1, 1)

        before_failure = memory.data["main"]
        memory.fail_reads = True
        try:
            with clip.revert():
                raise AssertionError("Failed snapshot allowed clipboard modification")
        except TimeoutError:
            pass
        finally:
            memory.fail_reads = False
        assert memory.data["main"] == before_failure
        clip.set_mime(None)
        assert clip.text() is None

        registry.actions["clip.set_text"][0].func(None, "action text")
        assert registry.actions["clip.text"][0].func(None) == "action text"
        registry.actions["clip.clear"][0].func(None)
        assert clip.text() is None
        print(
            "PASS: text, Unicode, capture, timeout, identical copy, revert, primary, "
            "modes, raw MIME restoration, typed rich data and edits, image formats, "
            "failed snapshots and core actions"
        )
    finally:
        if proxy is not None and clip._backend is proxy:
            clip._backend = original
        for name in tuple(sys.modules):
            if name == package_name or name.startswith(package_name + "."):
                del sys.modules[name]
        print("Original clipboard backend restored:", clip._backend is original)


if __name__ == "__main__":
    probe(Path(__file__).resolve().parents[1])
