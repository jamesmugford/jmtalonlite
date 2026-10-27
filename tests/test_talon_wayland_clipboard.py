import struct
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from plugins.wayland_clipboard import WaylandClipboard, decode_mime, encode_mime
finally:
    sys.path.remove(str(ROOT))


class MimeData:
    """Model Talon's distinct raw MIME map and typed properties."""

    text = html = rtf = urls = image = color = None

    def __init__(self):
        self.raw = {}

    @property
    def formats(self):
        typed = [
            key
            for key in ("text", "html", "rtf", "urls", "image", "color")
            if getattr(self, key) is not None
        ]
        return [*self.raw, *typed]

    def get(self, name):
        return self.raw.get(name)

    def __setitem__(self, name, value):
        self.raw[name] = value


class TalonClipboardTests(unittest.TestCase):
    def setUp(self):
        self.original = Mock(spec=["supports", "mime", "set_mime", "clear", "serial"])
        self.clip = types.SimpleNamespace(
            _backend=self.original,
            _modes={"main": [1], "select": [2], "find": [3]},
            MimeData=MimeData,
        )
        self.desktop = Mock()
        self.backend = WaylandClipboard(self.desktop, self.clip)
        self.backend.install()

    def test_common_snapshot_preserves_raw_binary_and_html_exactly(self):
        payload = (
            ("text/plain", b"text"),
            ("text/html", b"<b>text</b>"),
            ("application/custom", b"\x00\xff"),
        )
        self.desktop.read_clipboard.return_value = payload
        items = self.backend.mime([1], ["common"])
        self.desktop.read_clipboard.assert_called_once_with("main", None)
        self.assertEqual(items[0].text, "text")
        self.assertEqual(items[0].html, "<b>text</b>")
        self.backend.set_mime([1], items)
        outgoing, mode = self.desktop.write_clipboard.call_args.args
        self.assertEqual(mode, "main")
        self.assertEqual(dict(outgoing), dict(payload))

    def test_typed_text_is_encoded_and_explicit_edits_are_honored(self):
        item = MimeData()
        item.text = "α\n"
        self.assertEqual(dict(encode_mime(item))["text/plain"], "α\n".encode())
        item = decode_mime((("text/plain", b"old"),), MimeData)
        item.text = "new"
        self.assertEqual(dict(encode_mime(item))["text/plain"], b"new")

    def test_unicode_edit_retires_unrepresentable_legacy_string(self):
        item = decode_mime(
            (("text/plain;charset=utf-8", b"old"), ("STRING", b"old")), MimeData
        )
        item.text = "new α"
        payload = dict(encode_mime(item))
        self.assertEqual(payload["text/plain;charset=utf-8"], "new α".encode())
        self.assertEqual(payload["text/plain"], "new α".encode())
        self.assertNotIn("STRING", payload)

    def test_latin1_edit_keeps_legacy_string_consistent(self):
        original = (("STRING", b"old"),)
        item = decode_mime(original, MimeData)
        self.assertEqual(encode_mime(item), original)
        item.text = "café"
        payload = dict(encode_mime(item))
        self.assertEqual(payload["STRING"], b"caf\xe9")
        self.assertEqual(payload["text/plain"], "café".encode())

    def test_uri_snapshot_preserves_comments_until_urls_are_edited(self):
        original = (("text/uri-list", b"# source\nhttps://example.com/old\n"),)
        item = decode_mime(original, MimeData)
        self.assertEqual(encode_mime(item), original)
        item.urls = ["https://example.com/new", "file:///example"]
        self.assertEqual(
            dict(encode_mime(item))["text/uri-list"],
            b"https://example.com/new\r\nfile:///example\r\n",
        )

    def test_color_snapshot_and_edits_preserve_quantized_components(self):
        original = (("application/x-color", struct.pack("=4H", 1, 32768, 0, 65535)),)
        item = decode_mime(original, MimeData)
        self.assertEqual(encode_mime(item), original)
        item.color = [0.0, 0.0, 1.0, 1.0]
        self.assertEqual(
            dict(encode_mime(item))["application/x-color"],
            struct.pack("=4H", 0, 0, 65535, 65535),
        )

    def test_image_edit_replaces_stale_formats_but_snapshot_keeps_original_bytes(self):
        original_image = Mock()
        original_image.width = original_image.height = 1
        original_image.read_pixels.return_value = b"original pixels"
        original_image.encode.return_value.data.return_value = b"original canonical PNG"
        changed_image = Mock()
        changed_image.width = changed_image.height = 1
        changed_image.read_pixels.return_value = b"changed pixels"
        changed_image.encode.return_value.data.return_value = b"changed PNG"
        skia = types.SimpleNamespace(
            Image=types.SimpleNamespace(
                from_encoded=lambda data: original_image,
                ColorType=types.SimpleNamespace(RGBA_8888=object()),
                AlphaType=types.SimpleNamespace(PREMUL=object()),
            ),
            Data=types.SimpleNamespace(from_bytes=lambda data: data),
            EncodedImageFormat=types.SimpleNamespace(PNG=object()),
        )
        original = (
            ("image/png", b"original PNG with metadata"),
            ("image/jpeg", b"original JPEG"),
            ("application/custom", b"keep"),
        )
        with patch.dict(sys.modules, skia=skia):
            item = decode_mime(original, MimeData)
            self.assertEqual(encode_mime(item), original)
            item.image = changed_image
            self.assertEqual(
                dict(encode_mime(item)),
                {"image/png": b"changed PNG", "application/custom": b"keep"},
            )

    def test_absent_text_and_empty_text_are_distinct(self):
        self.assertIsNone(decode_mime((("custom", b""),), MimeData).text)
        self.assertEqual(decode_mime((("text/plain", b""),), MimeData).text, "")

    def test_kind_values_need_not_be_hashable(self):
        self.backend.clear([2])
        self.desktop.write_clipboard.assert_called_once_with((), "select")
        self.assertFalse(self.backend.supports([3]))

    def test_repeated_install_restore_and_replacement_ownership(self):
        self.backend.install()
        self.backend.restore()
        self.backend.restore()
        self.assertIs(self.clip._backend, self.original)
        self.backend.install()
        replacement = object()
        self.clip._backend = replacement
        self.backend.restore()
        self.assertIs(self.clip._backend, replacement)

    def test_incompatible_talon_backend_is_not_replaced(self):
        self.backend.restore()
        incompatible = object()
        self.clip._backend = incompatible
        with self.assertRaisesRegex(RuntimeError, "interface"):
            self.backend.install()
        self.assertIs(self.clip._backend, incompatible)

    def test_multiple_items_fail_before_any_selection_changes(self):
        with self.assertRaisesRegex(ValueError, "one MIME item"):
            self.backend.set_mime([1], [MimeData(), MimeData()])
        self.desktop.write_clipboard.assert_not_called()
