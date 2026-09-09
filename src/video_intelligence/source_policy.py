from __future__ import annotations

import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse

ALLOWED_HOSTS = (
    "youtube.com",
    "youtu.be",
    "instagram.com",
    "tiktok.com",
    "vimeo.com",
    "loom.com",
    "x.com",
    "twitter.com",
)
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}


class SourcePolicyError(ValueError):
    pass


def _host_allowed(host: str) -> bool:
    host = host.rstrip(".").lower()
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_HOSTS)


def _require_public_ip(value: str) -> None:
    address = ipaddress.ip_address(value)
    if not address.is_global:
        raise SourcePolicyError(f"source resolves to a non-public address: {address}")


def validate_public_url(source: str, *, resolve_dns: bool = True) -> str:
    parsed = urlparse(source)
    if parsed.scheme != "https":
        raise SourcePolicyError("video URLs must use HTTPS")
    if parsed.username or parsed.password:
        raise SourcePolicyError("credentials in video URLs are not allowed")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise SourcePolicyError("video URL has no hostname")
    if not _host_allowed(host):
        raise SourcePolicyError(
            f"unsupported video host: {host}; supported hosts are {', '.join(ALLOWED_HOSTS)}"
        )

    try:
        _require_public_ip(host)
    except ValueError:
        if resolve_dns:
            try:
                addresses = {
                    item[4][0]
                    for item in socket.getaddrinfo(
                        host, parsed.port or 443, type=socket.SOCK_STREAM
                    )
                }
            except socket.gaierror as exc:
                raise SourcePolicyError(f"could not resolve video host: {host}") from exc
            if not addresses:
                raise SourcePolicyError(f"video host resolved to no addresses: {host}")
            for address in addresses:
                _require_public_ip(address)

    return source


def validate_local_file(source: str) -> Path:
    unresolved = Path(source).expanduser()
    if unresolved.is_symlink():
        raise SourcePolicyError("symlinked local video paths are not allowed")
    path = unresolved.resolve()
    if not path.exists():
        raise SourcePolicyError(f"local video does not exist: {path}")
    if not path.is_file():
        raise SourcePolicyError(f"local video is not a regular file: {path}")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise SourcePolicyError(f"unsupported local video extension: {path.suffix or '(none)'}")
    return path


def classify_source(source: str, *, resolve_dns: bool = True) -> tuple[str, str]:
    parsed = urlparse(source)
    if parsed.scheme:
        return "url", validate_public_url(source, resolve_dns=resolve_dns)
    return "local", str(validate_local_file(source))
