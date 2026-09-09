from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .artifacts import (
    build_analysis_packet,
    clarify_uniform_fallback,
    collect_artifacts,
    inject_local_transcript,
    sha256_file,
    validate_analysis_result,
    validate_manifest,
    write_manifest,
)
from .local_transcription import (
    LocalTranscriptionError,
    extract_audio,
    write_transcript_json,
)
from .paths import (
    ARTIFACT_ROOT,
    DEFAULT_MODEL_PATH,
    EXPECTED_UPSTREAM_COMMIT,
    HF_HOME,
    ROOT,
    RUNTIME_ROOT,
    SAFE_YTDLP,
    UPSTREAM_WATCH,
    VENDOR_ROOT,
)
from .source_policy import SourcePolicyError, classify_source

PROFILES = ("general", "stack-relevance", "competitor-creative", "implementation-handoff")
DETAILS = ("transcript", "efficient", "balanced")
MAX_DURATION_SECONDS = 7200.0
MAX_LOCAL_BYTES = 2 * 1024 * 1024 * 1024
MIN_FREE_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 900
MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".opus", ".wav"}
EXPECTED_YTDLP_VERSION = "2026.08.19"


def _parse_time(value: str | None) -> float | None:
    if value is None:
        return None
    parts = value.split(":")
    try:
        if len(parts) == 1:
            parsed = float(parts[0])
            if not math.isfinite(parsed):
                raise ValueError(f"invalid time value: {value}")
            return parsed
        if len(parts) == 2:
            parsed = int(parts[0]) * 60 + float(parts[1])
            if not math.isfinite(parsed):
                raise ValueError(f"invalid time value: {value}")
            return parsed
        if len(parts) == 3:
            parsed = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            if not math.isfinite(parsed):
                raise ValueError(f"invalid time value: {value}")
            return parsed
    except ValueError as exc:
        raise ValueError(f"invalid time value: {value}") from exc
    raise ValueError(f"invalid time value: {value}")


def _command_version(command: str, args: list[str]) -> str | None:
    path = shutil.which(command)
    if not path:
        return None
    try:
        proc = subprocess.run([path, *args], capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or proc.stderr).splitlines()[0].strip()


def _distribution_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _deno_version() -> str | None:
    repository_local = Path(sys.executable).parent / "deno"
    command = str(repository_local) if repository_local.is_file() else "deno"
    return _command_version(command, ["--version"])


def _real_yt_dlp_path() -> Path | None:
    repository_local = Path(sys.executable).parent / "yt-dlp"
    if repository_local.is_file():
        return repository_local
    discovered = shutil.which("yt-dlp")
    if not discovered:
        return None
    path = Path(discovered).resolve()
    if path == SAFE_YTDLP.resolve():
        return None
    return path


def _upstream_commit() -> str | None:
    try:
        return json.loads((VENDOR_ROOT / "snapshot.json").read_text())["revision"]
    except (OSError, ValueError, KeyError):
        return None


def _upstream_clean() -> bool:
    try:
        snapshot = json.loads((VENDOR_ROOT / "snapshot.json").read_text())
        files = snapshot["files"]
        if not files or snapshot["revision"] != EXPECTED_UPSTREAM_COMMIT:
            return False
        for name, digest in files.items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                return False
            path = VENDOR_ROOT / relative
            if path.is_symlink() or not path.is_file() or sha256_file(path) != digest:
                return False
        actual_python = {str(p.relative_to(VENDOR_ROOT)) for p in VENDOR_ROOT.rglob("*.py")}
        return actual_python == {name for name in files if name.endswith(".py")}
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _model_status(*, deep: bool = False) -> dict[str, Any]:
    from .models import model_status

    return model_status(
        os.environ.get("VIDEO_ANALYSIS_BACKEND", "captions-only"), DEFAULT_MODEL_PATH, deep=deep
    )


