#!/usr/bin/env python3
"""Riffarchy — Qt (PySide6) front end for macOS, Windows and Linux.

Same features as the GTK/Omarchy app; all playback, library and download logic
comes from ``riffcore``. On Linux it picks up the Omarchy palette when present,
elsewhere it follows the system light/dark theme and accent colour.
"""

import os
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QFont, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPalette,
                           QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import (QAbstractButton, QApplication, QBoxLayout, QDialog, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
                               QSplitter, QStackedWidget, QStyle, QToolButton, QVBoxLayout, QWidget, QWidgetAction)

from riffcore import (APP_NAME, SPEED_MAX, SPEED_MIN, SPEED_PRESETS, VERSION, VOLUME_MAX, AUDIO_EXTS, Downloader,
                      Library, Session, WaveView, fmt_time, run_async, song_subtitle)
from riffcore import media, paths, youtube
from riffcore.theme import read_palette, wave_colors

APP_ID = "app.riffarchy.Riffarchy"
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_ICON = HERE / "data" / f"{APP_ID}.svg"
IS_MAC = sys.platform == "darwin"
MOD = "⌘" if IS_MAC else "Ctrl+"
SHORTCUTS_HINT = "⇧⌘?" if IS_MAC else "Ctrl+Shift+?"


class Dispatcher(QObject):
    """riffcore's dispatch: run ``fn(*args)`` on the Qt main thread (safe to call from any thread)."""
    _call = Signal(object, object)

    def __init__(self):
        super().__init__()
        self._call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, fn, args):
        fn(*args)

    def __call__(self, fn, *args):
        self._call.emit(fn, args)


def qcolor(rgba):
    r, g, b, a = rgba
    return QColor.fromRgbF(r, g, b, a)


def hexcolor(c):
    return c.name(QColor.NameFormat.HexRgb)


# ───────────────────────────── theme ─────────────────────────────

class Theme:
    """Palette + stylesheet. Omarchy palette on Linux when available, else the OS theme."""

    def __init__(self):
        self.omarchy = {}
        self.colors = {}
        self.wave = wave_colors({})

    def apply(self, app):
        self.omarchy = read_palette() if sys.platform.startswith("linux") else {}
        p = self.omarchy
        if p.get("background") and p.get("accent"):
            app.setStyle("Fusion")
            app.setPalette(self._omarchy_qpalette(p))
        elif sys.platform.startswith("linux"):
            app.setStyle("Fusion")
        qp = app.palette()
        window, text = qp.color(QPalette.ColorRole.Window), qp.color(QPalette.ColorRole.WindowText)
        accent = qp.color(QPalette.ColorRole.Accent) if hasattr(QPalette.ColorRole, "Accent") else qp.color(
            QPalette.ColorRole.Highlight)
        dark = window.lightnessF() < 0.5
        mix = lambda a, b, t: QColor.fromRgbF(*(a.getRgbF()[i] * (1 - t) + b.getRgbF()[i] * t for i in range(3)))  # noqa: E731
        c = self.colors = {
            "window": window, "text": text, "accent": accent,
            "accent_fg": QColor(p["background"]) if p.get("background") else (
                QColor("#ffffff") if accent.lightnessF() < 0.6 else QColor("#000000")),
            "card": mix(window, text, 0.06 if dark else 0.035),
            "button": mix(window, text, 0.10 if dark else 0.07),
            "button_hover": mix(window, text, 0.16 if dark else 0.11),
            "sidebar": QColor(p["dark_background"]) if p.get("dark_background") else mix(window, QColor("#000000"),
                                                                                       0.18 if dark else 0.03),
            "dim": mix(window, text, 0.62),
            "sep": mix(window, text, 0.10),
            "selection": mix(window, text, 0.13),
            "toast": mix(window, text, 0.82) if not dark else mix(window, text, 0.22),
            "toast_fg": window if not dark else text,
        }
        if p:
            self.wave = wave_colors(p)
        else:
            self.wave = wave_colors({
                "darker_background": "#141416" if dark else hexcolor(mix(window, text, 0.05)),
                "muted": hexcolor(mix(window, text, 0.45)),
                "accent": hexcolor(accent),
                "foreground": hexcolor(text),
                "bright_foreground": hexcolor(text),
                "dark_foreground": hexcolor(mix(window, text, 0.55)),
            })
        app.setStyleSheet(self._stylesheet(c))
        icons.color = text
        icons.accent_fg = c["accent_fg"]

    @staticmethod
    def _omarchy_qpalette(p):
        g = lambda *keys: QColor(next(p[k] for k in keys if k in p))  # noqa: E731
        bg, fg = g("background"), g("foreground")
        qp = QPalette()
        roles = {
            QPalette.ColorRole.Window: bg, QPalette.ColorRole.WindowText: fg,
            QPalette.ColorRole.Base: g("dark_background", "background"),
            QPalette.ColorRole.AlternateBase: g("lighter_background", "background"),
            QPalette.ColorRole.Text: fg, QPalette.ColorRole.Button: g("lighter_background", "background"),
            QPalette.ColorRole.ButtonText: fg, QPalette.ColorRole.BrightText: g("bright_foreground", "foreground"),
            QPalette.ColorRole.Highlight: g("accent"), QPalette.ColorRole.HighlightedText: bg,
            QPalette.ColorRole.ToolTipBase: g("lighter_background", "background"),
            QPalette.ColorRole.ToolTipText: fg, QPalette.ColorRole.PlaceholderText: g("muted", "foreground"),
            QPalette.ColorRole.Link: g("accent"),
        }
        if hasattr(QPalette.ColorRole, "Accent"):
            roles[QPalette.ColorRole.Accent] = g("accent")
        for role, col in roles.items():
            qp.setColor(role, col)
        qp.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, g("muted", "foreground"))
        qp.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, g("muted", "foreground"))
        qp.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, g("muted", "foreground"))
        return qp

    @staticmethod
    def _stylesheet(c):
        h = {k: hexcolor(v) for k, v in c.items()}
        return f"""
QWidget#sidebar {{ background: {h['sidebar']}; }}
QListWidget#library {{ background: transparent; border: none; outline: none; }}
QListWidget#library::item {{ border-radius: 8px; margin: 1px 6px; }}
QListWidget#library::item:selected {{ background: {h['selection']}; }}
QListWidget#results {{ background: transparent; border: none; outline: none; }}
QListWidget#results::item {{ background: {h['card']}; border-radius: 10px; margin: 3px 0; }}
QFrame#card {{ background: {h['card']}; border-radius: 12px; }}
QFrame#sep {{ background: {h['sep']}; border: none; min-height: 1px; max-height: 1px; }}
QLabel[role="dim"] {{ color: {h['dim']}; }}
QLabel[role="title"] {{ font-weight: 600; }}
QLabel[role="heading"] {{ font-weight: 700; }}
QLabel#bigvalue {{ font-weight: 800; }}
QPushButton, QToolButton {{ background: {h['button']}; color: {h['text']}; border: none; border-radius: 8px;
    padding: 6px 12px; }}
QPushButton:hover, QToolButton:hover {{ background: {h['button_hover']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {h['dim']}; }}
QPushButton[flat="true"], QToolButton[flat="true"] {{ background: transparent; }}
QPushButton[flat="true"]:hover, QToolButton[flat="true"]:hover {{ background: {h['button']}; }}
QPushButton[flat="true"]:checked, QToolButton[flat="true"]:checked {{ background: {h['button_hover']}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QPushButton#play {{ background: {h['accent']}; color: {h['accent_fg']}; border-radius: 28px; padding: 0; }}
QPushButton#play:hover {{ background: {h['accent']}; }}
QPushButton#suggested {{ background: {h['accent']}; color: {h['accent_fg']}; font-weight: 600; }}
QPushButton#suggested:hover {{ background: {h['accent']}; }}
QPushButton#preset {{ padding: 4px 8px; border-radius: 6px; font-weight: 600; }}
QFrame#toast {{ background: {h['toast']}; border-radius: 20px; }}
QFrame#toast QLabel {{ color: {h['toast_fg']}; font-weight: 600; }}
QFrame#toast QPushButton {{ background: transparent; color: {h['toast_fg']}; font-weight: 700; }}
QLineEdit {{ border-radius: 8px; padding: 6px 8px; border: 1px solid {h['sep']}; background: {h['card']}; }}
QLineEdit:focus {{ border: 1px solid {h['accent']}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QProgressBar {{ border: none; background: {h['sep']}; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {h['accent']}; border-radius: 3px; }}
"""


theme = Theme()


# ───────────────────────────── icons (drawn, so they look the same everywhere) ─────────────────────────────

