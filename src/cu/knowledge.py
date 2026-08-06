"""What a run learned about an application, so the next run need not rediscover it.

Finding a control costs a full-window capture, a model's attention, and often
several attempts. That work is worth keeping: the second visit to a site should
start from "the search box is about here" rather than from nothing.

**A landmark says where to look, never where to click.** This is the whole
safety argument, and it is what separates this store from recipes. A recipe is
gated by human review because it *acts*; knowledge is written automatically by
whatever ran last, so it can never be trusted to aim an injection directly. A
remembered coordinate whose control has since moved is exactly the confidently
wrong click this project exists to prevent — and it fails silently, because the
click lands on *something*.

So a landmark resolves to a **capture**, not to a click. `cu shot --landmark`
derives a region from the window's current geometry, and the model confirms
from those pixels before acting. Knowledge collapses the search; the frame
contract still decides the coordinate.

**Positions are stored window-relative** for the same reason recipes store them
that way: a fraction survives the window being moved or resized, and an
absolute coordinate describes one arrangement of one desktop at one moment.

**Storage is user-global.** How a site is laid out is a fact about that site,
not about whichever repository an agent happened to be working in.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

KEY_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._@-]*$")


class KnowledgeError(RuntimeError):
    pass


def store_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    path = Path(base).expanduser() / "ai-computer-use" / "knowledge"
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_key(key: str, what: str = "name") -> str:
    if not KEY_PATTERN.match(key or ""):
        raise KnowledgeError(
            f"{key!r} is not a usable {what}; use letters, digits, dot, dash, "
            "underscore, or @"
        )
    return key


def path_for(app: str) -> Path:
    return store_dir() / f"{validate_key(app, 'application')}.json"


def load(app: str) -> dict[str, Any]:
    path = path_for(app)
    if not path.is_file():
        return {"app": app, "landmarks": {}, "notes": []}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeError(f"{path}: {exc}") from exc


def save(record: dict[str, Any]) -> Path:
    path = path_for(record["app"])
    path.write_text(json.dumps(record, indent=2, sort_keys=True))
    return path


def _scoped(name: str, context: str | None) -> str:
    """Landmarks are keyed by name and optional context.

    A browser's controls differ per site, so `zen` alone is the wrong key for
    "the search box": it is the search box *of a particular page*.
    """
    return f"{context}/{name}" if context else name


def set_landmark(
    app: str,
    name: str,
    rect: dict[str, float],
    *,
    context: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Record where something is, as a fraction of the window."""
    validate_key(name)
    if context:
        validate_key(context, "context")
    for edge in ("x", "y", "w", "h"):
        if not 0.0 <= rect[edge] <= 1.0:
            raise KnowledgeError(
                f"{edge}={rect[edge]} is not a fraction of the window; "
                "landmarks are stored window-relative so they survive a resize"
            )
    record = load(app)
    key = _scoped(name, context)
    previous = record["landmarks"].get(key, {})
    record["landmarks"][key] = {
        "rect": rect,
        "note": note or previous.get("note", ""),
        "context": context,
        "learned_at": previous.get("learned_at", time.time()),
        "updated_at": time.time(),
        # Evidence, not decoration: a landmark that keeps missing is one whose
        # application changed, and the counters are how that becomes visible
        # instead of quietly costing an extra capture every run.
        "confirmed": previous.get("confirmed", 0),
        "missed": previous.get("missed", 0),
    }
    save(record)
    return record["landmarks"][key]


def get_landmark(app: str, name: str, context: str | None = None) -> dict[str, Any]:
    record = load(app)
    key = _scoped(name, context)
    if key not in record["landmarks"]:
        # Fall back to the unscoped landmark: a browser's "urlbar" belongs to
        # the browser regardless of which page is open.
        if context and name in record["landmarks"]:
            return record["landmarks"][name]
        known = ", ".join(sorted(record["landmarks"])) or "none"
        raise KnowledgeError(f"{app} has no landmark {key!r} (knows: {known})")
    return record["landmarks"][key]


def score(app: str, name: str, hit: bool, context: str | None = None) -> dict[str, Any]:
    """Record whether a landmark actually pointed at the right thing."""
    record = load(app)
    key = _scoped(name, context)
    if key not in record["landmarks"]:
        raise KnowledgeError(f"{app} has no landmark {key!r}")
    field = "confirmed" if hit else "missed"
    record["landmarks"][key][field] = record["landmarks"][key].get(field, 0) + 1
    record["landmarks"][key]["scored_at"] = time.time()
    save(record)
    return record["landmarks"][key]


def add_note(app: str, text: str, context: str | None = None) -> dict[str, Any]:
    """Keep a fact that has no coordinate.

    Most of what is learned about an application is not a position — that a
    dialog must be dismissed before anything else responds, that a field
    rejects pasted text. These cost as much to rediscover as a landmark does.
    """
    record = load(app)
    entry = {"text": text, "context": context, "at": time.time()}
    record.setdefault("notes", []).append(entry)
    save(record)
    return entry


def forget(app: str, name: str | None = None, context: str | None = None) -> str:
    record = load(app)
    if name is None:
        path_for(app).unlink(missing_ok=True)
        return f"forgot everything about {app}"
    key = _scoped(name, context)
    if key not in record["landmarks"]:
        raise KnowledgeError(f"{app} has no landmark {key!r}")
    del record["landmarks"][key]
    save(record)
    return f"forgot {app}/{key}"


def listing() -> list[dict[str, Any]]:
    out = []
    for path in sorted(store_dir().glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            {
                "app": record.get("app", path.stem),
                "landmarks": len(record.get("landmarks", {})),
                "notes": len(record.get("notes", [])),
            }
        )
    return out


def to_logical(landmark: dict[str, Any], window: dict[str, Any]) -> dict[str, float]:
    """A stored fraction against a window's *current* geometry.

    Resolved at call time from live geometry rather than from whatever the
    window measured when the landmark was learned, so a moved or resized window
    yields a correctly-aimed region instead of a confidently wrong one.
    """
    rect, frame = landmark["rect"], window["logical_rect"]
    return {
        "x": frame["x"] + rect["x"] * frame["w"],
        "y": frame["y"] + rect["y"] * frame["h"],
        "w": max(rect["w"] * frame["w"], 1.0),
        "h": max(rect["h"] * frame["h"], 1.0),
    }
