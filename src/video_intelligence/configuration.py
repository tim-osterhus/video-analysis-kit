"""Machine configuration, kept outside the distributable skill and package."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def config_path(value: str | None = None) -> Path:
    return (
        Path(
            value
            or os.environ.get("VIDEO_ANALYSIS_CONFIG")
            or str(
                Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
                / "video-analysis/config.json"
            )
        )
        .expanduser()
        .absolute()
    )


def data_path() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "video-analysis"


def validate(config: dict) -> dict:
    if config.get("version") != 1:
        raise ValueError("unsupported configuration version")
    if config.get("execution") not in ("local", "ssh"):
        raise ValueError("execution must be local or ssh")
    if config.get("backend") not in ("mlx", "faster-whisper", "captions-only"):
        raise ValueError("unsupported transcription backend")
    if config.get("device") not in ("cpu", "cuda"):
        raise ValueError("device must be cpu or cuda")
    for key in ("data_dir", "model_path"):
        value = config.get(key)
        if value is not None and (not isinstance(value, str) or not Path(value).is_absolute()):
            raise ValueError(f"{key} must be an absolute path")
    if not config.get("data_dir"):
        raise ValueError("data_dir is required")
    if config["execution"] == "ssh":
        host = config.get("host", "")
        if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", host):
            raise ValueError("SSH execution requires a valid --host alias")
        for key in ("remote_python", "remote_data_dir"):
            value = config.get(key, "")
            if not isinstance(value, str) or not value.startswith("/") or ".." in Path(value).parts:
                raise ValueError(f"SSH execution requires absolute --{key.replace('_', '-')}")
    return config


def load(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"No configuration at {path}; run video-analysis configure first")
    return validate(json.loads(path.read_text()))


def backup_name(path: Path) -> Path:
    return path.with_name(
        path.name + ".backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    )


def save(path: Path, config: dict, *, replace: bool = False) -> None:
    validate(config)
    if path.exists() or path.is_symlink():
        if not replace:
            raise ValueError(f"{path} already exists; use --replace to back it up before replacing")
        if path.is_symlink():
            raise ValueError("refusing to replace a symlinked configuration file")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(config, output, indent=2)
            output.write("\n")
        if path.exists():
            path.rename(backup_name(path))
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def environment(config: dict) -> dict[str, str]:
    result = dict(os.environ)
    for name in (
        "VIDEO_ANALYSIS_MODEL_PATH",
        "VIDEO_ANALYSIS_REMOTE_PYTHON",
        "VIDEO_INTELLIGENCE_SSH_HOST",
        "VIDEO_INTELLIGENCE_REMOTE_ROOT",
    ):
        result.pop(name, None)
    result.update(
        VIDEO_ANALYSIS_DATA_DIR=config["data_dir"],
        VIDEO_ANALYSIS_BACKEND=config["backend"],
        VIDEO_ANALYSIS_DEVICE=config["device"],
        VIDEO_INTELLIGENCE_LOCAL_RUNS=str(Path(config["data_dir"]) / "runs"),
    )
    if config.get("model_path"):
        result["VIDEO_ANALYSIS_MODEL_PATH"] = config["model_path"]
    if config["execution"] == "ssh":
        result.update(
            VIDEO_INTELLIGENCE_SSH_HOST=config["host"],
            VIDEO_INTELLIGENCE_REMOTE_ROOT=config["remote_data_dir"],
            VIDEO_ANALYSIS_REMOTE_PYTHON=config["remote_python"],
        )
    return result
