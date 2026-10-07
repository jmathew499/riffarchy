#!/usr/bin/env python3
"""Download the helper programs a packaged Riffarchy ships with: pinned versions, SHA-256 checked.

    python packaging/fetch_tools.py bin                    # tools for this OS into ./bin
    python packaging/fetch_tools.py bin --platform macos   # or windows (download/verify only)

mpv (playback, A-B loop, rubberband speed/pitch, export), ffmpeg + ffprobe (waveform,
metadata, yt-dlp post-processing), yt-dlp (YouTube) and QuickJS-ng (the JavaScript
runtime yt-dlp needs for YouTube; 2.6 MB instead of deno's 110 MB). Versions and
licences are listed in THIRD_PARTY.md — update both together.
"""

import argparse
import hashlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

GH = "https://github.com"
MPV = f"{GH}/mpv-player/mpv/releases/download/v0.41.0"
FFMPEG = f"{GH}/eugeneware/ffmpeg-static/releases/download/b6.1.1"
YTDLP = f"{GH}/yt-dlp/yt-dlp/releases/download/2026.08.19"
QJS = f"{GH}/quickjs-ng/quickjs/releases/download/v0.17.0"
# mpv's own Windows builds leave out rubberband (needed for pitch shifting), so Windows uses
# shinchiro's build (the one mpv.io links). GitHub only keeps ~2 months of those; SourceForge
# keeps the weekly ones, so it is the fallback for the same, checksum-verified file.
MPV_WIN = "mpv-x86_64-20261004-git-413ff0b1cd.7z"
MPV_WIN_URLS = [f"{GH}/shinchiro/mpv-winbuild-cmake/releases/download/20261004/{MPV_WIN}",
                f"https://sourceforge.net/projects/mpv-player-windows/files/64bit/{MPV_WIN}/download"]

TOOLS = {
    "macos": [
        {"url": f"{MPV}/mpv-v0.41.0-macos-14-arm.zip", "unpack": "mpv-macos",
         "sha256": "5c96f9b21355fc0a11d2e2161ad65f33031070e9fb3f6bd9865fb459b94587e6"},
        {"url": f"{FFMPEG}/ffmpeg-darwin-arm64", "dest": "ffmpeg", "sha256": "a90e3db6a3fd35f6074b013f948b1aa45b31c6375489d39e572bea3f18336584"},
        {"url": f"{FFMPEG}/ffprobe-darwin-arm64", "dest": "ffprobe", "sha256": "bb2db6f5d8cef919da12fbf592119a987202a8c060a886f3cab091f9cab90b64"},
        {"url": f"{YTDLP}/yt-dlp_macos", "dest": "yt-dlp", "sha256": "0f192b7ec147ab6288885d6351d9ab67367640029b4377576ef46dd79cf7b202"},
        {"url": f"{QJS}/qjs-darwin-arm64", "dest": "qjs", "sha256": "8be3ddfe3397d2e692e4e1e8972ee9d032a0a580505d2f8b4ea528cf1b651c11"},
    ],
    "windows": [
        {"url": MPV_WIN_URLS[0], "mirrors": MPV_WIN_URLS[1:], "unpack": "mpv-windows-7z",
         "sha256": "0703a0d62c60b2c68511c6a101db82a31c32c69bcdd86941632a1e76bb1699b7"},
        {"url": f"{FFMPEG}/ffmpeg-win32-x64", "dest": "ffmpeg.exe", "sha256": "04e1307997530f9cf2fe35cba2ca7e8875ca91da02f89d6c7243df819c94ad00"},
        {"url": f"{FFMPEG}/ffprobe-win32-x64", "dest": "ffprobe.exe", "sha256": "3a7e2dc003dc2cd1472827e4c7c4f056ae1ae0ae7c5bbc580c99b49827351ba4"},
        {"url": f"{YTDLP}/yt-dlp.exe", "dest": "yt-dlp.exe", "sha256": "66674953fe251b89f4d08c5f0e35e0728679bd67ab3d7d05c0562af101dd3e7a"},
        {"url": f"{QJS}/qjs-windows-x86_64.exe", "dest": "qjs.exe", "sha256": "2aeabf0092c3262d6b2609824418f7dd7ed1f1df939f73b2b15645230cac0d77"},
    ],
}

