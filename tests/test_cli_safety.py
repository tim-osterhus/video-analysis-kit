from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import video_intelligence.cli as cli
from video_intelligence.cli import (
    EXPECTED_YTDLP_VERSION,
    _backfill_remote_duration,
    _parse_time,
    _probe_remote,
    _record_failed_run,
    _run_upstream,
    _upstream_clean,
    _validate_duration,
    doctor,
)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "00:nan", "00:00:inf"])
def test_parse_time_rejects_non_finite_values(value: str):
    with pytest.raises(ValueError, match="invalid time value"):
        _parse_time(value)


def test_failed_run_retains_only_safe_receipt(tmp_path: Path):
    run_dir = tmp_path / "run"
    (run_dir / "frames").mkdir(parents=True)
    (run_dir / "frames" / "frame.jpg").write_bytes(b"media")
    (run_dir / "transcript.json").write_text("sensitive transcript", encoding="utf-8")
    (run_dir / "analysis-packet.md").write_text("untrusted evidence", encoding="utf-8")

    _record_failed_run(run_dir, phase="test", error=RuntimeError("bounded failure"))

    assert [path.name for path in run_dir.iterdir()] == ["failure.json"]
    receipt = json.loads((run_dir / "failure.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"
    assert receipt["phase"] == "test"
    assert receipt["run_contents_purged"] is True


def test_monitored_subprocess_enforces_artifact_byte_limit(tmp_path: Path):
    command = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            f"Path({str(tmp_path / 'oversized.bin')!r}).write_bytes(b'x' * 4096)"
        ),
    ]
    with pytest.raises(RuntimeError, match="hard limit"):
        _run_upstream(
            command,
            env=dict(os.environ),
            timeout_seconds=10,
            monitor_path=tmp_path,
            max_output_bytes=1024,
        )


def test_upstream_clean_detects_tracked_modification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    tracked = tmp_path / "tracked.py"
    tracked.write_text("pinned = True\n")
    import hashlib

    (tmp_path / "snapshot.json").write_text(
        json.dumps(
            {
                "revision": cli.EXPECTED_UPSTREAM_COMMIT,
                "files": {"tracked.py": hashlib.sha256(tracked.read_bytes()).hexdigest()},
            }
        )
    )
    monkeypatch.setattr(cli, "VENDOR_ROOT", tmp_path)
    assert _upstream_clean() is True
    tracked.write_text("pinned = False\n")
    assert _upstream_clean() is False


def test_doctor_requires_pinned_youtube_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    upstream_watch = tmp_path / "watch.py"
    upstream_watch.write_text("# pinned upstream entry point\n", encoding="utf-8")
    youtube_version = {"value": EXPECTED_YTDLP_VERSION}
    distributions = {"yt-dlp-ejs": "0.8.0", "curl-cffi": "0.16.2"}

    def command_version(command: str, args: list[str]) -> str:
        del args
        return youtube_version["value"] if command.endswith("yt-dlp") else "available"

    monkeypatch.setattr(cli, "_command_version", command_version)
    monkeypatch.setattr(cli, "_real_yt_dlp_path", lambda: Path("/runtime/yt-dlp"))
    monkeypatch.setattr(cli, "_deno_version", lambda: "deno 2.9.6")
    monkeypatch.setattr(cli, "_distribution_version", lambda name: distributions.get(name))
    monkeypatch.setattr(cli, "_upstream_commit", lambda: cli.EXPECTED_UPSTREAM_COMMIT)
    monkeypatch.setattr(cli, "_upstream_clean", lambda: True)
    monkeypatch.setattr(
        cli,
        "_model_status",
        lambda *, deep: {"installed": True, "pin_matches": True},
    )
    monkeypatch.setattr(cli, "UPSTREAM_WATCH", upstream_watch)
    monkeypatch.setattr(cli.platform, "machine", lambda: "arm64")

    assert doctor(quiet=True) == 0

    youtube_version["value"] = "2026.07.04"
    assert doctor(quiet=True) == 2

    youtube_version["value"] = EXPECTED_YTDLP_VERSION
    distributions["yt-dlp-ejs"] = None
    assert doctor(quiet=True) == 2


def test_remote_probe_preserves_missing_duration(
    monkeypatch: pytest.MonkeyPatch,
):
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(
            {
                "title": "Public Instagram video",
                "duration": None,
                "width": 720,
                "height": 1280,
                "acodec": "mp4a.40.5",
            }
        ),
        stderr="",
    )
    monkeypatch.setattr(cli, "_real_yt_dlp_path", lambda: Path("/runtime/yt-dlp"))
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: completed)

    metadata = _probe_remote("https://www.instagram.com/p/example/", timeout_seconds=10)

    assert metadata["duration"] is None


def test_missing_remote_duration_is_backfilled_from_downloaded_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    media_path = tmp_path / "video.mp4"
    media_path.write_bytes(b"fixture")
    metadata = {
        "duration": None,
        "width": 720,
        "height": 1280,
        "has_audio": True,
    }
    monkeypatch.setattr(
        cli,
        "_probe_local",
        lambda path, *, timeout_seconds: {
            "duration": 47.25,
            "width": 720,
            "height": 1280,
            "has_audio": True,
        },
    )

    duration = _backfill_remote_duration(
        metadata,
        media_path,
        max_duration=120.0,
        timeout_seconds=10,
    )

    assert duration == 47.25
    assert metadata["duration"] == 47.25
    assert metadata["duration_source"] == "downloaded-media-ffprobe"


def test_backfilled_remote_duration_still_enforces_hard_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    media_path = tmp_path / "video.mp4"
    media_path.write_bytes(b"fixture")
    monkeypatch.setattr(
        cli,
        "_probe_local",
        lambda path, *, timeout_seconds: {
            "duration": 121.0,
            "width": 720,
            "height": 1280,
            "has_audio": True,
        },
    )

    with pytest.raises(ValueError, match="exceeds approved maximum"):
        _backfill_remote_duration(
            {"duration": None},
            media_path,
            max_duration=120.0,
            timeout_seconds=10,
        )


@pytest.mark.parametrize("value", [None, 0, -1, float("nan"), float("inf")])
def test_duration_validation_rejects_invalid_values(value: object):
    with pytest.raises(ValueError, match="finite positive"):
        _validate_duration(value, max_duration=120.0)
