"""Opt-in real FFmpeg smoke test; no sites, models, or SSH required."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.environ.get("VIDEO_ANALYSIS_SMOKE") != "1", reason="opt-in worker smoke")
def test_synthetic_video_prepares_and_validates(tmp_path):
    video = tmp_path / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=5",
            "-t",
            "2",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )
    config = tmp_path / "config.json"
    cli = [sys.executable, "-m", "video_intelligence.frontend", "--config", str(config)]
    subprocess.run(
        cli
        + [
            "configure",
            "--execution",
            "local",
            "--backend",
            "captions-only",
            "--data-dir",
            str(tmp_path / "data"),
        ],
        check=True,
        capture_output=True,
    )
    prepared = subprocess.run(
        cli + ["prepare", str(video), "--max-frames", "2", "--detail", "efficient"],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if prepared.returncode:
        # Preparation purges partial media. Replay this synthetic fixture directly
        # to surface the underlying FFmpeg diagnostic in CI, without user media.
        from video_intelligence.paths import UPSTREAM_WATCH

        diagnostic = subprocess.run(
            [
                sys.executable,
                str(UPSTREAM_WATCH),
                str(video),
                "--out-dir",
                str(tmp_path / "diagnostic"),
                "--detail",
                "efficient",
                "--max-frames",
                "2",
                "--no-whisper",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        pytest.fail(prepared.stderr + "\nUpstream diagnostic:\n" + diagnostic.stderr)
    result = json.loads(prepared.stdout)
    subprocess.run(cli + ["validate", result["manifest"]], check=True, capture_output=True)
    from pathlib import Path

    packet = Path(result["analysis_packet"])
    assert packet.is_file()
    assert list(packet.parent.rglob("*.jpg"))
    assert str(packet.parent) not in packet.read_text()
