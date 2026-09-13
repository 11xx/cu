"""Privacy-minimal correlation for one CU invocation and its child steps."""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class Invocation:
    """Identifiers supplied by the caller or generated for this process turn."""

    invocation_id: str
    cu_session: str | None
    harness: str | None
    harness_session: str | None
    parent_invocation_id: str | None = None
    batch_step: int | None = None


_current: ContextVar[Invocation | None] = ContextVar("cu_invocation", default=None)


def _bounded(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value[:256] if value else None


def session(explicit: str | None = None) -> str | None:
    """Resolve explicit CLI session before the caller's environment."""
    if explicit is not None:
        return _bounded(explicit)
    value = _current.get()
    if value is not None:
        return value.cu_session
    return _bounded(os.environ.get("CU_SESSION"))


def metadata() -> dict[str, Any]:
    """Return only known non-content correlation fields for current output."""
    value = _current.get()
    if value is None:
        return {}
    fields: dict[str, Any] = {"invocation_id": value.invocation_id}
    for name in (
        "cu_session",
        "harness",
        "harness_session",
        "parent_invocation_id",
        "batch_step",
    ):
        item = getattr(value, name)
        if item is not None:
            fields[name] = item
    return fields


def should_correlate(value: dict[str, Any]) -> bool:
    """Select receipts and evidence envelopes, leaving user controls unchanged."""
    return bool(
        {"action", "frame", "source", "logical_rect", "condition", "completed", "failed"}
        & value.keys()
    )


@contextmanager
def active(explicit_session: str | None = None):
    """Install one invocation and always restore the previous context."""
    value = Invocation(
        invocation_id=uuid.uuid4().hex,
        cu_session=session(explicit_session),
        harness=_bounded(os.environ.get("CU_HARNESS")),
        harness_session=_bounded(os.environ.get("CU_HARNESS_SESSION")),
    )
    token = _current.set(value)
    try:
        yield value
    finally:
        _current.reset(token)


@contextmanager
def batch_step(number: int):
    """Correlate one batch command with its parent invocation."""
    value = _current.get()
    if value is None:
        yield None
        return
    token = _current.set(
        replace(value, parent_invocation_id=value.invocation_id, batch_step=number)
    )
    try:
        yield _current.get()
    finally:
        _current.reset(token)