def doctor(
    *, as_json: bool = False, deep: bool = False, quiet: bool = False, captions_only: bool = False
) -> int:
    backend = (
        "captions-only"
        if captions_only
        else os.environ.get("VIDEO_ANALYSIS_BACKEND", "captions-only")
    )
    if backend not in ("captions-only", "mlx", "faster-whisper"):
        raise ValueError("unsupported VIDEO_ANALYSIS_BACKEND")
    model = None if backend == "captions-only" else _model_status(deep=deep)
    distribution = {"mlx": "mlx-whisper", "faster-whisper": "faster-whisper"}.get(backend)
    inference_available = distribution is None or _distribution_version(distribution) is not None
    hardware_ok = backend != "mlx" or (
        platform.system() == "Darwin" and platform.machine() == "arm64"
    )
    real_yt_dlp = _real_yt_dlp_path()
    yt_dlp_version = _command_version(str(real_yt_dlp), ["--version"]) if real_yt_dlp else None
    report = {
        "contract_version": "1.0",
        "version": __version__,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "backend": backend,
        "data_dir": str(ARTIFACT_ROOT.parent),
        "ffmpeg": _command_version("ffmpeg", ["-version"]),
        "ffprobe": _command_version("ffprobe", ["-version"]),
        "yt_dlp": yt_dlp_version,
        "yt_dlp_pin_matches": yt_dlp_version == EXPECTED_YTDLP_VERSION,
        "yt_dlp_ejs": _distribution_version("yt-dlp-ejs"),
        "deno": _deno_version(),
        "curl_cffi": _distribution_version("curl-cffi"),
        "backend_installed": inference_available,
        "hardware_supported": hardware_ok,
        "upstream_commit": _upstream_commit(),
        "upstream_snapshot_valid": _upstream_clean(),
        "model": model,
    }
    healthy = all(
        (
            platform.system() in ("Darwin", "Linux"),
            report["ffmpeg"],
            report["ffprobe"],
            report["yt_dlp_pin_matches"],
            report["yt_dlp_ejs"],
            report["deno"],
            report["curl_cffi"],
            inference_available,
            hardware_ok,
            UPSTREAM_WATCH.is_file(),
            report["upstream_snapshot_valid"],
            model is None or (model["installed"] and model["pin_matches"]),
        )
    )
    report["healthy"] = bool(healthy)
    if not quiet:
        print(
            json.dumps(report, indent=2)
            if as_json
            else "\n".join(f"{k}: {v}" for k, v in report.items())
        )
    return 0 if healthy else 2


def _probe_local(path: Path, *, timeout_seconds: int) -> dict[str, Any]:
    if path.stat().st_size > MAX_LOCAL_BYTES:
        raise ValueError(f"local file exceeds {MAX_LOCAL_BYTES} bytes")
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"FFprobe exceeded {timeout_seconds} seconds") from exc
    if proc.returncode != 0:
        raise ValueError(f"FFprobe failed: {proc.stderr.strip()}")
    raw = json.loads(proc.stdout or "{}")
    streams = raw.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration = float((raw.get("format") or {}).get("duration") or video.get("duration") or 0)
    return {
        "title": path.name,
        "duration": duration,
        "width": video.get("width"),
        "height": video.get("height"),
        "has_audio": audio is not None,
        "size_bytes": path.stat().st_size,
    }


