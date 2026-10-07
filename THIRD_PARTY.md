# Third-party software

Riffarchy itself is MIT licensed (see `LICENSE`). The macOS and Windows builds also ship the
programs and libraries below. Each one keeps its own licence; the helper programs run as
separate processes and are unmodified upstream builds. Versions are pinned (with SHA-256
checksums) in `packaging/fetch_tools.py`.

| Component | Version | Licence | Source |
|---|---|---|---|
| [mpv](https://mpv.io), macOS: official build (includes Rubber Band and FFmpeg libraries) | 0.41.0 | GPL-2.0-or-later | https://github.com/mpv-player/mpv/tree/v0.41.0 |
| [mpv](https://mpv.io), Windows: [shinchiro](https://github.com/shinchiro/mpv-winbuild-cmake) build (the official one lacks Rubber Band) | git 413ff0b1cd (2026-10-04) | GPL-2.0-or-later | https://github.com/mpv-player/mpv/commit/413ff0b1cd, build scripts: https://github.com/shinchiro/mpv-winbuild-cmake |
| [Vulkan loader](https://github.com/KhronosGroup/Vulkan-Loader) `vulkan-1.dll` (Windows; taken from mpv 0.41.0's official Windows build) | as shipped with mpv 0.41.0 | Apache-2.0 | https://github.com/KhronosGroup/Vulkan-Loader |
| [FFmpeg](https://ffmpeg.org) `ffmpeg` and `ffprobe` (static builds by eugeneware/ffmpeg-static) | 6.1.1 | GPL-3.0-or-later | https://ffmpeg.org/releases/ffmpeg-6.1.1.tar.xz, build scripts: https://github.com/eugeneware/ffmpeg-static/tree/b6.1.1 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) (standalone build) | 2026.08.19 | Unlicense (bundled dependencies: see yt-dlp's THIRD_PARTY_LICENSES.txt) | https://github.com/yt-dlp/yt-dlp/tree/2026.08.19 |
| [QuickJS-ng](https://github.com/quickjs-ng/quickjs) `qjs` | 0.17.0 | MIT | https://github.com/quickjs-ng/quickjs/tree/v0.17.0 |
| [Qt](https://www.qt.io) via [PySide6](https://doc.qt.io/qtforpython-6/) | 6.11.2 | LGPL-3.0-only | https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.11.2 |
| [Python](https://www.python.org) runtime (packaged by PyInstaller) | 3.13 | PSF-2.0 | https://www.python.org/downloads/source/ |

Qt and the Python runtime are loaded as separate shared libraries from the app folder, so they
can be replaced with compatible versions. The GPL programs are complete upstream builds; their
corresponding source code is available at the links above.

The Linux (Omarchy) package bundles none of these; it uses the system's `mpv`, `ffmpeg`, `yt-dlp`,
GTK and libadwaita packages.
