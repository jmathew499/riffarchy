"""Practice session: everything a front end does with the current song.

``Session`` owns the mpv engine and the playback/loop/trainer/volume/section logic.
Front ends call its methods from the UI thread and react to ``notify(what)``:

    song      a song was loaded or unloaded (redo everything)
    peaks     waveform data arrived (``session.peaks``; None = unreadable)
    playing   play/pause/end state changed
    position  playhead moved while paused (when playing, poll ``current_pos()``)
    duration  real duration known
    speed, pitch, loop, trainer, volume, sections, library

``message(text, timeout)`` shows a transient notice; ``save_needed()`` asks the
front end to (debounce and) call ``library.save()``.
"""

import os
import threading
import time

from . import media, youtube
from .engine import Mpv
from .util import PEAK_RATE, SPEED_MAX, SPEED_MIN, VOLUME_MAX, fmt_time, pitch_scale, run_async

TRAINER_IDLE = "Speed up automatically after a fixed # of loops"


class Session:
    def __init__(self, lib, dispatch, notify=None, message=None, save_needed=None):
        self.lib, self.dispatch = lib, dispatch
        self.notify = notify or (lambda what: None)
        self.message = message or (lambda text, timeout=4: None)
        self.save_needed = save_needed or (lambda: None)
        self.song = None
        self.peaks = None
        self.pos = 0.0
        self.pos_t = time.monotonic()
        self.playing = False
        self.eof = False
        self.duration = 0.0
        self.last_raw = 0.0
        self.seek_guard = 0.0
        self.loop_count = 0
        self.trainer_on = False
        self.mpv = Mpv(self._on_prop, self._on_event, dispatch,
                       volume=lib.data["volume"], muted=lib.data["muted"])

    def _changed(self, *whats, save=True):
        for w in whats:
            self.notify(w)
        if save:
            self.save_needed()

    # ── song ──
    @property
    def speed(self):
        return self.song["speed"] if self.song else 100

    @property
    def loop(self):
        return self.song["loop"] if self.song else {"a": None, "b": None, "on": False}

    def has_loop(self):
        lp = self.loop
        return lp["a"] is not None and lp["b"] is not None

    def load(self, song, autoplay=False):
        if not os.path.exists(song["path"]):
            self.message("File is missing — it may have been moved or deleted")
            return False
        self.song = song
        self.lib.data["last"] = song["id"]
        self.eof = False
        self.pos, self.last_raw, self.duration = 0.0, 0.0, song.get("duration") or 0.0
        self.loop_count = 0
        self.peaks = None
        self.mpv.set("pause", not autoplay)
        self.mpv.set("af", f"@rb:rubberband=pitch-scale={self.pitch_scale():.6f}")
        self.mpv.set("speed", song["speed"] / 100)
        self.mpv.set("ab-loop-a", "no")
        self.mpv.set("ab-loop-b", "no")
        self.mpv.command("loadfile", song["path"], "replace")
        run_async(self.dispatch, media.load_peaks, lambda peaks, err: self._peaks_done(song, peaks),
                  song, self.lib.peaks_dir)
        self._changed("song")
        return True

    def _peaks_done(self, song, peaks):
        if self.song is not song:
            return
        self.peaks = peaks
        if peaks and not self.duration:
            self.duration = len(peaks) / PEAK_RATE
        self.notify("peaks")

    def unload(self):
        self.mpv.command("stop")
        self.song, self.peaks, self.playing = None, None, False
        self._changed("song", "playing", save=False)

    def remove_song(self, song, delete_file=False):
        if song is self.song:
            self.unload()
        self.lib.remove(song, delete_file=delete_file)
        self.notify("library")

    # ── mpv events ──
    def _on_prop(self, name, data):
        if name == "time-pos" and data is not None:
            now = time.monotonic()
            lp = self.song and self.song["loop"]
            if (lp and lp["on"] and lp["a"] is not None and self.playing and now > self.seek_guard
                    and data < self.last_raw - 0.25 and abs(data - lp["a"]) < 0.6):
                self._on_loop_wrap()
            self.last_raw = data
            self.pos, self.pos_t = data, now
            if not self.playing:
                self.notify("position")
        elif name == "duration" and data:
            self.duration = data
            if self.song and not self.song.get("duration"):
                self.song["duration"] = data
                self.save_needed()
            self.notify("duration")
        elif name == "pause":
            self._set_playing(not data and self.song is not None and not self.eof)
        elif name == "eof-reached":
            self.eof = bool(data)
            if self.eof:
                self._set_playing(False)

    def _on_event(self, event, msg):
        if event == "file-loaded":
            self._push_loop()
        elif event == "end-file" and msg.get("reason") == "error":
            self.message("mpv couldn't play this file")

    def _set_playing(self, playing):
        if playing != self.playing:
            self.playing = playing
            self.notify("playing")

    # ── transport ──
    def current_pos(self):
        """Interpolated playhead (mpv reports position only a few times a second)."""
        p = self.pos
        if self.playing:
            p += (time.monotonic() - self.pos_t) * self.speed / 100
            lp = self.song["loop"]
            if lp["on"] and lp["b"] is not None and self.pos <= lp["b"]:
                p = min(p, lp["b"])
        return min(p, self.duration) if self.duration else p

    def toggle_play(self):
        if not self.song:
            return
        if self.playing:
            self.mpv.set("pause", True)
            return
        if self.eof:
            lp = self.song["loop"]
            self.seek(lp["a"] if lp["on"] and lp["a"] is not None else 0)
        self.mpv.set("pause", False)

    def seek(self, t):
        if not self.song:
            return
        t = max(0.0, min(t, self.duration - 0.05 if self.duration else t))
        self.mpv.command("seek", t, "absolute+exact")
        self.eof = False
        self.pos, self.pos_t, self.last_raw = t, time.monotonic(), t
        self.seek_guard = time.monotonic() + 0.4
        self.notify("position")

    def seek_rel(self, d):
        self.seek(self.current_pos() + d)

    def go_start(self):
        lp = self.song and self.song["loop"]
        if lp and lp["a"] is not None and (lp["on"] or self.current_pos() > lp["a"] + 0.5):
            self.seek(lp["a"])
        else:
            self.seek(0)

    # ── speed / pitch ──
    def set_speed(self, pct):
        if not self.song:
            return
        pct = int(round(min(SPEED_MAX, max(SPEED_MIN, pct))))
        self.pos, self.pos_t = self.current_pos(), time.monotonic()
        self.song["speed"] = pct
        self.mpv.set("speed", pct / 100)
        self._changed("speed", "trainer")

    def pitch_scale(self):
        return pitch_scale(self.song["semis"], self.song["cents"]) if self.song else 1.0

    def set_pitch(self, semis=None, cents=None):
        if not self.song:
            return
        if semis is not None:
            self.song["semis"] = int(min(12, max(-12, semis)))
        if cents is not None:
            self.song["cents"] = int(min(50, max(-50, cents)))
        self.mpv.command("af-command", "rb", "set-pitch", f"{self.pitch_scale():.6f}")
        self._changed("pitch")

    def shift_pitch(self, semis):
        if self.song:
            self.set_pitch(self.song["semis"] + semis)

    def reset_speed_pitch(self):
        self.set_speed(100)
        self.set_pitch(0, 0)

    # ── loop ──
    def _push_loop(self):
        lp = self.song["loop"] if self.song else None
        if lp and lp["on"] and lp["a"] is not None and lp["b"] is not None and lp["b"] > lp["a"]:
            self.mpv.set("ab-loop-a", lp["a"])
            self.mpv.set("ab-loop-b", lp["b"])
        else:
            self.mpv.set("ab-loop-a", "no")
            self.mpv.set("ab-loop-b", "no")

    def set_loop(self, a, b, on=None):
        if not self.song:
            return
        lp = self.song["loop"]
        lp["a"], lp["b"] = (round(a, 3) if a is not None else None), (round(b, 3) if b is not None else None)
        if on is not None:
            lp["on"] = on and a is not None and b is not None
        self._loop_changed(seek_into=True)

    def clear_loop(self):
        self.set_loop(None, None, on=False)

    def set_loop_on(self, on):
        if not self.song:
            return
        lp = self.song["loop"]
        if on and (lp["a"] is None or lp["b"] is None):
            self.message("Mark a loop first — drag across the waveform or press [ and ]")
            on = False
        lp["on"] = on
        self._loop_changed(seek_into=True)

    def toggle_loop(self):
        if self.song:
            self.set_loop_on(not self.song["loop"]["on"])

    def _loop_changed(self, seek_into=False):
        lp = self.song["loop"]
        self._push_loop()
        self.loop_count = 0
        if seek_into and lp["on"]:
            p = self.current_pos()
            if not (lp["a"] <= p < lp["b"]):
                self.seek(lp["a"])
        self._changed("loop", "trainer")

    def mark(self, which):
        """Set loop start ('a') or end ('b') at the playhead."""
        if not self.song:
            return
        lp = self.song["loop"]
        p = self.current_pos()
        a, b = lp["a"], lp["b"]
        # Never silently discard the other marker: if the playhead is on the wrong
        # side of it, keep the loop as-is and explain instead.
        if which == "a":
            if b is not None and p >= b - 0.05:
                self.message(f"Playhead ({fmt_time(p, True)}) is at or after B — move it before B to set A here")
                return
            a = p
        else:
            if a is not None and p <= a + 0.05:
                self.message(f"Playhead ({fmt_time(p, True)}) is at or before A — move it past A to set B here")
                return
            b = p
            if a is None:
                a = max(0.0, b - 4)
        self.set_loop(a, b, on=a is not None and b is not None and (lp["on"] or which == "b"))

    def nudge(self, which, d):
        lp = self.song and self.song["loop"]
        if not lp or lp[which] is None:
            return
        val = lp[which] + d
        if which == "a":
            val = max(0.0, min(val, (lp["b"] or self.duration) - 0.05))
        else:
            val = min(self.duration or val, max(val, (lp["a"] or 0) + 0.05))
        lp[which] = round(val, 3)
        self._loop_changed()

    # ── speed trainer ──
    @property
    def trainer(self):
        return self.lib.data["trainer"]

    def set_trainer_enabled(self, on):
        self.trainer_on = bool(on)
        self.loop_count = 0
        self.notify("trainer")

    def set_trainer(self, inc=None, every=None, target=None):
        tr = self.trainer
        for k, v in (("inc", inc), ("every", every), ("target", target)):
            if v is not None:
                tr[k] = int(v)
        self.loop_count = 0
        self._changed("trainer")

    def trainer_status(self):
        if not self.trainer_on:
            return TRAINER_IDLE
        tr = self.trainer
        if self.speed >= tr["target"]:
            return f"Target {tr['target']}% reached — nice"
        left = tr["every"] - self.loop_count % tr["every"]
        nxt = min(tr["target"], self.speed + tr["inc"])
        return f"Loop {self.loop_count} · {nxt}% in {left} more loop{'s' * (left != 1)}"

    def _on_loop_wrap(self):
        self.loop_count += 1
        if self.trainer_on:
            tr = self.trainer
            if self.loop_count % tr["every"] == 0 and self.speed < tr["target"]:
                self.set_speed(min(tr["target"], self.speed + tr["inc"]))
                self.message(f"Speed up → {self.speed}%", 2)
        self.notify("trainer")

    # ── volume (app-wide, not per song) ──
    @property
    def volume(self):
        return self.lib.data["volume"]

    @property
    def muted(self):
        return self.lib.data["muted"]

    def set_volume(self, v):
        v = int(round(min(VOLUME_MAX, max(0, v))))
        self.lib.data["volume"] = v
        self.mpv.set("volume", v)
        if self.lib.data["muted"] and v > 0:  # moving the slider unmutes
            self.lib.data["muted"] = False
            self.mpv.set("mute", False)
        self._changed("volume")

    def set_muted(self, muted):
        self.lib.data["muted"] = bool(muted)
        self.mpv.set("mute", bool(muted))
        self._changed("volume")

    def toggle_mute(self):
        self.set_muted(not self.muted)
        self.message("Muted" if self.muted else f"Volume {self.volume}%", 1)

    # ── sections ──
    @property
    def sections(self):
        return self.song["sections"] if self.song else []

    def next_section_name(self):
        return f"Section {len(self.sections) + 1}"

    def add_section(self, name, a=None, b=None):
        """Save the current loop (or ``a``–``b``) as a named section."""
        if not self.song:
            return None
        lp = self.song["loop"]
        a = lp["a"] if a is None else a
        b = lp["b"] if b is None else b
        if a is None or b is None:
            self.message("Mark a loop first")
            return None
        sec = {"name": name or "Section", "a": a, "b": b}
        self.song["sections"].append(sec)
        self._changed("sections")
        return sec

    def rename_section(self, sec, name):
        if name and name != sec["name"]:
            sec["name"] = name
            self._changed("sections")

    def delete_section(self, sec):
        self.song["sections"].remove(sec)
        self._changed("sections")

    def recall_section(self, sec):
        self.set_loop(sec["a"], sec["b"], on=True)
        self.seek(sec["a"])
        if not self.playing:
            self.toggle_play()

    def close(self):
        self.mpv.quit()


