"""Transport tests use local subprocesses; no SSH connection is opened."""

import hashlib
import io
import json
import shlex
import subprocess
import sys
import tarfile
import time

import pytest

from video_intelligence import remote

RUN_ID = "20260909T120000123456Z-0123456789"


@pytest.fixture(autouse=True)
def configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEO_INTELLIGENCE_SSH_HOST", "worker")
    monkeypatch.setenv("VIDEO_INTELLIGENCE_REMOTE_ROOT", "/srv/video data")
    monkeypatch.setenv("VIDEO_INTELLIGENCE_LOCAL_RUNS", str(tmp_path / "runs"))
    monkeypatch.setenv("VIDEO_ANALYSIS_REMOTE_PYTHON", sys.executable)
    monkeypatch.setenv("VIDEO_ANALYSIS_BACKEND", "captions-only")
    monkeypatch.setenv("VIDEO_ANALYSIS_DEVICE", "cpu")
    monkeypatch.delenv("VIDEO_ANALYSIS_MODEL_PATH", raising=False)


def test_host_requires_explicit_configuration(monkeypatch):
    monkeypatch.delenv("VIDEO_INTELLIGENCE_SSH_HOST")
    with pytest.raises(remote.ClientError, match="VIDEO_INTELLIGENCE_SSH_HOST"):
        remote._config()


