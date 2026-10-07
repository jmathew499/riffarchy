"""Per-platform locations and helper-tool lookup.

Linux paths match what the GTK app has always used (``$XDG_DATA_HOME/riffarchy`` and
the XDG music dir), so existing libraries keep working.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Keep console windows from flashing up for every ffmpeg/yt-dlp call on Windows.
POPEN_KW = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}


def _xdg(var, fallback):
    v = os.environ.get(var)
    return Path(v) if v and os.path.isabs(v) else Path.home() / fallback


def data_dir():
    if IS_WINDOWS:
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "Riffarchy"
    if IS_MAC:
        return Path.home() / "Library/Application Support/Riffarchy"
    return _xdg("XDG_DATA_HOME", ".local/share") / "riffarchy"


def _xdg_music_dir():
    try:
        text = (_xdg("XDG_CONFIG_HOME", ".config") / "user-dirs.dirs").read_text()
    except OSError:
        return None
    m = re.search(r'^\s*XDG_MUSIC_DIR\s*=\s*"([^"]*)"', text, re.M)
    if not m:
        return None
    p = m.group(1).replace("$HOME", str(Path.home()))
    return Path(p) if os.path.isabs(p) else None


def music_dir():
    base = None if (IS_WINDOWS or IS_MAC) else _xdg_music_dir()
    return (base or Path.home() / "Music") / "Riffarchy"


def runtime_dir():
    rd = None if IS_WINDOWS else os.environ.get("XDG_RUNTIME_DIR")
    return rd if rd and os.path.isdir(rd) else tempfile.gettempdir()


def _bundle_dirs():
    """Where a packaged build keeps its own mpv/ffmpeg/yt-dlp copies."""
    dirs = []
    if os.environ.get("RIFFARCHY_BIN_DIR"):
        dirs.append(Path(os.environ["RIFFARCHY_BIN_DIR"]))
    if getattr(sys, "frozen", False):  # PyInstaller and friends
        exe_dir = Path(sys.executable).resolve().parent
        dirs.append(exe_dir / "bin")                      # Windows/Linux: next to Riffarchy.exe
        dirs.append(exe_dir.parent / "Resources" / "bin")  # macOS: Riffarchy.app/Contents/Resources/bin
        dirs.append(Path(getattr(sys, "_MEIPASS", exe_dir)) / "bin")
    dirs.append(Path(__file__).resolve().parent.parent / "bin")
    return dirs


def bundled_tool(name):
    """Path to a copy of ``name`` shipped with the app, or None."""
    exe = name + ".exe" if IS_WINDOWS else name
    for d in _bundle_dirs():
        candidates = [d / exe]
        if IS_WINDOWS and name == "mpv":  # mpv.exe is a GUI program: no stdout/exit status; mpv.com is
            candidates.insert(0, d / "mpv.com")  # its console wrapper, which subprocesses can talk to
        if IS_MAC and name == "mpv":  # mpv's official macOS build is an app bundle with its own dylibs
            candidates.append(d / "mpv.app" / "Contents" / "MacOS" / "mpv")
        for c in candidates:
            if c.is_file():
                return str(c)
    return None


def find_tool(name):
    """Path to a helper tool: bundled copy first, then PATH. Falls back to the bare name."""
    return bundled_tool(name) or shutil.which(name) or name


def missing_tools(names=("mpv", "ffmpeg", "ffprobe", "yt-dlp")):
    return [n for n in names if not (os.path.isfile(find_tool(n)) or shutil.which(find_tool(n)))]
