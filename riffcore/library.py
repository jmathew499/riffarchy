"""The song library: one JSON file holding every song and its practice settings."""

import json
import time
import uuid
from pathlib import Path

from . import paths
from .util import clean_stem


class Library:
    def __init__(self, data_dir=None, music_dir=None):
        self.data_dir = Path(data_dir) if data_dir else paths.data_dir()
        self.music_dir = Path(music_dir) if music_dir else paths.music_dir()
        self.lib_file = self.data_dir / "library.json"
        self.peaks_dir = self.data_dir / "peaks"
        self.thumbs_dir = self.data_dir / "thumbs"
        for d in (self.data_dir, self.peaks_dir, self.thumbs_dir, self.music_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.data = {"songs": [], "last": None, "trainer": {"inc": 5, "every": 3, "target": 100},
                     "volume": 100, "muted": False}
        if self.lib_file.exists():
            try:
                self.data.update(json.loads(self.lib_file.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                self.lib_file.replace(self.lib_file.with_suffix(".json.bak"))
        self.songs = self.data["songs"]

    def save(self):
        tmp = self.lib_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        tmp.replace(self.lib_file)

    def get(self, sid):
        return next((s for s in self.songs if s["id"] == sid), None)

    def find(self, path=None, yt=None):
        for s in self.songs:
            if (path and s["path"] == path) or (yt and s.get("yt") == yt):
                return s
        return None

    def sorted_songs(self):
        """Newest first."""
        return sorted(self.songs, key=lambda s: -s.get("added", 0))

    def owns(self, path):
        """True for files Riffarchy downloaded itself (safe to delete with the song)."""
        return bool(path) and (Path(path).is_relative_to(self.music_dir) or Path(path).is_relative_to(self.data_dir))

    def add(self, **kw):
        song = {"id": uuid.uuid4().hex[:12], "title": "Untitled", "artist": None, "path": "",
                "url": None, "yt": None, "thumb": None, "duration": None, "added": time.time(),
                "speed": 100, "semis": 0, "cents": 0,
                "loop": {"a": None, "b": None, "on": False}, "sections": []}
        song.update(kw)
        self.songs.append(song)
        self.save()
        return song

    def add_scanned(self, items):
        """Turn ``media.scan_files`` results into songs (existing songs pass through)."""
        songs = []
        for it in items:
            if "id" in it:
                songs.append(it)
            else:
                songs.append(self.add(path=it["path"], title=it.get("title") or clean_stem(it["path"]),
                                      artist=it.get("artist"), duration=it.get("duration")))
        return songs

    def add_download(self, result, fallback_title=None):
        """Add a song from a ``youtube.download`` result."""
        return self.add(path=result["filepath"], title=result.get("title") or fallback_title or "Untitled",
                        artist=result.get("channel") or result.get("uploader"),
                        duration=result.get("duration"), url=result.get("webpage_url"), yt=result.get("id"),
                        thumb=result.get("thumb"))

    def remove(self, song, delete_file=False):
        self.songs.remove(song)
        (self.peaks_dir / f"{song['id']}.bin").unlink(missing_ok=True)
        if delete_file:
            for p in (song["path"], song.get("thumb")):
                if self.owns(p):
                    Path(p).unlink(missing_ok=True)
        if self.data.get("last") == song["id"]:
            self.data["last"] = None
        self.save()