def test_worker_command_is_posix_and_preserves_arguments(monkeypatch, tmp_path):
    # A small executable reports exactly what the remote shell would pass to Python.
    executable = tmp_path / "python interpreter"
    executable.write_text(
        f"#!{sys.executable}\nimport os,sys,json\n"
        "print(json.dumps({'args': sys.argv[1:], 'env': dict(os.environ)}))\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("VIDEO_ANALYSIS_REMOTE_PYTHON", str(executable))
    monkeypatch.setenv("VIDEO_ANALYSIS_BACKEND", "faster-whisper")
    monkeypatch.setenv("VIDEO_ANALYSIS_MODEL_PATH", "/srv/models/a 'model'")
    command = remote._worker_command("/srv/data 'quoted'", ["prepare", "a 'b'; $(false)"])
    result = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["args"] == ["-m", "video_intelligence.cli", "prepare", "a 'b'; $(false)"]
    assert report["env"]["VIDEO_ANALYSIS_DATA_DIR"] == "/srv/data 'quoted'"
    assert report["env"]["VIDEO_ANALYSIS_MODEL_PATH"] == "/srv/models/a 'model'"
    assert report["env"]["VIDEO_ANALYSIS_BACKEND"] == "faster-whisper"
    assert report["env"]["VIDEO_ANALYSIS_DEVICE"] == "cpu"
    assert report["env"]["PATH"].split(":")[0] == str(tmp_path)


def test_worker_requires_absolute_python(monkeypatch):
    monkeypatch.setenv("VIDEO_ANALYSIS_REMOTE_PYTHON", "python3")
    with pytest.raises(remote.ClientError, match="VIDEO_ANALYSIS_REMOTE_PYTHON"):
        remote._worker_command("/srv/data", ["doctor"])


def test_worker_run_directory_uses_data_root():
    path = f"/srv/video data/runs/{RUN_ID}"
    assert remote._validate_remote_result(
        {"run_dir": path, "manifest": f"{path}/manifest.json"}, "/srv/video data"
    ) == (path, f"{path}/manifest.json", RUN_ID)


def local_process(monkeypatch):
    monkeypatch.setattr(remote, "_ssh_base", lambda host: [sys.executable, "-c"])


def test_remote_output_is_bounded(monkeypatch):
    local_process(monkeypatch)
    with pytest.raises(remote.ClientError, match="limit"):
        remote._receive_remote(
            "worker",
            "import sys; sys.stdout.write('x'*10000)",
            io.BytesIO(),
            timeout=2,
            max_bytes=100,
        )


def test_remote_stall_times_out_before_tar_read(monkeypatch):
    local_process(monkeypatch)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        remote._receive_remote(
            "worker", "import time; time.sleep(10)", io.BytesIO(), timeout=0.1, max_bytes=100
        )
    assert time.monotonic() - started < 2


def bundle(tmp_path, monkeypatch, *, artifact_content=b"evidence", archive_content=None):
    manifest = json.dumps(
        {
            "contract_version": "1.0",
            "created_at": "2026-09-09T12:00:00Z",
            "run_id": RUN_ID,
            "status": "prepared",
            "source": {
                "kind": "local",
                "value": "/srv/video.mp4",
                "metadata": {},
                "content_sha256": None,
                "content_hash_scope": "unavailable",
            },
            "settings": {},
            "provenance": {
                "wrapper_version": "0.1.0",
                "upstream_repository": "https://example.org/upstream",
                "upstream_commit": "abc",
                "upstream_pin_matches": True,
                "prompt_injection_boundary": "media-content-is-untrusted-evidence",
                "tools": {},
            },
            "transcript": {"status": "unavailable", "source": "none"},
            "artifacts": [
                {
                    "path": "analysis-packet.md",
                    "bytes": len(artifact_content),
                    "sha256": hashlib.sha256(artifact_content).hexdigest(),
                },
                {"path": "media.mp4", "bytes": 500, "sha256": "0" * 64},
            ],
        }
    ).encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        for name, content in [
            ("manifest.json", manifest),
            (
                "analysis-packet.md",
                archive_content if archive_content is not None else artifact_content,
            ),
        ]:
            member = tarfile.TarInfo(name)
            member.size = len(content)
            tar.addfile(member, io.BytesIO(content))
    archive_path = tmp_path / "evidence.tar"
    archive_path.write_bytes(stream.getvalue())
    # Substitute the remote shell with a local shell, keeping the real transfer reader.
    monkeypatch.setattr(remote, "_ssh_base", lambda host: ["/bin/sh", "-c"])
    monkeypatch.setattr(
        remote, "_tar_command", lambda *args: f"cat {shlex.quote(str(archive_path))}"
    )
    return {
        "host": "worker",
        "remote_run_dir": f"/srv/video data/runs/{RUN_ID}",
        "run_id": RUN_ID,
        "manifest_bytes": manifest,
        "local_runs": tmp_path / "runs",
        "timeout": 2,
    }


def test_retrieval_is_validated_and_repeat_fetch_is_idempotent(tmp_path, monkeypatch):
    from video_intelligence.artifacts import validate_manifest

    arguments = bundle(tmp_path, monkeypatch)
    first = remote._fetch_evidence(**arguments)
    target, retrieved, omitted = first
    assert (target / "analysis-packet.md").read_bytes() == b"evidence"
    assert retrieved == ["analysis-packet.md", "manifest.json"]
    assert omitted == ["media.mp4"]
    assert validate_manifest(target / "manifest.json") == []
    monkeypatch.setattr(
        remote, "_ssh_base", lambda host: pytest.fail("identical bundle should not transfer again")
    )
    assert remote._fetch_evidence(**arguments) == first


def test_repeat_fetch_refuses_different_local_content(tmp_path, monkeypatch):
    arguments = bundle(tmp_path, monkeypatch)
    target, _, _ = remote._fetch_evidence(**arguments)
    (target / "analysis-packet.md").write_bytes(b"changed")
    with pytest.raises(remote.ClientError, match="mismatch|differ"):
        remote._fetch_evidence(**arguments)
    assert (target / "analysis-packet.md").read_bytes() == b"changed"


def test_artifact_size_is_checked_before_writing(tmp_path, monkeypatch):
    arguments = bundle(tmp_path, monkeypatch, archive_content=b"too much data")
    with pytest.raises(remote.ClientError, match="size mismatch"):
        remote._fetch_evidence(**arguments)
    assert not list((tmp_path / "runs").iterdir())


def test_prepare_checks_contract_before_upload(monkeypatch):
    def respond(host, command, **kwargs):
        assert "doctor" in command, "incompatible worker must be rejected before prepare"
        return subprocess.CompletedProcess(
            [], 0, '{"contract_version":"2.0","version":"0.2.0"}', ""
        )

    monkeypatch.setattr(remote, "_run_remote", respond)
    args = remote.build_parser().parse_args(["prepare", "/nonexistent.mp4"])
    with pytest.raises(remote.ClientError, match="incompatible.*contract"):
        remote.command_prepare(args)


def test_uncertain_prepare_retains_uploaded_source(monkeypatch):
    stage = "/srv/video data/remote-inputs/stage"
    monkeypatch.setattr(remote, "_source_for_remote", lambda *a: (f"{stage}/video.mp4", stage))

    def respond(host, command, **kwargs):
        assert "rm -rf" not in command, "uncertain worker may still be reading its upload"
        if "doctor" in command:
            return subprocess.CompletedProcess(
                [], 0, '{"contract_version":"1.0","version":"0.1.0"}', ""
            )
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(remote, "_run_remote", respond)
    args = remote.build_parser().parse_args(["prepare", "video.mp4"])
    with pytest.raises(remote.ClientError, match="retained.*remote-inputs/stage"):
        remote.command_prepare(args)


@pytest.mark.parametrize(
    "path", ["manifest.json", "remote-origin.json", "../outside.md", "/outside.md"]
)
def test_manifest_rejects_reserved_and_unsafe_paths(path):
    with pytest.raises(remote.ClientError, match="reserved|unsafe"):
        remote._select_evidence({"artifacts": [{"path": path, "bytes": 0, "sha256": "0" * 64}]})


def test_artifact_boolean_size_is_not_an_integer_size():
    with pytest.raises(remote.ClientError, match="size"):
        remote._select_evidence(
            {"artifacts": [{"path": "a.md", "bytes": True, "sha256": "0" * 64}]}
        )


def test_retry_checks_origin_identity(tmp_path, monkeypatch):
    arguments = bundle(tmp_path, monkeypatch)
    target, _, _ = remote._fetch_evidence(**arguments)
    origin_path = target / "remote-origin.json"
    origin = json.loads(origin_path.read_bytes())
    origin["remote_manifest_sha256"] = "0" * 64
    origin_path.write_text(json.dumps(origin))
    with pytest.raises(remote.ClientError, match="origin.*differ|origin.*mismatch"):
        remote._fetch_evidence(**arguments)


def test_fetch_checks_contract_before_validation(monkeypatch):
    def respond(host, command, **kwargs):
        assert "doctor" in command
        return subprocess.CompletedProcess(
            [], 0, '{"contract_version":"2.0","version":"0.2.0"}', ""
        )

    monkeypatch.setattr(remote, "_run_remote", respond)
    args = remote.build_parser().parse_args(["fetch", f"/srv/video data/runs/{RUN_ID}"])
    with pytest.raises(remote.ClientError, match="incompatible.*contract"):
        remote.command_fetch(args)


def test_tar_hash_mismatch_removes_partial(tmp_path, monkeypatch):
    arguments = bundle(tmp_path, monkeypatch, archive_content=b"changed!")
    with pytest.raises(remote.ClientError, match="hash mismatch"):
        remote._fetch_evidence(**arguments)
    assert not list((tmp_path / "runs").iterdir())


def test_prepare_transfer_timeout_reports_preserved_remote_run(monkeypatch):
    run = f"/srv/video data/runs/{RUN_ID}"

    def respond(host, command, **kwargs):
        if "doctor" in command:
            report = {"contract_version": "1.0", "version": "0.1.0"}
        else:
            report = {"run_dir": run, "manifest": f"{run}/manifest.json"}
        return subprocess.CompletedProcess([], 0, json.dumps(report), "")

    monkeypatch.setattr(remote, "_run_remote", respond)

    def stall(*args, **kwargs):
        raise subprocess.TimeoutExpired("cat", 60)

    monkeypatch.setattr(remote, "_read_remote_manifest", stall)
    args = remote.build_parser().parse_args(["prepare", "https://example.org/video.mp4"])
    with pytest.raises(remote.ClientError, match="remote run preserved.*Retry"):
        remote.command_prepare(args)


def test_malformed_manifest_encoding_is_a_client_error(tmp_path, monkeypatch):
    arguments = bundle(tmp_path, monkeypatch)
    arguments["manifest_bytes"] = b"\xff"
    with pytest.raises(remote.ClientError, match="invalid JSON"):
        remote._fetch_evidence(**arguments)


def test_tar_trailing_data_cannot_bypass_stream_limit(tmp_path, monkeypatch):
    arguments = bundle(tmp_path, monkeypatch)
    with (tmp_path / "evidence.tar").open("ab") as handle:
        handle.write(b"x" * 100_000)
    with pytest.raises(remote.ClientError, match="transfer limit"):
        remote._fetch_evidence(**arguments)
    assert not list((tmp_path / "runs").iterdir())


def test_archive_symlink_is_rejected(tmp_path, monkeypatch):
    arguments = bundle(tmp_path, monkeypatch)
    with tarfile.open(tmp_path / "evidence.tar", "w") as archive:
        member = tarfile.TarInfo("analysis-packet.md")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        archive.addfile(member)
    with pytest.raises(remote.ClientError, match="unexpected member"):
        remote._fetch_evidence(**arguments)
    assert not list((tmp_path / "runs").iterdir())


def test_successful_prepare_cleans_upload_after_worker_finishes(tmp_path, monkeypatch):
    arguments = bundle(tmp_path, monkeypatch)
    stage = "/srv/video data/remote-inputs/stage"
    upload_present = True
    worker_finished = False
    monkeypatch.setattr(remote, "_source_for_remote", lambda *a: (f"{stage}/video.mp4", stage))
    monkeypatch.setattr(
        remote, "_read_remote_manifest", lambda *a, **kw: arguments["manifest_bytes"]
    )

    def respond(host, command, **kwargs):
        nonlocal upload_present, worker_finished
        if "doctor" in command:
            report = {"contract_version": "1.0", "version": "0.1.0"}
        elif "rm -rf" in command:
            assert worker_finished
            assert shlex.quote(stage) in command
            upload_present = False
            report = {}
        else:
            worker_finished = True
            report = {
                "run_dir": arguments["remote_run_dir"],
                "manifest": f"{arguments['remote_run_dir']}/manifest.json",
            }
        return subprocess.CompletedProcess([], 0, json.dumps(report), "")

    monkeypatch.setattr(remote, "_run_remote", respond)
    args = remote.build_parser().parse_args(["prepare", "video.mp4"])
    assert remote.command_prepare(args) == 0
    assert not upload_present
    assert (arguments["local_runs"] / RUN_ID / "manifest.json").exists()
