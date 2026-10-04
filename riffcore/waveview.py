"""Toolkit-independent waveform view model.

Holds the visible time window, playhead and loop markers, and turns pointer input
(in widget pixels) into seeks and loop edits. A front-end widget sets ``width``,
forwards drag/scroll events, and draws ``columns()``, ``grid()`` and the markers.
"""

import math

from .util import PEAK_RATE

GRID_STEPS = (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300)


class WaveView:
    HANDLE_PX = 8       # how close (px) a press must be to grab an A/B handle
    DRAG_PX = 3         # movement before a press counts as a drag rather than a click
    MIN_LOOP = 0.05     # seconds
    MIN_SPAN = 0.5      # tightest zoom, seconds on screen

    def __init__(self):
        self.peaks = None
        self.duration = 0.0
        self.pos = 0.0
        self.a = self.b = None
        self.loop_on = False
        self.v0, self.v1 = 0.0, None  # v1 None = whole song
        self.width = 1
        self._drag = None

    # data
    def set_peaks(self, peaks, duration):
        self.peaks, self.duration = peaks, duration or 0.0
        self.v0, self.v1 = 0.0, None

    def set_loop(self, a, b, on):
        self.a, self.b, self.loop_on = a, b, on

    def set_pos(self, pos):
        """Move the playhead; while zoomed, page the view to follow it."""
        self.pos = pos
        v0, v1 = self.view()
        if self.v1 is not None and not (v0 <= pos <= v1):
            span = v1 - v0
            self._set_view(pos - span * 0.1, pos + span * 0.9)

    @property
    def ready(self):
        return bool(self.duration and self.peaks)

    # view window
    def view(self):
        return self.v0, (self.v1 if self.v1 is not None else max(self.duration, 0.001))

    def view_key(self):
        """Changes whenever the rendered waveform would — handy as a cache key."""
        return (self.width, *self.view(), id(self.peaks))

    def _set_view(self, v0, v1):
        span = min(max(v1 - v0, self.MIN_SPAN), self.duration or 1)
        v0 = min(max(0.0, v0), max(0.0, self.duration - span))
        if span >= self.duration - 1e-6:
            self.v0, self.v1 = 0.0, None
        else:
            self.v0, self.v1 = v0, v0 + span

    def zoom_fit(self):
        self._set_view(0, self.duration)

    def zoom_to(self, a, b):
        pad = (b - a) * 0.08
        self._set_view(a - pad, b + pad)

    def zoom(self, factor, center_t=None):
        """factor < 1 zooms in. Keeps ``center_t`` (default: playhead) under the same pixel."""
        v0, v1 = self.view()
        c = self.pos if center_t is None else center_t
        span = (v1 - v0) * factor
        frac = (c - v0) / (v1 - v0) if v1 > v0 else 0.5
        self._set_view(c - span * frac, c - span * frac + span)

    def pan(self, steps):
        v0, v1 = self.view()
        d = (v1 - v0) * 0.08 * steps
        self._set_view(v0 + d, v1 + d)

    def scroll(self, dx, dy, x, shift=False):
        """Wheel input: vertical zooms around the pointer, horizontal (or Shift) pans."""
        if not self.duration:
            return False
        if dx or shift:
            self.pan(dx or dy)
        else:
            self.zoom(1.2 ** dy, self.x2t(x))
        return True

    # coordinates
    def t2x(self, t):
        v0, v1 = self.view()
        return (t - v0) / (v1 - v0) * self.width

    def x2t(self, x):
        v0, v1 = self.view()
        return min(max(v0 + x / max(1, self.width) * (v1 - v0), 0.0), self.duration)

    def near_handle(self, x):
        """'a', 'b' or None for a pointer at ``x``."""
        if self.a is None or self.b is None:
            return None
        for name, t in (("b", self.b), ("a", self.a)):
            if abs(self.t2x(t) - x) <= self.HANDLE_PX:
                return name
        return None

    # pointer drags: begin → update* → end
    def drag_begin(self, x):
        if not self.duration:
            return
        self._drag = {"x": x, "t": self.x2t(x), "mode": self.near_handle(x) or "new", "moved": False,
                      "orig": (self.a, self.b)}

    def drag_update(self, dx):
        """Returns True when the markers changed (redraw)."""
        d = self._drag
        if not d:
            return False
        if abs(dx) > self.DRAG_PX:
            d["moved"] = True
        if not d["moved"]:
            return False
        t = self.x2t(d["x"] + dx)
        if d["mode"] == "a":
            self.a = min(t, self.b - self.MIN_LOOP)
        elif d["mode"] == "b":
            self.b = max(t, self.a + self.MIN_LOOP)
        else:
            self.a, self.b = sorted((d["t"], t))
        return True

    def drag_end(self):
        """``("seek", t)``, ``("loop", a, b)`` or None. A too-short drag snaps back."""
        d, self._drag = self._drag, None
        if not d:
            return None
        if not d["moved"]:
            return ("seek", d["t"])
        if self.b - self.a >= self.MIN_LOOP:
            return ("loop", self.a, self.b)
        self.a, self.b = d["orig"]
        return None

    # drawing data
    def columns(self):
        """Peak level 0..1 for each pixel column of the current view."""
        if not self.peaks:
            return []
        v0, v1 = self.view()
        per_px = (v1 - v0) / max(1, self.width)
        n, out = len(self.peaks), []
        for x in range(int(self.width)):
            i0 = int((v0 + x * per_px) * PEAK_RATE)
            if i0 >= n:
                break
            i1 = max(i0 + 1, int((v0 + (x + 1) * per_px) * PEAK_RATE))
            out.append(max(self.peaks[i0:min(i1, n)]) / 255)
        return out

    def grid(self, min_px=90):
        """``(interval, [t, ...])`` for time-ruler ticks at least ``min_px`` apart."""
        v0, v1 = self.view()
        span = v1 - v0
        interval = next((i for i in GRID_STEPS if span / i <= self.width / min_px), 600)
        ticks, t = [], math.ceil(v0 / interval) * interval
        while t <= v1:
            ticks.append(t)
            t += interval
        return interval, ticks
