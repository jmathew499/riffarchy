# Installing Riffarchy on Windows

Riffarchy is a practice player for musicians. It can slow songs down without changing pitch,
transpose them without changing speed, loop exact phrases, and download audio from YouTube.
Everything it needs, including the YouTube downloader, comes inside the app. There's nothing else to install.

## Requirements

- **Windows 10 or 11, 64-bit** (x64). Windows on Arm PCs should run it through Windows' built-in
  emulation, but this hasn't been tested.
- About **450 MB** of free disk space.
- No administrator rights needed.

## 1. Download

Go to the [Releases page](https://github.com/jmathew499/riffarchy/releases) and pick one of these
from the latest release:

| File | What it is |
|---|---|
| **`Riffarchy-<version>-windows-x64-setup.exe`** (about 125 MB) | **Installer, recommended.** Adds Riffarchy to the Start menu and to *Installed apps* |
| `Riffarchy-<version>-windows-x64.zip` (about 165 MB) | Portable version: unzip and run, nothing gets installed |

> **Beta builds.** Until the first Windows release is published, test builds are on the
> [Actions page](https://github.com/jmathew499/riffarchy/actions/workflows/build.yml). Open the
> newest run with a green tick, scroll to **Artifacts** and download **Riffarchy-windows-x64**. You
> need to be signed in to GitHub. It's a `.zip` containing both the installer and the portable version:
> right-click it, choose **Extract All…**, and use the files inside.

### If your browser warns about the download

Riffarchy isn't code-signed yet, so browsers may warn about it.

- **Edge:** if it says the file *isn't commonly downloaded*, hover over the download, click **⋯** →
  **Keep** → **Show more** → **Keep anyway**.
- **Chrome:** in the downloads list, choose **Keep** (or **Download unverified file**).

## 2a. Install with the installer (recommended)

1. Double-click **`Riffarchy-…-setup.exe`**.
2. If a blue **"Windows protected your PC"** box appears, click **More info**, then **Run anyway**.
   This is Microsoft SmartScreen. It shows up for apps that aren't code-signed yet.
3. If asked, choose **Install for me only**. That needs no admin rights.
4. Accept the licence (MIT). Optionally tick **Create a desktop shortcut**, then click **Install**.
5. Leave **Launch Riffarchy** ticked and click **Finish**.

Riffarchy is installed in `%LOCALAPPDATA%\Programs\Riffarchy` and appears in the **Start menu**.

## 2b. Or use the portable version

1. Right-click the `.zip` → **Extract All…** and pick a folder, such as `Documents\Riffarchy`.
   Don't run it from inside the zip without extracting.
2. Open the extracted **Riffarchy** folder and double-click **`Riffarchy.exe`**.
3. If **"Windows protected your PC"** appears: **More info → Run anyway**.

Keep the whole folder together. Riffarchy needs the `bin` folder next to `Riffarchy.exe`.

## 3. Get started

- **Add music:** click the download icon (top left) to search YouTube or paste a link. Or click
  the folder icon to open audio files. You can also drag files onto the window.
- **Loop a part:** drag across the waveform to make an A–B loop. Drag the A/B handles to adjust it.
- **Slow it down / transpose it:** use the **Speed** slider and the **Pitch** controls.
- **All the shortcuts:** press **Ctrl+Shift+?**. Space plays and pauses; `[` and `]` set the loop start and end.

## Where your files go

| What | Where |
|---|---|
| Downloaded audio | `Music\Riffarchy` (in your user folder) |
| Library, loops, sections and settings | `%APPDATA%\Riffarchy` |

(To open one of these, paste the path into File Explorer's address bar.)

## Updating

- **Riffarchy itself:** run the new installer. It updates the existing install, and your library and
  settings are kept. For the portable version, extract the new zip and replace the old folder.
- **YouTube downloads stop working:** YouTube changes often. Click the **≡** menu (top right) and
  choose **Update YouTube Downloader**.

## Uninstalling

1. **Installer version:** **Settings → Apps → Installed apps**, find **Riffarchy**, click **⋯ →
   Uninstall**. **Portable version:** delete its folder.
2. To remove your library too, delete `%APPDATA%\Riffarchy`. Delete `Music\Riffarchy` as well if you
   don't want the downloaded audio.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Windows protected your PC" | **More info → Run anyway** (see section 2a) |
| Antivirus removes or blocks a file (often `yt-dlp.exe`) | The tools inside Riffarchy aren't signed yet, which some antivirus apps flag. Restore the file from quarantine and allow it, or add the Riffarchy folder as an exception, then reinstall |
| Riffarchy says tools are missing | Reinstall, or re-extract the zip, and make sure the `bin` folder is next to `Riffarchy.exe` |
| No sound | Check the volume button in Riffarchy (next to the zoom buttons) and Windows' output device (speaker icon in the taskbar) |
| A YouTube download fails | **≡ → Update YouTube Downloader**, then try again |
| Something else | [Open an issue](https://github.com/jmathew499/riffarchy/issues) and say which Windows version you're on |

Please only download audio you have the right to use, and respect YouTube's Terms of Service.