def _probe_remote(source: str, *, timeout_seconds: int) -> dict[str, Any]:
    real_yt_dlp = _real_yt_dlp_path()
    if not real_yt_dlp:
        raise ValueError(
            "yt-dlp is unavailable; install the worker extra from this checkout (see docs/setup.md)"
        )
    env = dict(os.environ)
    env["VIDEO_INTELLIGENCE_REAL_YTDLP"] = str(real_yt_dlp)
    try:
        proc = subprocess.run(
            [
                str(SAFE_YTDLP),
                "--dump-single-json",
                "--skip-download",
                "--no-playlist",
                "--",
                source,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"yt-dlp metadata probe exceeded {timeout_seconds} seconds") from exc
    if proc.returncode != 0:
        raise ValueError(f"yt-dlp metadata probe failed: {proc.stderr.strip()}")
    raw = json.loads(proc.stdout)
    raw_duration = raw.get("duration")
    return {
        "title": raw.get("title"),
        "uploader": raw.get("uploader") or raw.get("channel"),
        "duration": float(raw_duration) if raw_duration is not None else None,
        "width": raw.get("width"),
        "height": raw.get("height"),
        "has_audio": raw.get("acodec") not in (None, "none"),
        "webpage_url": raw.get("webpage_url") or source,
    }


def _validate_duration(value: Any, *, max_duration: float) -> float:
    try:
        duration = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("source duration must be a finite positive number") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("source duration must be a finite positive number")
    if duration > max_duration:
        raise ValueError(
            f"video duration {duration:.1f}s exceeds approved maximum {max_duration:.1f}s"
        )
    return duration


def _backfill_remote_duration(
    metadata: dict[str, Any],
    media_path: Path,
    *,
    max_duration: float,
    timeout_seconds: int,
) -> float:
    media_metadata = _probe_local(media_path, timeout_seconds=timeout_seconds)
    duration = _validate_duration(media_metadata.get("duration"), max_duration=max_duration)
    metadata["duration"] = duration
    metadata["duration_source"] = "downloaded-media-ffprobe"
    for field in ("width", "height", "has_audio"):
        if metadata.get(field) is None:
            metadata[field] = media_metadata.get(field)
    return duration


def _new_run_dir(source: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    source_id = hashlib.sha256(source.encode("utf-8")).hexdigest()[:10]
    run_dir = ARTIFACT_ROOT / f"{stamp}-{source_id}"
    if run_dir.exists():
        raise ValueError(f"output directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    return run_dir


@contextlib.contextmanager
def _single_job_lock():
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    lock_path = RUNTIME_ROOT / "processing.lock"
    with lock_path.open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another video-intelligence job is already running") from exc
        yield


def _run_upstream(
    command: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: int,
    monitor_path: Path | None = None,
    max_output_bytes: int | None = None,
) -> subprocess.CompletedProcess[str]:
    def monitored_bytes() -> int:
        if monitor_path is None or not monitor_path.exists():
            return 0
        total = 0
        for path in monitor_path.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
            except FileNotFoundError:
                continue
        return total

    def terminate() -> tuple[str, str]:
        try:
            os.killpg(process.pid, 15)
        except ProcessLookupError:
            return process.communicate()
        try:
            return process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, 9)
            except ProcessLookupError:
                pass
            return process.communicate()

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            terminate()
            raise RuntimeError(
                f"video preparation exceeded the {timeout_seconds}-second processing limit"
            )
        try:
            stdout, stderr = process.communicate(timeout=min(0.25, remaining))
        except subprocess.TimeoutExpired:
            if max_output_bytes is not None and monitored_bytes() > max_output_bytes:
                terminate()
                raise RuntimeError(
                    f"prepared artifacts exceeded the {max_output_bytes}-byte hard limit"
                )
            continue
        if max_output_bytes is not None and monitored_bytes() > max_output_bytes:
            raise RuntimeError(
                f"prepared artifacts exceeded the {max_output_bytes}-byte hard limit"
            )
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _find_media(run_dir: Path, source_kind: str, normalized_source: str) -> Path | None:
    if source_kind == "local":
        return Path(normalized_source)
    download_dir = run_dir / "download"
    candidates = [
        path
        for path in download_dir.glob("video.*")
        if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES
    ]
    return sorted(candidates)[0] if candidates else None


def _transcript_source(report: str) -> tuple[str, int]:
    if "- **Transcript:** none available" in report:
        return "none", 0
    match = re.search(r"- \*\*Transcript:\*\* (\d+) segments.*?\(via ([^)]+)\)", report)
    if match:
        return match.group(2), int(match.group(1))
    return "unknown", 0


def _record_failed_run(run_dir: Path, *, phase: str, error: Exception) -> None:
    for path in run_dir.iterdir():
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    receipt = {
        "contract_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_dir.name,
        "status": "failed",
        "phase": phase,
        "error_type": type(error).__name__,
        "error": str(error),
        "run_contents_purged": True,
    }
    (run_dir / "failure.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def prepare(args: argparse.Namespace) -> int:
    started = time.monotonic()

    def remaining_seconds() -> int:
        remaining = args.timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise RuntimeError(
                f"video preparation exceeded the {args.timeout}-second processing limit"
            )
        return max(1, int(math.ceil(remaining)))

    if (
        doctor(
            as_json=False,
            deep=False,
            quiet=True,
            captions_only=args.transcription == "captions-only",
        )
        != 0
    ):
        raise RuntimeError("doctor failed; repair the local environment before preparing video")
    remaining_seconds()

    model_for_run: dict[str, Any] | None = None
    backend = os.environ.get("VIDEO_ANALYSIS_BACKEND", "captions-only")
    if args.transcription == "local" and backend != "captions-only":
        model_for_run = _model_status(deep=True)
        if not model_for_run["pin_matches"]:
            raise RuntimeError("local transcription model failed live hash verification")
        remaining_seconds()

    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(ARTIFACT_ROOT).free
    if free_bytes < MIN_FREE_BYTES:
        raise RuntimeError(
            f"insufficient free disk space: {free_bytes} bytes available; {MIN_FREE_BYTES} required"
        )

    source_kind, normalized_source = classify_source(args.source)
    if source_kind == "local":
        local_source = Path(normalized_source)
        try:
            local_source.relative_to(ARTIFACT_ROOT.resolve())
        except ValueError:
            pass
        else:
            raise ValueError("local source files inside the managed artifact tree are not allowed")
        pre_source_hash = sha256_file(local_source)
    else:
        pre_source_hash = None
    metadata = (
        _probe_remote(normalized_source, timeout_seconds=min(60, remaining_seconds()))
        if source_kind == "url"
        else _probe_local(Path(normalized_source), timeout_seconds=min(60, remaining_seconds()))
    )
    duration_deferred = source_kind == "url" and metadata.get("duration") is None
    if not duration_deferred:
        _validate_duration(metadata.get("duration"), max_duration=args.max_duration)
        metadata["duration_source"] = "yt-dlp-metadata" if source_kind == "url" else "local-ffprobe"

    start_seconds = _parse_time(args.start)
    end_seconds = _parse_time(args.end)
    if start_seconds is not None and start_seconds < 0:
        raise ValueError("--start must be non-negative")
    if end_seconds is not None and end_seconds <= (start_seconds or 0):
        raise ValueError("--end must be greater than --start")

    run_dir = _new_run_dir(normalized_source)
    env = dict(os.environ)
    real_yt_dlp = _real_yt_dlp_path()
    if not real_yt_dlp:
        raise RuntimeError(
            "yt-dlp is unavailable; install the worker extra from this checkout (see docs/setup.md)"
        )
    env["VIDEO_INTELLIGENCE_REAL_YTDLP"] = str(real_yt_dlp)
    env["PATH"] = f"{ROOT / 'bin'}:{Path(sys.executable).parent}:{env.get('PATH', '')}"
    env["HF_HOME"] = str(HF_HOME)
    env["HF_HUB_CACHE"] = str(HF_HOME / "hub")

    command = [
        sys.executable,
        str(UPSTREAM_WATCH),
        normalized_source,
        "--out-dir",
        str(run_dir),
        "--detail",
        args.detail,
        "--max-frames",
        str(args.max_frames),
        "--resolution",
        str(args.resolution),
        "--no-whisper",
    ]
    if args.start:
        command += ["--start", args.start]
    if args.end:
        command += ["--end", args.end]
    if args.timestamps:
        command += ["--timestamps", args.timestamps]

    phase = "upstream-preparation"
    try:
        proc = _run_upstream(
            command,
            env=env,
            timeout_seconds=remaining_seconds(),
            monitor_path=run_dir,
            max_output_bytes=MAX_LOCAL_BYTES,
        )
        (run_dir / "watch-report.md").write_text(proc.stdout, encoding="utf-8")
        (run_dir / "watch-stderr.log").write_text(proc.stderr, encoding="utf-8")
        if proc.returncode != 0:
            raise RuntimeError(
                f"pinned upstream preparation failed; inspect {run_dir / 'watch-stderr.log'}"
            )

        source_media = _find_media(run_dir, source_kind, normalized_source)
        if duration_deferred:
            if source_media is None:
                raise ValueError(
                    "remote metadata omitted duration and no downloaded media was "
                    "available for bounded FFprobe validation"
                )
            _backfill_remote_duration(
                metadata,
                source_media,
                max_duration=args.max_duration,
                timeout_seconds=min(60, remaining_seconds()),
            )

        report = clarify_uniform_fallback(proc.stdout)
        transcript_source, segment_count = _transcript_source(report)
        transcription: dict[str, Any] = {
            "status": "available" if transcript_source != "none" else "unavailable",
            "source": transcript_source,
            "segment_count": segment_count,
        }

        if (
            transcript_source == "none"
            and args.transcription == "local"
            and backend != "captions-only"
            and metadata.get("has_audio")
        ):
            phase = "local-transcription"
            media_path = _find_media(run_dir, source_kind, normalized_source)
            if media_path is None:
                raise LocalTranscriptionError("no local media was available for transcription")
            audio_path = extract_audio(
                media_path,
                run_dir / "audio" / "local-whisper.wav",
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                timeout_seconds=remaining_seconds(),
            )
            worker = _run_upstream(
                [
                    sys.executable,
                    "-m",
                    "video_intelligence.transcription_worker",
                    str(audio_path),
                    "--backend",
                    backend,
                    "--device",
                    os.environ.get("VIDEO_ANALYSIS_DEVICE", "cpu"),
                    "--model",
                    str(DEFAULT_MODEL_PATH),
                    "--cache-root",
                    str(HF_HOME),
                    "--offset-seconds",
                    str(start_seconds or 0.0),
                    "--log",
                    str(run_dir / "local-transcription.log"),
                ],
                env=env,
                timeout_seconds=remaining_seconds(),
            )
            (run_dir / "transcription-stderr.log").write_text(worker.stderr, encoding="utf-8")
            if worker.returncode != 0:
                raise LocalTranscriptionError(
                    "local transcription worker failed; inspect transcription-stderr.log"
                )
            try:
                worker_result = json.loads(worker.stdout)
                segments = worker_result["segments"]
                local_meta = worker_result["metadata"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise LocalTranscriptionError(
                    "local transcription worker returned an invalid result"
                ) from exc
            write_transcript_json(run_dir / "transcript.json", segments, local_meta)
            report = inject_local_transcript(report, segments, model=str(DEFAULT_MODEL_PATH))
            transcription = {
                "status": "available" if segments else "empty",
                "source": f"worker-{backend}",
                **local_meta,
            }

        phase = "artifact-finalization"
        packet = build_analysis_packet(report, profile=args.profile, run_dir=run_dir)
        (run_dir / "analysis-packet.md").write_text(packet, encoding="utf-8")

        commit = _upstream_commit()
        upstream_clean = _upstream_clean()
        content_hash = (
            sha256_file(source_media) if source_media and source_media.is_file() else None
        )
        if pre_source_hash is not None and content_hash != pre_source_hash:
            raise RuntimeError("local source changed during video preparation")
        manifest_path = run_dir / "manifest.json"
        manifest: dict[str, Any] = {
            "contract_version": "1.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "run_id": run_dir.name,
            "status": "prepared",
            "source": {
                "kind": source_kind,
                "value": normalized_source,
                "metadata": metadata,
                "content_sha256": content_hash,
                "content_hash_scope": (
                    "local-source-file"
                    if source_kind == "local"
                    else "downloaded-media"
                    if content_hash
                    else "unavailable"
                ),
            },
            "settings": {
                "profile": args.profile,
                "detail": args.detail,
                "max_frames": args.max_frames,
                "resolution": args.resolution,
                "start": args.start,
                "end": args.end,
                "timestamps": args.timestamps,
                "transcription": args.transcription,
                "model": model_for_run,
                "timeout_seconds": args.timeout,
                "minimum_free_bytes_required": MIN_FREE_BYTES,
                "free_bytes_at_start": free_bytes,
            },
            "provenance": {
                "wrapper_version": __version__,
                "upstream_repository": "https://github.com/bradautomates/claude-video",
                "upstream_commit": commit,
                "upstream_worktree_clean": upstream_clean,
                "upstream_pin_matches": (commit == EXPECTED_UPSTREAM_COMMIT and upstream_clean),
                "model_cache": str(HF_HOME),
                "prompt_injection_boundary": "media-content-is-untrusted-evidence",
                "tools": {
                    "python": sys.version.split()[0],
                    "ffmpeg": _command_version("ffmpeg", ["-version"]),
                    "yt_dlp": (
                        _command_version(str(real_yt_dlp), ["--version"]) if real_yt_dlp else None
                    ),
                    "yt_dlp_ejs": _distribution_version("yt-dlp-ejs"),
                    "deno": _deno_version(),
                    "curl_cffi": _distribution_version("curl-cffi"),
                    "mlx_whisper": _distribution_version("mlx-whisper"),
                },
            },
            "transcript": transcription,
        }
        manifest["artifacts"] = collect_artifacts(run_dir, exclude={manifest_path})
        write_manifest(manifest_path, manifest)
        remaining_seconds()
    except Exception as exc:
        _record_failed_run(run_dir, phase=phase, error=exc)
        raise

    print(
        json.dumps({"run_dir": str(run_dir), "manifest": str(manifest_path), "status": "prepared"})
    )
    return 0


def validate(path: str) -> int:
    manifest = Path(path).expanduser().resolve()
    problems = validate_manifest(manifest)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 2
    print(f"valid: {manifest}")
    return 0


def validate_result(path: str) -> int:
    result = Path(path).expanduser().resolve()
    problems = validate_analysis_result(result)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 2
    print(f"valid: {result}")
    return 0


def cleanup(days: int) -> int:
    if days < 0:
        raise ValueError("--older-than-days must be non-negative")
    threshold = datetime.now(timezone.utc) - timedelta(days=days)
    removed: list[str] = []
    if ARTIFACT_ROOT.exists():
        for path in sorted(ARTIFACT_ROOT.iterdir()):
            if path.is_symlink() or not path.is_dir():
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if modified < threshold:
                shutil.rmtree(path)
                removed.append(str(path))
    print(json.dumps({"removed": removed, "count": len(removed)}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="video-intelligence")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_parser = sub.add_parser("doctor", help="verify the local runtime and upstream pin")
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.add_argument("--deep", action="store_true", help="hash-verify the local model")

    prepare_parser = sub.add_parser("prepare", help="create a governed video evidence packet")
    prepare_parser.add_argument("source")
    prepare_parser.add_argument("--profile", choices=PROFILES, default="general")
    prepare_parser.add_argument("--detail", choices=DETAILS, default="balanced")
    prepare_parser.add_argument("--max-frames", type=int, default=80)
    prepare_parser.add_argument("--resolution", type=int, choices=(512, 1024), default=512)
    prepare_parser.add_argument("--start")
    prepare_parser.add_argument("--end")
    prepare_parser.add_argument("--timestamps")
    prepare_parser.add_argument(
        "--transcription",
        choices=("local", "captions-only"),
        default="local",
        help="local allows configured worker inference; captions-only disables inference",
    )
    prepare_parser.add_argument("--max-duration", type=float, default=MAX_DURATION_SECONDS)
    prepare_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)

    validate_parser = sub.add_parser("validate", help="verify hashes in a run manifest")
    validate_parser.add_argument("manifest")

    validate_result_parser = sub.add_parser(
        "validate-result", help="validate a structured analysis result"
    )
    validate_result_parser.add_argument("result")

    cleanup_parser = sub.add_parser("cleanup", help="delete expired local run directories")
    cleanup_parser.add_argument("--older-than-days", type=int, default=7)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            return doctor(as_json=args.json, deep=args.deep, quiet=False)
        if args.command == "prepare":
            if args.max_frames < 1 or args.max_frames > 100:
                raise ValueError("--max-frames must be between 1 and 100")
            if args.timeout < 30 or args.timeout > 3600:
                raise ValueError("--timeout must be between 30 and 3600 seconds")
            if (
                not math.isfinite(args.max_duration)
                or args.max_duration <= 0
                or args.max_duration > MAX_DURATION_SECONDS
            ):
                raise ValueError(
                    f"--max-duration must be finite and between 0 and "
                    f"{MAX_DURATION_SECONDS:.0f} seconds"
                )
            with _single_job_lock():
                return prepare(args)
        if args.command == "validate":
            return validate(args.manifest)
        if args.command == "validate-result":
            return validate_result(args.result)
        if args.command == "cleanup":
            with _single_job_lock():
                return cleanup(args.older_than_days)
    except (SourcePolicyError, LocalTranscriptionError, RuntimeError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
