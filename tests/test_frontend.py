import json
import subprocess
import sys


def invoke(*args):
    return subprocess.run(
        [sys.executable, "-m", "video_intelligence.frontend", *map(str, args)],
        capture_output=True,
        text=True,
    )


def test_configure_private_file_and_refuse_accidental_overwrite(tmp_path):
    config = tmp_path / "config.json"
    args = (
        "--config",
        config,
        "configure",
        "--execution",
        "local",
        "--backend",
        "captions-only",
        "--data-dir",
        tmp_path / "data",
    )
    first = invoke(*args)
    assert first.returncode == 0, first.stderr
    assert json.loads(config.read_text())["backend"] == "captions-only"
    assert config.stat().st_mode & 0o077 == 0
    assert invoke(*args).returncode != 0
    assert invoke(*args, "--replace").returncode == 0
    assert list(tmp_path.glob("config.json.backup-*"))


def test_ssh_config_requires_explicit_host_and_worker_paths(tmp_path):
    config = tmp_path / "config.json"
    result = invoke("--config", config, "configure", "--execution", "ssh")
    assert result.returncode != 0
    assert not config.exists()


def test_installed_launcher_keeps_interpreter_and_config_with_spaces(tmp_path):
    config = tmp_path / "machine setup.json"
    assert (
        invoke(
            "--config", config, "configure", "--execution", "local", "--backend", "captions-only"
        ).returncode
        == 0
    )
    destination = tmp_path / "skills with spaces" / "analyze-video"
    installed = invoke("--config", config, "install-skill", "--destination", destination)
    assert installed.returncode == 0, installed.stderr
    launcher = destination / "scripts/video-analysis"
    result = subprocess.run([str(launcher), "config"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["execution"] == "local"
    (destination / "sentinel").write_text("keep")
    assert invoke("--config", config, "install-skill", "--destination", destination).returncode != 0
    assert (destination / "sentinel").read_text() == "keep"


def test_missing_config_never_guesses_local_processing(tmp_path):
    result = invoke("--config", tmp_path / "missing.json", "prepare", "/tmp/no-video.mp4")
    assert result.returncode != 0
    assert "configure" in result.stderr.lower()


def test_replaced_skill_backup_is_outside_discovery_root(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config = tmp_path / "config.json"
    assert invoke("--config", config, "configure", "--execution", "local").returncode == 0
    destination = tmp_path / "skills" / "analyze-video"
    assert invoke("--config", config, "install-skill", "--destination", destination).returncode == 0
    (destination / "sentinel").write_text("old setup")
    result = invoke("--config", config, "install-skill", "--destination", destination, "--replace")
    assert result.returncode == 0, result.stderr
    from pathlib import Path

    backup = Path(json.loads(result.stdout)["backup"])
    assert (backup / "sentinel").read_text() == "old setup"
    assert not backup.is_relative_to(destination.parent)
    assert list(destination.parent.rglob("SKILL.md")) == [destination / "SKILL.md"]