class Icons:
    def __init__(self):
        self.color = QColor("#dddddd")
        self.accent_fg = QColor("#000000")

    def get(self, name, color=None, size=24):
        dpr = 3
        pm = QPixmap(size * dpr, size * dpr)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(size / 24, size / 24)
        col = color or self.color
        pen = QPen(col, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        getattr(self, "_" + name)(p, col)
        p.end()
        return QIcon(pm)

    @staticmethod
    def _fill(p, col, *pts):
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
        p.restore()

    def _play(self, p, c):
        self._fill(p, c, (8, 5), (19, 12), (8, 19))

    def _pause(self, p, c):
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(6, 5, 4, 14), 1, 1)
        p.drawRoundedRect(QRectF(14, 5, 4, 14), 1, 1)
        p.restore()

    def _skip_back(self, p, c):
        p.drawLine(QPointF(6, 6), QPointF(6, 18))
        self._fill(p, c, (18, 6), (9, 12), (18, 18))

    def _seek_back(self, p, c):
        self._fill(p, c, (12, 6), (4, 12), (12, 18))
        self._fill(p, c, (20, 6), (12, 12), (20, 18))

    def _seek_fwd(self, p, c):
        self._fill(p, c, (4, 6), (12, 12), (4, 18))
        self._fill(p, c, (12, 6), (20, 12), (12, 18))

    def _repeat(self, p, c):
        path = QPainterPath(QPointF(4, 13))
        path.lineTo(4, 10)
        path.quadTo(4, 7, 7, 7)
        path.lineTo(18, 7)
        path.moveTo(20, 11)
        path.lineTo(20, 14)
        path.quadTo(20, 17, 17, 17)
        path.lineTo(6, 17)
        p.drawPath(path)
        p.drawPolyline(QPolygonF([QPointF(15, 4), QPointF(18, 7), QPointF(15, 10)]))
        p.drawPolyline(QPolygonF([QPointF(9, 14), QPointF(6, 17), QPointF(9, 20)]))

    def _speaker(self, p, c):
        self._fill(p, c, (3, 9), (7, 9), (12, 5), (12, 19), (7, 15), (3, 15))

    def _vol_muted(self, p, c):
        self._speaker(p, c)
        p.drawLine(QPointF(16, 9), QPointF(21, 15))
        p.drawLine(QPointF(21, 9), QPointF(16, 15))

    def _vol_low(self, p, c):
        self._speaker(p, c)
        p.drawArc(QRectF(10, 8.5, 6, 7), -60 * 16, 120 * 16)

    def _vol_medium(self, p, c):
        self._vol_low(p, c)
        p.drawArc(QRectF(9, 5.5, 10, 13), -60 * 16, 120 * 16)

    def _vol_high(self, p, c):
        self._vol_medium(p, c)
        p.drawArc(QRectF(8, 2.5, 14, 19), -60 * 16, 120 * 16)

    def _zoom_in(self, p, c):
        p.drawEllipse(QPointF(10.5, 10.5), 6, 6)
        p.drawLine(QPointF(15, 15), QPointF(20, 20))
        p.drawLine(QPointF(8, 10.5), QPointF(13, 10.5))
        p.drawLine(QPointF(10.5, 8), QPointF(10.5, 13))

    def _zoom_fit(self, p, c):
        for pts in (((4, 9), (4, 4), (9, 4)), ((15, 4), (20, 4), (20, 9)), ((20, 15), (20, 20), (15, 20)),
                    ((9, 20), (4, 20), (4, 15))):
            p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in pts]))

    def _undo(self, p, c):
        p.drawPolyline(QPolygonF([QPointF(9, 5), QPointF(5, 9), QPointF(9, 13)]))
        path = QPainterPath(QPointF(5, 9))
        path.lineTo(14, 9)
        path.quadTo(20, 9, 20, 14)
        path.quadTo(20, 19, 14, 19)
        path.lineTo(10, 19)
        p.drawPath(path)

    def _clear(self, p, c):
        p.drawPolygon(QPolygonF([QPointF(8, 5), QPointF(20, 5), QPointF(20, 19), QPointF(8, 19), QPointF(3, 12)]))
        p.drawLine(QPointF(11, 9), QPointF(16, 15))
        p.drawLine(QPointF(16, 9), QPointF(11, 15))

    def _prev(self, p, c):
        p.drawPolyline(QPolygonF([QPointF(15, 5), QPointF(8, 12), QPointF(15, 19)]))

    def _next(self, p, c):
        p.drawPolyline(QPolygonF([QPointF(9, 5), QPointF(16, 12), QPointF(9, 19)]))

    def _edit(self, p, c):
        p.drawPolygon(QPolygonF([QPointF(15, 5), QPointF(19, 9), QPointF(9, 19), QPointF(5, 19), QPointF(5, 15)]))
        p.drawLine(QPointF(13, 7), QPointF(17, 11))

    def _trash(self, p, c):
        p.drawLine(QPointF(4, 7), QPointF(20, 7))
        p.drawLine(QPointF(10, 4), QPointF(14, 4))
        p.drawRoundedRect(QRectF(6.5, 7, 11, 13), 2, 2)
        p.drawLine(QPointF(10, 11), QPointF(10, 16))
        p.drawLine(QPointF(14, 11), QPointF(14, 16))

    def _download(self, p, c):
        p.drawLine(QPointF(12, 4), QPointF(12, 15))
        p.drawPolyline(QPolygonF([QPointF(7, 10), QPointF(12, 15), QPointF(17, 10)]))
        p.drawLine(QPointF(5, 20), QPointF(19, 20))

    def _open(self, p, c):
        path = QPainterPath(QPointF(3, 18))
        path.lineTo(3, 6)
        path.lineTo(9, 6)
        path.lineTo(11, 8)
        path.lineTo(20, 8)
        path.lineTo(20, 18)
        path.closeSubpath()
        p.drawPath(path)

    def _menu(self, p, c):
        for y in (7, 12, 17):
            p.drawLine(QPointF(5, y), QPointF(19, y))

    def _note(self, p, c):
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(8.5, 17), 3.5, 3)
        p.drawEllipse(QPointF(17.5, 15), 3.5, 3)
        p.restore()
        p.drawLine(QPointF(11.5, 17), QPointF(11.5, 5))
        p.drawLine(QPointF(20.5, 15), QPointF(20.5, 3.5))
        p.drawLine(QPointF(11.5, 5), QPointF(20.5, 3.5))

    def _check(self, p, c):
        p.drawPolyline(QPolygonF([QPointF(5, 12), QPointF(10, 17), QPointF(19, 7)]))


icons = Icons()


# ───────────────────────────── small widgets ─────────────────────────────

def label(text="", role=None, wrap=False, align=None, point_scale=None, bold=False):
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    if align is not None:
        lb.setAlignment(align)
    if point_scale or bold:
        f = lb.font()
        if point_scale:
            f.setPointSizeF(f.pointSizeF() * point_scale)
        if bold:
            f.setBold(True)
        lb.setFont(f)
    return lb


def tnum(widget):
    """Tabular (fixed-width) digits so times don't jitter."""
    f = widget.font()
    try:
        f.setFeature(QFont.Tag("tnum"), 1)
    except (AttributeError, TypeError):
        pass
    widget.setFont(f)
    return widget


def icon_button(icon_name, tip, cb, flat=True, size=36, checkable=False):
    b = QPushButton()
    b.setProperty("flat", flat)
    b.icon_name = icon_name
    b.setIcon(icons.get(icon_name))
    b.setIconSize(QSize(18, 18))
    b.setFixedSize(size, size)
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    if cb:
        b.clicked.connect(lambda *_: cb())
    return b


def text_button(text, cb, flat=False, object_name=None, tip=None):
    b = QPushButton(text)
    b.setProperty("flat", flat)
    if object_name:
        b.setObjectName(object_name)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    if cb:
        b.clicked.connect(lambda *_: cb())
    return b


def cover_pixmap(image, w, h, radius=6, dpr=2.0):
    """Rounded, centre-cropped thumbnail."""
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.setClipPath(path)
    if image is not None and not image.isNull():
        k = max(w / image.width(), h / image.height())
        dw, dh = image.width() * k, image.height() * k
        p.drawImage(QRectF((w - dw) / 2, (h - dh) / 2, dw, dh), image)
    else:
        p.fillRect(QRectF(0, 0, w, h), QColor(128, 128, 128, 30))
    p.end()
    return pm


class ElidedLabel(QLabel):
    """Single-line label that ends in … instead of clipping."""

    def __init__(self, text, role=None):
        super().__init__(text)
        if role:
            self.setProperty("role", role)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(10)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)


class Switch(QAbstractButton):
    """A small iOS/Adwaita-style toggle."""

    def __init__(self, tip=""):
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def sizeHint(self):
        return QSize(46, 26)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme.colors
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c.get("accent", QColor("#3584e4")) if self.isChecked() else c.get("button_hover", QColor("#555")))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.right() - d - 3 if self.isChecked() else r.left() + 3
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


