#!/usr/bin/env python3
"""Riffarchy — a Riff Studio-style practice player for Omarchy (GTK4 + libadwaita front end).

Slow songs down without changing pitch, transpose without changing speed,
loop exact phrases, and pull audio straight off YouTube with yt-dlp.
All playback/library/download logic lives in ``riffcore``; this file is the GTK UI
and the Omarchy theme integration.
"""

import math
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

from riffcore import (APP_NAME, SPEED_MAX, SPEED_MIN, SPEED_PRESETS, VERSION, VOLUME_MAX,  # noqa: E402
                      Downloader, Library, Session, WaveView, fmt_time, run_async, song_subtitle)
from riffcore import media, paths, youtube  # noqa: E402

APP_ID = "app.riffarchy.Riffarchy"
THEME_STATE = Path.home() / ".local/state/omarchy/current"


def gtk_dispatch(fn, *args):
    """riffcore's dispatch: run ``fn(*args)`` on the GTK main loop."""
    def run():
        fn(*args)
        return False
    GLib.idle_add(run)


# ───────────────────────────── Omarchy theme ─────────────────────────────

def hex_rgb(h, alpha=1.0):
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, alpha)


def read_palette():
    """Resolved Omarchy palette as {name: '#rrggbb'}, or {} outside Omarchy."""
    pal = {}
    try:
        out = subprocess.run(["omarchy-theme-color", "--all"], capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            k, _, v = line.partition("\t")
            if v:
                pal[k.strip()] = v.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    if not pal:
        colors = THEME_STATE / "theme/colors.toml"
        if colors.exists():
            for k, v in re.findall(r'^\s*(\w+)\s*=\s*"(#[0-9a-fA-F]{6})"', colors.read_text(), re.M):
                pal[k] = v
    return pal


def palette_css(p):
    if "accent" not in p or "background" not in p:
        return ""
    g = lambda k, d: p.get(k) or p.get(d) or p["foreground"]  # noqa: E731
    bg, fg, acc = p["background"], p.get("foreground", "#ffffff"), p["accent"]
    dark, lighter = g("dark_background", "background"), g("lighter_background", "background")
    return f"""
@define-color accent_color {acc};
@define-color accent_bg_color {acc};
@define-color accent_fg_color {bg};
@define-color window_bg_color {bg};
@define-color window_fg_color {fg};
@define-color view_bg_color {bg};
@define-color view_fg_color {fg};
@define-color headerbar_bg_color {bg};
@define-color headerbar_fg_color {fg};
@define-color headerbar_backdrop_color {bg};
@define-color sidebar_bg_color {dark};
@define-color sidebar_fg_color {fg};
@define-color sidebar_backdrop_color {dark};
@define-color card_bg_color {lighter};
@define-color card_fg_color {fg};
@define-color dialog_bg_color {bg};
@define-color dialog_fg_color {fg};
@define-color popover_bg_color {lighter};
@define-color popover_fg_color {fg};
@define-color destructive_bg_color {g("red", "accent")};
@define-color success_color {g("green", "accent")};
@define-color warning_color {g("yellow", "accent")};
@define-color error_color {g("red", "accent")};
"""


BASE_CSS = """
.play-button { min-width: 56px; min-height: 56px; }
.play-button image { -gtk-icon-size: 24px; }
.wave-card { border-radius: 12px; }
.thumb { border-radius: 6px; }
.big-value { font-size: 1.6em; font-weight: 800; font-feature-settings: "tnum"; }
.time-label { font-feature-settings: "tnum"; }
.preset { min-width: 0; padding-left: 6px; padding-right: 6px; }
"""


# ───────────────────────────── waveform widget ─────────────────────────────

class Waveform(Gtk.DrawingArea):
    """Draws a ``riffcore.WaveView`` and feeds it pointer input."""

    __gsignals__ = {
        "seek": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "loop-set": (GObject.SignalFlags.RUN_FIRST, None, (float, float)),
    }

    def __init__(self):
        super().__init__(hexpand=True, content_height=190)
        self.m = WaveView()
        self.message = None
        self.colors = {}
        self.set_palette({})
        self._mask = None
        self._mask_key = None
        self._pointer_x = 0
        self.set_draw_func(self._draw)
        self.set_focusable(False)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", lambda g, x, y: self._sized() and self.m.drag_begin(x))
        drag.connect("drag-update", lambda g, dx, dy: self._sized() and self.m.drag_update(dx) and self.queue_draw())
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)
        dbl = Gtk.GestureClick()
        dbl.connect("pressed", lambda g, n, x, y: n == 2 and self.zoom_fit())
        self.add_controller(dbl)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._scroll)
        self.add_controller(scroll)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._motion)
        self.add_controller(motion)

    def set_palette(self, p):
        pick = lambda *keys, d: next((p[k] for k in keys if k in p), d)  # noqa: E731
        self.colors = {
            "bg": hex_rgb(pick("darker_background", "dark_background", d="#141416")),
            "wave": hex_rgb(pick("muted", "dark_foreground", "color8", d="#6f7b84")),
            "played": hex_rgb(pick("accent", d="#3584e4")),
            "loop": hex_rgb(pick("accent", d="#3584e4"), 0.16),
            "loop_off": hex_rgb(pick("foreground", d="#ffffff"), 0.06),
            "edge": hex_rgb(pick("accent", d="#3584e4")),
            "head": hex_rgb(pick("bright_foreground", "foreground", d="#ffffff")),
            "text": hex_rgb(pick("dark_foreground", "muted", d="#9a9a9a")),
        }
        self.queue_draw()

    # model passthroughs
    def set_peaks(self, peaks, duration):
        self.m.set_peaks(peaks, duration)
        self.queue_draw()

    def set_duration(self, duration):
        if self.m.peaks and self.m.duration != duration:
            self.m.duration = duration
            self.queue_draw()

    def set_loop(self, a, b, on):
        self.m.set_loop(a, b, on)
        self.queue_draw()

    def set_pos(self, pos):
        self.m.set_pos(pos)
        self.queue_draw()

    def zoom_fit(self):
        self.m.zoom_fit()
        self.queue_draw()

    def zoom_to(self, a, b):
        self.m.zoom_to(a, b)
        self.queue_draw()

    def zoom(self, factor):
        self.m.zoom(factor)
        self.queue_draw()

    # input
    def _sized(self):
        self.m.width = max(1, self.get_width())
        return True

    def _motion(self, _c, x, _y):
        self._pointer_x = x
        self._sized()
        self.set_cursor_from_name("ew-resize" if self.m.near_handle(x) else "text")

    def _drag_end(self, g, dx, dy):
        res = self.m.drag_end()
        self.queue_draw()
        if res and res[0] == "seek":
            self.emit("seek", res[1])
        elif res and res[0] == "loop":
            self.emit("loop-set", res[1], res[2])

    def _scroll(self, ctrl, dx, dy):
        self._sized()
        shift = bool(ctrl.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)
        if self.m.scroll(dx, dy, self._pointer_x, shift):
            self.queue_draw()
            return True
        return False

    # drawing
    def _build_mask(self, w, h, scale):
        import cairo
        surf = cairo.ImageSurface(cairo.FORMAT_A8, int(w * scale), int(h * scale))
        surf.set_device_scale(scale, scale)
        cr = cairo.Context(surf)
        mid, amp = h / 2, h / 2 - 22
        for x, val in enumerate(self.m.columns()):
            hh = max(1.0, val * amp)
            cr.rectangle(x, mid - hh, 1, hh * 2)
        cr.set_source_rgba(0, 0, 0, 1)
        cr.fill()
        return surf

    def _draw(self, area, cr, w, h):
        m, c = self.m, self.colors
        m.width = w
        r = 12
        cr.new_sub_path()
        cr.arc(w - r, r, r, -math.pi / 2, 0)
        cr.arc(w - r, h - r, r, 0, math.pi / 2)
        cr.arc(r, h - r, r, math.pi / 2, math.pi)
        cr.arc(r, r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgba(*c["bg"])
        cr.fill_preserve()
        cr.clip()

        if not m.ready:
            cr.set_source_rgba(*c["text"])
            cr.select_font_face("Sans")
            cr.set_font_size(13)
            msg = self.message or ""
            ext = cr.text_extents(msg)
            cr.move_to((w - ext.width) / 2, h / 2 + ext.height / 2)
            cr.show_text(msg)
            return

        # time grid
        interval, ticks = m.grid()
        cr.set_font_size(10)
        for t in ticks:
            x = m.t2x(t)
            cr.set_source_rgba(*c["text"][:3], 0.25)
            cr.rectangle(int(x), h - 14, 1, 5)
            cr.fill()
            cr.set_source_rgba(*c["text"][:3], 0.8)
            cr.move_to(x + 3, h - 4)
            cr.show_text(fmt_time(t, precise=interval < 1))

        # loop region (behind the wave)
        if m.a is not None and m.b is not None:
            xa, xb = m.t2x(m.a), m.t2x(m.b)
            cr.set_source_rgba(*(c["loop"] if m.loop_on else c["loop_off"]))
            cr.rectangle(xa, 0, xb - xa, h)
            cr.fill()

        # waveform: one cached A8 mask painted twice (unplayed / played)
        scale = self.get_scale_factor()
        key = (h, scale, *m.view_key())
        if key != self._mask_key:
            self._mask, self._mask_key = self._build_mask(w, h, scale), key
        px = m.t2x(m.pos)
        cr.set_source_rgba(*c["wave"])
        cr.mask_surface(self._mask, 0, 0)
        cr.save()
        cr.rectangle(0, 0, max(0, px), h)
        cr.clip()
        cr.set_source_rgba(*c["played"])
        cr.mask_surface(self._mask, 0, 0)
        cr.restore()

        # loop edges + handles
        if m.a is not None and m.b is not None:
            alpha = 1.0 if m.loop_on else 0.45
            for label, t in (("A", m.a), ("B", m.b)):
                x = m.t2x(t)
                cr.set_source_rgba(*c["edge"][:3], alpha)
                cr.rectangle(x - 1, 0, 2, h)
                cr.fill()
                bx = x - 16 if label == "B" else x
                cr.rectangle(bx, 0, 16, 16)
                cr.fill()
                cr.set_source_rgba(*c["bg"][:3], 1)
                cr.set_font_size(11)
                cr.move_to(bx + 4, 12)
                cr.show_text(label)

        # playhead
        cr.set_source_rgba(*c["head"])
        cr.rectangle(px - 1, 0, 2, h)
        cr.fill()


class Thumb(Gtk.Widget):
    """Fixed-size, rounded, cover-cropped image (Gtk.Picture insists on its natural size)."""

    def __init__(self, width, height, texture=None, radius=6):
        super().__init__(valign=Gtk.Align.CENTER)
        self.w, self.h, self.radius, self.texture = width, height, radius, texture

    def set_texture(self, texture):
        self.texture = texture
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        size = self.w if orientation == Gtk.Orientation.HORIZONTAL else self.h
        return size, size, -1, -1

    def do_snapshot(self, snap):
        w, h = self.get_width(), self.get_height()
        bounds = Graphene.Rect().init(0, 0, w, h)
        clip = Gsk.RoundedRect()
        clip.init_from_rect(bounds, self.radius)  # must init in place; the return value is unusable
        snap.push_rounded_clip(clip)
        if self.texture:
            tw, th = self.texture.get_width(), self.texture.get_height()
            k = max(w / tw, h / th)
            dw, dh = tw * k, th * k
            snap.append_scaled_texture(self.texture, Gsk.ScalingFilter.TRILINEAR,
                                       Graphene.Rect().init((w - dw) / 2, (h - dh) / 2, dw, dh))
        else:
            placeholder = Gdk.RGBA()
            placeholder.parse("rgba(128,128,128,0.12)")
            snap.append_color(placeholder, bounds)
        snap.pop()


# ───────────────────────────── YouTube dialog ─────────────────────────────

class YouTubeDialog(Adw.Dialog):
    def __init__(self, win):
        super().__init__(title="Get from YouTube", content_width=640, content_height=600)
        self.win = win
        self.gen = 0

        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=6, margin_bottom=12, margin_start=12, margin_end=12)
        row = Gtk.Box(spacing=6)
        self.entry = Gtk.SearchEntry(hexpand=True, placeholder_text="Search YouTube or paste a link…")
        self.entry.connect("activate", lambda *_: self.go())
        go = Gtk.Button(label="Search", css_classes=["suggested-action"])
        go.connect("clicked", lambda *_: self.go())
        row.append(self.entry)
        row.append(go)
        box.append(row)

        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        music = win.lib.music_dir.as_posix().replace(str(Path.home()), "~")
        self.status = Adw.StatusPage(icon_name="folder-download-symbolic", title="Find something to learn",
                                     description="Search for a song, a lesson or a backing track — or paste "
                                                 f"any YouTube link. Audio is saved to {music}.")
        self.status.add_css_class("compact")
        self.stack.add_named(self.status, "status")
        self.stack.add_named(Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                         width_request=48, height_request=48), "loading")
        self.results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"],
                                   valign=Gtk.Align.START)
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.results)
        self.stack.add_named(sw, "results")
        box.append(self.stack)
        tv.set_content(box)
        self.set_child(tv)
        self.set_focus(self.entry)

    def go(self):
        q = self.entry.get_text().strip()
        if not q:
            return
        self.gen += 1
        gen = self.gen
        self.stack.set_visible_child_name("loading")
        run_async(gtk_dispatch, youtube.search, lambda res, err: self._show(gen, res, err), q)

    def _show(self, gen, results, err):
        if gen != self.gen:
            return
        if err:
            self.status.set_icon_name("dialog-warning-symbolic")
            self.status.set_title("Couldn't reach YouTube")
            self.status.set_description(GLib.markup_escape_text(str(err)))
            self.stack.set_visible_child_name("status")
            return
        self.results.remove_all()
        if not results:
            self.status.set_icon_name("system-search-symbolic")
            self.status.set_title("No results")
            self.status.set_description("Try different words.")
            self.stack.set_visible_child_name("status")
            return
        for r in results:
            self.results.append(self._make_row(r))
        self.stack.set_visible_child_name("results")

    def _make_row(self, r):
        meta = [x for x in (r["channel"], fmt_time(r["duration"]) if r["duration"] else None) if x]
        row = Adw.ActionRow(use_markup=False, title_lines=2, subtitle_lines=1)
        row.set_title(r["title"] or r["url"])
        row.set_subtitle(" · ".join(meta))
        pic = Thumb(96, 54)
        pic.set_margin_top(6)
        pic.set_margin_bottom(6)
        row.add_prefix(pic)
        if r["thumb_url"]:
            threading.Thread(target=self._fetch_thumb, args=(r["thumb_url"], pic), daemon=True).start()

        if self.win.lib.find(yt=r["id"]):
            row.add_suffix(Gtk.Label(label="In library", css_classes=["dim-label", "caption"]))
        elif self.win.downloads.job_for(r["id"]):
            row.add_suffix(Gtk.Label(label="Downloading…", css_classes=["dim-label", "caption"]))
        else:
            btn = Gtk.Button(icon_name="folder-download-symbolic", tooltip_text="Download for practice",
                             valign=Gtk.Align.CENTER, css_classes=["flat", "circular"])

            def clicked(b):
                b.set_sensitive(False)
                b.set_icon_name("emblem-ok-symbolic")
                self.win.start_download(r)

            btn.connect("clicked", clicked)
            row.add_suffix(btn)
            row.set_activatable_widget(btn)
        return row

    @staticmethod
    def _fetch_thumb(url, pic):
        try:
            data = youtube.fetch_bytes(url)
        except Exception:  # noqa: BLE001 — thumbnails are optional
            return
        gtk_dispatch(lambda: pic.set_texture(Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))))


