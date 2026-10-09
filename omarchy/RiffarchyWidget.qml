import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Riffarchy bar button for the Omarchy shell.
//
// The app itself is a normal window (riffarchy.py in this plugin's folder). This widget only
// opens it and mirrors its "now playing" state from $XDG_RUNTIME_DIR/riffarchy/status.json,
// which the app rewrites on every visible change and deletes on exit.
//
//   left click    open Riffarchy (or focus its window); runs setup first if packages are missing
//   middle click  play / pause            right click   setup (packages + app-launcher entry)
BarWidget {
  id: root
  moduleName: "io.github.jmathew499.riffarchy"

  readonly property string appId: "app.riffarchy.Riffarchy"
  // This file lives in <plugin>/omarchy/, so the plugin root is one level up.
  readonly property string pluginDir: decodeURIComponent(Qt.resolvedUrl("..").toString()
    .replace(/^file:\/\//, "")).replace(/\/$/, "")
  readonly property string statusPath: (Quickshell.env("XDG_RUNTIME_DIR") || "/tmp") + "/riffarchy/status.json"

  property var status: null       // parsed status.json, or null when Riffarchy isn't running
  property bool depsOk: true
  readonly property bool running: status !== null
  readonly property var song: running ? status.song : null
  readonly property bool playing: running && status.playing === true
  readonly property bool detailed: !vertical && setting("display", "Icon and status") !== "Icon only"

  readonly property string glyph: "󰎈"
  readonly property string loopGlyph: "󰑖"

  function pitchText(s) {
    var parts = []
    if (s.semis) parts.push((s.semis > 0 ? "+" : "") + s.semis + " st")
    if (s.cents) parts.push((s.cents > 0 ? "+" : "") + s.cents + "¢")
    return parts.join(" ")
  }

  readonly property string label: {
    if (!detailed || !song) return glyph
    var text = glyph + " " + song.speed + "%"
    if (song.loop) text += " " + loopGlyph
    return text
  }

  readonly property string tooltip: {
    if (!depsOk) return "Riffarchy needs a few packages. Click to set up."
    if (!running) return "Riffarchy: click to open"
    if (!song) return "Riffarchy\nClick: show window · Right click: setup"
    var bits = [song.speed + "%"]
    var p = pitchText(song)
    if (p) bits.push(p)
    if (song.loop) bits.push("loop on")
    return (playing ? "▶ " : "⏸ ") + song.title + (song.artist ? " (" + song.artist + ")" : "")
      + "\n" + bits.join(" · ")
      + "\nClick: show window · Middle click: play/pause · Right click: setup"
  }

  function launch() {
    if (!root.bar) return
    var launch = "uwsm-app -- python3 " + Util.shellQuote(pluginDir + "/riffarchy.py")
    root.bar.run("omarchy-launch-or-focus " + Util.shellQuote(appId) + " " + Util.shellQuote(launch))
  }

  function setup() {
    if (!root.bar) return
    root.bar.run("omarchy-launch-floating-terminal-with-presentation "
      + Util.shellQuote("bash " + Util.shellQuote(pluginDir + "/omarchy/setup.sh")))
    depsTimer.restart()
  }

  function togglePlay() {
    if (root.bar && running) root.bar.run("gapplication action " + Util.shellQuote(appId) + " toggle-play")
  }

  function parseStatus(text) {
    try {
      var parsed = JSON.parse(text)
      root.status = parsed && parsed.pid ? parsed : null
    } catch (e) {
      root.status = null
    }
    if (root.status) aliveCheck.check()
  }

  // ---- state from the app

  FileView {
    id: statusFile
    path: root.statusPath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.parseStatus(text())
    onLoadFailed: root.status = null
  }

  // A crashed app can't delete its status file: treat a dead pid as "not running".
  Process {
    id: aliveCheck
    function check() {
      if (!root.status || running) return
      command = ["kill", "-0", String(root.status.pid)]
      running = true
    }
    onExited: function(code) { if (code !== 0) root.status = null }
  }

  Timer {
    interval: 5000
    repeat: true
    running: root.running
    onTriggered: aliveCheck.check()
  }

  // Are the packages Riffarchy needs installed? (Re-checked after setup runs.)
  Process {
    id: depsCheck
    command: ["bash", "-lc", "command -v mpv ffmpeg ffprobe yt-dlp >/dev/null && python3 -c "
      + "'import gi, cairo; gi.require_version(\"Gtk\", \"4.0\"); gi.require_version(\"Adw\", \"1\"); "
      + "from gi.repository import Adw' 2>/dev/null"]
    running: true
    onExited: function(code) { root.depsOk = (code === 0) }
  }

  Timer {
    id: depsTimer
    interval: 4000
    repeat: true
    property int tries: 0
    onTriggered: {
      depsCheck.running = true
      if (root.depsOk || ++tries > 45) { stop(); tries = 0 }  // poll ~3 minutes while setup runs
    }
  }

  // ---- the button

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.label
    active: root.playing
    dimmed: !root.depsOk
    tooltipText: root.tooltip
    horizontalMargin: 8.75
    verticalPadding: 8.75

    onPressed: function(b) {
      if (b === Qt.MiddleButton) root.togglePlay()
      else if (b === Qt.RightButton) root.setup()
      else if (!root.depsOk) root.setup()
      else root.launch()
    }
  }
}
