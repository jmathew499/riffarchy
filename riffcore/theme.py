"""Colours shared by front ends: the Omarchy palette (when present) and waveform colours."""

import re
import subprocess
from pathlib import Path

OMARCHY_STATE = Path.home() / ".local/state/omarchy/current"


def hex_rgb(h, alpha=1.0):
    """'#rrggbb' → (r, g, b, a) floats in 0..1."""
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
        colors = OMARCHY_STATE / "theme/colors.toml"
        if colors.exists():
            for k, v in re.findall(r'^\s*(\w+)\s*=\s*"(#[0-9a-fA-F]{6})"', colors.read_text(), re.M):
                pal[k] = v
    return pal


def wave_colors(p):
    """Waveform colours as {role: (r, g, b, a)} from a palette dict (empty → neutral dark defaults)."""
    pick = lambda *keys, d: next((p[k] for k in keys if k in p), d)  # noqa: E731
    return {
        "bg": hex_rgb(pick("darker_background", "dark_background", d="#141416")),
        "wave": hex_rgb(pick("muted", "dark_foreground", "color8", d="#6f7b84")),
        "played": hex_rgb(pick("accent", d="#3584e4")),
        "loop": hex_rgb(pick("accent", d="#3584e4"), 0.16),
        "loop_off": hex_rgb(pick("foreground", d="#ffffff"), 0.06),
        "edge": hex_rgb(pick("accent", d="#3584e4")),
        "head": hex_rgb(pick("bright_foreground", "foreground", d="#ffffff")),
        "text": hex_rgb(pick("dark_foreground", "muted", d="#9a9a9a")),
    }