# ───────────────────────────── main window ─────────────────────────────

class RiffWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=1240, default_height=800)
        self.app = app
        self.lib = app.lib
        self._updating = False
        self._save_src = None
        self._tick_id = None

        self.s = Session(self.lib, gtk_dispatch, notify=self._on_session, message=self.toast,
                         save_needed=self.schedule_save)
        self.downloads = Downloader(self.lib, gtk_dispatch, on_update=self._job_update,
                                    on_done=self._download_done)
        self._build()
        self._setup_actions()
        self.rebuild_list()
        last = self.lib.get(self.lib.data.get("last"))
        if last:
            self.s.load(last)
        self.connect("close-request", self._on_close)
        # keep keyboard focus off the filter entry so Space/arrows drive playback
        self.connect("map", lambda *_: GLib.idle_add(lambda: self.play_btn.grab_focus() and False))

    @property
    def song(self):
        return self.s.song

    # ── UI construction ──
    def _build(self):
        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView(min_sidebar_width=270, max_sidebar_width=360)
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)

        # sidebar
        side_tv = Adw.ToolbarView()
        side_hb = Adw.HeaderBar()
        yt_btn = Gtk.Button(icon_name="folder-download-symbolic", tooltip_text="Get from YouTube (Ctrl+Y)",
                            action_name="win.youtube")
        open_btn = Gtk.Button(icon_name="document-open-symbolic", tooltip_text="Open audio file (Ctrl+O)",
                              action_name="win.open")
        side_hb.pack_start(yt_btn)
        side_hb.pack_end(open_btn)
        side_tv.add_top_bar(side_hb)
        side_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.search = Gtk.SearchEntry(placeholder_text="Filter library", margin_start=10, margin_end=10,
                                      margin_bottom=6)
        self.search.connect("search-changed", lambda *_: self.listbox.invalidate_filter())
        self.search.connect("stop-search", lambda e: (e.set_text(""), self.play_btn.grab_focus()))
        self.search.set_key_capture_widget(None)
        side_box.append(self.search)
        self.listbox = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.listbox.set_filter_func(self._filter_row)
        self.listbox.connect("row-activated", self._on_row_activated)
        self.list_empty = Adw.StatusPage(icon_name="audio-x-generic-symbolic", title="No songs yet",
                                         description="Grab one from YouTube or drop audio files here.",
                                         css_classes=["compact"], vexpand=True)
        self.side_stack = Gtk.Stack(vexpand=True)
        self.side_stack.add_named(Gtk.ScrolledWindow(child=self.listbox, hscrollbar_policy=Gtk.PolicyType.NEVER),
                                  "list")
        self.side_stack.add_named(self.list_empty, "empty")
        side_box.append(self.side_stack)
        side_tv.set_content(side_box)
        self.split.set_sidebar(Adw.NavigationPage(title="Library", child=side_tv))

        # content
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        self.wtitle = Adw.WindowTitle(title=APP_NAME, subtitle="")
        hb.set_title_widget(self.wtitle)
        menu = Gio.Menu()
        s1 = Gio.Menu()
        s1.append("Export Audio…", "win.export")
        s1.append("Show in Folder", "win.reveal")
        s1.append("Remove from Library…", "win.remove")
        s2 = Gio.Menu()
        s2.append("Keyboard Shortcuts", "win.shortcuts")
        s2.append(f"About {APP_NAME}", "app.about")
        menu.append_section(None, s1)
        menu.append_section(None, s2)
        hb.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True))
        tv.add_top_bar(hb)

        self.content_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        empty = Adw.StatusPage(icon_name="media-playlist-repeat-symbolic", title=APP_NAME,
                               description="Slow it down. Loop it. Learn it.")
        eb = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        b1 = Gtk.Button(label="Get from YouTube", action_name="win.youtube", css_classes=["pill", "suggested-action"])
        b2 = Gtk.Button(label="Open File…", action_name="win.open", css_classes=["pill"])
        eb.append(b1)
        eb.append(b2)
        empty.set_child(eb)
        self.content_stack.add_named(empty, "empty")
        self.content_stack.add_named(self._build_player(), "player")
        tv.set_content(self.content_stack)
        hint_box = Gtk.Box(halign=Gtk.Align.END, margin_end=8, margin_bottom=4)
        hint_btn = Gtk.Button(action_name="win.shortcuts", css_classes=["flat"], focus_on_click=False,
                              child=Gtk.Label(label="Keyboard Shortcuts  Ctrl+Shift+?",
                                              css_classes=["caption", "dim-label"]))
        hint_box.append(hint_btn)
        tv.add_bottom_bar(hint_box)
        self.split.set_content(Adw.NavigationPage(title=APP_NAME, child=tv))

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", lambda t, v, x, y: self.add_files([f.get_path() for f in v.get_files()]) or True)
        self.toasts.add_controller(drop)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 1080sp"))
        bp.add_setter(self.columns, "orientation", Gtk.Orientation.VERTICAL)
        self.add_breakpoint(bp)
        bp2 = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 680sp"))
        bp2.add_setter(self.columns, "orientation", Gtk.Orientation.VERTICAL)
        bp2.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp2)

    def _build_player(self):
        s = self.s
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                        margin_start=18, margin_end=18, margin_top=6, margin_bottom=0)

        self.wave = Waveform()
        self.wave.add_css_class("wave-card")
        self.wave.set_palette(self.app.palette)
        self.wave.connect("seek", lambda w, t: s.seek(t))
        self.wave.connect("loop-set", lambda w, a, b: s.set_loop(a, b, on=True))
        outer.append(self.wave)

        transport = Gtk.CenterBox()
        self.pos_label = Gtk.Label(label="0:00.00", css_classes=["time-label", "title-4"], xalign=0)
        transport.set_start_widget(self.pos_label)
        tb = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)

        def tbtn(icon, tip, cb, classes=("flat", "circular")):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER, css_classes=list(classes))
            b.connect("clicked", lambda *_: cb())
            tb.append(b)
            return b

        tbtn("media-skip-backward-symbolic", "Back to loop start / beginning (Home)", s.go_start)
        tbtn("media-seek-backward-symbolic", "Back 5 seconds (←)", lambda: s.seek_rel(-5))
        self.play_btn = tbtn("media-playback-start-symbolic", "Play / Pause (Space)", s.toggle_play,
                             ("circular", "suggested-action", "play-button"))
        tbtn("media-seek-forward-symbolic", "Forward 5 seconds (→)", lambda: s.seek_rel(5))
        self.loop_toggle = Gtk.ToggleButton(icon_name="media-playlist-repeat-symbolic", tooltip_text="Loop A–B (L)",
                                            valign=Gtk.Align.CENTER, css_classes=["flat", "circular"])
        self.loop_toggle.connect("toggled", lambda b: self._updating or s.set_loop_on(b.get_active()))
        tb.append(self.loop_toggle)
        transport.set_center_widget(tb)
        endbox = Gtk.Box(spacing=6)
        endbox.append(self._build_volume())
        zl = Gtk.Button(icon_name="zoom-in-symbolic", tooltip_text="Zoom to loop (Z)", css_classes=["flat"],
                        valign=Gtk.Align.CENTER)
        zl.connect("clicked", lambda *_: self.zoom_loop())
        zf = Gtk.Button(icon_name="zoom-fit-best-symbolic", tooltip_text="Show whole song (Shift+Z)",
                        css_classes=["flat"], valign=Gtk.Align.CENTER)
        zf.connect("clicked", lambda *_: self.wave.zoom_fit())
        self.dur_label = Gtk.Label(label="0:00", css_classes=["time-label", "dim-label"])
        endbox.append(zl)
        endbox.append(zf)
        endbox.append(self.dur_label)
        transport.set_end_widget(endbox)
        outer.append(transport)

        hint = Gtk.Label(label="Drag on the waveform to make a loop · drag A/B to adjust · scroll to zoom",
                         css_classes=["caption", "dim-label"])
        outer.append(hint)

        # control columns
        self.columns = Gtk.Box(spacing=18, homogeneous=True, margin_top=12, margin_bottom=18)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.columns.append(left)
        self.columns.append(right)

        # speed
        g = Adw.PreferencesGroup(title="Speed", description="Tempo changes without changing pitch")
        reset = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Reset to 100%", css_classes=["flat"],
                           valign=Gtk.Align.CENTER)
        reset.connect("clicked", lambda *_: s.set_speed(100))
        g.set_header_suffix(reset)
        prow = Adw.PreferencesRow(activatable=False)
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10, margin_bottom=10,
                       margin_start=12, margin_end=12)
        top = Gtk.Box(spacing=8)
        minus = Gtk.Button(label="−5%", css_classes=["flat"], valign=Gtk.Align.CENTER)
        minus.connect("clicked", lambda *_: s.set_speed(s.speed - 5))
        self.speed_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, SPEED_MIN, SPEED_MAX, 1)
        self.speed_scale.set_hexpand(True)
        self.speed_scale.set_draw_value(False)
        for m in (50, 100, 150, 200):
            self.speed_scale.add_mark(m, Gtk.PositionType.BOTTOM, f"{m}")
        self.speed_scale.connect("value-changed", lambda sc: self._updating or s.set_speed(sc.get_value()))
        plus = Gtk.Button(label="+5%", css_classes=["flat"], valign=Gtk.Align.CENTER)
        plus.connect("clicked", lambda *_: s.set_speed(s.speed + 5))
        self.speed_label = Gtk.Label(label="100%", css_classes=["big-value"], width_chars=5, xalign=1)
        top.append(minus)
        top.append(self.speed_scale)
        top.append(plus)
        top.append(self.speed_label)
        pbox.append(top)
        presets = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER, css_classes=["linked"])
        for v in SPEED_PRESETS:
            b = Gtk.Button(label=f"{v}%", css_classes=["preset"])
            b.connect("clicked", lambda _b, v=v: s.set_speed(v))
            presets.append(b)
        pbox.append(presets)
        prow.set_child(pbox)
        g.add(prow)

        self.trainer = Adw.ExpanderRow(title="Speed trainer", show_enable_switch=True, enable_expansion=False,
                                       subtitle=s.trainer_status())
        tr = s.trainer
        self.tr_inc = Adw.SpinRow.new_with_range(1, 25, 1)
        self.tr_inc.set_title("Increase by (%)")
        self.tr_inc.set_value(tr["inc"])
        self.tr_every = Adw.SpinRow.new_with_range(1, 20, 1)
        self.tr_every.set_title("After every N loops")
        self.tr_every.set_value(tr["every"])
        self.tr_target = Adw.SpinRow.new_with_range(30, SPEED_MAX, 5)
        self.tr_target.set_title("Stop at (%)")
        self.tr_target.set_value(tr["target"])
        for w in (self.tr_inc, self.tr_every, self.tr_target):
            w.connect("notify::value", lambda *_: s.set_trainer(self.tr_inc.get_value(), self.tr_every.get_value(),
                                                                self.tr_target.get_value()))
            self.trainer.add_row(w)
        self.trainer.connect("notify::enable-expansion",
                             lambda r, _p: s.set_trainer_enabled(r.get_enable_expansion()))
        g.add(self.trainer)
        left.append(g)

        # pitch
        g = Adw.PreferencesGroup(title="Pitch", description="Transpose without changing tempo")
        reset = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Reset pitch", css_classes=["flat"],
                           valign=Gtk.Align.CENTER)
        reset.connect("clicked", lambda *_: s.set_pitch(0, 0))
        g.set_header_suffix(reset)
        self.semis = Adw.SpinRow.new_with_range(-12, 12, 1)
        self.semis.set_title("Semitones")
        self.semis.set_subtitle("PgUp / PgDn")
        self.semis.connect("notify::value", lambda r, _p: self._updating or s.set_pitch(semis=r.get_value()))
        self.cents = Adw.SpinRow.new_with_range(-50, 50, 1)
        self.cents.set_title("Fine tune (cents)")
        self.cents.set_subtitle("Match a detuned recording")
        self.cents.connect("notify::value", lambda r, _p: self._updating or s.set_pitch(cents=r.get_value()))
        g.add(self.semis)
        g.add(self.cents)
        left.append(g)

        # loop
        g = Adw.PreferencesGroup(title="Loop")
        self.loop_switch = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text="Loop on/off (L)")
        self.loop_switch.connect("notify::active", lambda sw, _p: self._updating or s.set_loop_on(sw.get_active()))
        g.set_header_suffix(self.loop_switch)
        self.a_row = self._loop_row("Start  (A)", "a", "[")
        self.b_row = self._loop_row("End  (B)", "b", "]")
        g.add(self.a_row)
        g.add(self.b_row)
        self.loop_info = Adw.ActionRow(title="No loop yet", subtitle="Drag across the waveform, or press [ and ]")
        save = Gtk.Button(label="Save Section", valign=Gtk.Align.CENTER)
        save.connect("clicked", lambda *_: self.save_section())
        clear = Gtk.Button(icon_name="edit-clear-symbolic", tooltip_text="Clear loop", valign=Gtk.Align.CENTER,
                           css_classes=["flat"])
        clear.connect("clicked", lambda *_: s.clear_loop())
        self.loop_info.add_suffix(clear)
        self.loop_info.add_suffix(save)
        g.add(self.loop_info)
        right.append(g)

        self.sections = Adw.PreferencesGroup(title="Sections",
                                             description="Saved loops — verse, solo, that tricky bar…")
        self.sections_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.sections.add(self.sections_list)
        right.append(self.sections)

        clamp = Adw.Clamp(maximum_size=1200, child=self.columns)
        sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)
        outer.append(sw)
        return outer

    def _loop_row(self, title, which, key):
        row = Adw.ActionRow(title=title, subtitle="–")
        row.add_css_class("property")
        for icon, tip, d in (("go-previous-symbolic", "Nudge earlier (50 ms)", -0.05),
                             ("go-next-symbolic", "Nudge later (50 ms)", 0.05)):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER, css_classes=["flat", "circular"])
            b.connect("clicked", lambda _b, d=d: self.s.nudge(which, d))
            row.add_suffix(b)
        btn = Gtk.Button(label="Set at Playhead", tooltip_text=f"Shortcut: {key}", valign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_: self.s.mark(which))
        row.add_suffix(btn)
        return row

    def _build_volume(self):
        s = self.s
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6)
        self.vol_scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, 0, VOLUME_MAX, 1)
        self.vol_scale.set_inverted(True)
        self.vol_scale.set_draw_value(False)
        self.vol_scale.set_size_request(-1, 170)
        self.vol_scale.add_mark(100, Gtk.PositionType.RIGHT, "100")
        self.vol_scale.connect("value-changed", lambda sc: self._updating or s.set_volume(sc.get_value()))
        self.vol_label = Gtk.Label(css_classes=["caption", "time-label"], width_chars=4)
        self.mute_btn = Gtk.ToggleButton(icon_name="audio-volume-muted-symbolic", tooltip_text="Mute (M)",
                                         halign=Gtk.Align.CENTER, css_classes=["flat", "circular"])
        self.mute_btn.connect("toggled", lambda b: self._updating or s.set_muted(b.get_active()))
        box.append(self.vol_label)
        box.append(self.vol_scale)
        box.append(self.mute_btn)
        self.vol_btn = Gtk.MenuButton(popover=Gtk.Popover(child=box), css_classes=["flat"],
                                      valign=Gtk.Align.CENTER, tooltip_text="Volume (9 / 0, M to mute)")
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", lambda c, dx, dy: s.set_volume(s.volume - 5 * dy) or True)
        self.vol_btn.add_controller(scroll)
        self._sync_volume()
        return self.vol_btn

    # ── session → UI ──
    def _on_session(self, what):
        s = self.s
        if what == "song":
            self._song_changed()
        elif what == "peaks":
            self.wave.message = None if s.peaks else "Couldn't read waveform"
            self.wave.set_peaks(s.peaks, s.duration)
            self.wave.set_pos(s.pos)
        elif what == "playing":
            self._playing_changed()
        elif what == "position":
            self._show_pos(s.pos)
        elif what == "duration":
            self.dur_label.set_label(fmt_time(s.duration))
            self.wave.set_duration(s.duration)
        elif what in ("speed", "pitch"):
            self._sync_controls()
            self._refresh_current_row()
        elif what == "loop":
            self._sync_controls()
        elif what == "trainer":
            self.trainer.set_subtitle(s.trainer_status())
        elif what == "volume":
            self._sync_volume()
        elif what == "sections":
            self._rebuild_sections()
        elif what == "library":
            self.rebuild_list()

    def _song_changed(self):
        s = self.s
        song = s.song
        if song:
            self.wtitle.set_title(song["title"])
            self.wtitle.set_subtitle(song.get("artist") or "")
            self.dur_label.set_label(fmt_time(s.duration))
            self.content_stack.set_visible_child_name("player")
            self.wave.message = "Reading waveform…"
            self.wave.set_peaks(None, s.duration)
            self.wave.set_pos(0)
            self._show_pos(0)
            self._sync_controls()
        else:
            self.content_stack.set_visible_child_name("empty")
            self.wtitle.set_title(APP_NAME)
            self.wtitle.set_subtitle("")
        self._rebuild_sections()
        self._update_song_actions()
        self.rebuild_list()

    def _playing_changed(self):
        playing = self.s.playing
        self.play_btn.set_icon_name("media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
        if playing and self._tick_id is None:
            self._tick_id = self.wave.add_tick_callback(self._tick)
        elif not playing and self._tick_id is not None:
            self.wave.remove_tick_callback(self._tick_id)
            self._tick_id = None
            self._show_pos(self.s.pos)

    def _tick(self, widget, clock):
        self._show_pos(self.s.current_pos())
        return GLib.SOURCE_CONTINUE

    def _show_pos(self, p):
        self.wave.set_pos(p)
        txt = fmt_time(p, precise=True)
        if self.pos_label.get_label() != txt:
            self.pos_label.set_label(txt)

    def _sync_controls(self):
        s = self.s
        song = s.song
        if not song:
            return
        self._updating = True
        try:
            self.speed_scale.set_value(song["speed"])
            self.speed_label.set_label(f"{song['speed']}%")
            self.semis.set_value(song["semis"])
            self.cents.set_value(song["cents"])
            lp = song["loop"]
            self.loop_switch.set_active(lp["on"])
            self.loop_toggle.set_active(lp["on"])
            self.a_row.set_subtitle(fmt_time(lp["a"], True))
            self.b_row.set_subtitle(fmt_time(lp["b"], True))
            if s.has_loop():
                self.loop_info.set_title(f"{lp['b'] - lp['a']:.2f} s loop")
                self.loop_info.set_subtitle("Looping" if lp["on"] else "Loop is off — press L")
            else:
                self.loop_info.set_title("No loop yet")
                self.loop_info.set_subtitle("Drag across the waveform, or press [ and ]")
            self.wave.set_loop(lp["a"], lp["b"], lp["on"])
            self.trainer.set_subtitle(s.trainer_status())
        finally:
            self._updating = False

    def _sync_volume(self):
        v, muted = self.s.volume, self.s.muted
        level = "muted" if muted or v == 0 else "low" if v < 34 else "medium" if v < 67 else "high"
        self.vol_btn.set_icon_name(f"audio-volume-{level}-symbolic")
        self._updating = True
        try:
            self.vol_scale.set_value(v)
            self.mute_btn.set_active(muted)
        finally:
            self._updating = False
        self.vol_label.set_label("Muted" if muted else f"{v}%")

    # ── actions & keys ──
    def _setup_actions(self):
        for name, cb in (("youtube", self.open_youtube), ("open", self.open_files), ("export", self.export),
                         ("remove", self.remove_song), ("reveal", self.reveal), ("shortcuts", self.show_shortcuts),
                         ("search", lambda: self.search.grab_focus())):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, cb=cb: cb())
            self.add_action(act)
        app = self.app
        app.set_accels_for_action("win.youtube", ["<Control>y", "<Control>d"])
        app.set_accels_for_action("win.open", ["<Control>o"])
        app.set_accels_for_action("win.export", ["<Control>e"])
        app.set_accels_for_action("win.search", ["<Control>f"])
        app.set_accels_for_action("win.shortcuts", ["<Control>question", "<Control><Shift>question",
                                                   "<Control><Shift>slash"])
        self._update_song_actions()

    def _update_song_actions(self):
        for n in ("export", "remove", "reveal"):
            self.lookup_action(n).set_enabled(self.song is not None)

    def _on_key(self, ctrl, keyval, keycode, state):
        if self.get_visible_dialog() or not self.song:
            return False
        focus = self.get_focus()
        if isinstance(focus, (Gtk.Text, Gtk.Editable)):
            return False
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        k = Gdk.keyval_name(Gdk.keyval_to_lower(keyval))
        s = self.s
        actions = {
            "space": s.toggle_play,
            "Left": lambda: s.seek_rel(-1 if shift else -5),
            "Right": lambda: s.seek_rel(1 if shift else 5),
            "Up": lambda: s.set_speed(s.speed + (1 if shift else 5)),
            "Down": lambda: s.set_speed(s.speed - (1 if shift else 5)),
            "Page_Up": lambda: s.shift_pitch(1),
            "Page_Down": lambda: s.shift_pitch(-1),
            "bracketleft": lambda: s.mark("a"),
            "bracketright": lambda: s.mark("b"),
            "braceleft": lambda: s.mark("a"),
            "braceright": lambda: s.mark("b"),
            "l": s.toggle_loop,
            "Home": s.go_start,
            "r": s.reset_speed_pitch,
            "s": self.save_section,
            "z": lambda: self.wave.zoom_fit() if shift else self.zoom_loop(),
            "plus": lambda: self.wave.zoom(1 / 1.5),
            "equal": lambda: self.wave.zoom(1 / 1.5),
            "minus": lambda: self.wave.zoom(1.5),
            "m": s.toggle_mute,
            "9": lambda: s.set_volume(s.volume - 5),
            "0": lambda: s.set_volume(s.volume + 5),
        }
        fn = actions.get(k)
        if fn:
            fn()
            return True
        return False

    def zoom_loop(self):
        if self.s.has_loop():
            self.wave.zoom_to(self.s.loop["a"], self.s.loop["b"])

    # ── library list ──
    def rebuild_list(self):
        self.listbox.remove_all()
        for job in self.downloads.jobs:
            self.listbox.append(self._job_row(job))
        for song in self.lib.sorted_songs():
            row = self._song_row(song)
            self.listbox.append(row)
            if self.song and song["id"] == self.song["id"]:
                self.listbox.select_row(row)
        self.side_stack.set_visible_child_name("list" if self.downloads.jobs or self.lib.songs else "empty")

    def _song_row(self, song):
        row = Gtk.ListBoxRow()
        row.song_id = song["id"]
        box = Gtk.Box(spacing=10, margin_top=4, margin_bottom=4)
        tex = None
        if song.get("thumb") and os.path.exists(song["thumb"]):
            try:
                tex = Gdk.Texture.new_from_filename(song["thumb"])
            except GLib.Error:
                pass
        if tex:
            box.append(Thumb(64, 36, tex))
        else:
            frame = Gtk.Box(width_request=64, height_request=36, css_classes=["thumb", "card"],
                            valign=Gtk.Align.CENTER)
            frame.append(Gtk.Image(icon_name="audio-x-generic-symbolic", pixel_size=18, hexpand=True,
                                   css_classes=["dim-label"]))
            box.append(frame)
        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        t = Gtk.Label(label=song["title"], xalign=0, ellipsize=Pango.EllipsizeMode.END, tooltip_text=song["title"])
        row.sub_label = Gtk.Label(label=song_subtitle(song), xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                  css_classes=["caption", "dim-label"])
        txt.append(t)
        txt.append(row.sub_label)
        box.append(txt)
        row.set_child(box)
        return row

    def _job_row(self, job):
        row = Gtk.ListBoxRow(activatable=False, selectable=False)
        row.song_id = None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=6, margin_bottom=6)
        box.append(Gtk.Label(label=job["title"], xalign=0, ellipsize=Pango.EllipsizeMode.END))
        job["bar"] = Gtk.ProgressBar(fraction=job["progress"])
        job["label"] = Gtk.Label(label=job["status"], xalign=0, css_classes=["caption", "dim-label"])
        box.append(job["bar"])
        box.append(job["label"])
        row.set_child(box)
        return row

    def _filter_row(self, row):
        q = self.search.get_text().strip().lower()
        if not q or row.song_id is None:
            return True
        song = self.lib.get(row.song_id)
        return bool(song) and q in f"{song['title']} {song.get('artist') or ''}".lower()

    def _refresh_current_row(self):
        if not self.song:
            return
        row = self.listbox.get_first_child()
        while row:
            if getattr(row, "song_id", None) == self.song["id"]:
                row.sub_label.set_label(song_subtitle(self.song))
            row = row.get_next_sibling()

    def _on_row_activated(self, lb, row):
        song = self.lib.get(row.song_id) if row.song_id else None
        if song and self.s.load(song):
            self.split.set_show_content(True)

    # ── adding songs ──
    def open_files(self):
        dlg = Gtk.FileDialog(title="Open Audio")
        flt = Gtk.FileFilter(name="Audio and video")
        flt.add_mime_type("audio/*")
        flt.add_mime_type("video/*")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        dlg.set_filters(store)

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            self.add_files([files.get_item(i).get_path() for i in range(files.get_n_items())])

        dlg.open_multiple(self, None, done)

    def add_files(self, file_paths):
        def done(items, err):
            songs = self.lib.add_scanned(items or [])
            self.rebuild_list()
            if songs:
                self.s.load(songs[0])
            else:
                self.toast("Those files don't look like audio")

        run_async(gtk_dispatch, media.scan_files, done, file_paths, lambda p: self.lib.find(path=p))

    def open_youtube(self):
        YouTubeDialog(self).present(self)

    def start_download(self, result):
        existing = self.lib.find(yt=result.get("id"))
        if existing:
            self.toast("Already in your library")
            self.s.load(existing)
            return
        if self.downloads.start(result):
            self.rebuild_list()

    def _job_update(self, job):
        if job.get("bar"):
            job["bar"].set_fraction(job["progress"])
        if job.get("label"):
            job["label"].set_label(job["status"])

    def _download_done(self, job, song, error):
        self.rebuild_list()
        if not song:
            self.toast(f"Download failed: {error}", timeout=8)
            return
        toast = Adw.Toast(title=GLib.markup_escape_text(f"Downloaded “{song['title']}”"),
                          button_label="Practice", timeout=6)
        toast.connect("button-clicked", lambda *_: self.s.load(song))
        self.toasts.add_toast(toast)
        if not self.song:
            self.s.load(song)

    # ── sections ──
    def save_section(self):
        s = self.s
        if not s.has_loop():
            self.toast("Mark a loop first")
            return
        a, b = s.loop["a"], s.loop["b"]
        self._ask_name("Save Section", f"{fmt_time(a, True)} – {fmt_time(b, True)}", s.next_section_name(),
                       "Save", lambda name: s.add_section(name, a, b))

    def rename_section(self, sec):
        self._ask_name("Rename Section", f"{fmt_time(sec['a'], True)} – {fmt_time(sec['b'], True)}",
                       sec["name"], "Rename", lambda name: self.s.rename_section(sec, name))

    def _ask_name(self, heading, body, initial, verb, on_done):
        dlg = Adw.AlertDialog(heading=heading, body=body)
        entry = Gtk.Entry(text=initial, activates_default=True)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", verb)
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("ok")
        dlg.set_close_response("cancel")
        dlg.connect("response", lambda d, r: r == "ok" and on_done(entry.get_text().strip()))
        dlg.present(self)
        entry.grab_focus()  # selects the text, so typing replaces the old name

    def _rebuild_sections(self):
        self.sections_list.remove_all()
        secs = self.s.sections
        self.sections_list.set_visible(bool(secs))
        for sec in secs:
            row = Adw.ActionRow(use_markup=False, activatable=True)
            row.set_title(sec["name"])
            row.set_subtitle(f"{fmt_time(sec['a'], True)} – {fmt_time(sec['b'], True)}  "
                             f"({sec['b'] - sec['a']:.1f}s)")
            row.add_prefix(Gtk.Image(icon_name="media-playlist-repeat-symbolic"))
            ed = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Rename section",
                            valign=Gtk.Align.CENTER, css_classes=["flat", "circular"])
            ed.connect("clicked", lambda _b, sec=sec: self.rename_section(sec))
            row.add_suffix(ed)
            rm = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete section",
                            valign=Gtk.Align.CENTER, css_classes=["flat", "circular"])
            rm.connect("clicked", lambda _b, sec=sec: self.s.delete_section(sec))
            row.add_suffix(rm)
            row.connect("activated", lambda _r, sec=sec: self.s.recall_section(sec))
            self.sections_list.append(row)

    # ── song menu ──
    def export(self):
        song = self.song
        if not song:
            return
        lp = song["loop"]

        def pick_file(region):
            fd = Gtk.FileDialog(title="Export Audio", initial_name=media.export_name(song, region))
            fd.set_initial_folder(Gio.File.new_for_path(str(self.lib.music_dir)))

            def done(d, res):
                try:
                    out = d.save_finish(res).get_path()
                except GLib.Error:
                    return
                self.toast("Exporting…", timeout=2)
                run_async(gtk_dispatch, media.export_audio,
                          lambda _r, err: self.toast(f"Export failed: {err}" if err
                                                     else f"Exported {os.path.basename(out)}"),
                          song, out, region)

            fd.save(self, None, done)

        if not self.s.has_loop():
            pick_file(None)
            return
        dlg = Adw.AlertDialog(heading="Export Audio",
                              body="Speed and pitch changes are rendered in. Export the whole song or just the loop?")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("song", "Whole Song")
        dlg.add_response("loop", "Loop Only")
        dlg.set_response_appearance("loop", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_close_response("cancel")
        dlg.connect("response", lambda d, r: r != "cancel" and pick_file((lp["a"], lp["b"]) if r == "loop" else None))
        dlg.present(self)

    def reveal(self):
        if self.song:
            Gtk.FileLauncher.new(Gio.File.new_for_path(self.song["path"])).open_containing_folder(self, None, None)

    def remove_song(self):
        song = self.song
        if not song:
            return
        dlg = Adw.AlertDialog(heading=f"Remove “{song['title']}”?",
                              body="Saved loops, sections and settings for this song will be lost.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("remove", "Remove")
        if self.lib.owns(song["path"]):
            dlg.add_response("delete", "Remove & Delete File")
            dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_close_response("cancel")
        dlg.connect("response", lambda d, r: r != "cancel" and self.s.remove_song(song, delete_file=(r == "delete")))
        dlg.present(self)

    def show_shortcuts(self):
        dlg = Adw.ShortcutsDialog()
        groups = {
            "Playback": [("Play / pause", "space"), ("Seek ±5 s", "Left Right"), ("Seek ±1 s", "<Shift>Left <Shift>Right"),
                         ("Back to loop start", "Home")],
            "Speed & Pitch": [("Speed ±5%", "Up Down"), ("Speed ±1%", "<Shift>Up <Shift>Down"),
                              ("Pitch ±1 semitone", "Page_Up Page_Down"), ("Reset speed and pitch", "r")],
            "Looping": [("Set loop start", "bracketleft"), ("Set loop end", "bracketright"), ("Toggle loop", "l"),
                        ("Save section", "s"), ("Zoom to loop", "z"), ("Show whole song", "<Shift>z"),
                        ("Zoom in / out", "plus minus")],
            "Volume": [("Volume down / up", "9 0"), ("Mute", "m")],
            "Library": [("Get from YouTube", "<Control>y"), ("Open file", "<Control>o"),
                        ("Export audio", "<Control>e"), ("Filter library", "<Control>f")],
        }
        for title, items in groups.items():
            sec = Adw.ShortcutsSection(title=title)
            for label, accel in items:
                sec.add(Adw.ShortcutsItem(title=label, accelerator=accel))
            dlg.add(sec)
        dlg.present(self)

    # ── persistence & misc ──
    def schedule_save(self):
        if self._save_src:
            GLib.source_remove(self._save_src)
        self._save_src = GLib.timeout_add(600, self.flush_save)

    def flush_save(self):
        if self._save_src:
            GLib.source_remove(self._save_src)
            self._save_src = None
        try:
            self.lib.save()
        except OSError as e:
            self.toast(f"Couldn't save library: {e}")
        return False

    def toast(self, msg, timeout=4):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(msg), timeout=timeout))

    def _on_close(self, *_):
        self.flush_save()
        self.s.close()
        return False


