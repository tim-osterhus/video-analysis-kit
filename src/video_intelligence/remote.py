#!/usr/bin/env python3
"""Run a configured SSH worker and retrieve bounded, verified evidence."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import selectors
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from urllib.parse import urlparse

DEFAULT_LOCAL_RUNS = "~/.local/share/video-analysis/runs"
CONTRACT_VERSION = "1.0"
REMOTE_RUN_ID = re.compile(r"^\d{8}T\d{12,}Z-[a-f0-9]{10}$")
ALLOWED_EVIDENCE_SUFFIXES = {
    ".json",
    ".jpeg",
    ".jpg",
    ".log",
    ".md",
    ".png",
    ".srt",
    ".txt",
    ".vtt",
    ".webp",
}
MAX_MANIFEST_BYTES = 10 * 1024 * 1024
MAX_EVIDENCE_BYTES = 512 * 1024 * 1024


class ClientError(RuntimeError):
    """Expected remote-client failure."""


def _config() -> tuple[str, str, Path]:
    host = os.environ.get("VIDEO_INTELLIGENCE_SSH_HOST", "").strip()
    remote_root = _absolute_remote_path("VIDEO_INTELLIGENCE_REMOTE_ROOT")
    local_runs = Path(
        os.environ.get("VIDEO_INTELLIGENCE_LOCAL_RUNS", DEFAULT_LOCAL_RUNS)
    ).expanduser()
    if not host or host.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_.@:-]+", host):
        raise ClientError("VIDEO_INTELLIGENCE_SSH_HOST is invalid")
    return host, remote_root, local_runs


def _ssh_base(host: str) -> list[str]:
    ssh = shutil.which("ssh")
    if not ssh:
        raise ClientError("ssh is unavailable")
    return [
        ssh,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        host,
    ]


def _absolute_remote_path(name: str) -> str:
    value = os.environ.get(name, "").strip()
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or any(ord(c) < 32 for c in value):
        raise ClientError(f"{name} must be an absolute safe path")
    return str(path)


def _worker_command(remote_root: str, arguments: Sequence[str]) -> str:
    executable = _absolute_remote_path("VIDEO_ANALYSIS_REMOTE_PYTHON")
    backend = os.environ.get("VIDEO_ANALYSIS_BACKEND", "captions-only")
    device = os.environ.get("VIDEO_ANALYSIS_DEVICE", "cpu")
    if backend not in {"mlx", "faster-whisper", "captions-only"}:
        raise ClientError("VIDEO_ANALYSIS_BACKEND must be mlx, faster-whisper, or captions-only")
    if device not in {"cpu", "cuda"}:
        raise ClientError("VIDEO_ANALYSIS_DEVICE must be cpu or cuda")
    model = (
        _absolute_remote_path("VIDEO_ANALYSIS_MODEL_PATH")
        if os.environ.get("VIDEO_ANALYSIS_MODEL_PATH")
        else ""
    )
    environment = [
        f"VIDEO_ANALYSIS_DATA_DIR={remote_root}",
        f"VIDEO_ANALYSIS_BACKEND={backend}",
        f"VIDEO_ANALYSIS_MODEL_PATH={model}",
        f"VIDEO_ANALYSIS_DEVICE={device}",
        f"PATH={PurePosixPath(executable).parent}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
    ]
    return shlex.join(["env", *environment, executable, "-m", "video_intelligence.cli", *arguments])


def _tar_command(remote_run_dir: str, paths: Sequence[str]) -> str:
    return (
        f"cd {shlex.quote(remote_run_dir)} && COPYFILE_DISABLE=1 tar -cf - -- {shlex.join(paths)}"
    )


def _receive_remote(
    host: str,
    command: str,
    output: BinaryIO,
    *,
    timeout: float,
    max_bytes: int,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Drain both SSH pipes with one deadline and strict byte limits."""
    arguments = [*_ssh_base(host), command]
    deadline = time.monotonic() + timeout
    stderr = bytearray()
    received = 0
    with subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        try:
            with selectors.DefaultSelector() as selector:
                assert proc.stdout is not None and proc.stderr is not None
                selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
                selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(arguments, timeout)
                    for key, _ in selector.select(remaining):
                        block = os.read(key.fileobj.fileno(), 64 * 1024)
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        if key.data == "stdout":
                            received += len(block)
                            if received > max_bytes:
                                raise ClientError(
                                    f"remote output exceeds the {max_bytes}-byte transfer limit"
                                )
                            output.write(block)
                        else:
                            stderr.extend(block)
                            if len(stderr) > 1024 * 1024:
                                raise ClientError("remote stderr exceeds the 1 MiB transfer limit")
            returncode = proc.wait(timeout=max(0, deadline - time.monotonic()))
        except BaseException:
            proc.kill()
            proc.wait()
            raise
    detail = stderr.decode("utf-8", errors="replace").strip()
    if check and returncode:
        raise ClientError(detail or f"remote command failed (exit {returncode})")
    return subprocess.CompletedProcess(arguments, returncode, "", detail)


