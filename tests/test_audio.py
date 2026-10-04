"""Real playback and export through mpv/ffmpeg (bundled or system)."""

import subprocess
import sys
import textwrap
import time

import pytest

from conftest import make_tone, media_duration, needs_tools, pid_alive, tone_frequency
from riffcore import PEAK_RATE, Session, media, paths
from riffcore.paths import POPEN_KW

pytestmark = needs_tools


def test_mpv_has_rubberband():
    out = subprocess.run([paths.find_tool("mpv"), "--no-config", "--af=help"], capture_output=True, text=True,
                         **POPEN_KW).stdout
    assert "rubberband" in out


def test_peaks_and_probe(tone):
    peaks = media.compute_peaks(str(tone))
    assert abs(len(peaks) - 6 * PEAK_RATE) <= 2 and max(peaks) == 255
    assert abs(media.probe(str(tone))["duration"] - 6.0) < 0.05


@pytest.mark.parametrize("semis,speed,want_hz", [(12, 100, 880), (0, 50, 440), (-12, 100, 220)])
def test_export_pitch_and_length(tmp_path, tone, semis, speed, want_hz):
    song = {"path": str(tone), "speed": speed, "semis": semis, "cents": 0, "title": "t"}
    out = media.export_audio(song, tmp_path / "out.wav", (1.0, 5.0))
    assert abs(tone_frequency(out) - want_hz) < want_hz * 0.02
    assert abs(media_duration(out) - 4.0 / (speed / 100)) < 0.3


def test_export_mp3_bitrate(tmp_path, tone):
    song = {"path": str(tone), "speed": 100, "semis": 0, "cents": 0, "title": "t"}
    out = media.export_audio(song, tmp_path / "out.mp3")
    probe = subprocess.run([paths.find_tool("ffprobe"), "-v", "error", "-show_entries", "format=bit_rate",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True, **POPEN_KW)
    assert int(probe.stdout.strip()) > 180_000


def test_session_playback_loop_and_trainer(lib, pump, tmp_path):
    tone = make_tone(tmp_path / "long.wav", seconds=20)
    messages = []
    s = Session(lib, pump, message=lambda t, timeout=4: messages.append(t))
    try:
        song = lib.add(path=str(tone), title="Tone")
        assert s.load(song)
        assert pump.run(10, until=lambda: s.peaks is not None and s.duration > 0)

        s.set_loop(2.0, 3.0, on=True)
        s.seek(0)
        s.mark("b")  # playhead sits on A after the loop jump: must not wreck the loop
        assert (s.loop["a"], s.loop["b"]) == (2.0, 3.0) and "before A" in messages[-1]

        s.set_trainer_enabled(True)
        s.set_trainer(inc=10, every=1, target=100)
        s.set_speed(70)
        s.toggle_play()
        assert pump.run(10, until=lambda: s.playing)
        assert pump.run(15, until=lambda: s.loop_count >= 2), "loop never wrapped"
        assert s.speed >= 80, f"trainer didn't speed up (speed={s.speed})"
        p = s.current_pos()
        assert 1.9 <= p <= 3.1, f"playhead {p} left the loop"
    finally:
        s.close()


def test_mpv_quits_when_app_is_killed(tmp_path):
    """A crashed/killed Riffarchy must not leave mpv running (socketpair on Unix, job object on Windows)."""
    child = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(paths.Path(__file__).resolve().parent.parent)!r})
        from riffcore.engine import Mpv
        m = Mpv(lambda *a: None, lambda *a: None, lambda fn, *a: None)
        print(m.proc.pid, flush=True)
        time.sleep(60)
    """)
    proc = subprocess.Popen([sys.executable, "-c", child], stdout=subprocess.PIPE, text=True, **POPEN_KW)
    mpv_pid = int(proc.stdout.readline())
    assert pid_alive(mpv_pid)
    proc.kill()  # SIGKILL / TerminateProcess: no cleanup code runs
    proc.wait()
    deadline = time.monotonic() + 5
    while pid_alive(mpv_pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not pid_alive(mpv_pid), "mpv outlived the app"
