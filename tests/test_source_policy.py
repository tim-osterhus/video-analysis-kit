from __future__ import annotations

from pathlib import Path

import pytest

from video_intelligence.source_policy import SourcePolicyError, classify_source, validate_public_url


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com/watch?v=abc",
        "https://www.instagram.com/reel/abc/",
        "https://subdomain.tiktok.com/@user/video/1",
    ],
)
def test_supported_public_hosts(url: str):
    assert validate_public_url(url, resolve_dns=False) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://youtube.com/watch?v=abc",
        "https://user:pass@youtube.com/watch?v=abc",
        "https://example.com/video",
        "file:///etc/passwd",
    ],
)
def test_unsafe_or_unsupported_urls_rejected(url: str):
    with pytest.raises(SourcePolicyError):
        validate_public_url(url, resolve_dns=False)


def test_local_file_is_resolved(tmp_path: Path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"not-real-video")
    kind, value = classify_source(str(clip))
    assert kind == "local"
    assert value == str(clip.resolve())


def test_symlinked_local_file_is_rejected(tmp_path: Path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"not-real-video")
    link = tmp_path / "link.mp4"
    link.symlink_to(clip)
    with pytest.raises(SourcePolicyError):
        classify_source(str(link))
