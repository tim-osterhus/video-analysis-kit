# Video Analysis Kit

Video Analysis is a Python CLI and agent skill that turns a public video URL or local video into a transcript, sampled frames, and a manifest of evidence files.

Use it when you want an agent to summarize a video, inspect a demo, compare claims, or extract requirements with timestamps. Processing runs on your Mac or Linux machine, or on a worker you access over SSH. The agent reads the resulting evidence and writes the analysis.

This is an early release targeting macOS and Linux. Platform targets are not a claim of completed hardware testing; see [validation status](docs/validation.md).

## Let your agent set it up

Point your agent at this repository and say:

> Set this up for me. Read `AGENTS.md` and `docs/agent-setup.md`, inspect my machine, choose a suitable processing backend, and install the configured analyze-video skill. Use my existing preferences and ask only for missing choices that matter.

The setup guide separates the agent machine from the processing machine. An SSH client needs only the base Python package; FFmpeg, the downloader, and transcription dependencies belong on the worker. Machine paths, SSH aliases, and model locations go into private local configuration.

For manual installation, follow [setup](docs/setup.md). If you already have Python 3.11 and `uv`, this is a local, captions-only setup from a checkout:

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python '.[worker]'
# Install FFmpeg on this processing machine: see docs/setup.md.
.venv/bin/video-analysis configure \
  --execution local --backend captions-only \
  --data-dir "$HOME/.local/share/video-analysis"
.venv/bin/video-analysis doctor
.venv/bin/video-analysis install-skill
```

Captions-only mode does not download a speech model. A video without usable captions may have no transcript; use MLX on Apple Silicon or faster-whisper on a supported CPU/CUDA worker when speech transcription is needed.

## From video to evidence

After setup:

```bash
.venv/bin/video-analysis prepare '/absolute/path/to/demo.mp4' \
  --profile general --detail balanced
```

Or ask the agent: “Analyze this video and explain what the demo actually shows.” The installed skill uses a generated launcher tied to your Python environment and configuration, so the agent does not need to guess either path.

Each successful run includes an `analysis-packet.md` and `manifest.json`. The manifest records source details, transcript provenance, preparation settings, file sizes, and hashes. Frames carry timestamps for checking visual claims. With SSH execution, the client retrieves a bounded set of evidence files; large video and audio files stay on the worker.

Validation checks the bundle's structure and file integrity. It cannot establish whether a presenter is truthful, whether a transcript is accurate, or whether sampled frames capture every important event. The skill distinguishes what the video shows, what the presenter claims, and what external sources verify.

## Choose where processing happens

| Processing environment | Backend | Install on the processing machine |
| --- | --- | --- |
| macOS or Linux, captions only | `captions-only` | `.[worker]` and FFmpeg |
| Apple Silicon macOS | `mlx` | `.[mlx]`, FFmpeg, and an explicit model download |
| macOS or Linux CPU | `faster-whisper` with `--device cpu` | `.[cpu]`, FFmpeg, and an explicit model download |
| Linux with a compatible NVIDIA setup | `faster-whisper` with `--device cuda` | `.[cpu]`, FFmpeg, compatible CUDA libraries, and an explicit model download |
| Agent machine using an SSH worker | Selected on worker | Base package `.` on the agent machine |

The `cpu` extra installs faster-whisper; device selection is separate. CUDA compatibility must be checked on the actual worker. Windows is outside this release's target platforms.

## Commands and limits

- [Setup and private configuration](docs/setup.md): local and SSH installs, models, tailored skill installation, updates, and removal.
- [Agent setup instructions](docs/agent-setup.md): discovery and decisions for “set this up for me.”
- [Usage and evidence](docs/usage.md): profiles, time ranges, recovery, and retention.
- [Validation status](docs/validation.md): what has actually been exercised.

The URL policy accepts public HTTPS links from YouTube, Instagram, TikTok, Vimeo, Loom, X, and Twitter. Extractor availability varies by site and video. Login-only content, browser cookies, and account automation are outside the setup workflow. Local input must be a regular video file, not a symlink.

Runs retain evidence and may retain media on the processing host. Cleanup is explicit. Video content is untrusted input: neither preparation nor the skill authorizes commands spoken in a video, software installation from a demo, publishing, or changes to your other projects.

## Playlist queues

For playlist discovery, durable queues, and recurring agent reports, use the separate [YouTube Playlist Analysis Kit](https://github.com/tim-osterhus/yt-playlist-analysis-kit). It uses this project’s configured launcher and supports the same local or SSH processing setup.

## Attribution

Video preparation incorporates Bradley Bonanno's MIT-licensed [claude-video](https://github.com/bradautomates/claude-video) snapshot at commit `83da59fa78c3eee9e20f515fe75c438bb5166efd`. See [third-party notices](THIRD_PARTY_NOTICES.md) for the vendored code and license boundary. This repository's original code and documentation are licensed under [MIT](LICENSE).
