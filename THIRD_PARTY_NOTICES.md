# Third-party notices

## claude-video

The source snapshot in `src/video_intelligence/resources/vendor/claude-video/` comes from:

- Repository: [bradautomates/claude-video](https://github.com/bradautomates/claude-video)
- Commit: `83da59fa78c3eee9e20f515fe75c438bb5166efd`
- Copyright: Copyright (c) 2026 Bradley Bonanno
- License: MIT; the full notice is retained in `src/video_intelligence/resources/vendor/claude-video/LICENSE`.

Video Analysis uses the vendored preparation scripts for downloading, captions, and frame extraction. The surrounding CLI, configuration, execution selection, and agent workflow are maintained by this project. Bundled upstream files remain subject to their original copyright and license notice.

The snapshot includes one maintained compatibility patch: `frames.py` uses `-fps_mode vfr` in place of the removed `-vsync vfr` option for scene and keyframe extraction. This supports FFmpeg 9 while retaining compatibility with FFmpeg 5.1 and newer. The original file hash, patch description, and installed-file hashes are recorded in `snapshot.json`. See [upstream issue 126](https://github.com/bradautomates/claude-video/issues/126).

## Dependencies and models

Python dependencies and system tools retain their own licenses. Python dependency versions are declared in `pyproject.toml` and resolved in `uv.lock`; system tool versions depend on the processing machine. These dependencies are not relicensed by this project's MIT license.

Speech model weights are downloaded separately, not included in this repository. Consult the selected model repository's license and model card before use or redistribution. `src/video_intelligence/resources/models/model-lock.json` identifies the default pinned MLX model.
