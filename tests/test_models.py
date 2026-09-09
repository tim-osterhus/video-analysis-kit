from __future__ import annotations

import hashlib
import json
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def models(tmp_path, monkeypatch):
    from video_intelligence import models

    lock = tmp_path / "model-lock.json"
    lock.write_text(
        json.dumps(
            {
                "repository": "test/mlx",
                "revision": "a" * 40,
                "weights_file": "weights.safetensors",
                "weights_sha256": hashlib.sha256(b"weights").hexdigest(),
            }
        )
    )
    monkeypatch.setattr(models, "MODEL_LOCK", lock)
    return models


def snapshot(tmp_path, monkeypatch, *, backend="faster-whisper", missing=None):
    source = tmp_path / "snapshots" / ("a" * 40)
    source.mkdir(parents=True)
    artifacts = {"config.json": b"{}", "tokenizer.json": b"{}"}
    artifacts["model.bin" if backend == "faster-whisper" else "weights.safetensors"] = b"weights"
    for name, content in artifacts.items():
        if name != missing:
            (source / name).write_bytes(content)

    def download(**kwargs):
        assert kwargs["repo_id"]
        return str(source)

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    return source


def test_explicit_download_records_commit_and_detects_same_size_tamper(
    models, tmp_path, monkeypatch
):
    snapshot(tmp_path, monkeypatch)
    output = tmp_path / "installed"
    assert models.main(["--backend", "faster-whisper", "--output", str(output)]) == 0
    receipt = json.loads((output / "verification.json").read_text())
    assert receipt["repository"] == "Systran/faster-whisper-small"
    assert receipt["revision"] == "a" * 40
    assert set(receipt["files"]) == {"config.json", "tokenizer.json", "model.bin"}
    assert models.model_status("faster-whisper", output, deep=True)["pin_matches"] is True
    (output / "tokenizer.json").write_bytes(b"[]")
    status = models.model_status("faster-whisper", output, deep=True)
    assert status["installed"] is True
    assert status["pin_matches"] is False
    assert "tokenizer.json" in status["error"]


def test_missing_tokenizer_rejected_without_partial_install(models, tmp_path, monkeypatch):
    snapshot(tmp_path, monkeypatch, missing="tokenizer.json")
    output = tmp_path / "installed"
    assert models.main(["--backend", "faster-whisper", "--output", str(output)]) != 0
    assert not output.exists()


def test_download_refuses_conflicting_directory(models, tmp_path, monkeypatch):
    output = tmp_path / "installed"
    output.mkdir()
    original = output / "keep.txt"
    original.write_text("keep")

    def download(**kwargs):
        pytest.fail("conflicting output must be rejected before network access")

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    assert models.main(["--backend", "faster-whisper", "--output", str(output)]) != 0
    assert original.read_text() == "keep"


def test_valid_install_is_idempotent_without_network(models, tmp_path, monkeypatch):
    snapshot(tmp_path, monkeypatch)
    output = tmp_path / "installed"
    args = ["--backend", "faster-whisper", "--output", str(output)]
    assert models.main(args) == 0

    def download(**kwargs):
        pytest.fail("valid installed model must not be downloaded again")

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    assert models.main(args) == 0
    assert models.main(args + ["--repository", "another/model"]) != 0


def test_legacy_mlx_hash_and_receipt_supported(models, tmp_path):
    output = tmp_path / "installed"
    output.mkdir()
    weights = output / "weights.safetensors"
    weights.write_bytes(b"weights")
    (output / "config.json").write_text("{}")
    lock = json.loads(models.MODEL_LOCK.read_text())
    receipt = {**lock, "size_bytes": 7}
    (output / "verification.json").write_text(json.dumps(receipt))
    assert models.model_status("mlx", output)["pin_matches"] is True
    assert models.model_status("mlx", output, deep=True)["pin_matches"] is True
    weights.write_bytes(b"changed")
    assert models.model_status("mlx", output, deep=True)["pin_matches"] is False


def test_new_mlx_snapshot_requires_config_and_pinned_weights(models, tmp_path, monkeypatch):
    source = snapshot(tmp_path, monkeypatch, backend="mlx", missing="config.json")
    output = tmp_path / "installed"
    args = ["--backend", "mlx", "--output", str(output)]
    assert models.main(args) != 0
    (source / "config.json").write_text("{}")
    (source / "weights.safetensors").write_text("wrong")
    assert models.main(args) != 0
    (source / "weights.safetensors").write_bytes(b"weights")
    assert models.main(args) == 0
    assert models.model_status("mlx", output, deep=True)["pin_matches"] is True


def test_missing_models_and_malformed_receipts_report_cleanly(models, tmp_path):
    status = models.model_status("faster-whisper", tmp_path / "missing", deep=True)
    assert status["installed"] is False
    assert status["pin_matches"] is False
    output = tmp_path / "broken"
    output.mkdir()
    (output / "verification.json").write_text("[]")
    assert models.model_status("faster-whisper", output)["pin_matches"] is False


def test_deleted_required_artifact_marks_new_install_missing(models, tmp_path, monkeypatch):
    snapshot(tmp_path, monkeypatch)
    output = tmp_path / "installed"
    assert models.main(["--backend", "faster-whisper", "--output", str(output)]) == 0
    (output / "config.json").unlink()
    status = models.model_status("faster-whisper", output, deep=True)
    assert status["installed"] is False
    assert status["pin_matches"] is False


def test_invalid_json_configuration_is_not_installed(models, tmp_path, monkeypatch):
    source = snapshot(tmp_path, monkeypatch)
    (source / "config.json").write_text("invalid JSON")
    output = tmp_path / "installed"
    assert models.main(["--backend", "faster-whisper", "--output", str(output)]) != 0
    assert not output.exists()


def test_explicit_commit_must_match_downloaded_snapshot(models, tmp_path, monkeypatch):
    snapshot(tmp_path, monkeypatch)
    output = tmp_path / "installed"
    assert (
        models.main(
            ["--backend", "faster-whisper", "--output", str(output), "--revision", "b" * 40]
        )
        != 0
    )
    assert not output.exists()


@pytest.mark.parametrize("deep", [False, True])
@pytest.mark.parametrize("configuration", [None, "not JSON", "[]"])
def test_legacy_mlx_requires_valid_configuration(models, tmp_path, deep, configuration):
    output = tmp_path / "installed"
    output.mkdir()
    (output / "weights.safetensors").write_bytes(b"weights")
    lock = json.loads(models.MODEL_LOCK.read_text())
    (output / "verification.json").write_text(json.dumps({**lock, "size_bytes": 7}))
    if configuration is not None:
        (output / "config.json").write_text(configuration)
    status = models.model_status("mlx", output, deep=deep)
    assert status["pin_matches"] is False
    assert "config.json" in status["error"]
    if configuration is None:
        assert status["installed"] is False


@pytest.mark.parametrize("deep", [False, True])
def test_receipted_mlx_rejects_nonobject_configuration(models, tmp_path, monkeypatch, deep):
    snapshot(tmp_path, monkeypatch, backend="mlx")
    output = tmp_path / "installed"
    assert models.main(["--backend", "mlx", "--output", str(output)]) == 0
    (output / "config.json").write_text("[]")
    # Even a matching receipt must not make an unusable configuration ready.
    receipt_path = output / "verification.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["files"]["config.json"]["sha256"] = hashlib.sha256(b"[]").hexdigest()
    receipt_path.write_text(json.dumps(receipt))
    status = models.model_status("mlx", output, deep=deep)
    assert status["pin_matches"] is False
    assert "config.json" in status["error"]
