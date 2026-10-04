#!/bin/bash
# Per-user install that runs straight from this checkout (edits take effect on restart).
# For a system-wide install use the AUR package, or: sudo make install
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ID=app.riffarchy.Riffarchy

missing=()
for pkg in python-gobject python-cairo gtk4 libadwaita mpv ffmpeg yt-dlp deno; do
  pacman -Q "$pkg" &>/dev/null || missing+=("$pkg")
done
if ((${#missing[@]})); then
  echo "Installing missing packages: ${missing[*]}"
  sudo pacman -S --needed "${missing[@]}"
fi

mkdir -p ~/.local/bin ~/.local/share/applications ~/.local/share/icons/hicolor/scalable/apps
ln -sf "$DIR/riffarchy.py" ~/.local/bin/riffarchy
cp "$DIR/data/$ID.svg" ~/.local/share/icons/hicolor/scalable/apps/
# Same entry as the packaged one, but launching via an absolute path (launchers may not have ~/.local/bin on PATH)
sed "s|^Exec=riffarchy|Exec=$HOME/.local/bin/riffarchy|" "$DIR/data/$ID.desktop" > ~/.local/share/applications/$ID.desktop
update-desktop-database ~/.local/share/applications &>/dev/null || true
gtk-update-icon-cache -q ~/.local/share/icons/hicolor &>/dev/null || true
echo "Installed. Launch “Riffarchy” from the app launcher (Super+Space) or run: riffarchy"
