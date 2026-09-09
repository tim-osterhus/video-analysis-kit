from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
from pathlib import Path
from typing import Any


class LocalTranscriptionError(RuntimeError):
    pass


def extract_audio(
    media_path: Path,
    output_path: Path,
    *,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    timeout_seconds: int = 120,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start_seconds is not None:
        cmd += ["-ss", f"{start_seconds:.3f}"]
    cmd += ["-i", str(media_path)]
    if end_seconds is not None:
        duration = end_seconds - (start_seconds or 0.0)
        if duration <= 0:
            raise LocalTranscriptionError("transcription end must be greater than start")
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output_path)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise LocalTranscriptionError(
            f"FFmpeg audio extraction exceeded {timeout_seconds} seconds"
        ) from exc
    if proc.returncode != 0:
        raise LocalTranscriptionError(f"FFmpeg audio extraction failed: {proc.stderr.strip()}")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise LocalTranscriptionError("FFmpeg produced no transcription audio")
    return output_path


def transcribe_local(
    audio_path: Path,
    *,
    model: str,
    cache_root: Path,
    offset_seconds: float = 0.0,
    log_path: Path | None = None,
    backend: str = "mlx",
    device: str = "cpu",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if backend not in {"mlx", "faster-whisper"}:
        raise LocalTranscriptionError(f"unsupported transcription backend: {backend}")
    if device not in {"cpu", "cuda"}:
        raise LocalTranscriptionError(f"unsupported transcription device: {device}")
    model_path = Path(model).expanduser().resolve()
    if not model_path.is_dir():
        raise LocalTranscriptionError(f"local model directory is missing: {model_path}")
    cache_root.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache_root)
    os.environ["HF_HUB_CACHE"] = str(cache_root / "hub")

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            if backend == "mlx":
                import mlx_whisper

                result = mlx_whisper.transcribe(
                    str(audio_path),
                    path_or_hf_repo=str(model_path),
                    word_timestamps=False,
                    verbose=False,
                )
            else:
                from faster_whisper import WhisperModel

                engine = WhisperModel(str(model_path), device=device, local_files_only=True)
                decoded, info = engine.transcribe(str(audio_path), word_timestamps=False)
                # Decoding is lazy; consume the iterator while output is redirected.
                result = {
                    "segments": [
                        {"start": segment.start, "end": segment.end, "text": segment.text}
                        for segment in decoded
                    ],
                    "language": info.language,
                }
    except ImportError as exc:
        extra = "mlx" if backend == "mlx" else "cpu"
        raise LocalTranscriptionError(
            f"{backend} is unavailable; install video-analysis[{extra}]"
        ) from exc
    except Exception as exc:
        raise LocalTranscriptionError(f"{backend} Whisper failed: {exc}") from exc
    finally:
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                stdout_buffer.getvalue() + stderr_buffer.getvalue(), encoding="utf-8"
            )

    segments: list[dict[str, Any]] = []
    for raw in result.get("segments") or []:
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        segments.append(
            {
                "start": round(float(raw.get("start") or 0.0) + offset_seconds, 2),
                "end": round(float(raw.get("end") or 0.0) + offset_seconds, 2),
                "text": text,
            }
        )

    metadata = {
        "backend": "mlx-whisper" if backend == "mlx" else "faster-whisper",
        "model": model,
        "language": result.get("language"),
        "segment_count": len(segments),
        "cache_root": str(cache_root),
    }
    return segments, metadata


def write_transcript_json(
    output_path: Path,
    segments: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> None:
    output_path.write_text(
        json.dumps({"metadata": metadata, "segments": segments}, indent=2) + "\n",
        encoding="utf-8",
    )


def format_timestamp(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def render_transcript(segments: list[dict[str, Any]]) -> str:
    return "\n".join(f"[{format_timestamp(float(s['start']))}] {s['text']}" for s in segments)
