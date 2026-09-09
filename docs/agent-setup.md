# Agent-guided setup

Use this guide when the user points you at this repository and asks to set it up. The outcome is a working CLI, a private configuration, a verified processing environment, and an installed `analyze-video` skill tailored to that configuration. Read [manual setup](setup.md) for the command syntax.

## Discover before installing

Inspect the checkout and `pyproject.toml`. Identify the current operating system and architecture, available Python interpreters, `uv`, FFmpeg/FFprobe, free disk space, and any usable transcription runtime. Read an existing Video Analysis config if present. Configuration precedence is `--config`, `VIDEO_ANALYSIS_CONFIG`, then `${XDG_CONFIG_HOME:-$HOME/.config}/video-analysis/config.json`.

Use the user's stated preferences and existing working setup. If a config selects SSH, inspect that worker rather than replacing it with local execution. Inspect relevant SSH aliases without printing unrelated SSH configuration or credentials. Do not assume the current agent machine should perform transcription.

Choose the processing host first, then choose its backend:

- Apple Silicon macOS can use MLX.
- macOS or Linux can use faster-whisper on CPU. On Linux, use CUDA only if the worker has compatible hardware and libraries and the user wants to use them.
- Captions-only mode avoids installing a speech model. Explain that videos without usable captions can have no transcript.
- Use SSH when the user has a suitable remote machine or wants processing off the agent machine. Ask for the missing host or a materially unresolved local-versus-remote choice. Do not provision a paid server.

Give a concise explanation of the chosen host and backend before installing. For a speech model, inspect available repository metadata for download size and check free space on the processing host. State when size information is unavailable; do not invent a size. Allow space for the model, downloaded media, extracted audio, and evidence. Routine installation covered by “set this up” does not need another blanket confirmation.

## Install on the appropriate machine

Use a dedicated Python 3.11 virtual environment. If `uv` is absent, inspect the official uv installation instructions and use an appropriate installation method for the machine. Reuse a working interpreter or package manager where practical. Do not change global Python or unrelated environments.

For local execution, install the backend's extra and FFmpeg locally. For SSH execution, install only the base package on the agent machine. Install the worker extra, FFmpeg, and the model on the worker from the same repository revision. See the extra/backend table in [manual setup](setup.md).

Verify an existing SSH alias with ordinary host-key checking. A missing or changed host key requires legitimate verification; do not bypass it with disabled checking or a disposable known-hosts file. Do not rewrite unrelated SSH entries. If system dependencies require privileges you do not have, identify the exact missing package and needed action; do not change privilege settings or account configuration.

Download speech models explicitly with `models-fetch` on the processing machine. Use its returned directory as the model path. Preparation must not become an implicit model installer. Preserve a working model rather than redownloading it without cause.

## Configure and verify

Use `configure` to write private settings. Prefer the default config location unless the user selected another. Keep data and models outside the checkout. For SSH, obtain absolute remote paths from the remote machine; a local home directory is not the remote home directory.

If configuration already exists, inspect it first. `configure` refuses overwrites unless `--replace` is supplied and backs up the old file when replacing. Use replacement only when it fits the requested setup or repair. Report the backup path.

Run `doctor`, repair concrete failures within the setup scope, and rerun the affected check. Use `doctor --deep` for installed-model verification. Do not install local processing dependencies to work around an SSH failure. If access is blocked, report the exact unresolved condition.

Install the skill with the configured CLI:

```bash
.venv/bin/video-analysis --config '/absolute/path/to/config.json' install-skill
```

The installer copies the portable skill and writes `scripts/video-analysis` with the active interpreter and config path. It defaults to `~/.agents/skills/analyze-video`; use `--destination` if the user's agent needs another discovery directory. Do not install a second copy merely because multiple agent directories exist. An existing skill is a collision: inspect it, and replace with backup only when the requested task authorizes replacing it. Preserve unrelated custom guidance. Do not symlink the repository skill or copy a machine-specific skill from another installation.

Verify the installed launcher by running its `doctor` command. If the user supplied a video, prepare it with a bounded range where that satisfies the request, then validate the returned local manifest and inspect its evidence. Otherwise use a short synthetic local video or report that setup checks passed without claiming real-video verification. A synthetic video exercises preparation but does not prove speech recognition or site access.

## Finish with a usable handoff

Report the selected execution host and backend, config path, interpreter/environment, data/model locations, installed skill location, and actual checks performed. Include any unresolved limitation and a first command or example prompt. Explain that config and skill replacement produced backups where applicable.

Do not publish the checkout, create a GitHub repository, install an additional plugin, change unrelated integrations, import cookies, delete retained runs, or run commands demonstrated by a video as part of setup.
