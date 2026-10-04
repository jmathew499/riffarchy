"""riffcore — the UI-independent heart of Riffarchy.

Everything here is plain Python (no GTK/Qt imports) so any front end can use it:

- ``paths``     per-OS data/music/runtime directories and bundled-tool lookup
- ``library``   the song library (``library.json``) and per-song settings
- ``engine``    mpv control over JSON IPC (Unix socket or Windows named pipe)
- ``media``     waveform peaks, metadata probing, file scanning, export
- ``youtube``   yt-dlp search and download
- ``session``   playback/loop/trainer/volume/sections logic, plus downloads
- ``waveview``  waveform zoom, pan, A/B handle dragging and column maths

Threading contract: front ends call into ``Session``/``Downloader`` from their UI
thread and pass a ``dispatch(fn, *args)`` that runs ``fn`` on that thread later
(``GLib.idle_add`` for GTK, a queued signal for Qt). All callbacks back into the
front end are delivered through ``dispatch``.
"""

from .library import Library
from .session import Downloader, Session
from .util import (AUDIO_EXTS, PEAK_RATE, SPEED_MAX, SPEED_MIN, SPEED_PRESETS, VOLUME_MAX, clean_stem,
                   fmt_time, pitch_label, pitch_scale, run_async, song_subtitle)
from .waveview import WaveView

APP_NAME = "Riffarchy"
VERSION = "1.0.0"

__all__ = [
    "APP_NAME", "VERSION", "Library", "Session", "Downloader", "WaveView",
    "AUDIO_EXTS", "PEAK_RATE", "SPEED_MIN", "SPEED_MAX", "SPEED_PRESETS", "VOLUME_MAX",
    "clean_stem", "fmt_time", "pitch_label", "pitch_scale", "run_async", "song_subtitle",
]
