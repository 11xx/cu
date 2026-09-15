"""Windows that appear right after an action, reported as evidence to read.

An input receipt says an event was dispatched, never how the application
answered. The answer is often a new surface: a confirmation, an error dialog,
a chooser. Subscribing to the compositor's window events before dispatch and
reading them for a short bounded settle afterwards turns that answer into a
fact in the receipt, instead of something a caller finds only if it happens to
look again, and wide enough, before its next action.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from typing import Any, Self

from . import hypr, observe, wait


def _intersects(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (
        a["x"] < b["x"] + b["w"]
        and b["x"] < a["x"] + a["w"]
        and a["y"] < b["y"] + b["h"]
        and b["y"] < a["y"] + a["h"]
    )


def _describe(window: dict[str, Any], target: dict[str, Any] | None) -> dict[str, Any]:
    rect = window.get("logical_rect")
    reach = window.get("reachability") or {}
    target_rect = (target or {}).get("logical_rect")
    return {
        "address": window.get("address"),
        "class": window.get("class"),
        "title": window.get("title"),
        "floating": window.get("floating"),
        "workspace": window.get("workspace"),
        "monitor": window.get("monitor"),
        "logical_rect": rect,
        "reachability": reach.get("status") if isinstance(reach, dict) else None,
        "over_target": bool(rect and target_rect and _intersects(rect, target_rect)),
    }


class Watch:
    """Subscribe before an action; report what opened within the settle after it."""

    def __init__(
        self,
        settle_ms: int,
        *,
        connect: Callable[[], socket.socket] = wait.connect,
        snapshot: Callable[[], dict[str, Any]] = observe.snapshot,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settle_ms = max(0, int(settle_ms))
        self._connect = connect
        self._snapshot = snapshot
        self._clock = clock
        self._conn: socket.socket | None = None
        self._known: set[str] = set()
        self._unavailable: str | None = None

    def start(self) -> Self:
        """Subscribe and record the windows that already exist."""
        if self.settle_ms == 0:
            return self
        try:
            self._conn = self._connect()
        except (hypr.HyprError, OSError) as exc:
            self._unavailable = str(exc) or "the compositor event stream is unavailable"
            return self
        self._known = {
            wait.normalise_address(w["address"]) for w in self._snapshot().get("windows", [])
        }
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def report(self, target: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read the settle interval, then name every window that was not there before."""
        if self.settle_ms == 0:
            return {"status": "disabled", "settle_ms": 0, "windows": []}
        if self._conn is None:
            return {
                "status": "unavailable",
                "settle_ms": self.settle_ms,
                "reason": self._unavailable,
                "windows": [],
            }
        opened: set[str] = set()
        deadline = self._clock() + self.settle_ms / 1000
        for name, args in wait.stream(self._conn, deadline):
            if name == "openwindow" and args:
                opened.add(wait.normalise_address(args[0].strip()))
        # The snapshot also catches a window mapped just before the event arrived.
        windows = [
            window
            for window in self._snapshot().get("windows", [])
            if wait.normalise_address(window["address"]) in opened
            or wait.normalise_address(window["address"]) not in self._known
        ]
        return {
            "status": "observed",
            "settle_ms": self.settle_ms,
            "windows": [_describe(window, target) for window in windows],
        }


def notice(result: dict[str, Any]) -> str | None:
    """The stderr line a caller should not miss, or None when nothing appeared."""
    windows = (result.get("appeared") or {}).get("windows") or []
    if not windows:
        return None
    named = "; ".join(
        f"{w.get('class')} {w.get('title')!r} at {w.get('logical_rect')}"
        + (" over the target" if w.get("over_target") else "")
        + (
            ""
            if w.get("reachability") in ("fully-reachable", None)
            else f" ({w.get('reachability')})"
        )
        for w in windows
    )
    return (
        f"cu: {len(windows)} window(s) appeared right after this action: {named}. "
        "Read it before acting again; it is likely the application's answer."
    )
