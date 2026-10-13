"""Bounded browser text chunks and exclusive private runtime artifacts."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import secrets
import stat
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import Any

from . import browser_read as wire

POLICY = "readable-nodes/1"
OMISSIONS = [
    "form-values-and-defaults", "hidden-nodes", "scripts-and-styles",
    "sensitive-field-containers", "iframes", "shadow-roots", "virtualized-content",
    "browser-chrome",
]
MAX_ARTIFACTS = 16


def validate_chunk(
    body: dict[str, Any], *, snapshot: str, document: str, generation: int,
    selector: str | None, offset: int, allow_partial: bool,
) -> tuple[bytes, int | None, dict[str, Any]]:
    """Reject inconsistent chunks without including page text in diagnostics."""
    error = wire.ProtocolError("browser text chunk did not match its snapshot")
    if not isinstance(body, dict):
        raise error
    if (
        body.get("schema") != "cu-browser-text-chunk/1"
        or body.get("kind") != "export-text"
        or body.get("snapshot") != snapshot
        or body.get("document_id") != document
        or type(body.get("generation")) is not int
        or body["generation"] != generation
        or body.get("scope") != {"selector": selector}
        or body.get("redaction_policy") != POLICY
        or body.get("omissions") != OMISSIONS
        or not isinstance(body.get("completeness"), str)
        or body["completeness"] not in {"complete", "empty", "truncated"}
        or type(body.get("offset")) is not int
        or body["offset"] != offset
        or type(body.get("total_bytes")) is not int
        or not 0 <= body["total_bytes"] <= wire.MAX_EXPORT_BYTES
    ):
        raise error
    if body["completeness"] == "truncated" and not allow_partial:
        raise wire.OversizeMessage("browser text requires explicit partial-output acceptance")
    chunk = body.get("chunk")
    if not isinstance(chunk, str):
        raise error
    try:
        data = chunk.encode("utf-8")
        units = len(chunk.encode("utf-16-le")) // 2
    except UnicodeError as exc:
        raise error from exc
    if units > wire.MAX_EXPORT_CHUNK_CHARS:
        raise error
    next_offset = body.get("next_offset")
    if next_offset is not None and (
        type(next_offset) is not int or not units or next_offset != offset + units
        or next_offset > wire.MAX_EXPORT_BYTES
    ):
        raise error
    if body["completeness"] == "empty" and (data or body["total_bytes"] or next_offset):
        raise error
    metadata = {key: body[key] for key in (
        "scope", "redaction_policy", "completeness", "omissions", "total_bytes",
    )}
    return data, next_offset, metadata


def _private(fd: int, *, directory: bool, private: bool = True) -> None:
    info = os.fstat(fd)
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    permissions = 0o077 if private else 0o022
    if not kind(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & permissions:
        raise wire.HelperUnavailable("browser text artifacts require private owned storage")


def save(data: bytes) -> dict[str, Any]:
    """Keep at most sixteen exclusive UTF-8 artifacts; never follow storage symlinks."""
    if len(data) > wire.MAX_EXPORT_BYTES:
        raise wire.OversizeMessage("browser text exceeds the artifact budget")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime or not Path(runtime).is_absolute():
        raise wire.HelperUnavailable("XDG_RUNTIME_DIR is required for browser text artifacts")
    path = Path(runtime)
    with ExitStack() as stack:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, fd)
        _private(fd, directory=True)
        for name in ("cu", "browser", "text"):
            with suppress(FileExistsError):
                os.mkdir(name, 0o700, dir_fd=fd)
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, fd)
            _private(fd, directory=True, private=name == "text")
            path /= name
        lock = os.open(".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        stack.callback(os.close, lock)
        _private(lock, directory=False)
        fcntl.flock(lock, fcntl.LOCK_EX)
        name = f"text-{secrets.token_hex(12)}.txt"
        out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(out, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            os.unlink(name, dir_fd=fd)
            raise
        retained = []
        for old in os.listdir(fd):
            if old == name or re.fullmatch(r"text-[0-9a-f]{24}\.txt", old) is None:
                continue
            info = os.stat(old, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid():
                retained.append((info.st_mtime_ns, old))
        for _, old in sorted(retained, reverse=True)[MAX_ARTIFACTS - 1:]:
            os.unlink(old, dir_fd=fd)
    return {
        "path": str(path / name), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
    }
