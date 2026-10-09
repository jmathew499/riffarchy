#!/bin/bash
# Riffarchy setup for the Omarchy plugin (run from the bar widget's right click, or by hand).
#
#   setup.sh            install missing packages and add Riffarchy to the app launcher — asks first
#   setup.sh --remove   remove the app-launcher entry this script added (before `omarchy plugin remove`)
#
# Nothing is installed or changed without a yes from you.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ID=app.riffarchy.Riffarchy
DESKTOP="$HOME/.local/share/applications/$ID.desktop"
ICON="$HOME/.local/share/icons/hicolor/scalable/apps/$ID.svg"
PACKAGES=(python-gobject python-cairo gtk4 libadwaita mpv ffmpeg yt-dlp deno)

ask() {
  if command -v gum >/dev/null; then gum confirm "$1"; else read -r -p "$1 [y/N] " a; [[ $a == [yY]* ]]; fi
}

refresh_launcher() {
  update-desktop-database "$HOME/.local/share/applications" &>/dev/null || true
  gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" &>/dev/null || true
}

if [[ ${1:-} == --remove ]]; then
  if [[ -f $DESKTOP ]] && grep -qF "$DIR" "$DESKTOP"; then
    rm -f "$DESKTOP" "$ICON"
    refresh_launcher
    echo "Removed Riffarchy from the app launcher."
  else
    echo "No launcher entry from this plugin to remove."
  fi
  exit 0
fi

echo "Riffarchy setup"
echo

missing=()
for pkg in "${PACKAGES[@]}"; do pacman -Q "$pkg" &>/dev/null || missing+=("$pkg"); done
if ((${#missing[@]})); then
  echo "Riffarchy needs these packages: ${missing[*]}"
  if ask "Install them now with pacman?"; then
    sudo pacman -S --needed "${missing[@]}"
  else
    echo "Skipped. Riffarchy won't start until they're installed."
  fi
else
  echo "✓ All required packages are installed."
fi
echo

exec_line="Exec=python3 \"$DIR/riffarchy.py\" %F"
if [[ -f $DESKTOP ]] && grep -qF "$DIR" "$DESKTOP"; then
  echo "✓ Riffarchy is already in the app launcher."
elif [[ -f $DESKTOP ]]; then
  echo "There's already a Riffarchy launcher entry (from install.sh or the AUR package)."
  if ask "Point it at this plugin's copy instead?"; then add=1; else add=0; fi
elif ask "Add Riffarchy to the app launcher (Super+Space)?"; then
  add=1
else
  add=0
fi
if [[ ${add:-0} == 1 ]]; then
  mkdir -p "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
  cp "$DIR/data/$ID.svg" "$ICON"
  sed "s|^Exec=.*|$exec_line|" "$DIR/data/$ID.desktop" > "$DESKTOP"
  refresh_launcher
  echo "✓ Added. Find it with Super+Space → Riffarchy."
fi
echo
echo "Done. You can close this window."