CACHE = Path(__file__).resolve().parent.parent / ".cache" / "tools"


def current_platform():
    return {"darwin": "macos", "win32": "windows"}.get(sys.platform)


def file_name(url):
    return url.removesuffix("/download").rsplit("/", 1)[1]


def download(urls, expected):
    """Fetch the first working URL (cached), verify its SHA-256, return the bytes."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / file_name(urls[0])
    if cached.exists() and (not expected or hashlib.sha256(cached.read_bytes()).hexdigest() == expected):
        data = cached.read_bytes()
        return data, hashlib.sha256(data).hexdigest()
    problems = []
    for url in urls:
        print(f"  downloading {url}", flush=True)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "riffarchy-build"})
            with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310 — pinned release URLs
                data = r.read()
        except OSError as e:
            problems.append(f"{url}: {e}")
            continue
        digest = hashlib.sha256(data).hexdigest()
        if expected and digest != expected:
            problems.append(f"{url}: checksum mismatch (got {digest})")
            continue
        cached.write_bytes(data)
        return data, digest
    raise SystemExit("could not fetch a verified copy:\n  " + "\n  ".join(problems))


def make_executable(path):
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def unpack(kind, data, bin_dir):
    if kind == "mpv-macos":  # zip → mpv.tar.gz → mpv.app (keep symlinks + modes intact)
        inner = zipfile.ZipFile(io.BytesIO(data)).read("mpv.tar.gz")
        shutil.rmtree(bin_dir / "mpv.app", ignore_errors=True)
        with tarfile.open(fileobj=io.BytesIO(inner)) as tar:
            tar.extractall(bin_dir, filter="tar")
    elif kind == "mpv-windows-7z":  # just mpv.exe and the DLL it loads from the top of the archive
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "mpv.7z"
            archive.write_bytes(data)
            try:
                import py7zr  # CI: pip install py7zr
                with py7zr.SevenZipFile(archive) as z:
                    z.extractall(Path(tmp) / "x")
            except ImportError:  # locally: libarchive's bsdtar (Windows ships it as tar.exe) reads .7z
                tar = shutil.which("bsdtar") or shutil.which("tar")
                (Path(tmp) / "x").mkdir()
                subprocess.run([tar, "-xf", str(archive), "-C", str(Path(tmp) / "x")], check=True)
            for f in (Path(tmp) / "x").iterdir():
                if f.is_file() and f.suffix.lower() in (".exe", ".dll"):
                    shutil.copy2(f, bin_dir / f.name)
    else:
        raise ValueError(kind)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bin_dir", type=Path)
    ap.add_argument("--platform", choices=sorted(TOOLS), default=current_platform())
    args = ap.parse_args()
    if not args.platform:
        raise SystemExit("no bundled tools for this OS (Linux uses the system packages); pass --platform")
    args.bin_dir.mkdir(parents=True, exist_ok=True)
    unpinned = []
    for tool in TOOLS[args.platform]:
        data, digest = download([tool["url"], *tool.get("mirrors", [])], tool["sha256"])
        if not tool["sha256"]:
            unpinned.append((tool["url"], digest))
        if "unpack" in tool:
            unpack(tool["unpack"], data, args.bin_dir)
        else:
            dest = args.bin_dir / tool["dest"]
            dest.write_bytes(data)
            make_executable(dest)
        print(f"  ok  {file_name(tool['url'])}  {digest[:16]}…", flush=True)
    for url, digest in unpinned:
        print(f"UNPINNED {url}\n         sha256={digest}")
    if unpinned and os.environ.get("CI"):
        raise SystemExit("refusing to use unpinned downloads in CI")


if __name__ == "__main__":
    main()
