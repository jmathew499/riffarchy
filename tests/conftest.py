"""Shared fixtures. Tests use the real helper tools (system copies, or bundled ones via RIFFARCHY_BIN_DIR)."""

import array
import os
import queue
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("RIFFARCHY_MPV_ARGS", "--ao=null")  # no sound card needed (CI) and no noise locally

from riffcore import Library, paths  # noqa: E402
from riffcore.paths import POPEN_KW  # noqa: E402

needs_tools = pytest.mark.skipif(bool(paths.missing_tools()), reason=f"missing tools: {paths.missing_tools()}")


class Pump:
    """A ``dispatch`` for riffcore that queues calls; ``run()`` executes them like a UI main loop would."""

    def __init__(self):
        self.q = queue.Queue()

    def __call__(self, fn, *args):
        self.q.put((fn, args))

    def run(self, seconds, until=None):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if until and until():
                return True
            try:
                fn, args = self.q.get(timeout=0.02)
            except queue.Empty:
                continue
            fn(*args)
        return bool(until and until())


@pytest.fixture
def pump():
    return Pump()


@pytest.fixture
def lib(tmp_path):
    return Library(data_dir=tmp_path / "data", music_dir=tmp_path / "music")


def make_tone(path, freq=440, seconds=6):
    subprocess.run([paths.find_tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi",
                    "-i", f"sine=frequency={freq}:duration={seconds}:sample_rate=48000", "-ac", "2", str(path)],
                   check=True, **POPEN_KW)
    return path


@pytest.fixture
def tone(tmp_path):
    return make_tone(tmp_path / "tone.wav")


def decode_mono(path):
    raw = subprocess.run([paths.find_tool("ffmpeg"), "-v", "error", "-i", str(path), "-ac", "1", "-ar", "48000",
                          "-f", "s16le", "-"], capture_output=True, check=True, **POPEN_KW).stdout
    return array.array("h", raw)


def tone_frequency(path):
    """Frequency of a pure tone from its zero crossings (edges trimmed)."""
    a = decode_mono(path)[24000:-24000]
    crossings = sum(1 for i in range(1, len(a)) if (a[i - 1] < 0) != (a[i] < 0))
    return crossings / 2 / (len(a) / 48000)


def media_duration(path):
    out = subprocess.run([paths.find_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True, **POPEN_KW)
    return float(out.stdout.strip())


def pid_alive(pid):
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
