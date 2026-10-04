"""The helper programs a packaged build ships (skipped where not bundled)."""

import os
import subprocess

import pytest

from riffcore import paths, youtube
from riffcore.paths import POPEN_KW


def test_yt_dlp_runs():
    if not paths.find_tool("yt-dlp") or paths.find_tool("yt-dlp") == "yt-dlp":
        pytest.skip("yt-dlp not installed")
    out = subprocess.run(youtube._yt_dlp("--version"), capture_output=True, text=True, timeout=120, **POPEN_KW)
    assert out.returncode == 0 and out.stdout.strip()[:4].isdigit()


def test_bundled_quickjs_runs():
    qjs = paths.bundled_tool("qjs")
    if not qjs:
        pytest.skip("no bundled QuickJS (Linux uses the system deno)")
    out = subprocess.run([qjs, "-e", "print(6 * 7)"], capture_output=True, text=True, timeout=30, **POPEN_KW)
    assert out.stdout.strip() == "42"
    assert "--js-runtimes" in youtube._yt_dlp()


@pytest.mark.skipif(not os.environ.get("RIFFARCHY_NET_TESTS"), reason="set RIFFARCHY_NET_TESTS=1 to hit YouTube")
def test_youtube_search():
    results = youtube.search("blackbird guitar lesson", limit=3)
    assert results and results[0]["id"]