class Card(QFrame):
    """Rounded group of rows with separators, like an Adwaita boxed list."""

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.rows = []

    def add(self, widget):
        if self.rows:
            sep = QFrame()
            sep.setObjectName("sep")
            self.lay.addWidget(sep)
        self.rows.append(widget)
        self.lay.addWidget(widget)
        return widget

    def clear(self):
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.rows = []


class Row(QWidget):
    """Title/subtitle on the left, widgets on the right."""
    clicked = Signal()

    def __init__(self, title, subtitle=None, prefix=None, suffixes=(), clickable=False):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 10)
        lay.setSpacing(8)
        if prefix:
            lay.addWidget(prefix)
        txt = QVBoxLayout()
        txt.setSpacing(1)
        self.title = label(title)
        self.subtitle = label(subtitle or "", role="dim")
        f = self.subtitle.font()
        f.setPointSizeF(f.pointSizeF() * 0.9)
        self.subtitle.setFont(f)
        tnum(self.subtitle)
        self.subtitle.setVisible(bool(subtitle))
        txt.addWidget(self.title)
        txt.addWidget(self.subtitle)
        lay.addLayout(txt, 1)
        for s in suffixes:
            lay.addWidget(s)
        self.clickable = clickable
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_subtitle(self, text):
        self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text))

    def mouseReleaseEvent(self, e):
        if self.clickable and e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class Group(QWidget):
    """Heading + optional description and header widget, then a Card."""

    def __init__(self, title, description=None, header=None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        top = QHBoxLayout()
        txt = QVBoxLayout()
        txt.setSpacing(2)
        txt.addWidget(label(title, role="heading"))
        if description:
            txt.addWidget(label(description, role="dim", wrap=True))
        top.addLayout(txt, 1)
        if header:
            top.addWidget(header, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(top)
        self.card = Card()
        lay.addWidget(self.card)


class Toast(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("toast")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 8, 8, 8)
        lay.setSpacing(10)
        self.text = QLabel()
        self.action = QPushButton()
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        close = QPushButton("✕")
        close.setFixedWidth(30)
        close.clicked.connect(self.hide)
        lay.addWidget(self.text)
        lay.addWidget(self.action)
        lay.addWidget(close)
        self.timer = QTimer(self, singleShot=True, timeout=self.hide)
        self._cb = None
        self.action.clicked.connect(self._act)
        self.hide()

    def show_message(self, msg, timeout=4, action=None, on_action=None):
        self.text.setText(msg)
        self._cb = on_action
        self.action.setText(action or "")
        self.action.setVisible(bool(action))
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self.timer.start(int(timeout * 1000))

    def _act(self):
        if self._cb:
            self._cb()
        self.hide()

    def reposition(self):
        par = self.parentWidget()
        self.adjustSize()
        self.move((par.width() - self.width()) // 2, par.height() - self.height() - 44)


# ───────────────────────────── waveform ─────────────────────────────

class Waveform(QWidget):
    """Paints a ``riffcore.WaveView`` and feeds it mouse input."""
    seek = Signal(float)
    loopSet = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.m = WaveView()
        self.message = ""
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self._path = None
        self._path_key = None
        self._press_x = None

    def sizeHint(self):
        return QSize(800, 190)

    # model passthroughs
    def set_peaks(self, peaks, duration):
        self.m.set_peaks(peaks, duration)
        self.update()

    def set_duration(self, duration):
        if self.m.peaks and self.m.duration != duration:
            self.m.duration = duration
            self.update()

    def set_loop(self, a, b, on):
        self.m.set_loop(a, b, on)
        self.update()

    def set_pos(self, pos):
        self.m.set_pos(pos)
        self.update()

    def zoom_fit(self):
        self.m.zoom_fit()
        self.update()

    def zoom_to(self, a, b):
        self.m.zoom_to(a, b)
        self.update()

    def zoom(self, factor):
        self.m.zoom(factor)
        self.update()

    # input
    def resizeEvent(self, e):
        self.m.width = max(1, self.width())
        super().resizeEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_x = e.position().x()
            self.m.drag_begin(self._press_x)

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._press_x is not None:
            if self.m.drag_update(x - self._press_x):
                self.update()
        else:
            self.setCursor(Qt.CursorShape.SizeHorCursor if self.m.near_handle(x) else Qt.CursorShape.IBeamCursor)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._press_x is None:
            return
        self._press_x = None
        res = self.m.drag_end()
        self.update()
        if res and res[0] == "seek":
            self.seek.emit(res[1])
        elif res and res[0] == "loop":
            self.loopSet.emit(res[1], res[2])

    def mouseDoubleClickEvent(self, e):
        self.zoom_fit()

    def wheelEvent(self, e):
        d = e.angleDelta()
        dx, dy = -d.x() / 120, -d.y() / 120  # GTK convention: positive = down/right
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if self.m.scroll(dx, dy, e.position().x(), shift):
            self.update()
            e.accept()

    # painting
    def paintEvent(self, _e):
        m, c = self.m, theme.wave
        w, h = self.width(), self.height()
        m.width = w
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), 12, 12)
        p.setClipPath(clip)
        p.fillRect(self.rect(), qcolor(c["bg"]))
        if not m.ready:
            p.setPen(qcolor(c["text"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message or "")
            return

        # time grid
        interval, ticks = m.grid()
        f = p.font()
        f.setPointSizeF(max(7.0, f.pointSizeF() * 0.75))
        p.setFont(f)
        tc = qcolor(c["text"])
        for t in ticks:
            x = m.t2x(t)
            tc.setAlphaF(0.25)
            p.fillRect(QRectF(int(x), h - 14, 1, 5), tc)
            tc.setAlphaF(0.8)
            p.setPen(tc)
            p.drawText(QPointF(x + 3, h - 4), fmt_time(t, precise=interval < 1))

        # loop region behind the wave
        if m.a is not None and m.b is not None:
            xa, xb = m.t2x(m.a), m.t2x(m.b)
            p.fillRect(QRectF(xa, 0, xb - xa, h), qcolor(c["loop"] if m.loop_on else c["loop_off"]))

        # waveform: one cached path, filled twice (unplayed / played)
        key = (h, *m.view_key())
        if key != self._path_key:
            path = QPainterPath()
            mid, amp = h / 2, h / 2 - 22
            for x, val in enumerate(m.columns()):
                hh = max(1.0, val * amp)
                path.addRect(QRectF(x, mid - hh, 1, hh * 2))
            self._path, self._path_key = path, key
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        px = m.t2x(m.pos)
        p.fillPath(self._path, qcolor(c["wave"]))
        p.save()
        p.setClipRect(QRectF(0, 0, max(0.0, px), h), Qt.ClipOperation.IntersectClip)
        p.fillPath(self._path, qcolor(c["played"]))
        p.restore()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # loop edges + handles
        if m.a is not None and m.b is not None:
            edge = qcolor(c["edge"])
            edge.setAlphaF(1.0 if m.loop_on else 0.45)
            hf = p.font()
            hf.setBold(True)
            hf.setPointSizeF(max(7.0, hf.pointSizeF()))
            p.setFont(hf)
            for name, t in (("A", m.a), ("B", m.b)):
                x = m.t2x(t)
                p.fillRect(QRectF(x - 1, 0, 2, h), edge)
                bx = x - 16 if name == "B" else x
                p.fillRect(QRectF(bx, 0, 16, 16), edge)
                p.setPen(qcolor(c["bg"]))
                p.drawText(QRectF(bx, 0, 16, 16), Qt.AlignmentFlag.AlignCenter, name)

        # playhead
        p.fillRect(QRectF(px - 1, 0, 2, h), qcolor(c["head"]))


class TickLabels(QWidget):
    """Value labels lined up under a QSlider's ticks."""

    def __init__(self, slider, values):
        super().__init__()
        self.slider, self.values = slider, values
        self.setFixedHeight(self.fontMetrics().height() + 2)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setPen(theme.colors.get("dim", QColor("#888")))
        s = self.slider
        handle = s.style().pixelMetric(QStyle.PixelMetric.PM_SliderLength, None, s)
        span = s.width() - handle
        off = s.geometry().x() - self.geometry().x()
        for v in self.values:
            x = off + handle / 2 + (v - s.minimum()) / (s.maximum() - s.minimum()) * span
            txt = str(v)
            tw = self.fontMetrics().horizontalAdvance(txt)
            p.drawText(QPointF(x - tw / 2, self.fontMetrics().ascent()), txt)


# ───────────────────────────── dialogs ─────────────────────────────

class NameDialog(QDialog):
    def __init__(self, parent, heading, body, initial, verb):
        super().__init__(parent)
        self.setWindowTitle(heading)
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        lay.addWidget(label(heading, role="heading", align=Qt.AlignmentFlag.AlignCenter, point_scale=1.3))
        lay.addWidget(tnum(label(body, align=Qt.AlignmentFlag.AlignCenter)))
        self.entry = QLineEdit(initial)
        self.entry.selectAll()
        lay.addWidget(self.entry)
        row = QHBoxLayout()
        cancel = text_button("Cancel", self.reject)
        ok = text_button(verb, self.accept, object_name="suggested")
        ok.setDefault(True)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self.entry.returnPressed.connect(self.accept)
        self.setMinimumWidth(340)

    @staticmethod
    def ask(parent, heading, body, initial, verb):
        d = NameDialog(parent, heading, body, initial, verb)
        return d.entry.text().strip() if d.exec() == QDialog.DialogCode.Accepted else None


class ShortcutsDialog(QDialog):
    GROUPS = {
        "Playback": [("Play / pause", "Space"), ("Seek ±5 s", "← →"), ("Seek ±1 s", "Shift+← →"),
                     ("Back to loop start", "Home")],
        "Speed & Pitch": [("Speed ±5%", "↑ ↓"), ("Speed ±1%", "Shift+↑ ↓"), ("Pitch ±1 semitone", "PgUp PgDn"),
                          ("Reset speed and pitch", "R")],
        "Looping": [("Set loop start", "["), ("Set loop end", "]"), ("Toggle loop", "L"), ("Save section", "S"),
                    ("Zoom to loop", "Z"), ("Show whole song", "Shift+Z"), ("Zoom in / out", "+ −")],
        "Volume": [("Volume down / up", "9 0"), ("Mute", "M")],
        "Library": [("Get from YouTube", f"{MOD}Y"), ("Open file", f"{MOD}O"), ("Export audio", f"{MOD}E"),
                    ("Filter library", f"{MOD}F")],
    }

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(4)
        r = 0
        for title, items in self.GROUPS.items():
            if r:
                grid.setRowMinimumHeight(r, 10)
                r += 1
            grid.addWidget(label(title, role="heading"), r, 0, 1, 2)
            r += 1
            for name, keys in items:
                grid.addWidget(label(name), r, 0)
                grid.addWidget(label(keys, role="dim"), r, 1, Qt.AlignmentFlag.AlignRight)
                r += 1
        lay.addLayout(grid)


class YouTubeDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.gen = 0
        self.setWindowTitle("Get from YouTube")
        self.resize(640, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)
        row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Search YouTube or paste a link…")
        self.entry.setClearButtonEnabled(True)
        self.entry.returnPressed.connect(self.go)
        row.addWidget(self.entry, 1)
        row.addWidget(text_button("Search", self.go, object_name="suggested"))
        lay.addLayout(row)

        self.stack = QStackedWidget()
        status = QWidget()
        sl = QVBoxLayout(status)
        sl.addStretch(1)
        self.status_icon = QLabel()
        self.status_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_title = label("", role="heading", align=Qt.AlignmentFlag.AlignCenter, point_scale=1.4)
        self.status_desc = label("", role="dim", wrap=True, align=Qt.AlignmentFlag.AlignCenter)
        for w in (self.status_icon, self.status_title, self.status_desc):
            sl.addWidget(w)
        sl.addStretch(2)
        self.stack.addWidget(status)
        self.stack.addWidget(label("Searching…", role="dim", align=Qt.AlignmentFlag.AlignCenter))
        self.results = QListWidget()
        self.results.setObjectName("results")
        self.results.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.results.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.stack.addWidget(self.results)
        lay.addWidget(self.stack, 1)
        music = str(win.lib.music_dir).replace(str(Path.home()), "~")
        self._status("download", "Find something to learn",
                     f"Search for a song, a lesson or a backing track — or paste any YouTube link. "
                     f"Audio is saved to {music}.")
        self.entry.setFocus()

    def _status(self, icon, title, desc):
        self.status_icon.setPixmap(icons.get(icon, theme.colors.get("dim"), 64).pixmap(64, 64))
        self.status_title.setText(title)
        self.status_desc.setText(desc)
        self.stack.setCurrentIndex(0)

    def go(self):
        q = self.entry.text().strip()
        if not q:
            return
        self.gen += 1
        gen = self.gen
        self.stack.setCurrentIndex(1)
        run_async(self.win.dispatch, youtube.search, lambda res, err: self._show(gen, res, err), q)

    def _show(self, gen, results, err):
        if gen != self.gen:
            return
        if err:
            self._status("clear", "Couldn't reach YouTube", str(err))
            return
        self.results.clear()
        if not results:
            self._status("zoom_in", "No results", "Try different words.")
            return
        for r in results:
            item = QListWidgetItem(self.results)
            w = self._row(r)
            item.setSizeHint(QSize(w.sizeHint().width(), 74))  # fixed: wrapped titles misreport height
            self.results.setItemWidget(item, w)
        self.stack.setCurrentIndex(2)

    def _row(self, r):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(12)
        pic = QLabel()
        pic.setFixedSize(96, 54)
        pic.setPixmap(cover_pixmap(None, 96, 54))
        lay.addWidget(pic)
        txt = QVBoxLayout()
        txt.setSpacing(2)
        title = label(r["title"] or r["url"], wrap=True)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setMaximumHeight(title.fontMetrics().lineSpacing() * 2 + 2)  # at most two lines
        meta = " · ".join(x for x in (r["channel"], fmt_time(r["duration"]) if r["duration"] else None) if x)
        sub = label(meta, role="dim")
        sub.setTextFormat(Qt.TextFormat.PlainText)
        txt.addWidget(title)
        txt.addWidget(sub)
        lay.addLayout(txt, 1)
        if self.win.downloads.playable(r["id"]):
            lay.addWidget(label("In library", role="dim"))
        elif self.win.downloads.job_for(r["id"]):
            lay.addWidget(label("Downloading…", role="dim"))
        else:
            btn = icon_button("download", "Download for practice", None)

            def clicked():
                btn.setEnabled(False)
                btn.setIcon(icons.get("check"))
                self.win.start_download(r)

            btn.clicked.connect(clicked)
            lay.addWidget(btn)
        if r["thumb_url"]:
            threading.Thread(target=self._fetch_thumb, args=(r["thumb_url"], pic), daemon=True).start()
        return w

    def _fetch_thumb(self, url, pic):
        try:
            data = youtube.fetch_bytes(url)
        except Exception:  # noqa: BLE001 — thumbnails are optional
            return
        self.win.dispatch(lambda: pic.setPixmap(cover_pixmap(QImage.fromData(data), 96, 54,
                                                             dpr=pic.devicePixelRatioF())))


# ───────────────────────────── main window ─────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self, app, lib):
        super().__init__()
        self.app, self.lib = app, lib
        self.dispatch = Dispatcher()
        self._updating = False
        self.setWindowTitle(APP_NAME)
        self.resize(1240, 800)
        self.setAcceptDrops(True)
        self.save_timer = QTimer(self, singleShot=True, interval=600, timeout=self.flush_save)
        self.tick = QTimer(self, interval=16, timeout=lambda: self._show_pos(self.s.current_pos()))

        self.s = Session(lib, self.dispatch, notify=self._on_session, message=self.toast,
                         save_needed=self.save_timer.start)
        self.downloads = Downloader(lib, self.dispatch, on_update=self._job_update, on_done=self._download_done)
        self._build()
        self._build_actions()
        self.rebuild_list()
        last = lib.get(lib.data.get("last"))
        if last:
            self.s.load(last)
        else:
            self._song_changed()
        app.installEventFilter(self)
        QTimer.singleShot(0, self.play_btn.setFocus)

    @property
    def song(self):
        return self.s.song

    # ── UI construction ──
    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        split = QSplitter()
        split.setChildrenCollapsible(False)
        split.setHandleWidth(1)
        outer.addWidget(split)

        # sidebar
        side = QWidget()
        side.setObjectName("sidebar")
        side.setMinimumWidth(250)
        side.setMaximumWidth(420)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(10, 10, 10, 6)
        sl.setSpacing(8)
        top = QHBoxLayout()
        self.yt_btn = icon_button("download", f"Get from YouTube ({MOD}Y)", self.open_youtube)
        self.open_btn = icon_button("open", f"Open audio file ({MOD}O)", self.open_files)
        top.addWidget(self.yt_btn)
        top.addStretch(1)
        top.addWidget(label("Library", role="heading"))
        top.addStretch(1)
        top.addWidget(self.open_btn)
        sl.addLayout(top)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter library")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        sl.addWidget(self.search)
        self.side_stack = QStackedWidget()
        self.listbox = QListWidget()
        self.listbox.setObjectName("library")
        self.listbox.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.listbox.itemClicked.connect(self._on_item)
        self.listbox.itemActivated.connect(self._on_item)
        self.side_stack.addWidget(self.listbox)
        empty_side = QWidget()
        el = QVBoxLayout(empty_side)
        el.addStretch(1)
        el.addWidget(label("No songs yet", role="heading", align=Qt.AlignmentFlag.AlignCenter))
        el.addWidget(label("Grab one from YouTube or drop audio files here.", role="dim", wrap=True,
                           align=Qt.AlignmentFlag.AlignCenter))
        el.addStretch(2)
        self.side_stack.addWidget(empty_side)
        sl.addWidget(self.side_stack, 1)
        split.addWidget(side)

        # content
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 6, 0, 0)
        cl.setSpacing(0)
        header = QGridLayout()
        header.setContentsMargins(12, 0, 8, 0)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title_lbl = label(APP_NAME, role="heading", align=Qt.AlignmentFlag.AlignCenter)
        self.subtitle_lbl = label("", role="dim", align=Qt.AlignmentFlag.AlignCenter)
        titles.addWidget(self.title_lbl)
        titles.addWidget(self.subtitle_lbl)
        header.addLayout(titles, 0, 1)
        self.menu_btn = QToolButton()
        self.menu_btn.setProperty("flat", True)
        self.menu_btn.setIcon(icons.get("menu"))
        self.menu_btn.setIconSize(QSize(18, 18))
        self.menu_btn.setFixedSize(36, 36)
        self.menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        header.addWidget(self.menu_btn, 0, 2, Qt.AlignmentFlag.AlignRight)
        header.setColumnStretch(0, 1)
        header.setColumnStretch(1, 4)
        header.setColumnStretch(2, 1)
        cl.addLayout(header)

        self.content_stack = QStackedWidget()
        empty = QWidget()
        eml = QVBoxLayout(empty)
        eml.addStretch(1)
        big = QLabel()
        big.setAlignment(Qt.AlignmentFlag.AlignCenter)
        big.setPixmap(icons.get("repeat", theme.colors.get("dim"), 96).pixmap(96, 96))
        eml.addWidget(big)
        eml.addWidget(label(APP_NAME, role="heading", align=Qt.AlignmentFlag.AlignCenter, point_scale=2.0))
        eml.addWidget(label("Slow it down. Loop it. Learn it.", role="dim", align=Qt.AlignmentFlag.AlignCenter))
        eb = QHBoxLayout()
        eb.addStretch(1)
        eb.addWidget(text_button("Get from YouTube", self.open_youtube, object_name="suggested"))
        eb.addWidget(text_button("Open File…", self.open_files))
        eb.addStretch(1)
        eml.addSpacing(12)
        eml.addLayout(eb)
        eml.addStretch(2)
        self.content_stack.addWidget(empty)
        self.content_stack.addWidget(self._build_player())
        cl.addWidget(self.content_stack, 1)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 10, 6)
        foot.addStretch(1)
        hint = text_button(f"Keyboard Shortcuts  {SHORTCUTS_HINT}", self.show_shortcuts, flat=True)
        f = hint.font()
        f.setPointSizeF(f.pointSizeF() * 0.85)
        hint.setFont(f)
        hint.setStyleSheet(f"color: {hexcolor(theme.colors.get('dim', QColor('#888')))};")
        foot.addWidget(hint)
        cl.addLayout(foot)
        split.addWidget(content)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 940])

        self.toasts = Toast(central)

    def _build_player(self):
        s = self.s
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(18, 6, 18, 0)
        outer.setSpacing(10)

        self.wave = Waveform()
        self.wave.seek.connect(s.seek)
        self.wave.loopSet.connect(lambda a, b: s.set_loop(a, b, on=True))
        outer.addWidget(self.wave)

        transport = QGridLayout()
        self.pos_lbl = tnum(label("0:00.00", bold=True, point_scale=1.35))
        transport.addWidget(self.pos_lbl, 0, 0, Qt.AlignmentFlag.AlignLeft)
        tb = QHBoxLayout()
        tb.setSpacing(8)
        tb.addWidget(icon_button("skip_back", "Back to loop start / beginning (Home)", s.go_start))
        tb.addWidget(icon_button("seek_back", "Back 5 seconds (←)", lambda: s.seek_rel(-5)))
        self.play_btn = icon_button("play", "Play / Pause (Space)", s.toggle_play, flat=False, size=56)
        self.play_btn.setObjectName("play")
        self.play_btn.setIconSize(QSize(26, 26))
        tb.addWidget(self.play_btn)
        tb.addWidget(icon_button("seek_fwd", "Forward 5 seconds (→)", lambda: s.seek_rel(5)))
        self.loop_toggle = icon_button("repeat", "Loop A–B (L)", None, checkable=True)
        self.loop_toggle.toggled.connect(lambda on: self._updating or s.set_loop_on(on))
        tb.addWidget(self.loop_toggle)
        transport.addLayout(tb, 0, 1, Qt.AlignmentFlag.AlignCenter)
        endbox = QHBoxLayout()
        endbox.setSpacing(4)
        endbox.addWidget(self._build_volume())
        endbox.addWidget(icon_button("zoom_in", "Zoom to loop (Z)", self.zoom_loop))
        endbox.addWidget(icon_button("zoom_fit", "Show whole song (Shift+Z)", self.wave.zoom_fit))
        self.dur_lbl = tnum(label("0:00", role="dim"))
        endbox.addWidget(self.dur_lbl)
        transport.addLayout(endbox, 0, 2, Qt.AlignmentFlag.AlignRight)
        transport.setColumnStretch(0, 1)
        transport.setColumnStretch(2, 1)
        outer.addLayout(transport)
        outer.addWidget(label("Drag on the waveform to make a loop · drag A/B to adjust · scroll to zoom",
                              role="dim", align=Qt.AlignmentFlag.AlignCenter))

        # control columns (side by side when wide, stacked when narrow)
        cols = QWidget()
        cols.setMaximumWidth(1200)
        self.columns = QBoxLayout(QBoxLayout.Direction.LeftToRight, cols)
        self.columns.setContentsMargins(0, 12, 0, 18)
        self.columns.setSpacing(18)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(18)
        right.setSpacing(18)
        self.columns.addLayout(left, 1)
        self.columns.addLayout(right, 1)

        # speed
        g = Group("Speed", "Tempo changes without changing pitch",
                  icon_button("undo", "Reset to 100%", lambda: s.set_speed(100)))
        sw = QWidget()
        sv = QVBoxLayout(sw)
        sv.setContentsMargins(14, 10, 14, 12)
        sv.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(text_button("−5%", lambda: s.set_speed(s.speed - 5), flat=True))
        sbox = QVBoxLayout()
        sbox.setSpacing(0)
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(SPEED_MIN, SPEED_MAX)
        self.speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.speed_slider.setTickInterval(25)
        self.speed_slider.valueChanged.connect(lambda v: self._updating or s.set_speed(v))
        sbox.addWidget(self.speed_slider)
        sbox.addWidget(TickLabels(self.speed_slider, (50, 100, 150, 200)))
        top.addLayout(sbox, 1)
        top.addWidget(text_button("+5%", lambda: s.set_speed(s.speed + 5), flat=True))
        self.speed_lbl = tnum(label("100%", point_scale=1.6))
        self.speed_lbl.setObjectName("bigvalue")
        self.speed_lbl.setMinimumWidth(self.speed_lbl.fontMetrics().horizontalAdvance("250%") + 8)
        self.speed_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.speed_lbl)
        sv.addLayout(top)
        presets = QHBoxLayout()
        presets.setSpacing(4)
        presets.addStretch(1)
        for v in SPEED_PRESETS:
            b = text_button(f"{v}%", lambda v=v: s.set_speed(v), object_name="preset")
            presets.addWidget(b)
        presets.addStretch(1)
        sv.addLayout(presets)
        g.card.add(sw)

        self.trainer_switch = Switch("Speed trainer on/off")
        self.trainer_switch.toggled.connect(self._trainer_toggled)
        self.trainer_row = g.card.add(Row("Speed trainer", s.trainer_status(), suffixes=(self.trainer_switch,)))
        tr = s.trainer
        self.trainer_rows = []
        self.tr_spins = {}
        for key, title, lo, hi, step in (("inc", "Increase by (%)", 1, 25, 1), ("every", "After every N loops", 1, 20, 1),
                                          ("target", "Stop at (%)", 30, SPEED_MAX, 5)):
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setSingleStep(step)
            spin.setValue(tr[key])
            spin.valueChanged.connect(lambda _v: s.set_trainer(**{k: sp.value() for k, sp in self.tr_spins.items()}))
            self.tr_spins[key] = spin
            row = g.card.add(Row(title, suffixes=(spin,)))
            row.setVisible(False)
            self.trainer_rows.append(row)
        left.addWidget(g)

        # pitch
        g = Group("Pitch", "Transpose without changing tempo", icon_button("undo", "Reset pitch", lambda: s.set_pitch(0, 0)))
        self.semis = QSpinBox()
        self.semis.setRange(-12, 12)
        self.semis.valueChanged.connect(lambda v: self._updating or s.set_pitch(semis=v))
        self.cents = QSpinBox()
        self.cents.setRange(-50, 50)
        self.cents.valueChanged.connect(lambda v: self._updating or s.set_pitch(cents=v))
        g.card.add(Row("Semitones", "PgUp / PgDn", suffixes=(self.semis,)))
        g.card.add(Row("Fine tune (cents)", "Match a detuned recording", suffixes=(self.cents,)))
        left.addWidget(g)
        left.addStretch(1)

        # loop
        self.loop_switch = Switch("Loop on/off (L)")
        self.loop_switch.toggled.connect(lambda on: self._updating or s.set_loop_on(on))
        g = Group("Loop", header=self.loop_switch)
        self.a_row = g.card.add(self._loop_row("Start  (A)", "a", "["))
        self.b_row = g.card.add(self._loop_row("End  (B)", "b", "]"))
        self.loop_info = g.card.add(Row("No loop yet", "Drag across the waveform, or press [ and ]", suffixes=(
            icon_button("clear", "Clear loop", s.clear_loop),
            text_button("Save Section", self.save_section))))
        right.addWidget(g)

        self.sections = Group("Sections", "Saved loops — verse, solo, that tricky bar…")
        right.addWidget(self.sections)
        right.addStretch(1)

        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(cols)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll, 1)
        return page

    def _loop_row(self, title, which, key):
        return Row(title, "–", suffixes=(
            icon_button("prev", "Nudge earlier (50 ms)", lambda: self.s.nudge(which, -0.05)),
            icon_button("next", "Nudge later (50 ms)", lambda: self.s.nudge(which, 0.05)),
            text_button("Set at Playhead", lambda: self.s.mark(which), tip=f"Shortcut: {key}")))

    def _build_volume(self):
        s = self.s
        self.vol_btn = QToolButton()
        self.vol_btn.setProperty("flat", True)
        self.vol_btn.setIconSize(QSize(18, 18))
        self.vol_btn.setFixedSize(36, 36)
        self.vol_btn.setToolTip("Volume (9 / 0, M to mute)")
        self.vol_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.vol_btn)
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(10, 8, 10, 8)
        self.vol_lbl = tnum(label("100%", role="dim", align=Qt.AlignmentFlag.AlignCenter))
        self.vol_slider = QSlider(Qt.Orientation.Vertical)
        self.vol_slider.setRange(0, VOLUME_MAX)
        self.vol_slider.setFixedHeight(170)
        self.vol_slider.setTickPosition(QSlider.TickPosition.TicksRight)
        self.vol_slider.setTickInterval(50)
        self.vol_slider.valueChanged.connect(lambda v: self._updating or s.set_volume(v))
        self.mute_btn = icon_button("vol_muted", "Mute (M)", None, checkable=True)
        self.mute_btn.toggled.connect(lambda on: self._updating or s.set_muted(on))
        bl.addWidget(self.vol_lbl)
        bl.addWidget(self.vol_slider, 0, Qt.AlignmentFlag.AlignHCenter)
        bl.addWidget(self.mute_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        act = QWidgetAction(menu)
        act.setDefaultWidget(box)
        menu.addAction(act)
        self.vol_btn.setMenu(menu)
        self._sync_volume()
        return self.vol_btn

    def _build_actions(self):
        def act(text, shortcuts, cb, menu=None):
            a = QAction(text, self)
            a.setShortcuts([QKeySequence(k) for k in shortcuts])
            a.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            a.triggered.connect(lambda *_: cb())
            self.addAction(a)
            if menu is not None:
                menu.addAction(a)
            return a

        menu = QMenu(self)
        self.song_actions = [act("Export Audio…", ["Ctrl+E"], self.export, menu),
                             act("Show in Folder", [], self.reveal, menu),
                             act("Remove from Library…", [], self.remove_song, menu)]
        self.update_ytdlp_action = act("Update YouTube Downloader", [], self.update_ytdlp, menu)
        self.update_ytdlp_action.setVisible(youtube.can_self_update())
        menu.addSeparator()
        act("Keyboard Shortcuts", ["Ctrl+?", "Ctrl+Shift+?", "Ctrl+Shift+/"], self.show_shortcuts, menu)
        act(f"About {APP_NAME}", [], self.show_about, menu)
        self.menu_btn.setMenu(menu)
        act("Get from YouTube", ["Ctrl+Y", "Ctrl+D"], self.open_youtube)
        act("Open File", ["Ctrl+O"], self.open_files)
        act("Filter Library", ["Ctrl+F"], lambda: (self.search.setFocus(), self.search.selectAll()))
        act("Quit", ["Ctrl+Q"], self.close)
        self._update_song_actions()

    def _update_song_actions(self):
        for a in self.song_actions:
            a.setEnabled(self.song is not None)

    # ── keyboard (single keys only when not typing) ──
    def eventFilter(self, obj, e):
        if obj is self.vol_btn and e.type() == e.Type.Wheel:
            self.s.set_volume(self.s.volume + 5 * (1 if e.angleDelta().y() > 0 else -1))
            return True
        if e.type() != e.Type.KeyPress or not self.isActiveWindow() or QApplication.activeModalWidget():
            return False
        if QApplication.activePopupWidget():
            return False
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QSpinBox)) or not self.song:
            return False
        mods = e.modifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier |
                   Qt.KeyboardModifier.MetaModifier):
            return False
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        s, K = self.s, Qt.Key
        actions = {
            K.Key_Space: s.toggle_play,
            K.Key_Left: lambda: s.seek_rel(-1 if shift else -5),
            K.Key_Right: lambda: s.seek_rel(1 if shift else 5),
            K.Key_Up: lambda: s.set_speed(s.speed + (1 if shift else 5)),
            K.Key_Down: lambda: s.set_speed(s.speed - (1 if shift else 5)),
            K.Key_PageUp: lambda: s.shift_pitch(1),
            K.Key_PageDown: lambda: s.shift_pitch(-1),
            K.Key_BracketLeft: lambda: s.mark("a"), K.Key_BraceLeft: lambda: s.mark("a"),
            K.Key_BracketRight: lambda: s.mark("b"), K.Key_BraceRight: lambda: s.mark("b"),
            K.Key_L: s.toggle_loop,
            K.Key_Home: s.go_start,
            K.Key_R: s.reset_speed_pitch,
            K.Key_S: self.save_section,
            K.Key_Z: lambda: self.wave.zoom_fit() if shift else self.zoom_loop(),
            K.Key_Plus: lambda: self.wave.zoom(1 / 1.5), K.Key_Equal: lambda: self.wave.zoom(1 / 1.5),
            K.Key_Minus: lambda: self.wave.zoom(1.5),
            K.Key_M: s.toggle_mute,
            K.Key_9: lambda: s.set_volume(s.volume - 5),
            K.Key_0: lambda: s.set_volume(s.volume + 5),
        }
        try:
            fn = actions.get(K(e.key()))
        except ValueError:  # key code Qt has no enum member for
            fn = None
        if fn:
            fn()
            return True
        return False

    # ── session → UI ──
    def _on_session(self, what):
        s = self.s
        if what == "song":
            self._song_changed()
        elif what == "peaks":
            self.wave.message = "" if s.peaks else "Couldn't read waveform"
            self.wave.set_peaks(s.peaks, s.duration)
            self.wave.set_pos(s.pos)
        elif what == "playing":
            self.play_btn.setIcon(icons.get("pause" if s.playing else "play", theme.colors.get("accent_fg")))
            if s.playing:
                self.tick.start()
            else:
                self.tick.stop()
                self._show_pos(s.pos)
        elif what == "position":
            self._show_pos(s.pos)
        elif what == "duration":
            self.dur_lbl.setText(fmt_time(s.duration))
            self.wave.set_duration(s.duration)
        elif what in ("speed", "pitch"):
            self._sync_controls()
            self._refresh_current_row()
        elif what == "loop":
            self._sync_controls()
        elif what == "trainer":
            self.trainer_row.set_subtitle(s.trainer_status())
        elif what == "volume":
            self._sync_volume()
        elif what == "sections":
            self._rebuild_sections()
        elif what == "library":
            self.rebuild_list()

    def _song_changed(self):
        s, song = self.s, self.s.song
        self.play_btn.setIcon(icons.get("play", theme.colors.get("accent_fg")))
        if song:
            self.title_lbl.setText(song["title"])
            self.subtitle_lbl.setText(song.get("artist") or "")
            self.setWindowTitle(f"{song['title']} — {APP_NAME}")
            self.dur_lbl.setText(fmt_time(s.duration))
            self.content_stack.setCurrentIndex(1)
            self.wave.message = "Reading waveform…"
            self.wave.set_peaks(None, s.duration)
            self._show_pos(0)
            self._sync_controls()
        else:
            self.content_stack.setCurrentIndex(0)
            self.title_lbl.setText(APP_NAME)
            self.subtitle_lbl.setText("")
            self.setWindowTitle(APP_NAME)
        self._rebuild_sections()
        self._update_song_actions()
        self.rebuild_list()

    def _show_pos(self, p):
        self.wave.set_pos(p)
        txt = fmt_time(p, precise=True)
        if self.pos_lbl.text() != txt:
            self.pos_lbl.setText(txt)

    def _sync_controls(self):
        s, song = self.s, self.s.song
        if not song:
            return
        self._updating = True
        try:
            self.speed_slider.setValue(song["speed"])
            self.speed_lbl.setText(f"{song['speed']}%")
            self.semis.setValue(song["semis"])
            self.cents.setValue(song["cents"])
            lp = song["loop"]
            self.loop_switch.setChecked(lp["on"])
            self.loop_toggle.setChecked(lp["on"])
            self.a_row.set_subtitle(fmt_time(lp["a"], True))
            self.b_row.set_subtitle(fmt_time(lp["b"], True))
            if s.has_loop():
                self.loop_info.title.setText(f"{lp['b'] - lp['a']:.2f} s loop")
                self.loop_info.set_subtitle("Looping" if lp["on"] else "Loop is off — press L")
            else:
                self.loop_info.title.setText("No loop yet")
                self.loop_info.set_subtitle("Drag across the waveform, or press [ and ]")
            self.wave.set_loop(lp["a"], lp["b"], lp["on"])
            self.trainer_row.set_subtitle(s.trainer_status())
        finally:
            self._updating = False

    def _sync_volume(self):
        v, muted = self.s.volume, self.s.muted
        level = "muted" if muted or v == 0 else "low" if v < 34 else "medium" if v < 67 else "high"
        self.vol_btn.setIcon(icons.get(f"vol_{level}"))
        self._updating = True
        try:
            self.vol_slider.setValue(v)
            self.mute_btn.setChecked(muted)
        finally:
            self._updating = False
        self.vol_lbl.setText("Muted" if muted else f"{v}%")

    def _trainer_toggled(self, on):
        for r in self.trainer_rows:
            r.setVisible(on)
        self.s.set_trainer_enabled(on)

    def zoom_loop(self):
        if self.s.has_loop():
            self.wave.zoom_to(self.s.loop["a"], self.s.loop["b"])

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "columns"):
            narrow = self.width() < 1080
            self.columns.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
        self.toasts.reposition()

    # ── library list ──
    def rebuild_list(self):
        self.listbox.clear()
        for job in self.downloads.jobs:
            item = QListWidgetItem(self.listbox)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.song_id = None
            w = self._job_widget(job)
            item.setSizeHint(w.sizeHint())
            self.listbox.setItemWidget(item, w)
        for song in self.lib.sorted_songs():
            item = QListWidgetItem(self.listbox)
            item.song_id = song["id"]
            w = self._song_widget(song)
            item.song_widget = w
            item.setSizeHint(QSize(w.sizeHint().width(), max(52, w.sizeHint().height())))
            self.listbox.setItemWidget(item, w)
            if self.song and song["id"] == self.song["id"]:
                item.setSelected(True)
                self.listbox.setCurrentItem(item)
        self.side_stack.setCurrentIndex(0 if self.downloads.jobs or self.lib.songs else 1)
        self._filter(self.search.text())

    def _song_widget(self, song):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(10)
        pic = QLabel()
        pic.setFixedSize(64, 36)
        img = QImage(song["thumb"]) if song.get("thumb") and os.path.exists(song["thumb"]) else None
        if img is not None and not img.isNull():
            pic.setPixmap(cover_pixmap(img, 64, 36, dpr=self.devicePixelRatioF()))
        else:
            pm = cover_pixmap(None, 64, 36, dpr=self.devicePixelRatioF())
            p = QPainter(pm)
            icons.get("note", theme.colors.get("dim"), 18).paint(p, 23, 9, 18, 18)
            p.end()
            pic.setPixmap(pm)
        lay.addWidget(pic)
        txt = QVBoxLayout()
        txt.setSpacing(0)
        t = ElidedLabel(song["title"])
        t.setToolTip(song["title"])
        sub = ElidedLabel(song_subtitle(song), role="dim")
        f = sub.font()
        f.setPointSizeF(f.pointSizeF() * 0.88)
        sub.setFont(f)
        txt.addWidget(t)
        txt.addWidget(sub)
        lay.addLayout(txt, 1)
        w.sub_label = sub
        return w

    def _job_widget(self, job):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)
        lay.addWidget(ElidedLabel(job["title"]))
        job["bar"] = QProgressBar()
        job["bar"].setRange(0, 1000)
        job["bar"].setValue(int(job["progress"] * 1000))
        job["bar"].setTextVisible(False)
        job["label"] = label(job["status"], role="dim")
        lay.addWidget(job["bar"])
        lay.addWidget(job["label"])
        return w

    def _filter(self, text):
        q = text.strip().lower()
        for i in range(self.listbox.count()):
            item = self.listbox.item(i)
            song = self.lib.get(item.song_id) if getattr(item, "song_id", None) else None
            item.setHidden(bool(q and song and q not in f"{song['title']} {song.get('artist') or ''}".lower()))

    def _refresh_current_row(self):
        if not self.song:
            return
        for i in range(self.listbox.count()):
            item = self.listbox.item(i)
            if getattr(item, "song_id", None) == self.song["id"]:
                item.song_widget.sub_label.setText(song_subtitle(self.song))

    def _on_item(self, item):
        song = self.lib.get(item.song_id) if getattr(item, "song_id", None) else None
        if song and (not self.song or song["id"] != self.song["id"]):
            self.s.load(song)

    # ── adding songs ──
    def open_files(self):
        exts = " ".join(f"*{e}" for e in sorted(AUDIO_EXTS))
        files, _ = QFileDialog.getOpenFileNames(self, "Open Audio", str(Path.home()),
                                                f"Audio and video ({exts});;All files (*)")
        if files:
            self.add_files(files)

    def add_files(self, file_paths):
        def done(items, err):
            songs = self.lib.add_scanned(items or [])
            self.rebuild_list()
            if songs:
                self.s.load(songs[0])
            else:
                self.toast("Those files don't look like audio")

        run_async(self.dispatch, media.scan_files, done, file_paths, lambda p: self.lib.find(path=p))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def open_youtube(self):
        YouTubeDialog(self).exec()

    def start_download(self, result):
        existing = self.downloads.playable(result.get("id"))
        if existing:
            self.toast("Already in your library")
            self.s.load(existing)
            return
        if self.downloads.start(result):
            self.rebuild_list()

    def _job_update(self, job):
        if job.get("bar"):
            job["bar"].setValue(int(job["progress"] * 1000))
        if job.get("label"):
            job["label"].setText(job["status"])

    def _download_done(self, job, song, error):
        self.rebuild_list()
        if not song:
            self.toast(f"Download failed: {error}", timeout=8)
            return
        self.toast(f"Downloaded “{song['title']}”", timeout=6, action="Practice", on_action=lambda: self.s.load(song))
        if not self.song:
            self.s.load(song)

    def update_ytdlp(self):
        self.toast("Updating YouTube downloader…", timeout=3)
        run_async(self.dispatch, youtube.self_update,
                  lambda res, err: self.toast(f"Update failed: {err}" if err else res, timeout=6))

    # ── sections ──
    def save_section(self):
        s = self.s
        if not s.has_loop():
            self.toast("Mark a loop first")
            return
        a, b = s.loop["a"], s.loop["b"]
        name = NameDialog.ask(self, "Save Section", f"{fmt_time(a, True)} – {fmt_time(b, True)}",
                              s.next_section_name(), "Save")
        if name is not None:
            s.add_section(name, a, b)

    def rename_section(self, sec):
        name = NameDialog.ask(self, "Rename Section", f"{fmt_time(sec['a'], True)} – {fmt_time(sec['b'], True)}",
                              sec["name"], "Rename")
        if name is not None:
            self.s.rename_section(sec, name)

    def _rebuild_sections(self):
        card = self.sections.card
        card.clear()
        secs = self.s.sections
        card.setVisible(bool(secs))
        for sec in secs:
            prefix = QLabel()
            prefix.setPixmap(icons.get("repeat", theme.colors.get("dim"), 18).pixmap(18, 18))
            row = Row(sec["name"], f"{fmt_time(sec['a'], True)} – {fmt_time(sec['b'], True)}  "
                                   f"({sec['b'] - sec['a']:.1f}s)", prefix=prefix, clickable=True, suffixes=(
                icon_button("edit", "Rename section", lambda sec=sec: self.rename_section(sec)),
                icon_button("trash", "Delete section", lambda sec=sec: self.s.delete_section(sec))))
            row.title.setTextFormat(Qt.TextFormat.PlainText)
            row.clicked.connect(lambda sec=sec: self.s.recall_section(sec))
            card.add(row)

    # ── song menu ──
    def export(self):
        song = self.song
        if not song:
            return
        region = None
        if self.s.has_loop():
            box = QMessageBox(QMessageBox.Icon.NoIcon, "Export Audio",
                              "Speed and pitch changes are rendered in. Export the whole song or just the loop?",
                              parent=self)
            whole = box.addButton("Whole Song", QMessageBox.ButtonRole.AcceptRole)
            loop = box.addButton("Loop Only", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(loop)
            box.exec()
            if box.clickedButton() not in (whole, loop):
                return
            if box.clickedButton() is loop:
                region = (self.s.loop["a"], self.s.loop["b"])
        out, _ = QFileDialog.getSaveFileName(self, "Export Audio",
                                             str(self.lib.music_dir / media.export_name(song, region)),
                                             "MP3 (*.mp3);;WAV (*.wav);;FLAC (*.flac)")
        if not out:
            return
        self.toast("Exporting…", timeout=2)
        run_async(self.dispatch, media.export_audio,
                  lambda _r, err: self.toast(f"Export failed: {err}" if err else f"Exported {os.path.basename(out)}"),
                  song, out, region)

    def reveal(self):
        if not self.song:
            return
        path = self.song["path"]
        if IS_MAC:
            subprocess.Popen(["open", "-R", path])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])

    def remove_song(self):
        song = self.song
        if not song:
            return
        box = QMessageBox(QMessageBox.Icon.Question, f"Remove “{song['title']}”?",
                          "Saved loops, sections and settings for this song will be lost.", parent=self)
        remove = box.addButton("Remove", QMessageBox.ButtonRole.AcceptRole)
        delete = box.addButton("Remove && Delete File", QMessageBox.ButtonRole.DestructiveRole) \
            if self.lib.owns(song["path"]) else None
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() in (remove, delete) and box.clickedButton() is not None:
            self.s.remove_song(song, delete_file=box.clickedButton() is delete)

    def show_shortcuts(self):
        ShortcutsDialog(self).exec()

    def show_about(self):
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<h3>{APP_NAME} {VERSION}</h3>"
                          "<p>Practice tool for musicians: slow down, transpose and loop any song — "
                          "including ones pulled straight from YouTube.</p>"
                          "<p>© Joson Mathew · MIT License<br>"
                          "<a href='https://github.com/jmathew499/riffarchy'>github.com/jmathew499/riffarchy</a></p>")

    # ── persistence & misc ──
    def flush_save(self):
        self.save_timer.stop()
        try:
            self.lib.save()
        except OSError as e:
            self.toast(f"Couldn't save library: {e}")

    def toast(self, msg, timeout=4, action=None, on_action=None):
        self.toasts.show_message(msg, timeout, action, on_action)

    def closeEvent(self, e):
        self.flush_save()
        self.s.close()
        super().closeEvent(e)


