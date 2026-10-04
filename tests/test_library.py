"""Library persistence and download bookkeeping (no network)."""

import json

from riffcore import Downloader, Library


def test_roundtrip(lib, tmp_path):
    song = lib.add(path=str(tmp_path / "a.mp3"), title="A")
    song["speed"] = 80
    lib.save()
    again = Library(data_dir=lib.data_dir, music_dir=lib.music_dir)
    assert again.get(song["id"])["speed"] == 80
    assert json.loads(lib.lib_file.read_text())["songs"][0]["title"] == "A"


def test_owns_and_remove(lib, tmp_path):
    inside = lib.music_dir / "x.opus"
    inside.write_bytes(b"x")
    outside = tmp_path / "y.mp3"
    outside.write_bytes(b"y")
    a, b = lib.add(path=str(inside)), lib.add(path=str(outside))
    assert lib.owns(a["path"]) and not lib.owns(b["path"])
    lib.remove(a, delete_file=True)
    lib.remove(b, delete_file=True)  # never deletes files Riffarchy didn't download
    assert not inside.exists() and outside.exists() and lib.songs == []


def test_redownload_repairs_stale_entry(lib, pump):
    stale = lib.add(path=str(lib.music_dir / "gone.opus"), title="Lesson", yt="vid123",
                    sections=[{"name": "Solo", "a": 1.0, "b": 2.0}])
    d = Downloader(lib, pump)
    assert d.playable("vid123") is None  # file is missing → downloading again is allowed
    job = {"yt": "vid123", "title": "Lesson", "progress": 0, "status": "", "replace": stale}
    d.jobs.append(job)
    new_file = lib.music_dir / "Lesson [vid123].opus"
    new_file.write_bytes(b"audio")
    done = []
    d.on_done = lambda j, song, err: done.append(song)
    d._finish(job, {"id": "vid123", "filepath": str(new_file), "title": "Lesson", "thumb": None}, None)
    assert done == [stale] and stale["path"] == str(new_file)
    assert stale["sections"][0]["name"] == "Solo" and len(lib.songs) == 1
    assert d.playable("vid123") is stale
