"""Small shared helpers and constants."""

import re
import threading
from pathlib import Path

PEAK_RATE = 100  # waveform peaks per second of audio
SPEED_MIN, SPEED_MAX = 25, 250  # percent
SPEED_PRESETS = (50, 60, 70, 75, 80, 90, 100)
VOLUME_MAX = 150  # allow a boost for quiet recordings
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".opus", ".ogg", ".oga", ".flac", ".wav", ".aiff", ".aif",
              ".wma", ".webm", ".mka", ".mp4", ".mkv", ".mov", ".alac"}


def fmt_time(t, precise=False):
    if t is None:
        return "–"
    t = max(0.0, t)
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:05.2f}" if precise else f"{int(m)}:{int(s):02d}"


def clean_stem(path):
    """File name without extension or a trailing yt-dlp style ' [videoid]'."""
    return re.sub(r"\s*\[[\w-]{11}\]$", "", Path(path).stem) or Path(path).stem


def pitch_label(semis, cents):
    parts = []
    if semis:
        parts.append(f"{semis:+d} st")
    if cents:
        parts.append(f"{cents:+d}¢")
    return " ".join(parts)


def pitch_scale(semis, cents):
    return 2 ** ((semis + cents / 100) / 12)


def song_subtitle(s):
    """One-line summary for library lists: artist · length · speed · pitch."""
    bits = [s.get("artist"), fmt_time(s["duration"]) if s.get("duration") else None]
    if s.get("speed", 100) != 100:
        bits.append(f"{s['speed']}%")
    if s.get("semis") or s.get("cents"):
        bits.append(pitch_label(s["semis"], s["cents"]))
    return " · ".join(b for b in bits if b)


def run_async(dispatch, fn, done, *args):
    """Run ``fn(*args)`` on a worker thread, then ``done(result, error)`` via ``dispatch``."""
    def work():
        try:
            result, error = fn(*args), None
        except Exception as e:  # noqa: BLE001 — handed to the caller
            result, error = None, e
        dispatch(done, result, error)

    threading.Thread(target=work, daemon=True).start()
