# Installing Riffarchy on macOS

Riffarchy is a practice player for musicians. It can slow songs down without changing pitch,
transpose them without changing speed, loop exact phrases, and download audio from YouTube.
Everything it needs, including the YouTube downloader, comes inside the app. There's nothing else to install.

## Requirements

- A Mac with **Apple silicon** (M1 or newer). Intel Macs aren't supported yet.
- **macOS 14 Sonoma or later.**
- About **400 MB** of free disk space.

## 1. Download

Go to the [Releases page](https://github.com/jmathew499/riffarchy/releases) and download
**`Riffarchy-<version>-macos-arm64.dmg`** (about 160 MB) from the latest release.

> **Beta builds.** Until the first Mac release is published, test builds are on the
> [Actions page](https://github.com/jmathew499/riffarchy/actions/workflows/build.yml). Open the
> newest run with a green tick, scroll to **Artifacts** and download **Riffarchy-macos-arm64**. You
> need to be signed in to GitHub. The download is a `.zip`: double-click it to get the `.dmg`.

## 2. Install

1. Double-click the `.dmg` to open it.
2. Drag **Riffarchy** onto the **Applications** folder in the same window.
3. Eject the disk image (click ⏏ next to "Riffarchy" in Finder's sidebar). You can delete the `.dmg` afterwards.

## 3. Open it the first time

Riffarchy isn't signed with an Apple Developer certificate yet, so macOS blocks it the first time
you open it. You only need to allow it once.

**macOS 15 Sequoia and later (including macOS 26):**

1. Open **Applications** and double-click **Riffarchy**. macOS says it *can't verify* the app.
   Click **Done** (not "Move to Trash").
2. Open **System Settings → Privacy & Security**.
3. Scroll down to **Security**. You'll see *"Riffarchy" was blocked to protect your Mac*.
   Click **Open Anyway**.
4. Enter your Mac password (or use Touch ID), then click **Open Anyway** again.

**macOS 14 Sonoma:** Control-click (or right-click) **Riffarchy** in Applications, choose **Open**,
then click **Open** in the dialog.

From then on, Riffarchy opens normally from Launchpad, Spotlight or the Dock.

### If it still won't open

If macOS says the app **"is damaged and can't be opened"**, or Riffarchy opens but says it can't
play audio, open **Terminal** and run:

```
xattr -dr com.apple.quarantine /Applications/Riffarchy.app
```

This removes the "downloaded from the internet" flag macOS puts on the app and the tools inside
it. Then open Riffarchy again.

## 4. Get started

- **Add music:** click the download icon (top left) to search YouTube or paste a link. Or click
  the folder icon to open audio files from your Mac. You can also drag files onto the window.
- **Loop a part:** drag across the waveform to make an A–B loop. Drag the A/B handles to adjust it.
- **Slow it down / transpose it:** use the **Speed** slider and the **Pitch** controls.
- **All the shortcuts:** press **⇧⌘?**. Space plays and pauses; `[` and `]` set the loop start and end.

## Where your files go

| What | Where |
|---|---|
| Downloaded audio | `~/Music/Riffarchy` |
| Library, loops, sections and settings | `~/Library/Application Support/Riffarchy` |

## Updating

- **Riffarchy itself:** download the new `.dmg` and drag Riffarchy into Applications again. Choose
  **Replace**. Your library and settings are kept. You may need to do the first-time step in
  section 3 again.
- **YouTube downloads stop working:** YouTube changes often. Click the **≡** menu (top right) and
  choose **Update YouTube Downloader**.

## Uninstalling

1. Quit Riffarchy and drag it from **Applications** to the **Trash**.
2. To remove your library too, delete `~/Library/Application Support/Riffarchy`. Delete
   `~/Music/Riffarchy` as well if you don't want the downloaded audio.
   (In Finder, press **⇧⌘G** and paste a path to go to it.)

## Troubleshooting

| Problem | Fix |
|---|---|
| "Riffarchy can't be opened" / "can't verify the developer" | Follow section 3 |
| "Riffarchy is damaged and can't be opened" | Run the `xattr` command in section 3 |
| No sound | Check the volume button in Riffarchy (next to the zoom buttons) and your Mac's output device |
| A YouTube download fails | **≡ → Update YouTube Downloader**, then try again |
| Something else | [Open an issue](https://github.com/jmathew499/riffarchy/issues) and say which macOS version you're on |

Please only download audio you have the right to use, and respect YouTube's Terms of Service.