def selftest(out_dir):
    """``Riffarchy --selftest <dir>``: headless end-to-end check of a (packaged) build.

    Writes report.json and window.png to ``out_dir``; exit code 0 only if every check passed.
    """
    import array
    import faulthandler
    import json
    import tempfile
    import time

    os.environ.setdefault("RIFFARCHY_MPV_ARGS", "--ao=null")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Progress goes to a log as it happens (a windowed app has no console), and a watchdog dumps every
    # thread's stack there and exits if the test hangs, so a stuck run still explains itself.
    log = open(out_dir / "selftest.log", "w", buffering=1, encoding="utf-8")  # noqa: SIM115
    faulthandler.enable(file=log)
    faulthandler.dump_traceback_later(120, exit=True, file=log)

    def note(msg):
        log.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    tmp = Path(tempfile.mkdtemp(prefix="riffarchy-selftest-"))
    report = {"version": VERSION, "platform": sys.platform, "frozen": bool(getattr(sys, "frozen", False)),
              "tools": {t: paths.find_tool(t) for t in ("mpv", "ffmpeg", "ffprobe", "yt-dlp", "qjs")},
              "bundled": {t: bool(paths.bundled_tool(t)) for t in ("mpv", "ffmpeg", "ffprobe", "yt-dlp", "qjs")},
              "checks": {}}

    def check(name, ok, detail=""):
        report["checks"][name] = {"ok": bool(ok), "detail": str(detail)}
        line = ("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else "")
        note(line)
        print(line, flush=True)

    def run(*cmd, **kw):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **paths.POPEN_KW, **kw)

    def tone_hz(path):
        raw = subprocess.run([paths.find_tool("ffmpeg"), "-v", "error", "-i", str(path), "-ac", "1", "-ar", "48000",
                              "-f", "s16le", "-"], capture_output=True, **paths.POPEN_KW).stdout
        a = array.array("h", raw)[4800:-4800]
        return sum(1 for i in range(1, len(a)) if (a[i - 1] < 0) != (a[i] < 0)) / 2 / max(1e-9, len(a) / 48000)

    note(f"selftest {VERSION} on {sys.platform}, frozen={report['frozen']}, tools={report['tools']}")
    app = QApplication([sys.argv[0]])
    theme.apply(app)
    check("tools present", not paths.missing_tools(), paths.missing_tools() or "all found")
    check("mpv has rubberband", "rubberband" in run(paths.find_tool("mpv"), "--no-config", "--af=help").stdout)
    ytv = run(*youtube._yt_dlp("--version"))
    check("yt-dlp runs", ytv.returncode == 0, ytv.stdout.strip() or ytv.stderr.strip()[-200:])
    if paths.bundled_tool("qjs"):
        q = run(paths.bundled_tool("qjs"), "-e", "print(6 * 7)")
        check("QuickJS runs", q.stdout.strip() == "42", q.stdout.strip() or q.stderr.strip()[-200:])

    tone = tmp / "tone.wav"
    run(paths.find_tool("ffmpeg"), "-v", "error", "-y", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=20:sample_rate=48000", "-ac", "2", str(tone))
    note(f"tone written: {tone.exists()}")
    lib = Library(data_dir=tmp / "data", music_dir=tmp / "music")
    note("starting main window (spawns mpv)")
    win = MainWindow(app, lib)
    note(f"main window up, mpv pid {win.s.mpv.proc.pid}")
    win.resize(1280, 820)
    win.show()
    win.add_files([str(tone)])
    s = win.s
    state = {"t0": time.monotonic(), "phase": "load"}

    def finish():
        note("finishing")
        report["ok"] = all(c["ok"] for c in report["checks"].values())
        win.grab().save(str(out_dir / "window.png"))
        (out_dir / "report.json").write_text(json.dumps(report, indent=2))
        note("report written; closing (stops mpv)")
        win.close()
        note("closed")
        faulthandler.cancel_dump_traceback_later()
        app.exit(0 if report["ok"] else 1)

    def tick():
        elapsed = time.monotonic() - state["t0"]
        if state["phase"] == "load":
            if int(elapsed * 10) % 20 == 0:
                note(f"waiting for song: song={bool(s.song)} peaks={s.peaks is not None} dur={s.duration}")
            if s.song and s.peaks:
                check("song loads with waveform", True, f"{s.duration:.1f}s, {len(s.peaks)} peaks")
                s.set_loop(2.0, 3.0, on=True)
                s.set_speed(75)
                s.set_pitch(12, 0)
                s.toggle_play()
                state.update(phase="play", t0=time.monotonic())
            elif elapsed > 20:
                check("song loads with waveform", False, "timed out")
                return finish()
        elif state["phase"] == "play" and elapsed > 4:
            p = s.current_pos()
            check("plays and loops A-B", s.playing and s.loop_count >= 1 and 1.9 <= p <= 3.1,
                  f"playing={s.playing} loops={s.loop_count} pos={p:.2f}")
            s.toggle_play()
            try:
                out = media.export_audio(s.song, tmp / "loop.wav", (2.0, 3.0))
                hz = tone_hz(out)
                check("export applies pitch (+12 st → 880 Hz)", abs(hz - 880) < 20, f"{hz:.0f} Hz")
            except Exception as e:  # noqa: BLE001
                check("export applies pitch (+12 st → 880 Hz)", False, e)
            return finish()
        QTimer.singleShot(100, tick)

    QTimer.singleShot(100, tick)
    return app.exec()


def main():
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        return selftest(sys.argv[i + 1] if len(sys.argv) > i + 1 else "selftest-report")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("Riffarchy")
    app.setDesktopFileName(APP_ID)
    if APP_ICON.exists():
        app.setWindowIcon(QIcon(str(APP_ICON)))
    theme.apply(app)
    missing = paths.missing_tools()
    if missing:
        QMessageBox.critical(None, APP_NAME, "Riffarchy can't start because these tools are missing:\n\n"
                             + "\n".join(f"  • {t}" for t in missing))
        return 1
    win = MainWindow(app, Library())
    win.show()
    files = [a for a in sys.argv[1:] if os.path.isfile(a)]
    if files:
        win.add_files(files)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
