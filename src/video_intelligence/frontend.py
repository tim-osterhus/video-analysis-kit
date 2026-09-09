"""Public CLI: select a configured execution target without changing analysis."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import __version__
from .configuration import config_path, data_path, environment, load, save


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="video-analysis")
    parser.add_argument("--config", help="private machine configuration JSON")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    configure = sub.add_parser("configure", help="write private machine configuration")
    configure.add_argument("--execution", choices=("local", "ssh"), required=True)
    configure.add_argument(
        "--backend", choices=("captions-only", "mlx", "faster-whisper"), default="captions-only"
    )
    configure.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    configure.add_argument("--data-dir", default=str(data_path()))
    configure.add_argument("--model-path")
    configure.add_argument("--host")
    configure.add_argument("--remote-python")
    configure.add_argument("--remote-data-dir")
    configure.add_argument("--replace", action="store_true")
    sub.add_parser("config", help="show effective machine configuration")
    install = sub.add_parser("install-skill", help="install instructions and configured launcher")
    install.add_argument("--destination", default="~/.agents/skills/analyze-video")
    install.add_argument("--replace", action="store_true")
    for name in (
        "doctor",
        "prepare",
        "fetch",
        "validate",
        "validate-result",
        "cleanup",
        "models-fetch",
    ):
        sub.add_parser(name, add_help=False, help=f"{name} (use {name} --help for options)")
    return parser


def main() -> int:
    parser = build_parser()
    args, remaining = parser.parse_known_args()
    path = config_path(args.config)
    try:
        if args.command == "configure":
            if remaining:
                parser.error("unrecognized arguments: " + " ".join(remaining))
            config = {
                key: value
                for key, value in vars(args).items()
                if key not in ("config", "command", "replace") and value is not None
            }
            config["version"] = 1
            config["data_dir"] = str(Path(config["data_dir"]).expanduser().absolute())
            if config.get("model_path") and config["execution"] == "local":
                config["model_path"] = str(Path(config["model_path"]).expanduser().absolute())
            save(path, config, replace=args.replace)
            print(json.dumps({"config": str(path), **config}, indent=2))
            return 0
        if args.command == "models-fetch":
            from .models import main as fetch_model

            return fetch_model(remaining) or 0
        if args.command in ("validate", "validate-result"):
            return subprocess.call(
                [sys.executable, "-m", "video_intelligence.cli", args.command, *remaining]
            )
        # Help never requires a deployment or connectivity.
        if "--help" in remaining or "-h" in remaining:
            module = (
                "video_intelligence.remote" if args.command == "fetch" else "video_intelligence.cli"
            )
            return subprocess.call([sys.executable, "-m", module, args.command, *remaining])
        config = load(path)
        if args.command == "config":
            print(json.dumps(config, indent=2))
            return 0
        if args.command == "install-skill":
            from .installation import install_skill

            destination = Path(args.destination).expanduser().absolute()
            installed, backup = install_skill(destination, path, replace=args.replace)
            print(
                json.dumps(
                    {
                        "installed_skill": str(installed),
                        "config": str(path),
                        "backup": str(backup) if backup else None,
                    }
                )
            )
            return 0
        env = environment(config)
        if config["execution"] == "ssh":
            if args.command == "cleanup":
                # Validate cleanup arguments locally, then forward only the intended operation.
                cleanup = argparse.ArgumentParser(prog="video-analysis cleanup")
                cleanup.add_argument("--older-than-days", type=int, default=7)
                options = cleanup.parse_args(remaining)
                if options.older_than_days < 0:
                    raise ValueError("retention days must be non-negative")
                os.environ.update(env)
                from . import remote

                host, remote_root, _ = remote._config()
                result = remote._run_remote(
                    host,
                    remote._worker_command(
                        remote_root, ["cleanup", "--older-than-days", str(options.older_than_days)]
                    ),
                    timeout=120,
                )
                print(result.stdout, end="")
                return result.returncode
            return subprocess.call(
                [sys.executable, "-m", "video_intelligence.remote", args.command, *remaining],
                env=env,
            )
        if args.command == "fetch":
            raise ValueError(
                "fetch requires SSH execution; local runs already reside in data_dir/runs"
            )
        result = subprocess.run(
            [sys.executable, "-m", "video_intelligence.cli", args.command, *remaining],
            env=env,
            text=True,
            capture_output=True,
        )
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        if args.command == "prepare" and result.returncode == 0:
            output = json.loads(result.stdout)
            output.update(
                local_run_dir=output["run_dir"],
                analysis_packet=str(Path(output["run_dir"]) / "analysis-packet.md"),
            )
            print(json.dumps(output))
        else:
            print(result.stdout, end="")
        return result.returncode
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
