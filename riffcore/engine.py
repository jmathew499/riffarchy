"""mpv as the audio engine, driven over its JSON IPC.

mpv does decoding, output, A–B looping, and pitch/tempo via rubberband
(``@rb`` filter label), so front ends never touch audio themselves.
"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from .paths import IS_WINDOWS, POPEN_KW, find_tool, runtime_dir
from .util import VOLUME_MAX


class _UnixTransport:
    def __init__(self, path):
        import socket
        self.path = path
        self.sock = socket.socket(socket.AF_UNIX)
        try:
            self.sock.connect(path)
        except OSError:
            self.sock.close()
            raise

    def send(self, data):
        self.sock.sendall(data)

    def lines(self):
        yield from self.sock.makefile("rb")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
        Path(self.path).unlink(missing_ok=True)


class _PipeTransport:
    """Windows named pipe.

    A synchronous pipe handle serialises reads and writes, so a reader blocked in
    ReadFile would stall every command. Instead, peek for available bytes and only
    read what is already there (polling every 10 ms).
    """

    def __init__(self, path):
        import msvcrt
        self.f = open(path, "r+b", buffering=0)  # noqa: SIM115 — lives as long as the engine
        self.handle = msvcrt.get_osfhandle(self.f.fileno())
        self.lock = threading.Lock()
        self.closed = False

    def send(self, data):
        with self.lock:
            self.f.write(data)

    def lines(self):
        import _winapi
        buf = b""
        while not self.closed:
            try:
                with self.lock:
                    avail = _winapi.PeekNamedPipe(self.handle, 0)[0]
                    chunk = self.f.read(avail) if avail else b""
            except OSError:
                return
            if not chunk:
                time.sleep(0.01)
                continue
            *complete, buf = (buf + chunk).split(b"\n")
            yield from complete

    def close(self):
        self.closed = True
        try:
            self.f.close()
        except OSError:
            pass


class Mpv:
    OBSERVED = ("time-pos", "duration", "pause", "eof-reached")

    def __init__(self, on_prop, on_event, dispatch, volume=100, muted=False):
        """``on_prop(name, value)`` and ``on_event(name, msg)`` are delivered via ``dispatch``."""
        self.on_prop, self.on_event, self.dispatch = on_prop, on_event, dispatch
        name = f"riffarchy-{os.getpid()}"
        self.ipc_path = rf"\\.\pipe\{name}" if IS_WINDOWS else os.path.join(runtime_dir(), f"{name}.sock")
        args = [find_tool("mpv"), "--idle=yes", "--no-video", "--no-terminal", "--no-config", "--keep-open=yes",
                "--hr-seek=yes", "--audio-display=no", f"--volume-max={VOLUME_MAX}",
                f"--volume={volume}", f"--mute={'yes' if muted else 'no'}",
                f"--input-ipc-server={self.ipc_path}", "--af=@rb:rubberband"]
        for mpris in ("/usr/lib/mpv-mpris/mpris.so", "/etc/mpv/scripts/mpris.so"):  # media keys on Linux
            if os.path.exists(mpris):
                args.append(f"--script={mpris}")
        self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, **POPEN_KW)
        transport_cls = _PipeTransport if IS_WINDOWS else _UnixTransport
        for _ in range(100):
            try:
                self.transport = transport_cls(self.ipc_path)
                break
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError("mpv exited during startup") from None
                time.sleep(0.05)
        else:
            self.proc.kill()
            raise RuntimeError("could not connect to mpv")
        self.lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True).start()
        for i, prop in enumerate(self.OBSERVED, 1):
            self.command("observe_property", i, prop)

    def command(self, *args):
        msg = (json.dumps({"command": list(args)}) + "\n").encode()
        with self.lock:
            try:
                self.transport.send(msg)
            except OSError:
                pass

    def set(self, name, value):
        self.command("set_property", name, value)

    def _reader(self):
        for line in self.transport.lines():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "event" in msg:
                self.dispatch(self._handle, msg)

    def _handle(self, msg):
        if msg["event"] == "property-change":
            self.on_prop(msg["name"], msg.get("data"))
        else:
            self.on_event(msg["event"], msg)

    def quit(self):
        self.command("quit")
        try:
            self.proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.transport.close()
