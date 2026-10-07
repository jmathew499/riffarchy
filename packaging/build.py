#!/usr/bin/env python3
"""Build the Qt app into a distributable for this OS.

    python packaging/fetch_tools.py bin     # mpv, ffmpeg, ffprobe, yt-dlp, QuickJS
    python packaging/build.py               # → dist/Riffarchy-<version>-<platform>.{dmg,zip,exe}

macOS: Riffarchy.app (ad-hoc signed, not notarized) in a .dmg.
Windows: a portable .zip and, when Inno Setup is installed, a per-user installer.
Linux: a .tar.gz of the PyInstaller folder (only used to test the build locally;
Omarchy users get the GTK app from the AUR/install.sh).
"""

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from riffcore import VERSION  # noqa: E402

APP_ID = "app.riffarchy.Riffarchy"
NAME = "Riffarchy"
DIST, WORK = ROOT / "dist", ROOT / "build"
BIN = ROOT / "bin"
PLATFORM = {"darwin": "macos-arm64", "win32": "windows-x64"}.get(sys.platform, "linux-x64")
VERSION = (os.environ.get("RIFFARCHY_VERSION") or VERSION).lstrip("v")  # CI sets it to "" off tags


def sh(*cmd, **kw):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def render_icons():
    """SVG → PNGs (via Qt) → .icns/.ico (via Pillow)."""
    from PIL import Image
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 — needed for QPainter
    out = WORK / "icons"
    out.mkdir(parents=True, exist_ok=True)
    renderer = QSvgRenderer(str(ROOT / "data" / f"{APP_ID}.svg"))
    img = QImage(1024, 1024, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    renderer.render(p)
    p.end()
    png = out / "icon-1024.png"
    img.save(str(png))
    big = Image.open(png)
    big.save(out / "Riffarchy.icns")
    big.save(out / "Riffarchy.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    return out


def pyinstaller(icons):
    icon = icons / ("Riffarchy.icns" if sys.platform == "darwin" else "Riffarchy.ico")
    sh(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", NAME,
       "--distpath", DIST, "--workpath", WORK / "pyinstaller", "--specpath", WORK,
       "--icon", icon, "--osx-bundle-identifier", APP_ID,
       "--add-data", f"{ROOT / 'data' / (APP_ID + '.svg')}{os.pathsep}data",
       # only QtCore/QtGui/QtWidgets(+Svg for the icon) are used
       "--exclude-module", "PySide6.QtQml", "--exclude-module", "PySide6.QtQuick",
       "--exclude-module", "PySide6.QtNetwork", "--exclude-module", "tkinter",
       ROOT / "riffarchy_qt.py")


def copy_tools(dest):
    if not BIN.is_dir() or not any(BIN.iterdir()):
        print(f"! no bundled tools in {BIN} — the build will rely on tools installed on the user's PATH")
        return
    shutil.copytree(BIN, dest, symlinks=True, dirs_exist_ok=True)
    shutil.copy2(ROOT / "THIRD_PARTY.md", dest.parent / "THIRD_PARTY.md")


def build_macos():
    app = DIST / f"{NAME}.app"
    resources = app / "Contents" / "Resources"
    copy_tools(resources / "bin")
    info_path = app / "Contents" / "Info.plist"
    info = plistlib.loads(info_path.read_bytes())
    info.update(CFBundleShortVersionString=VERSION, CFBundleVersion=VERSION, CFBundleDisplayName=NAME,
                LSMinimumSystemVersion="14.0", NSHighResolutionCapable=True,
                NSHumanReadableCopyright="© Joson Mathew · MIT License")
    info_path.write_bytes(plistlib.dumps(info))
    # Apple silicon refuses to run unsigned arm64 code: ad-hoc sign every helper, then the app.
    bin_dir = resources / "bin"
    if bin_dir.is_dir():
        for f in bin_dir.iterdir():
            if f.is_file():
                sh("codesign", "--force", "--sign", "-", f)
        if (bin_dir / "mpv.app").is_dir():
            sh("codesign", "--force", "--deep", "--sign", "-", bin_dir / "mpv.app")
    sh("codesign", "--force", "--sign", "-", app)
    sh("codesign", "--verify", "--strict", "--verbose=1", app)
    with tempfile.TemporaryDirectory() as stage:
        stage = Path(stage)
        shutil.copytree(app, stage / app.name, symlinks=True)
        (stage / "Applications").symlink_to("/Applications")
        dmg = DIST / f"{NAME}-{VERSION}-{PLATFORM}.dmg"
        dmg.unlink(missing_ok=True)
        sh("hdiutil", "create", "-volname", NAME, "-srcfolder", stage, "-ov", "-format", "UDZO", dmg)
    return [dmg]


def build_windows():
    folder = DIST / NAME
    copy_tools(folder / "bin")
    zip_base = DIST / f"{NAME}-{VERSION}-{PLATFORM}"
    outputs = [Path(shutil.make_archive(str(zip_base), "zip", DIST, NAME))]
    iscc = shutil.which("iscc") or next((p for p in (Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" /
                                                     "ISCC.exe",) if p.is_file()), None)
    if iscc:
        sh(iscc, f"/DMyVersion={VERSION}", f"/DSourceDir={folder}", f"/DOutputDir={DIST}",
           f"/DOutputName={NAME}-{VERSION}-{PLATFORM}-setup", f"/DIconFile={WORK / 'icons' / 'Riffarchy.ico'}",
           ROOT / "packaging" / "windows" / "riffarchy.iss")
        outputs.append(DIST / f"{NAME}-{VERSION}-{PLATFORM}-setup.exe")
    else:
        print("! Inno Setup not found — skipping the installer (the .zip still works)")
    return outputs


def build_linux():
    folder = DIST / NAME
    copy_tools(folder / "bin")
    return [Path(shutil.make_archive(str(DIST / f"{NAME}-{VERSION}-{PLATFORM}"), "gztar", DIST, NAME))]


def main():
    shutil.rmtree(DIST, ignore_errors=True)
    icons = render_icons()
    pyinstaller(icons)
    outputs = {"darwin": build_macos, "win32": build_windows}.get(sys.platform, build_linux)()
    print("\nBuilt:")
    for o in outputs:
        print(f"  {o.relative_to(ROOT)}  ({o.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
