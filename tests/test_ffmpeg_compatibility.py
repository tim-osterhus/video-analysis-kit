"""Exercise frame extraction against the FFmpeg 9 command-line boundary."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

from video_intelligence.paths import VENDOR_ROOT


@pytest.mark.parametrize("mode", ["scene", "keyframe"])
def test_frame_extraction_accepts_ffmpeg_without_legacy_vsync(tmp_path, monkeypatch, mode):
    script = VENDOR_ROOT / "skills/watch/scripts/frames.py"
    spec = importlib.util.spec_from_file_location("compat_frames", script)
    frames = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frames)
    monkeypatch.setattr(frames.shutil, "which", lambda name: "/test/ffmpeg")

    def ffmpeg9(command, **kwargs):
        if "-vsync" in command:
            return subprocess.CompletedProcess(command, 1, "", "Unrecognized option 'vsync'")
        for index in range(1, 4):
            Path(command[-1].replace("%04d", f"{index:04d}")).write_bytes(b"frame")
        return subprocess.CompletedProcess(
            command, 0, "", "pts_time:0\npts_time:1\npts_time:2\n"
        )

    monkeypatch.setattr(frames.subprocess, "run", ffmpeg9)
    if mode == "scene":
        result = frames.extract_scene_candidates("sample.mp4", tmp_path / "frames")
    else:
        result, _ = frames.extract_keyframes("sample.mp4", tmp_path / "frames", dedup=False)
    assert len(result) == 3
    assert result[1]["timestamp_seconds"] == 1.0
    assert all(Path(item["path"]).is_file() for item in result)
