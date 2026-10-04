"""YouTube search and download through yt-dlp. All functions block."""

import json
import re
import subprocess
import urllib.request
from pathlib import Path

from .paths import POPEN_KW, bundled_tool, find_tool

# Prefer plain HTTPS audio: YouTube's HLS audio formats frequently 403.
YT_FORMATS = (
    "bestaudio[protocol=https][format_id!*=drc]/bestaudio[protocol=https]/bestaudio/best",
    "18/best",
)


class YouTubeError(Exception):
    pass


def is_url(text):
    return bool(re.match(r"^(https?://|www\.|youtu)", text.strip()))


def search(query, limit=20):
    """Search terms or a video/playlist URL → list of result dicts.

    Each result has ``id, title, channel, duration, url, thumb_url``.
    """
    q = query.strip()
    target = q if is_url(q) else f"ytsearch{limit}:{q}"
    try:
        p = subprocess.run([find_tool("yt-dlp"), "-J", "--flat-playlist", "--no-warnings", target],
                           capture_output=True, text=True, timeout=90, **POPEN_KW)
    except (OSError, subprocess.SubprocessError) as e:
        raise YouTubeError(str(e)) from e
    if p.returncode != 0:
        raise YouTubeError((p.stderr.strip().splitlines() or ["yt-dlp failed"])[-1].removeprefix("ERROR: "))
    info = json.loads(p.stdout)
    results = []
    for e in info.get("entries") or [info]:
        if not e:
            continue
        vid = e.get("id")
        thumbs = e.get("thumbnails") or []
        results.append({
            "id": vid,
            "title": e.get("title"),
            "channel": e.get("channel") or e.get("uploader"),
            "duration": e.get("duration"),
            "url": e.get("webpage_url") or e.get("url") or f"https://www.youtube.com/watch?v={vid}",
            "thumb_url": (thumbs[0] if thumbs else {}).get("url")
                         or (vid and f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"),
        })
    return results


def fetch_bytes(url, timeout=10):
    """Small HTTP GET, e.g. for result thumbnails."""
    return urllib.request.urlopen(url, timeout=timeout).read()  # noqa: S310 — yt-dlp supplied https URLs


def download(url, music_dir, thumbs_dir, on_progress=None):
    """Download audio (and thumbnail) for one video.

    ``on_progress(fraction, status_text)`` is called from this (worker) thread.
    Returns ``{id, title, channel, uploader, duration, filepath, webpage_url, thumb}``;
    raises ``YouTubeError``.
    """
    report = on_progress or (lambda frac, status: None)
    result, errors = None, []
    for fmt in YT_FORMATS:
        cmd = [find_tool("yt-dlp"), "--no-playlist", "-f", fmt, "-x", "--audio-quality", "0", "--embed-metadata",
               "--write-thumbnail", "--convert-thumbnails", "jpg", "--no-warnings",
               "-P", str(music_dir), "-P", f"thumbnail:{thumbs_dir}",
               "-o", "%(title).120B [%(id)s].%(ext)s", "-o", "thumbnail:%(id)s.%(ext)s",
               "--newline", "--progress", "--progress-template",
               "download:RIFFPROG %(progress._percent_str)s|%(progress._eta_str)s",
               "--print", "after_move:RIFFJSON %(.{id,title,channel,uploader,duration,filepath,webpage_url})j",
               url]
        if bundled_tool("ffmpeg"):  # packaged builds: point yt-dlp at our ffmpeg
            cmd[-1:-1] = ["--ffmpeg-location", bundled_tool("ffmpeg")]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace", **POPEN_KW)
        except OSError as e:
            errors.append(str(e))
            break
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("RIFFPROG"):
                m = re.search(r"([\d.]+)%\|?(.*)", line)
                if m:
                    pct, eta = float(m.group(1)), m.group(2).strip()
                    report(pct / 100, f"Downloading {pct:.0f}%" + (f" · {eta} left" if eta and eta != "NA" else ""))
            elif line.startswith("RIFFJSON"):
                try:
                    result = json.loads(line[len("RIFFJSON"):])
                except ValueError:
                    pass
            elif line.startswith(("[ExtractAudio]", "[Metadata]")):
                report(1.0, "Converting…")
            elif "ERROR" in line:
                errors.append(line.removeprefix("ERROR: "))
        proc.wait()
        if result and proc.returncode == 0:
            break
        result = None
        report(0.0, "Retrying with another format…")
    if not result or not Path(result.get("filepath", "")).exists():
        raise YouTubeError(errors[-1] if errors else "unknown error")
    thumb = Path(thumbs_dir) / f"{result['id']}.jpg"
    result["thumb"] = str(thumb) if thumb.exists() else None
    return result
