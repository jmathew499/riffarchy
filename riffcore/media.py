"""ffmpeg/ffprobe helpers: waveform peaks, metadata, file scanning and export.

All functions block; run them off the UI thread (see ``util.run_async``).
"""

import array
import json
import os
import re
import subprocess
from pathlib import Path

from .paths import POPEN_KW, find_tool
from .util import AUDIO_EXTS, PEAK_RATE, pitch_label, pitch_scale


def compute_peaks(path):
    """Decode to 8 kHz mono and return one uint8 peak per 1/PEAK_RATE s."""
    rate, binsz = 8000, 8000 // PEAK_RATE
    proc = subprocess.Popen([find_tool("ffmpeg"), "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "1",
                             "-ar", str(rate), "-f", "s16le", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **POPEN_KW)
    peaks, rest = array.array("H"), b""
    while True:
        chunk = proc.stdout.read(binsz * 2 * 2000)
        if not chunk:
            break
        chunk = rest + chunk
        usable = len(chunk) - len(chunk) % (binsz * 2)
        rest = chunk[usable:]
        samples = array.array("h", chunk[:usable])
        for i in range(0, len(samples), binsz):
            seg = samples[i:i + binsz]
            peaks.append(max(max(seg), -min(seg)))
    proc.wait()
    if not peaks:
        return None
    top = max(peaks) or 1
    return bytes(min(255, 255 * v // top) for v in peaks)


def load_peaks(song, peaks_dir):
    """Cached peaks for a song, computing (and caching) them on first use."""
    cache = Path(peaks_dir) / f"{song['id']}.bin"
    if cache.exists():
        return cache.read_bytes()
    peaks = compute_peaks(song["path"])
    if peaks:
        cache.write_bytes(peaks)
    return peaks


def probe(path):
    try:
        out = subprocess.run([find_tool("ffprobe"), "-v", "error", "-show_entries",
                              "format=duration:format_tags=title,artist", "-of", "json", path],
                             capture_output=True, text=True, timeout=15, **POPEN_KW).stdout
        fmt = json.loads(out).get("format", {})
        tags = {k.lower(): v for k, v in fmt.get("tags", {}).items()}
        return {"title": tags.get("title"), "artist": tags.get("artist"),
                "duration": float(fmt["duration"]) if fmt.get("duration") else None}
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return {}


def scan_files(paths, find):
    """Inspect dropped/opened files. ``find(path)`` returns an existing song or None.

    Returns existing song dicts as-is and ``{"path", "title", "artist", "duration"}``
    for new audio; files that aren't audio are skipped. Feed to ``Library.add_scanned``.
    """
    items = []
    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        existing = find(p)
        if existing:
            items.append(existing)
            continue
        meta = probe(p)
        if Path(p).suffix.lower() not in AUDIO_EXTS and not meta.get("duration"):
            continue
        items.append(meta | {"path": p})
    return items


def export_name(song, region=None, ext=".mp3"):
    """Suggested export file name, e.g. ``Song (75%, +2 st, loop).mp3``."""
    tag = ", ".join(x for x in (f"{song['speed']}%" if song["speed"] != 100 else "",
                                pitch_label(song["semis"], song["cents"]),
                                "loop" if region else "") if x)
    return re.sub(r'[/\\:*?"<>|]', "_", song["title"]) + (f" ({tag})" if tag else "") + ext


def export_audio(song, out, region=None):
    """Render with speed and pitch applied. ``region`` is ``(a, b)`` seconds or None.

    Output format follows the file extension. Raises ``RuntimeError`` on failure.
    """
    af = (f"rubberband=tempo={song['speed'] / 100:.4f}"
          f":pitch={pitch_scale(song['semis'], song['cents']):.6f}:pitchq=quality")
    cmd = [find_tool("ffmpeg"), "-y", "-v", "error"]
    if region:  # trim on the input side so the cut happens before time-stretching
        cmd += ["-ss", f"{region[0]:.3f}", "-to", f"{region[1]:.3f}"]
    cmd += ["-i", song["path"], "-map", "0:a:0", "-af", af]
    if str(out).lower().endswith(".mp3"):
        cmd += ["-c:a", "libmp3lame", "-q:a", "2"]
    cmd.append(str(out))
    p = subprocess.run(cmd, capture_output=True, text=True, **POPEN_KW)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[-200:] or "ffmpeg failed")
    return out
