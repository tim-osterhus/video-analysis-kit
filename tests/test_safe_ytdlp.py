from __future__ import annotations

import os
import subprocess
from pathlib import Path

from video_intelligence.paths import SAFE_YTDLP


def test_safe_wrapper_injects_required_flags(tmp_path: Path):
    fake = tmp_path / "real-yt-dlp"
    fake.write_text(
        "#!/bin/sh\nprintf 'PATH=%s\\n' \"$PATH\"\nprintf '%s\\n' \"$@\"\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = dict(os.environ)
    env["VIDEO_INTELLIGENCE_REAL_YTDLP"] = str(fake)
    proc = subprocess.run(
        [str(SAFE_YTDLP), "--skip-download", "--", "https://youtube.com/watch?v=abc"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0
    output = proc.stdout.splitlines()
    assert Path(output[0].removeprefix("PATH=").split(os.pathsep)[0]) == tmp_path
    args = output[1:]
    assert args[:4] == [
        "--ignore-config",
        "--no-cookies",
        "--no-cookies-from-browser",
        "--no-remote-components",
    ]
    assert "--socket-timeout" in args
    assert "--max-filesize" in args
    assert args[-1] == "https://youtube.com/watch?v=abc"
