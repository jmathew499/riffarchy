"""A tiny "now playing" status file for desktop integrations (the Omarchy bar widget).

Linux only: ``$XDG_RUNTIME_DIR/riffarchy/status.json``, rewritten atomically whenever
something visible changes and removed when the app exits. Readers should treat a
file whose ``pid`` is no longer running as stale (the app crashed).
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from . import VERSION


def status_path():
    if not sys.platform.startswith("linux"):
        return None
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return Path(base) / "riffarchy" / "status.json"


def publish(session):
    """Write the session's current state. Never raises: the app must not care if this fails."""
    path = status_path()
    if path is None:
        return
    song = session.song
    data = {"version": VERSION, "pid": os.getpid(), "updated": time.time(),
            "song": None, "playing": bool(session.playing)}
    if song:
        lp = song["loop"]
        data["song"] = {"title": song["title"], "artist": song.get("artist"), "speed": song["speed"],
                        "semis": song["semis"], "cents": song["cents"],
                        "loop": bool(lp["on"] and lp["a"] is not None and lp["b"] is not None)}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
    except OSError:
        pass


def clear():
    path = status_path()
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