def _run_remote(
    host: str,
    command: str,
    *,
    timeout: int,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    with io.BytesIO() as output:
        proc = _receive_remote(
            host,
            command,
            output,
            timeout=timeout,
            max_bytes=MAX_MANIFEST_BYTES,
            check=check,
        )
        proc.stdout = output.getvalue().decode("utf-8", errors="replace")
    return proc


def _parse_final_json(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ClientError("remote worker did not return JSON") from exc
    if not isinstance(value, dict):
        raise ClientError("remote worker returned a non-object JSON value")
    return value


def _validate_remote_result(result: dict[str, Any], remote_root: str) -> tuple[str, str, str]:
    run_dir = str(result.get("run_dir") or "")
    manifest = str(result.get("manifest") or "")
    run_path = PurePosixPath(run_dir)
    expected_parent = PurePosixPath(remote_root) / "runs"
    if run_path.parent != expected_parent or not REMOTE_RUN_ID.fullmatch(run_path.name):
        raise ClientError("remote worker returned an unexpected run directory")
    expected_manifest = run_path / "manifest.json"
    if PurePosixPath(manifest) != expected_manifest:
        raise ClientError("remote worker returned an unexpected manifest path")
    return str(run_path), str(expected_manifest), run_path.name


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or any(ord(character) < 32 for character in value)
    ):
        raise ClientError(f"unsafe artifact path in remote manifest: {value!r}")
    return path


def _select_evidence(manifest: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    selected: dict[str, dict[str, Any]] = {}
    omitted: list[str] = []
    total = 0
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ClientError("remote manifest has no artifact list")
    for raw in artifacts:
        if not isinstance(raw, dict):
            raise ClientError("remote manifest contains an invalid artifact")
        path = _safe_relative(str(raw.get("path") or ""))
        size = raw.get("bytes")
        digest = raw.get("sha256")
        if type(size) is not int or size < 0:
            raise ClientError(f"invalid artifact size for {path}")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ClientError(f"invalid artifact hash for {path}")
        name = str(path)
        if name in {"manifest.json", "remote-origin.json"}:
            raise ClientError(f"reserved artifact path in remote manifest: {name}")
        if path.suffix.lower() not in ALLOWED_EVIDENCE_SUFFIXES:
            omitted.append(name)
            continue
        if name in selected:
            raise ClientError(f"duplicate artifact in remote manifest: {name}")
        total += size
        if total > MAX_EVIDENCE_BYTES:
            raise ClientError("retrieved evidence would exceed the 512 MiB client limit")
        selected[name] = {"bytes": size, "sha256": digest}
    return selected, omitted


def _read_remote_manifest(host: str, manifest_path: str, timeout: int) -> bytes:
    with io.BytesIO() as output:
        _receive_remote(
            host,
            f"cat -- {shlex.quote(manifest_path)}",
            output,
            timeout=timeout,
            max_bytes=MAX_MANIFEST_BYTES,
        )
        payload = output.getvalue()
    if not payload:
        raise ClientError("remote manifest is empty")
    return payload


def _validate_existing(
    target: Path, expected: dict[str, dict[str, Any]], origin: dict[str, Any]
) -> None:
    if target.is_symlink() or not target.is_dir():
        raise ClientError(f"existing local evidence differs: {target}")
    actual = set()
    for path in target.rglob("*"):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ClientError(f"existing local evidence differs: {path}")
        if path.is_file():
            actual.add(path.relative_to(target).as_posix())
    if actual != set(expected) | {"remote-origin.json"}:
        raise ClientError(f"existing local evidence files differ: {target}")
    try:
        origin_path = target / "remote-origin.json"
        if origin_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ClientError(f"existing local origin differs: {origin_path}")
        stored_origin = json.loads(origin_path.read_bytes())
    except (ValueError, OSError) as exc:
        raise ClientError(f"existing local origin differs: {target}") from exc
    if not isinstance(stored_origin, dict) or any(
        stored_origin.get(key) != value for key, value in origin.items()
    ):
        raise ClientError(f"existing local origin differs: {target}")
    for name, metadata in expected.items():
        path = target.joinpath(*PurePosixPath(name).parts)
        if path.stat().st_size != metadata["bytes"]:
            raise ClientError(f"size mismatch in existing local evidence: {name}")
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != metadata["sha256"]:
            raise ClientError(f"hash mismatch in existing local evidence: {name}")


def validate_evidence_bundle(path: Path, manifest: dict[str, Any]) -> None:
    """Verify an offline bundle against its manifest and deterministic transfer selection.

    The caller must validate the manifest schema first. A provenance record can
    acknowledge excluded media, but cannot exclude frames or other evidence.
    Raises ClientError when the bundle or its provenance differs.
    """
    manifest_bytes = path.read_bytes()
    selected, omitted = _select_evidence(manifest)
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    expected = {
        "manifest.json": {"bytes": len(manifest_bytes), "sha256": digest},
        **selected,
    }
    origin = {
        "contract_version": CONTRACT_VERSION,
        "remote_manifest_sha256": digest,
        "retrieved_files": sorted(expected),
        "omitted_media": sorted(omitted),
    }
    _validate_existing(path.parent, expected, origin)


def _fetch_evidence(
    *,
    host: str,
    remote_run_dir: str,
    run_id: str,
    manifest_bytes: bytes,
    local_runs: Path,
    timeout: int,
) -> tuple[Path, list[str], list[str]]:
    try:
        manifest = json.loads(manifest_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ClientError("remote manifest is invalid JSON") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("run_id") != run_id
        or manifest.get("status") != "prepared"
    ):
        raise ClientError("remote manifest identity or status is invalid")
    selected, omitted = _select_evidence(manifest)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    expected = {
        "manifest.json": {
            "bytes": len(manifest_bytes),
            "sha256": manifest_digest,
        },
        **selected,
    }

    origin = {
        "contract_version": CONTRACT_VERSION,
        "host": host,
        "remote_run_dir": remote_run_dir,
        "remote_manifest_sha256": manifest_digest,
        "retrieved_files": sorted(expected),
        "omitted_media": sorted(omitted),
    }
    local_runs.mkdir(parents=True, exist_ok=True)
    target = local_runs / run_id
    if target.exists() or target.is_symlink():
        _validate_existing(target, expected, origin)
        return target, sorted(expected), sorted(omitted)
    partial = local_runs / f".{run_id}.partial-{uuid.uuid4().hex}"
    partial.mkdir()

    paths = list(expected)
    command = _tar_command(remote_run_dir, paths)
    received: set[str] = set()
    try:
        with tempfile.TemporaryFile() as stream:
            # Allow bounded tar headers/padding in addition to declared evidence bytes.
            limit = sum(item["bytes"] for item in expected.values()) + 10240 + len(expected) * 8192
            _receive_remote(host, command, stream, timeout=timeout, max_bytes=limit)
            stream.seek(0)
            with tarfile.open(fileobj=stream, mode="r|") as archive:
                for member in archive:
                    name = member.name.removeprefix("./")
                    if name not in expected or name in received or not member.isfile():
                        raise ClientError(f"unexpected member in remote evidence archive: {name}")
                    if member.size != expected[name]["bytes"]:
                        raise ClientError(f"size mismatch while retrieving {name}")
                    source = archive.extractfile(member)
                    if source is None:
                        raise ClientError(f"could not read remote artifact: {name}")
                    destination = partial.joinpath(*PurePosixPath(name).parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    written = 0
                    with source, destination.open("xb") as handle:
                        while block := source.read(1024 * 1024):
                            written += len(block)
                            if written > expected[name]["bytes"]:
                                raise ClientError(f"size mismatch while retrieving {name}")
                            handle.write(block)
                            digest.update(block)
                    if written != expected[name]["bytes"]:
                        raise ClientError(f"size mismatch while retrieving {name}")
                    if digest.hexdigest() != expected[name]["sha256"]:
                        raise ClientError(f"hash mismatch while retrieving {name}")
                    received.add(name)
        missing = set(expected) - received
        if missing:
            raise ClientError(f"remote evidence archive omitted: {', '.join(sorted(missing))}")
        origin["retrieved_at"] = datetime.now(UTC).isoformat()
        (partial / "remote-origin.json").write_text(
            json.dumps(origin, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        partial.rename(target)
        return target, sorted(received), sorted(omitted)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise


def _source_for_remote(
    host: str,
    remote_root: str,
    source: str,
) -> tuple[str, str | None]:
    parsed = urlparse(source)
    if parsed.scheme:
        if parsed.scheme != "https":
            raise ClientError("remote video URLs must use HTTPS")
        return source, None
    unresolved = Path(source).expanduser()
    if unresolved.is_symlink():
        raise ClientError("symlinked local video paths are not allowed")
    try:
        local_source = unresolved.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ClientError(f"local video does not exist: {unresolved}") from exc
    if not local_source.is_file():
        raise ClientError(f"local video is not a regular file: {local_source}")

    stage_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}"
    stage_dir = f"{remote_root}/remote-inputs/{stage_id}"
    _run_remote(host, f"mkdir -p -- {shlex.quote(stage_dir)}", timeout=30)
    scp = shutil.which("scp")
    if not scp:
        _run_remote(host, f"rm -rf -- {shlex.quote(stage_dir)}", timeout=30, check=False)
        raise ClientError("scp is unavailable")
    proc = subprocess.run(
        [
            scp,
            "-p",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            str(local_source),
            f"{host}:{stage_dir}/",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    if proc.returncode != 0:
        _run_remote(host, f"rm -rf -- {shlex.quote(stage_dir)}", timeout=30, check=False)
        raise ClientError(proc.stderr.strip() or "local video upload failed")
    return f"{stage_dir}/{local_source.name}", stage_dir


def _check_contract(report: dict[str, Any]) -> None:
    if report.get("contract_version") != CONTRACT_VERSION:
        raise ClientError(
            f"incompatible worker contract {report.get('contract_version')!r}; "
            f"client requires {CONTRACT_VERSION}"
        )
    if not isinstance(report.get("version"), str) or not report["version"]:
        raise ClientError("remote worker did not report its version")


def _check_worker(host: str, remote_root: str) -> None:
    proc = _run_remote(
        host, _worker_command(remote_root, ["doctor", "--json"]), timeout=60, check=False
    )
    if not proc.stdout.strip():
        raise ClientError(proc.stderr.strip() or "remote worker health check failed")
    _check_contract(_parse_final_json(proc.stdout))


def command_doctor(args: argparse.Namespace) -> int:
    host, remote_root, _ = _config()
    arguments = ["doctor", "--json"]
    if args.deep:
        arguments.append("--deep")
    proc = _run_remote(
        host,
        _worker_command(remote_root, arguments),
        timeout=300 if args.deep else 60,
        check=False,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise ClientError(proc.stderr.strip() or "remote worker health check failed")
    report = _parse_final_json(proc.stdout)
    _check_contract(report)
    report["remote_host"] = host
    print(json.dumps(report, indent=2, sort_keys=True))
    return proc.returncode


def command_prepare(args: argparse.Namespace) -> int:
    host, remote_root, default_local_runs = _config()
    _check_worker(host, remote_root)
    local_runs = Path(args.output_dir).expanduser() if args.output_dir else default_local_runs
    remote_source, stage_dir = _source_for_remote(host, remote_root, args.source)
    worker_args = [
        "prepare",
        remote_source,
        "--profile",
        args.profile,
        "--detail",
        args.detail,
        "--max-frames",
        str(args.max_frames),
        "--resolution",
        str(args.resolution),
        "--transcription",
        args.transcription,
        "--max-duration",
        str(args.max_duration),
        "--timeout",
        str(args.timeout),
    ]
    for option, value in (
        ("--start", args.start),
        ("--end", args.end),
        ("--timestamps", args.timestamps),
    ):
        if value is not None:
            worker_args += [option, value]
    try:
        proc = _run_remote(
            host,
            _worker_command(remote_root, worker_args),
            timeout=args.timeout + 180,
        )
        result = _parse_final_json(proc.stdout)
        remote_run_dir, manifest_path, run_id = _validate_remote_result(result, remote_root)
    except (ClientError, OSError, subprocess.TimeoutExpired) as exc:
        if stage_dir is not None:
            raise ClientError(
                f"{exc}; upload retained at {stage_dir} because worker completion is uncertain"
            ) from exc
        raise
    if stage_dir is not None:
        try:
            cleanup = _run_remote(
                host, f"rm -rf -- {shlex.quote(stage_dir)}", timeout=30, check=False
            )
            if cleanup.returncode:
                print(f"warning: upload cleanup failed; retained at {stage_dir}", file=sys.stderr)
        except (ClientError, OSError, subprocess.TimeoutExpired):
            print(f"warning: upload cleanup failed; retained at {stage_dir}", file=sys.stderr)
    try:
        _run_remote(
            host,
            _worker_command(remote_root, ["validate", manifest_path]),
            timeout=120,
        )
        manifest_bytes = _read_remote_manifest(host, manifest_path, timeout=60)
        target, retrieved, omitted = _fetch_evidence(
            host=host,
            remote_run_dir=remote_run_dir,
            run_id=run_id,
            manifest_bytes=manifest_bytes,
            local_runs=local_runs,
            timeout=180,
        )
    except (ClientError, OSError, subprocess.TimeoutExpired, tarfile.TarError) as exc:
        raise ClientError(
            f"{exc}; remote run preserved at {remote_run_dir}. Retry with the fetch command"
        ) from exc
    output = {
        "status": "prepared",
        "remote_host": host,
        "remote_run_dir": remote_run_dir,
        "local_run_dir": str(target),
        "analysis_packet": str(target / "analysis-packet.md"),
        "manifest": str(target / "manifest.json"),
        "retrieved_file_count": len(retrieved),
        "omitted_media": omitted,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def command_fetch(args: argparse.Namespace) -> int:
    host, remote_root, default_local_runs = _config()
    _check_worker(host, remote_root)
    local_runs = Path(args.output_dir).expanduser() if args.output_dir else default_local_runs
    supplied = {
        "run_dir": args.remote_run_dir,
        "manifest": f"{args.remote_run_dir.rstrip('/')}/manifest.json",
    }
    remote_run_dir, manifest_path, run_id = _validate_remote_result(supplied, remote_root)
    _run_remote(
        host,
        _worker_command(remote_root, ["validate", manifest_path]),
        timeout=120,
    )
    manifest_bytes = _read_remote_manifest(host, manifest_path, timeout=60)
    target, retrieved, omitted = _fetch_evidence(
        host=host,
        remote_run_dir=remote_run_dir,
        run_id=run_id,
        manifest_bytes=manifest_bytes,
        local_runs=local_runs,
        timeout=180,
    )
    print(
        json.dumps(
            {
                "status": "retrieved",
                "remote_host": host,
                "remote_run_dir": remote_run_dir,
                "local_run_dir": str(target),
                "analysis_packet": str(target / "analysis-packet.md"),
                "manifest": str(target / "manifest.json"),
                "retrieved_file_count": len(retrieved),
                "omitted_media": omitted,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="remote-video-intelligence")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="check the configured SSH worker")
    doctor.add_argument("--deep", action="store_true", help="hash the pinned model")
    doctor.set_defaults(handler=command_doctor)

    fetch = subparsers.add_parser("fetch", help="retrieve an existing validated worker run")
    fetch.add_argument("remote_run_dir")
    fetch.add_argument("--output-dir")
    fetch.set_defaults(handler=command_fetch)

    prepare = subparsers.add_parser("prepare", help="prepare and retrieve video evidence")
    prepare.add_argument("source")
    prepare.add_argument(
        "--profile",
        choices=("general", "stack-relevance", "competitor-creative", "implementation-handoff"),
        default="general",
    )
    prepare.add_argument(
        "--detail", choices=("transcript", "efficient", "balanced"), default="balanced"
    )
    prepare.add_argument("--max-frames", type=int, choices=range(1, 101), default=80)
    prepare.add_argument("--resolution", type=int, choices=(512, 1024), default=512)
    prepare.add_argument("--start")
    prepare.add_argument("--end")
    prepare.add_argument("--timestamps")
    prepare.add_argument("--transcription", choices=("local", "captions-only"), default="local")
    prepare.add_argument("--max-duration", type=float, default=7200.0)
    prepare.add_argument("--timeout", type=int, choices=range(30, 3601), default=900)
    prepare.add_argument("--output-dir")
    prepare.set_defaults(handler=command_prepare)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.handler(args))
    except (ClientError, OSError, subprocess.TimeoutExpired, tarfile.TarError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
