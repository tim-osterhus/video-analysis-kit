"""Explicit model installation and offline verification of local artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import MODEL_LOCK

DEFAULT_FASTER_WHISPER_REPOSITORY = "Systran/faster-whisper-small"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _lock() -> dict[str, Any]:
    return json.loads(MODEL_LOCK.read_text(encoding="utf-8"))


def _required(backend: str) -> tuple[str, ...]:
    if backend == "mlx":
        # MLX Whisper uses its package's bundled tokenizer, not a Hub tokenizer.
        return ("config.json", "weights.safetensors")
    if backend == "faster-whisper":
        return ("config.json", "model.bin", "tokenizer.json")
    raise ValueError(f"unsupported transcription backend: {backend}")


def _read_receipt(model_path: Path) -> dict[str, Any]:
    try:
        value = json.loads((model_path / "verification.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _validate_json_artifact(path: Path) -> None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"model artifact is missing or invalid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"model artifact must contain a JSON object: {path.name}")


def model_status(backend: str, model_path: Path, *, deep: bool = False) -> dict[str, Any]:
    """Check recorded pins offline; deep checks hash files instead of trusting sizes."""
    required = _required(backend)
    model_path = Path(model_path).expanduser()
    lock = _lock() if backend == "mlx" else {}
    receipt = _read_receipt(model_path)
    status: dict[str, Any] = {
        "path": str(model_path),
        "installed": False,
        "pin_matches": False,
        "repository": receipt.get(
            "repository", lock.get("repository", DEFAULT_FASTER_WHISPER_REPOSITORY)
        ),
        "revision": receipt.get("revision", lock.get("revision")),
    }
    files = receipt.get("files")
    if backend == "mlx" and files is None:
        # Preserve installations made by the original pinned-weights downloader.
        weights = model_path / lock["weights_file"]
        status["installed"] = weights.is_file() and (model_path / "config.json").is_file()
        try:
            _validate_json_artifact(model_path / "config.json")
        except ValueError as exc:
            status["error"] = str(exc)
            return status
        actual = _sha256(weights) if deep and weights.is_file() else None
        status.update(weights_sha256=lock["weights_sha256"], actual_weights_sha256=actual)
        status["pin_matches"] = (
            actual == lock["weights_sha256"]
            if deep
            else bool(
                weights.is_file()
                and receipt.get("repository") == lock["repository"]
                and receipt.get("revision") == lock["revision"]
                and receipt.get("weights_sha256") == lock["weights_sha256"]
                and receipt.get("size_bytes") == weights.stat().st_size
            )
        )
        if not status["pin_matches"]:
            status["error"] = "MLX model is missing or does not match the pinned weights"
        return status

    status["installed"] = all((model_path / name).is_file() for name in required)
    try:
        if not status["installed"]:
            raise ValueError("model is missing required artifacts: " + ", ".join(required))
        for name in required:
            if name.endswith(".json"):
                _validate_json_artifact(model_path / name)
        if not isinstance(files, dict) or not files:
            raise ValueError(
                "model has no valid verification receipt; run the explicit model download command"
            )
        if receipt.get("backend") != backend or not receipt.get("repository"):
            raise ValueError("model receipt backend or repository does not match")
        if not re.fullmatch(r"[0-9a-f]{40}", str(receipt.get("revision", ""))):
            raise ValueError("model receipt has no resolved commit revision")
        if not set(required).issubset(files):
            raise ValueError("model receipt does not cover required artifacts")
        for name, record in files.items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or not isinstance(record, dict):
                raise ValueError(f"invalid model receipt entry: {name}")
            artifact = model_path / relative
            if not artifact.is_file() or artifact.stat().st_size != record.get("size_bytes"):
                raise ValueError(f"model artifact is missing or has changed size: {name}")
            expected = record.get("sha256", "")
            if not re.fullmatch(r"[0-9a-f]{64}", str(expected)):
                raise ValueError(f"model artifact has no valid hash: {name}")
            if deep and _sha256(artifact) != expected:
                raise ValueError(f"model artifact hash mismatch: {name}")
        if (
            backend == "mlx"
            and receipt["repository"] == lock["repository"]
            and receipt["revision"] == lock["revision"]
            and files[lock["weights_file"]]["sha256"] != lock["weights_sha256"]
        ):
            raise ValueError("MLX weights do not match the model lock")
        status["pin_matches"] = True
        status["verified_at"] = receipt.get("verified_at")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        status["error"] = str(exc)
    return status


def _download(backend: str, output: Path, repository: str | None, revision: str | None) -> None:
    required = _required(backend)
    lock = _lock() if backend == "mlx" else {}
    repository = repository or lock.get("repository", DEFAULT_FASTER_WHISPER_REPOSITORY)
    if revision is None and repository == lock.get("repository"):
        revision = lock["revision"]
    if output.exists():
        status = model_status(backend, output, deep=True)
        if (
            status["pin_matches"]
            and status["repository"] == repository
            and (revision is None or status["revision"] == revision)
        ):
            return
        raise ValueError(f"refusing to overwrite conflicting model directory: {output}")

    from huggingface_hub import snapshot_download

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".video-model-", dir=output.parent) as temporary:
        staging = Path(temporary)
        source = Path(
            snapshot_download(repo_id=repository, revision=revision, cache_dir=staging / "hub")
        )
        resolved_revision = source.name
        if not re.fullmatch(r"[0-9a-f]{40}", resolved_revision):
            raise ValueError("download did not resolve to an immutable snapshot commit")
        if revision and re.fullmatch(r"[0-9a-f]{40}", revision) and resolved_revision != revision:
            raise ValueError("downloaded snapshot does not match the requested commit")
        for name in required:
            if not (source / name).is_file() or (source / name).stat().st_size == 0:
                raise ValueError(f"model download is missing required artifact: {name}")
            if name.endswith(".json"):
                _validate_json_artifact(source / name)
        destination = staging / "model"
        shutil.copytree(source, destination)
        files = {
            str(path.relative_to(destination)): {
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
            for path in sorted(destination.rglob("*"))
            if path.is_file() and path.name != "verification.json"
        }
        receipt = {
            "backend": backend,
            "repository": repository,
            "revision": resolved_revision,
            "files": files,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
        (destination / "verification.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        status = model_status(backend, destination, deep=True)
        if not status["pin_matches"]:
            raise ValueError(status.get("error", "model verification failed"))
        if output.exists():
            raise ValueError(
                f"refusing to overwrite model directory created during download: {output}"
            )
        destination.rename(output)


def main(argv: list[str] | None = None) -> int:
    """Download only when explicitly invoked; importing this module stays offline."""
    parser = argparse.ArgumentParser(prog="video-intelligence models download")
    parser.add_argument("--backend", choices=("mlx", "faster-whisper"), default="mlx")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repository")
    parser.add_argument("--revision")
    args = parser.parse_args(argv)
    try:
        _download(args.backend, args.output.expanduser().resolve(), args.repository, args.revision)
    except Exception as exc:
        print(f"model download failed: {exc}", file=sys.stderr)
        return 1
    print(f"model ready: {args.output.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
