"""A tiny socket-pair Wayland peer for native clipboard contract tests.

It advertises only a seat and data-control manager. Real libwayland/PyWayland
marshal requests, new-id events, nullable selections and SCM_RIGHTS descriptors;
no connection to the user's compositor or input devices is made.
"""

import array
import os
import socket
import struct
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace


def uint(value):
    return struct.pack("=I", value)


def string(value):
    data = value.encode() + b"\0"
    return uint(len(data)) + data + b"\0" * (-len(data) % 4)


def read_string(data, offset=0):
    length = struct.unpack_from("=I", data, offset)[0]
    value = data[offset + 4 : offset + 4 + length - 1].decode()
    return value, offset + 4 + ((length + 3) & ~3)


class ClipboardPeer:
    def __init__(self, sock):
        self.sock = sock
        self.objects = {1: "display"}
        self.sources = {}
        self.offers = {}
        self.selection = {0: None, 2: None}
        self.next_offer = 0xFF000000
        self.fds = deque()
        self.stalled_fds = []
        self.stall_received = threading.Event()
        self.error = None

    def send(self, target, opcode, payload=b"", fd=None):
        header = struct.pack("=II", target, ((8 + len(payload)) << 16) | opcode)
        ancillary = (
            []
            if fd is None
            else [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))]
        )
        data = header + payload
        count = self.sock.sendmsg([data], ancillary)
        if count < len(data):
            self.sock.sendall(data[count:])

    def retire(self, target):
        self.objects.pop(target, None)
        if target < 0xFF000000:
            self.send(1, 1, uint(target))

    def advertise(self, device, selection_event, source=None, initial=False):
        if source is None and not initial:
            self.send(device, selection_event, uint(0))
            return
        offer = self.next_offer
        self.next_offer += 1
        self.objects[offer] = "offer"
        self.offers[offer] = source
        names = (
            ("text/plain", "application/x-stall") if initial else self.sources[source]
        )
        self.send(device, 0, uint(offer))
        for name in names:
            self.send(offer, 0, string(name))
        self.send(device, selection_event, uint(offer))

    def request(self, target, opcode, data):
        kind = self.objects[target]
        first = struct.unpack_from("=I", data)[0] if data else None
        if kind == "display":
            if opcode == 0:
                self.send(first, 0, uint(1))
                self.send(1, 1, uint(first))
            else:
                self.objects[first] = "registry"
                self.send(first, 0, uint(1) + string("wl_seat") + uint(9))
                self.send(
                    first, 0, uint(2) + string("ext_data_control_manager_v1") + uint(1)
                )
        elif kind == "registry":
            name, offset = read_string(data, 4)
            _version, object_id = struct.unpack_from("=II", data, offset)
            self.objects[object_id] = "seat" if name == "wl_seat" else "manager"
            if name == "wl_seat":
                self.send(object_id, 0, uint(0))
                self.send(object_id, 1, string("seat0"))
        elif kind == "seat":
            assert opcode == 3, opcode  # release; input is never requested
            self.retire(target)
        elif kind == "manager":
            if opcode == 0:
                self.objects[first] = "source"
                self.sources[first] = []
            elif opcode == 1:
                self.objects[first] = "device"
                self.advertise(first, 1, initial=True)
                self.advertise(first, 3)
            else:
                self.retire(target)
        elif kind == "device":
            if opcode == 1:
                self.retire(target)
            else:
                previous = self.selection[opcode]
                if previous is not None and previous in self.objects:
                    self.send(previous, 1)
                self.selection[opcode] = first or None
                # Exercise compositors which omit an echo for our own clear.
                if first:
                    self.advertise(target, 1 if opcode == 0 else 3, first)
        elif kind == "source":
            if opcode == 0:
                name, _ = read_string(data)
                self.sources[target].append(name)
            else:
                self.retire(target)
        elif kind == "offer":
            if opcode == 1:
                self.retire(target)
                return
            name, _ = read_string(data)
            fd = self.fds.popleft()
            source = self.offers[target]
            if source is not None:
                self.send(source, 0, string(name), fd)
                os.close(fd)
            elif name == "application/x-stall":
                self.stalled_fds.append(fd)
                self.stall_received.set()
            else:
                os.write(fd, b"initial clipboard")
                os.close(fd)

    def run(self):
        pending = bytearray()
        try:
            while True:
                data, ancillary, _flags, _address = self.sock.recvmsg(
                    65536, socket.CMSG_SPACE(512)
                )
                if not data:
                    break
                for level, kind, value in ancillary:
                    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                        fds = array.array("i")
                        fds.frombytes(value)
                        self.fds.extend(fds)
                pending.extend(data)
                while len(pending) >= 8:
                    target, header = struct.unpack_from("=II", pending)
                    size, opcode = header >> 16, header & 0xFFFF
                    if len(pending) < size:
                        break
                    payload = bytes(pending[8:size])
                    del pending[:size]
                    self.request(target, opcode, payload)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client flushed destructors and disconnected without another read.
        except Exception as exc:
            self.error = exc
        finally:
            for fd in (*self.fds, *self.stalled_fds):
                os.close(fd)
            self.sock.close()


def exercise():
    from plugins.wayland_backend.bindings import load_wayland_bindings
    from plugins.wayland_backend.desktop import WaylandDesktop

    bindings = load_wayland_bindings()
    client, server = socket.socketpair()
    peer = ClipboardPeer(server)
    worker = threading.Thread(target=peer.run, daemon=True)
    worker.start()
    fd = client.detach()
    test_bindings = replace(bindings, Display=lambda _name: bindings.Display(fd))
    desktop = WaylandDesktop(load_bindings=lambda: test_bindings)
    try:
        desktop.start()
        # Include child-object initial events even if discovery finished first.
        desktop._connection.synchronize(lambda: None)
        assert desktop.clipboard_available()
        assert desktop.clipboard_available("select")
        assert desktop.read_clipboard(formats=("text/plain",)) == (
            ("text/plain", b"initial clipboard"),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                desktop.read_clipboard, "main", ("application/x-stall",), timeout=0.1
            )
            assert peer.stall_received.wait(2)
            assert desktop._connection.execute(lambda: "responsive") == "responsive"
            try:
                future.result(2)
            except TimeoutError:
                pass
            else:
                raise AssertionError("Stalled transfer did not time out")
        assert desktop.status().error is None
        payload = (
            ("text/plain;charset=utf-8", "large α\n".encode() * 65536),
            ("text/html", b"<b>rich</b>"),
            ("application/x-binary", bytes(range(256))),
        )
        desktop.write_clipboard(payload)
        assert dict(desktop.read_clipboard()) == dict(payload)
        serial = desktop.clipboard_serial()
        desktop.write_clipboard(payload)
        assert desktop.clipboard_serial() > serial
        desktop.write_clipboard((("text/plain", b"primary"),), "select")
        assert desktop.read_clipboard("select") == (("text/plain", b"primary"),)
        assert dict(desktop.read_clipboard()) == dict(payload)
        desktop.write_clipboard(())
        serial = desktop.clipboard_serial()
        assert desktop.read_clipboard() == ()
        desktop._connection.synchronize(lambda: None)
        assert desktop.clipboard_serial() == serial  # No delayed clear echo.
        desktop.write_clipboard((("text/plain", b""),))
        assert desktop.read_clipboard() == (("text/plain", b""),)
        assert desktop.status().error is None
    finally:
        desktop.stop()
        worker.join(2)
    assert not worker.is_alive()
    if peer.error is not None:
        raise peer.error
    print("Native clipboard wire contract passed")


if __name__ == "__main__":
    exercise()
