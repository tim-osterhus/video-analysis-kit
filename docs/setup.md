# Setup and private configuration

Obtain the source first:

```bash
git clone https://github.com/tim-osterhus/video-analysis-kit.git
cd video-analysis-kit
```

Install from a checkout of this repository. The examples use Python 3.11 and `uv`; the package declares its supported Python range in `pyproject.toml`. Use `uv --version` to check for uv. If it is missing, follow the [official uv installation instructions](https://docs.astral.sh/uv/getting-started/installation/) for your machine.

## Local processing

Create an isolated environment in the checkout:

```bash
uv venv --python 3.11 .venv
```

Select one installation:

| Use | Install command | Configure backend |
| --- | --- | --- |
| Captions and frames without speech recognition | `uv pip install --python .venv/bin/python '.[worker]'` | `captions-only` |
| Speech recognition on Apple Silicon macOS | `uv pip install --python .venv/bin/python '.[mlx]'` | `mlx` |
| Speech recognition on CPU or a compatible Linux CUDA setup | `uv pip install --python .venv/bin/python '.[cpu]'` | `faster-whisper` |
| Client that uses an SSH worker | `uv pip install --python .venv/bin/python .` | Selected on worker |

Install FFmpeg 5.1 or newer on the processing machine, using the package manager already available there. Homebrew on macOS:

```bash
brew install ffmpeg
```

Debian/Ubuntu, from an appropriately privileged shell:

```bash
apt-get update
apt-get install ffmpeg
```

Other Linux distributions use their own FFmpeg packages. Check both `ffmpeg -version` and `ffprobe -version`. The Python worker extra includes the downloader dependencies; the agent machine does not need them for SSH execution.

For captions-only processing:

```bash
.venv/bin/video-analysis configure \
  --execution local --backend captions-only \
  --data-dir "$HOME/.local/share/video-analysis"
```

For speech recognition, first inspect the model repository metadata and available disk space. Downloads happen only when explicitly requested. For MLX:

```bash
.venv/bin/video-analysis models-fetch --backend mlx \
  --output "$HOME/.local/share/video-analysis/models/whisper-mlx"
.venv/bin/video-analysis configure \
  --execution local --backend mlx \
  --model-path "$HOME/.local/share/video-analysis/models/whisper-mlx" \
  --data-dir "$HOME/.local/share/video-analysis"
```

The default MLX repository and revision are pinned in the bundled model lock. For CPU transcription:

```bash
.venv/bin/video-analysis models-fetch --backend faster-whisper \
  --output "$HOME/.local/share/video-analysis/models/whisper-small"
.venv/bin/video-analysis configure \
  --execution local --backend faster-whisper --device cpu \
  --model-path "$HOME/.local/share/video-analysis/models/whisper-small" \
  --data-dir "$HOME/.local/share/video-analysis"
```

The faster-whisper default is `small`. Both download commands accept `--repository` and `--revision` when selecting another supported model. Use the path returned by the download command. A CUDA worker uses the same extra and `--device cuda`; inspect the installed faster-whisper/CTranslate2 requirements before changing system CUDA libraries.

Run `.venv/bin/video-analysis doctor` and, for an installed model, `.venv/bin/video-analysis doctor --deep` before preparing evidence. Configuration creation alone does not prove the runtime is ready.

## SSH processing

Install the base package in a local virtual environment. On the worker, obtain the same repository revision, create a dedicated virtual environment, and perform the local processing dependency/model installation above. Check the remote operating system, architecture, disk space, and absolute environment paths.

Use an existing trusted SSH alias such as `video-worker`. Verify it with host-key checking enabled:

```bash
ssh -o BatchMode=yes video-worker 'uname -s; uname -m'
```

Resolve unknown or changed host keys through your normal SSH verification process. The CLI does not supply credentials or create a remote account.

The following example uses illustrative Linux worker paths. Replace them with the actual absolute paths on your worker. The model path is remote; `--data-dir` is local evidence storage:

```bash
.venv/bin/video-analysis configure \
  --execution ssh --backend faster-whisper --device cpu \
  --host video-worker \
  --remote-python /home/worker/video-analysis/.venv/bin/python \
  --remote-data-dir /home/worker/.local/share/video-analysis \
  --model-path /home/worker/.local/share/video-analysis/models/whisper-small \
  --data-dir "$HOME/.local/share/video-analysis"
.venv/bin/video-analysis doctor --deep
```

For an Apple Silicon worker, select `--backend mlx` and its downloaded model path. For captions-only, select `--backend captions-only` and omit the model path. A failed SSH run does not switch to local processing.

## Configuration and skill installation

The config path is selected in this order:

1. Global `--config PATH`, placed before the command.
2. `VIDEO_ANALYSIS_CONFIG`.
3. `${XDG_CONFIG_HOME:-$HOME/.config}/video-analysis/config.json`.

`configure` writes local machine settings and refuses to overwrite a config without `--replace`. Replacement creates a backup. Keep this JSON, model files, caches, and evidence out of the repository. The examples pass private paths at setup time; reusable skill instructions contain none of them.

Install the skill after a successful doctor check:

```bash
.venv/bin/video-analysis install-skill
```

The default destination is `~/.agents/skills/analyze-video`. To use another skill discovery location, pass `--destination /absolute/path/to/skills/analyze-video`. Use one location your agent actually reads.

Installation bundles the reusable `SKILL.md` and references, then generates an executable `scripts/video-analysis` launcher tied to the current Python interpreter and selected config. No shell activation is required when invoking that launcher. Reload the agent's skill list or start a new session if the skill does not appear immediately.

The installer refuses to overwrite an existing destination unless passed `--replace`, which backs it up. Inspect an existing skill before replacement, especially if it contains custom instructions. This project installs a skill and CLI; it does not require a separate plugin installation.

## Updates, removal, and restoration

Before updating, note the current repository revision and preserve local checkout changes. Obtain the intended new revision, reinstall the same package extra into the existing environment, run `doctor`, then run `install-skill --replace`. Update an SSH worker and its client to the same revision. Review dependency changes rather than upgrading unrelated environments.

If the virtual environment moves or is recreated at another path, regenerate the installed skill launcher. If configuration moves, reinstall with `--config NEW_PATH`. Editing the portable skill source to hardcode the new paths defeats the configuration boundary.

To remove the integration, remove only the installed `analyze-video` skill directory after checking that it is the intended installation. Remove the dedicated virtual environment or uninstall the package from it if no longer needed. Configuration, evidence, and downloaded models remain; inspect and delete them separately only when you intend to discard them. Removing the skill does not clean remote data.

Configuration replacements create a sibling backup named with `.backup-` and a timestamp. Skill backups go outside the discovery directory, under `${XDG_STATE_HOME:-$HOME/.local/state}/video-analysis/skill-backups/`; installation reports the exact `backup` path. To restore a config or skill, inspect that backup, move the current replacement aside, then return the backup to its original path. Run the restored launcher's `doctor` to check that its interpreter and config still exist. Do not overwrite an unrelated directory during restoration.