# ───────────────────────────── application ─────────────────────────────

class RiffApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.lib = None
        self.palette = {}
        self.css = Gtk.CssProvider()
        self._theme_src = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)
        self.lib = Library()
        base = Gtk.CssProvider()
        base.load_from_string(BASE_CSS)
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(display, base, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.StyleContext.add_provider_for_display(display, self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.apply_theme()
        if THEME_STATE.exists():
            self._monitor = Gio.File.new_for_path(str(THEME_STATE)).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES)
            self._monitor.connect("changed", self._theme_changed)

        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", lambda *_: Adw.AboutDialog(
            application_name=APP_NAME, application_icon=APP_ID, version=VERSION,
            comments="Practice tool for musicians: slow down, transpose and loop any song — "
                     "including ones pulled straight from YouTube.",
            developer_name="Joson Mathew", license_type=Gtk.License.MIT_X11).present(self.props.active_window))
        self.add_action(about)
        quit_ = Gio.SimpleAction.new("quit", None)
        quit_.connect("activate", lambda *_: self.props.active_window and self.props.active_window.close())
        self.add_action(quit_)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def _theme_changed(self, *_):
        if self._theme_src:
            GLib.source_remove(self._theme_src)
        self._theme_src = GLib.timeout_add(400, self.apply_theme)

    def apply_theme(self):
        self._theme_src = None
        self.palette = read_palette()
        self.css.load_from_string(palette_css(self.palette))
        mode = self.palette.get("mode") or self.palette.get("theme_type")
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.FORCE_LIGHT if mode == "light" else
            Adw.ColorScheme.FORCE_DARK if mode == "dark" else Adw.ColorScheme.DEFAULT)
        for w in self.get_windows():
            if isinstance(w, RiffWindow):
                w.wave.set_palette(self.palette)
        return False

    def do_activate(self):
        win = self.props.active_window or RiffWindow(self)
        win.present()

    def do_open(self, files, n, hint):
        self.do_activate()
        self.props.active_window.add_files([f.get_path() for f in files])


def main():
    missing = paths.missing_tools()
    if missing:
        pkgs = sorted({"ffprobe": "ffmpeg"}.get(t, t) for t in missing)
        print(f"{APP_NAME}: missing {', '.join(missing)} — install with: sudo pacman -S {' '.join(pkgs)}",
              file=sys.stderr)
        return 1
    return RiffApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
