# Riffarchy

A practice player for musicians, built for [Omarchy](https://omarchy.org) and inspired by Riff Studio.
Slow songs down without changing pitch, transpose without changing speed, loop exact phrases,
and pull audio straight off YouTube.

![Riffarchy: waveform with an A–B loop, speed and pitch controls, saved sections](docs/screenshot.png)

## Install

**From the AUR** (once published): open the Omarchy menu, go to **Install → AUR** and pick `riffarchy`,
or run:

    yay -S riffarchy

**From source**:

    git clone https://github.com/jmathew499/riffarchy.git && cd riffarchy
    ./install.sh          # per-user: runs from the checkout, adds a launcher entry
    # or system-wide:
    sudo make install     # installs to /usr (what the AUR package does)

Requires `python-gobject`, `python-cairo`, `gtk4`, `libadwaita` ≥ 1.8, `mpv`, `ffmpeg`, `yt-dlp` and `deno`
(`install.sh` installs any that are missing). `mpv-mpris` is optional and adds media-key support.

### macOS and Windows (in progress)
`riffarchy_qt.py` is a Qt version with the same features, for macOS and Windows (it also runs on Linux).
Signed, packaged builds aren't out yet. To try it from source you need Python 3.10+ and `mpv`, `ffmpeg`
and `yt-dlp` on your PATH:

    python -m venv .venv
    .venv/bin/pip install -r requirements-qt.txt      # Windows: .venv\Scripts\pip
    .venv/bin/python riffarchy_qt.py

## Features
- **YouTube**: Ctrl+Y opens a search box. Search for something or paste a link, then hit download.
  It uses yt-dlp, saves the audio to `~/Music/Riffarchy`, and shows progress in the library.
- **Speed** 25–250% with pitch preserved (rubberband), plus preset buttons and a **speed trainer**
  that raises the tempo by N% every N loops, up to a target.
- **Pitch**: ±12 semitones, plus ±50 cents fine tuning for detuned recordings.
- **A–B loops**: drag across the waveform, drag the A/B handles, or press `[` / `]`. Nudge in 50 ms steps.
  Scroll to zoom (Shift+scroll pans).
- **Sections**: save named loops per song. Speed, pitch and loop settings are remembered per song.
- **Export** the whole song or just the loop with speed and pitch applied (MP3/WAV/FLAC by extension).
- Follows the active **Omarchy theme** and updates live when you switch themes.
- Drag and drop audio/video files, or open them from the file manager.

## Keys
Space play/pause · ←/→ seek 5 s (Shift: 1 s) · ↑/↓ speed ±5% (Shift: 1%) · PgUp/PgDn ±1 semitone ·
`[` `]` set A/B · L loop · Home back to A · S save section · Z zoom to loop · Shift+Z whole song ·
R reset speed+pitch · Ctrl+Y YouTube · Ctrl+O open · Ctrl+E export · Ctrl+Shift+? all shortcuts

## Files
Library: `~/.local/share/riffarchy/library.json` (waveform and thumbnail caches are stored next to it).
Downloads: `~/Music/Riffarchy`. Built on mpv (JSON IPC) + GTK4/libadwaita, so there are no pip dependencies.

## Code layout
```
riffarchy.py     GTK4/libadwaita front end + Omarchy theming (the only GTK code)
riffarchy_qt.py  Qt (PySide6) front end for macOS/Windows (and Linux); icons drawn in code
riffcore/        UI-independent core, shared by every front end (no GTK or Qt imports)
  session.py     Session: playback, A–B loop, Set at Playhead, trainer, volume, sections
                 Downloader: background YouTube downloads into the library
  waveview.py    WaveView: waveform zoom/pan, A/B handle dragging, columns + ruler ticks
  library.py     library.json and per-song settings
  engine.py      mpv over JSON IPC: inherited socketpair on Linux/macOS (mpv quits with the app),
                 named pipe + kill-on-close job on Windows
  media.py       waveform peaks, ffprobe metadata, file scanning, export (rendered by mpv + rubberband)
  theme.py       Omarchy palette reader and waveform colours
  youtube.py     yt-dlp search and download
  paths.py       per-OS data/music dirs; finds bundled or PATH copies of mpv/ffmpeg/yt-dlp
```
A front end creates a `Library`, then a `Session(lib, dispatch, notify=…, message=…, save_needed=…)`.
`dispatch(fn, *args)` must run `fn` on the UI thread (`GLib.idle_add` here, a queued signal in Qt).
After that it reacts to `notify("song" | "peaks" | "playing" | "position" | "speed" | "loop" | …)`.
For packaged macOS/Windows builds, put `mpv`, `ffmpeg`, `ffprobe`, `yt-dlp` and `qjs` (QuickJS-ng,
2.6 MB, used by yt-dlp instead of deno) in a `bin/` folder next to `riffcore/`, or set `RIFFARCHY_BIN_DIR`.
A bundled yt-dlp can update itself from the app menu. `RIFFARCHY_MPV_ARGS=--ao=null` runs mpv without
an audio device (tests, CI).

## Responsible use
Riffarchy downloads audio with yt-dlp so you can practise along with it. Only download material you
have the right to use, and respect YouTube's Terms of Service and the creators whose work you learn from.

## License
MIT. See [LICENSE](LICENSE).
