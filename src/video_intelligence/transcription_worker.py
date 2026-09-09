from __future__ import annotations

import argparse
import json
from pathlib import Path

from .local_transcription import transcribe_local


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="video-intelligence-transcription-worker")
    parser.add_argument("audio")
    parser.add_argument("--model", required=True)
    parser.add_argument("--backend", choices=("mlx", "faster-whisper"), default="mlx")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--cache-root", required=True)
    parser.add_argument("--offset-seconds", type=float, default=0.0)
    parser.add_argument("--log", required=True)
    args = parser.parse_args(argv)

    segments, metadata = transcribe_local(
        Path(args.audio),
        model=args.model,
        backend=args.backend,
        device=args.device,
        cache_root=Path(args.cache_root),
        offset_seconds=args.offset_seconds,
        log_path=Path(args.log),
    )
    print(json.dumps({"segments": segments, "metadata": metadata}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
