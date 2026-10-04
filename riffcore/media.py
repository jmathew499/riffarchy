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

    Uses mpv's encoder with the same rubberband filter as playback, so exports sound
    like what you practised and no rubberband-enabled ffmpeg build is needed.
    Output format follows the file extension. Raises ``RuntimeError`` on failure.
    """
    cmd = [find_tool("mpv"), "--no-config", "--no-terminal", "--no-video", "--msg-level=all=error",
           f"--speed={song['speed'] / 100:.4f}",
           f"--af=rubberband=pitch-scale={pitch_scale(song['semis'], song['cents']):.6f}:pitch=quality"]
    if region:
        cmd += [f"--start={region[0]:.3f}", f"--end={region[1]:.3f}"]
    if str(out).lower().endswith(".mp3"):
        cmd += ["--oac=libmp3lame", "--oacopts=b=192k"]  # mpv ignores "q"; 192k ≈ ffmpeg -q:a 2
    cmd += [f"--o={out}", song["path"]]
    p = subprocess.run(cmd, capture_output=True, text=True, **POPEN_KW)
    if p.returncode != 0 or not Path(out).is_file() or Path(out).stat().st_size == 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[-200:] or "mpv could not export this file")
    return out
