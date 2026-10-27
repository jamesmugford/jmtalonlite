"""Small compatibility shim for Talon's private five-operation clipboard backend."""

import struct
from typing import Any

from .wayland_backend.clipboard import MimePayload
from .wayland_backend.desktop import WaylandDesktop
from .wayland_backend.errors import CapabilityUnavailable

_FORMATS = {
    "text": (
        "text/plain;charset=utf-8",
        "text/plain;charset=UTF-8",
        "UTF8_STRING",
        "text/plain",
        "STRING",
        "TEXT",
    ),
    "html": ("text/html",),
    "rtf": ("text/rtf", "application/rtf"),
    "urls": ("text/uri-list",),
    "image": ("image/png", "image/jpeg", "image/webp", "image/bmp", "image/tiff"),
    "color": ("application/x-color",),
}


def _text(data: bytes, name: str) -> str:
    return data.decode("latin-1" if name == "STRING" else "utf-8", errors="replace")


def _urls(data: bytes) -> list[str]:
    return [
        line
        for line in _text(data, "text/uri-list").splitlines()
        if line and not line.startswith("#")
    ]


def _image(data: bytes) -> Any:
    from skia import Data, Image

    # Invalid image bytes remain available by MIME name for restoration.
    try:
        return Image.from_encoded(Data.from_bytes(data))
    except (ValueError, RuntimeError):
        return None


def _same_image(left: Any, right: Any) -> bool:
    from skia import Image

    if (left.width, left.height) != (right.width, right.height):
        return False
    # MimeData normalizes image storage. Compare a common pixel layout rather
    # than encodings whose metadata can change without an image edit.
    arguments = (
        0,
        0,
        left.width,
        left.height,
        Image.ColorType.RGBA_8888,
        Image.AlphaType.PREMUL,
    )
    return left.read_pixels(*arguments) == right.read_pixels(*arguments)


def encode_mime(item: Any) -> MimePayload:
    """Keep raw representations intact and encode Talon's typed fields."""
    raw = {name: item.get(name) for name in item.formats if item.get(name) is not None}
    for field, names in _FORMATS.items():
        value = getattr(item, field)
        if value is None:
            continue
        present = [name for name in names if name in raw]
        if field in ("text", "html", "rtf"):
            if present and value == _text(raw[present[0]], present[0]):
                continue
            # Honour an explicitly edited typed field on a previously read item.
            utf8 = value.encode("utf-8")
            for name in present:
                if name == "STRING":
                    try:
                        raw[name] = value.encode("latin-1")
                    except UnicodeEncodeError:
                        del raw[name]  # Retire an alias that cannot represent the edit.
                else:
                    raw[name] = utf8
            raw.setdefault(names[0], utf8)
            if field == "text":
                raw.setdefault("text/plain", utf8)
        elif field == "urls":
            if present and value == _urls(raw[present[0]]):
                continue
            raw[names[0]] = ("\r\n".join(value) + "\r\n").encode("utf-8")
        elif field == "image":
            from skia import EncodedImageFormat

            original = _image(raw[present[0]]) if present else None
            if original is not None and _same_image(value, original):
                continue
            # Keep original encodings for snapshots, but retire stale alternatives
            # when an edited image is published as PNG.
            for name in present:
                del raw[name]
            raw[names[0]] = value.encode(EncodedImageFormat.PNG).data()
        elif field == "color":
            raw[names[0]] = struct.pack("=4H", *(round(v * 65535) for v in value))
    return tuple(raw.items())


def decode_mime(payload: MimePayload, factory: Any) -> Any:
    """Preserve every raw format and populate fields used by Talon's helpers."""
    item = factory()
    raw = dict(payload)
    for name, data in payload:
        item[name] = data
    for field, names in _FORMATS.items():
        name = next((name for name in names if name in raw), None)
        if name is None:
            continue
        data = raw[name]
        if field in ("text", "html", "rtf"):
            setattr(item, field, _text(data, name))
        elif field == "urls":
            item.urls = _urls(data)
        elif field == "color" and len(data) == 8:
            item.color = [v / 65535 for v in struct.unpack("=4H", data)]
        elif field == "image":
            image = _image(data)
            if image is not None:
                item.image = image
    return item


class WaylandClipboard:
    """Replace one backend object, retaining Talon's capture/revert/action code."""

    def __init__(self, desktop: WaylandDesktop, clip_module: Any) -> None:
        self._desktop = desktop
        self._clip = clip_module
        self._original = None
        self._modes: tuple[tuple[str, Any], ...] = ()

    def install(self) -> None:
        """Check the installed Talon contract using only synthetic MIME data."""
        if self._clip._backend is self:
            return
        original = self._clip._backend
        methods = ("supports", "mime", "set_mime", "clear", "serial")
        if not all(callable(getattr(original, name, None)) for name in methods):
            raise RuntimeError("Unsupported Talon clipboard backend interface")
        probe = self._clip.MimeData()
        probe["application/x-jmtalonlite-probe"] = b"probe"
        probe.text = "probe"
        if (
            probe.get("application/x-jmtalonlite-probe") != b"probe"
            or probe.text != "probe"
        ):
            raise RuntimeError("Unsupported Talon MIME data interface")
        self._modes = tuple(
            (mode, self._clip._modes[mode]) for mode in ("main", "select")
        )
        self._original = original
        self._clip._backend = self

    def restore(self) -> None:
        """Restore only the backend still owned by this installation."""
        if self._clip._backend is self:
            self._clip._backend = self._original
        self._original = None

    def _mode(self, kind: Any) -> str:
        for mode, candidate in self._modes:
            if kind == candidate:
                return mode
        raise CapabilityUnavailable("Unsupported Wayland clipboard kind")

    def supports(self, kind: Any) -> bool:
        """Expose only selections actually supported by this seat."""
        return any(
            kind == candidate and self._desktop.clipboard_available(mode)
            for mode, candidate in self._modes
        )

    def mime(self, kind: Any, formats: list[str] | None) -> list[Any]:
        """Translate Talon's format names and return a native MimeData item."""
        # Talon's capture/revert request "common". Preserve the complete offer,
        # including application-specific formats needed to restore rich content.
        requested = (
            None
            if formats is None or "common" in formats
            else tuple(name for fmt in formats for name in _FORMATS.get(fmt, (fmt,)))
        )
        payload = self._desktop.read_clipboard(self._mode(kind), requested)
        return [decode_mime(payload, self._clip.MimeData)] if payload else []

    def set_mime(self, kind: Any, data: list[Any]) -> None:
        """Encode the single MIME item supported by Wayland selections."""
        if len(data) > 1:
            raise ValueError("Wayland selections support one MIME item")
        payload = encode_mime(data[0]) if data else ()
        self._desktop.write_clipboard(payload, self._mode(kind))

    def clear(self, kind: Any) -> None:
        """Clear one selection and await its compositor acknowledgement."""
        self._desktop.write_clipboard((), self._mode(kind))

    def serial(self, kind: Any) -> int:
        """Provide Talon's change promises with selection-event revisions."""
        return self._desktop.clipboard_serial(self._mode(kind))
