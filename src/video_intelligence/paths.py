from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "resources"
VENDOR_ROOT = ROOT / "vendor" / "claude-video"
UPSTREAM_WATCH = VENDOR_ROOT / "skills" / "watch" / "scripts" / "watch.py"
DATA_ROOT = Path(
    os.environ.get("VIDEO_ANALYSIS_DATA_DIR", "~/.local/share/video-analysis")
).expanduser()
ARTIFACT_ROOT = DATA_ROOT / "runs"
RUNTIME_ROOT = DATA_ROOT / "runtime"
HF_HOME = RUNTIME_ROOT / "huggingface"
MODEL_LOCK = ROOT / "models" / "model-lock.json"
DEFAULT_MODEL_PATH = Path(
    os.environ.get(
        "VIDEO_ANALYSIS_MODEL_PATH", str(DATA_ROOT / "models" / "whisper-large-v3-turbo")
    )
).expanduser()
SAFE_YTDLP = ROOT / "bin" / "yt-dlp"
EXPECTED_UPSTREAM_COMMIT = "83da59fa78c3eee9e20f515fe75c438bb5166efd"
