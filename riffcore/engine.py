"""mpv as the audio engine, driven over its JSON IPC.

mpv does decoding, output, A–B looping, and pitch/tempo via rubberband
(``@rb`` filter label), so front ends never touch audio themselves.

mpv must never outlive the app (a crash or kill would otherwise leave it running):
on Linux/macOS it talks over an inherited socketpair (``--input-ipc-client``), and
mpv quits by itself when our end closes; on Windows it is put in a kill-on-close job.
"""

import json
import os
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .paths import IS_WINDOWS, POPEN_KW, find_tool
from .util import VOLUME_MAX


class _SocketTransport:
    """Our end of the socketpair whose other end mpv inherited."""

    def __init__(self, sock):
        self.sock = sock

    def send(self, data):
        self.sock.sendall(data)

    def lines(self):
        yield from self.sock.makefile("rb")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


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


def _kill_on_close_job(proc):
    """Windows: put ``proc`` in a job object that kills it when our handle closes (i.e. when we exit).

    Best effort — returns the job handle (keep it alive) or None if anything fails.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                                                        "OtherOperationCount", "ReadTransferCount",
                                                        "WriteTransferCount", "OtherTransferCount")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        job = k32.CreateJobObjectW(None, None)
        info = ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = (job and k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))  # 9 = extended
              and k32.AssignProcessToJobObject(job, int(proc._handle)))
        return job if ok else None
    except Exception:  # noqa: BLE001 — worst case mpv can outlive a crash, as before
        return None


class Mpv:
    OBSERVED = ("time-pos", "duration", "pause", "eof-reached")

    def __init__(self, on_prop, on_event, dispatch, volume=100, muted=False):
        """``on_prop(name, value)`` and ``on_event(name, msg)`` are delivered via ``dispatch``."""
        self.on_prop, self.on_event, self.dispatch = on_prop, on_event, dispatch
        args = [find_tool("mpv"), "--idle=yes", "--no-video", "--no-terminal", "--no-config", "--keep-open=yes",
                "--hr-seek=yes", "--audio-display=no", f"--volume-max={VOLUME_MAX}",
                f"--volume={volume}", f"--mute={'yes' if muted else 'no'}", "--af=@rb:rubberband"]
        for mpris in ("/usr/lib/mpv-mpris/mpris.so", "/etc/mpv/scripts/mpris.so"):  # media keys on Linux
            if os.path.exists(mpris):
                args.append(f"--script={mpris}")
        # Extra options, e.g. RIFFARCHY_MPV_ARGS=--ao=null for headless tests and CI machines without audio
        args += shlex.split(os.environ.get("RIFFARCHY_MPV_ARGS", ""))
        # mpv's own log: the only way to see why it failed to start (it has no console in a GUI app)
        self.log_path = Path(tempfile.gettempdir()) / f"riffarchy-mpv-{os.getpid()}.log"
        args.append(f"--log-file={self.log_path}")
        quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        self._job = None
        if IS_WINDOWS:
            self.transport = self._start_windows(args, quiet)
        else:
            import socket
            ours, theirs = socket.socketpair()
            args.append(f"--input-ipc-client=fd://{theirs.fileno()}")
            self.proc = subprocess.Popen(args, pass_fds=(theirs.fileno(),), **quiet, **POPEN_KW)
            theirs.close()  # only mpv holds that end now; when ours closes (even on a crash), mpv quits
            self.transport = _SocketTransport(ours)
        self.lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True).start()
        for i, prop in enumerate(self.OBSERVED, 1):
            self.command("observe_property", i, prop)

    def _start_windows(self, args, quiet):
        path = rf"\\.\pipe\riffarchy-{os.getpid()}"
        self.proc = subprocess.Popen(args + [f"--input-ipc-server={path}"], **quiet, **POPEN_KW)
        self._job = _kill_on_close_job(self.proc)
        for _ in range(100):
            try:
                return _PipeTransport(path)
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"mpv exited during startup (code {self.proc.returncode}):\n"
                                       + self.log_tail()) from None
                time.sleep(0.05)
        self.proc.kill()
        raise RuntimeError("could not connect to mpv")

    def log_tail(self, lines=12):
        try:
            text = self.log_path.read_text(errors="replace").strip().splitlines()
        except OSError:
            return "(no mpv log)"
        return "\n".join(text[-lines:]) or "(mpv log is empty)"

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
        self.log_path.unlink(missing_ok=True)