class Downloader:
    """Background YouTube downloads that land in the library.

    ``on_update(job)`` fires as progress changes and ``on_done(job, song, error)``
    when a job finishes; both via ``dispatch``. Jobs are plain dicts
    (``yt, title, progress, status``) — front ends may stash widgets on them.
    """

    def __init__(self, lib, dispatch, on_update=None, on_done=None):
        self.lib, self.dispatch = lib, dispatch
        self.on_update = on_update or (lambda job: None)
        self.on_done = on_done or (lambda job, song, error: None)
        self.jobs = []

    def job_for(self, yt):
        return next((j for j in self.jobs if j["yt"] == yt), None)

    def start(self, result):
        """Start downloading a ``youtube.search`` result. Returns the job, or None if
        the video is already in the library or already downloading."""
        if self.lib.find(yt=result.get("id")) or self.job_for(result.get("id")):
            return None
        job = {"yt": result.get("id"), "title": result.get("title") or result["url"], "progress": 0.0,
               "status": "Starting…"}
        self.jobs.append(job)
        threading.Thread(target=self._run, args=(job, result["url"]), daemon=True).start()
        return job

    def _run(self, job, url):
        def progress(frac, status):
            self.dispatch(self._progress, job, frac, status)

        try:
            res, err = youtube.download(url, self.lib.music_dir, self.lib.thumbs_dir, progress), None
        except youtube.YouTubeError as e:
            res, err = None, str(e)
        self.dispatch(self._finish, job, res, err)

    def _progress(self, job, frac, status):
        job["progress"], job["status"] = frac, status
        self.on_update(job)

    def _finish(self, job, res, err):
        self.jobs.remove(job)
        song = self.lib.add_download(res, job["title"]) if res else None
        self.on_done(job, song, err)
