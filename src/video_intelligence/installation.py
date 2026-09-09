"""Install generic instructions with a private, interpreter-pinned launcher."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

from .configuration import backup_name


def install_skill(
    destination: Path, config: Path, *, replace: bool = False
) -> tuple[Path, Path | None]:
    packaged = Path(__file__).parent / "resources/skill"
    source = (
        packaged
        if packaged.is_dir()
        else Path(__file__).resolve().parents[2] / "skills/analyze-video"
    )
    if not (source / "SKILL.md").is_file():
        raise ValueError("packaged skill is missing; reinstall the complete distribution")
    if destination.exists() or destination.is_symlink():
        if not replace:
            raise ValueError(f"{destination} already exists; --replace creates a backup")
    backup = None
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".analyze-video-", dir=destination.parent))
    try:
        shutil.copytree(source, temporary, dirs_exist_ok=True)
        scripts = temporary / "scripts"
        scripts.mkdir(exist_ok=True)
        launcher = scripts / "video-analysis"
        launcher.write_text(
            "#!/bin/sh\nexec "
            + shlex.join(
                [sys.executable, "-m", "video_intelligence.frontend", "--config", str(config)]
            )
            + ' "$@"\n'
        )
        launcher.chmod(0o755)
        if destination.exists() or destination.is_symlink():
            backup_root = (
                Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
                / "video-analysis/skill-backups"
            )
            if backup_root.resolve().is_relative_to(destination.parent.resolve()):
                raise ValueError(
                    "skill backup storage must be outside the skill discovery directory"
                )
            backup_root.mkdir(parents=True, exist_ok=True)
            backup = backup_root / backup_name(destination).name
            shutil.move(str(destination), str(backup))
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return destination, backup
