# Validation status

Verified on 2026-09-09. These checks describe this initial release; they are not a transcription accuracy benchmark or a guarantee that every supported website works.

## Observed results

| Check | Result |
| --- | --- |
| Python tests, including the opt-in FFmpeg smoke test | 100 passed on Apple Silicon macOS with Python 3.11 |
| Ruff and skill metadata validation | Passed |
| Source distribution and wheel build | Passed |
| Fresh base-only wheel installation outside the checkout | CLI, packaged resources, and generated skill launcher worked; no yt-dlp, MLX, or faster-whisper installed on that client |
| Local captions-only preparation | A synthetic video produced sampled frames and a valid evidence manifest without a speech model |
| SSH processing on a separate Apple Silicon Mac | A fresh worker environment passed deep model verification and prepared a short synthetic video using the existing pinned MLX model |
| Actual MLX transcription | Correctly transcribed both known sentences in the fixture, with two timestamped segments |
| SSH evidence retrieval | Retrieved three frames and supporting evidence while omitting extracted WAV media; local manifest validation passed |
| Repeat fetch | Reused the same validated local evidence directory successfully |
| Existing installation isolation | Tests used a separate worker environment, private configuration, and test skill; the existing installed skill and worker were not replaced |

The local machine used FFmpeg 7.1.1 and Python 3.11.14. The separate macOS worker used FFmpeg 8.1.2, Python 3.11.15, MLX Whisper 0.4.3, and the pinned `whisper-large-v3-turbo` model. Processing used yt-dlp 2026.8.19. Test media, machine settings, and live-run evidence were kept outside the repository.

## Regression coverage

The tests exercise source URL restrictions, bounded downloader arguments, configuration collision refusal and private permissions, generated launchers with spaces in paths, and replacement backups outside skill discovery directories. Transport tests cover deadlines, output limits, incompatible worker contracts, unsafe archives, failed retrieval recovery, and origin/file/hash mismatches.

Model tests cover explicit download receipts, offline inference arguments, incomplete or altered models, and both MLX and faster-whisper adapters. Fetched-bundle validation accepts only the deterministic media omissions; missing frames still fail. Full worker-run validation continues to require its media artifacts. Packet tests ensure frame paths survive moving evidence between machines.

## Remaining validation limits

- Linux and CUDA hardware were not available for this verification. The repository includes a GitHub Actions matrix for Ubuntu and macOS, but that workflow has not run because the repository has not been published.
- The faster-whisper CPU/CUDA adapter was tested with controlled substitutes, not real model inference. Its system-library requirements still need checking on the target worker.
- The explicit model-download flow has automated tests; this live test reused existing, deeply verified MLX weights rather than downloading them again.
- No live website extraction, authenticated media, long video, concurrency, or broad speech-accuracy benchmark was performed.
- Passing integrity checks establishes agreement with the recorded manifest, not the truth of video claims. Offline validation of a retrieved bundle cannot verify the continued existence of media left on the worker.

## Repeat the checks

From a checkout with Python 3.11 and FFmpeg available:

```bash
uv sync --extra worker --extra dev
uv run --no-sync ruff check --config pyproject.toml src/video_intelligence tests
VIDEO_ANALYSIS_SMOKE=1 uv run --no-sync pytest
uv build
```

Without `VIDEO_ANALYSIS_SMOKE=1`, the synthetic FFmpeg integration test is skipped. The other tests use controlled fixtures and do not require SSH, site access, or downloaded speech models. Dependency installation can require network access.

For a real deployment, follow [agent setup](agent-setup.md), run the installed launcher's `doctor --deep` for a speech backend, and prepare a short video with known speech and visual content. Validate the returned local manifest, inspect its transcript and frames, then repeat `fetch` for SSH execution. Record the actual backend, operating system, tool versions, and limitations; do not infer Linux or CUDA verification from a successful macOS run.
