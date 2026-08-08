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

**Positions are stored window-relative**, because an absolute coordinate
describes one arrangement of one desktop at one moment. A fraction survives the
window being *moved*. It does not survive the window being *resized*, and under
a tiling compositor resizing is the common case: a toolbar is a fixed number of
pixels tall whatever height the window is, so halving the window halves where a
fraction says the toolbar is while the toolbar stays where it was.

So a landmark carries the window geometry it was learned at, and resolving it
projects the stored fraction under every way the control might have followed
the resize — stretching with the window, or holding a fixed size against one
edge. The region handed back **covers all of them**, and it widens as the
window diverges from the geometry that was measured. A landmark that would have
to cover most of the window to stay honest reports itself stale instead, and
the caller looks at the whole window as it would have without one.

That is the shape of the whole store: it steers, it does not assert. A hint
that hedges costs one slightly larger capture; a hint that claims a precision
it does not have costs a confident mistake, and the environment it describes is
too dynamic to support the claim.

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

#: How a control follows its window along one axis. `auto` declines to choose,
#: which is the default because nothing about a stored fraction reveals which
#: one is true — only the run that learned it can know, and usually it doesn't
#: either. Naming an anchor narrows the region; it is an optimisation, and a
#: wrong one costs a miss rather than a wrong coordinate.
ANCHORS = {
    "x": ("auto", "fraction", "left", "right"),
    "y": ("auto", "fraction", "top", "bottom"),
}

#: Within this much of the learned size, every projection lands in the same
#: place, so the region is used exactly as it was learned.
SAME_SIZE = 0.02

#: A hedged region is still a hypothesis, so it gets a margin — as a fraction
#: of the window, on each side.
MARGIN = 0.02

#: Past this share of the window's area, a landmark has stopped collapsing the
#: search and is only pretending to.
USELESS_SHARE = 0.5


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


#: Where facts about the machine itself live, rather than about one
#: application. The leading underscore is what makes the separation real:
#: KEY_PATTERN requires an application key to start alphanumeric, so no
#: application can ever resolve to this file, and an application genuinely
#: named "host" keeps its own.
HOST_FILENAME = "_host.json"


def path_for(app: str) -> Path:
    return store_dir() / f"{validate_key(app, 'application')}.json"


def host_path() -> Path:
    return store_dir() / HOST_FILENAME


def load_host() -> dict[str, Any]:
    """What runs have learned about this machine.

    Notes only, and deliberately so: a landmark is a fraction of a window, and
    the host is not a window. What belongs here is behaviour no application
    owns — that keystrokes pass through a remapping layer before any client
    sees them, that one output reports a scale the others do not.
    """
    path = host_path()
    if not path.is_file():
        return {"notes": []}
    try:
        record = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeError(f"{path}: {exc}") from exc
    if not isinstance(record, dict):
        raise KnowledgeError(f"{path}: not a record")
    record.setdefault("notes", [])
    return record


def add_host_note(text: str) -> dict[str, Any]:
    record = load_host()
    entry = {"text": text, "at": time.time()}
    record["notes"].append(entry)
    host_path().write_text(json.dumps(record, indent=2, sort_keys=True))
    return entry


def forget_host(index: int | None = None) -> str:
    """Drop one note, or everything known about the host.

    Notes are addressed by the position `cu know host` prints, because a fact
    about a machine has no name to key it by.
    """
    if index is None:
        host_path().unlink(missing_ok=True)
        return "forgot everything about this host"
    record = load_host()
    if not 1 <= index <= len(record["notes"]):
        raise KnowledgeError(
            f"no host note {index}; there are {len(record['notes'])}"
        )
    dropped = record["notes"].pop(index - 1)
    host_path().write_text(json.dumps(record, indent=2, sort_keys=True))
    return f"forgot: {dropped['text']}"


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
    learned_rect: dict[str, float] | None = None,
    anchor_x: str = "auto",
    anchor_y: str = "auto",
) -> dict[str, Any]:
    """Record where something is, as a fraction of the window.

    `learned_rect` is the window's geometry when the fraction was measured.
    Without it a landmark can only ever be projected by stretching, so it is
    permanently hedged — the store keeps it rather than refusing it, because a
    hedged hint still beats a full-window search.
    """
    validate_key(name)
    if context:
        validate_key(context, "context")
    for edge in ("x", "y", "w", "h"):
        if not 0.0 <= rect[edge] <= 1.0:
            raise KnowledgeError(
                f"{edge}={rect[edge]} is not a fraction of the window; "
                "landmarks are stored window-relative so they outlive the "
                "desktop arrangement they were measured on"
            )
    if rect["x"] + rect["w"] > 1.0 or rect["y"] + rect["h"] > 1.0:
        raise KnowledgeError("a landmark must fit entirely inside its window")
    for axis, anchor in (("x", anchor_x), ("y", anchor_y)):
        if anchor not in ANCHORS[axis]:
            raise KnowledgeError(
                f"{anchor!r} is not an anchor for {axis}; "
                f"use one of {', '.join(ANCHORS[axis])}"
            )
    record = load(app)
    key = _scoped(name, context)
    previous = record["landmarks"].get(key, {})
    record["landmarks"][key] = {
        "rect": rect,
        "learned_rect": learned_rect or previous.get("learned_rect"),
        "anchor": {"x": anchor_x, "y": anchor_y},
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
        if path.name == HOST_FILENAME:
            continue  # not an application; `cu know host` reads it
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
    """A stored fraction stretched across a window's *current* geometry.

    One hypothesis about how a control followed its window, and the only one
    available when the learned geometry is unknown. It is right for content
    that scales with the window and wrong for chrome that does not, which is
    why `resolve` is what callers aim a capture with.
    """
    rect, frame = landmark["rect"], window["logical_rect"]
    return {
        "x": frame["x"] + rect["x"] * frame["w"],
        "y": frame["y"] + rect["y"] * frame["h"],
        "w": max(rect["w"] * frame["w"], 1.0),
        "h": max(rect["h"] * frame["h"], 1.0),
    }


def _spans(
    start: float, length: float, origin: float, extent: float,
    learned: float | None, anchor: str,
) -> list[tuple[float, float]]:
    """Where one axis of a landmark lands, under each surviving hypothesis.

    `start` and `length` are the stored fractions; `origin` and `extent` are
    the window's position and size on this axis now; `learned` is that size
    when the landmark was measured. Stretching assumes the control scales with
    the window. The two edge hypotheses assume it keeps the pixel size it was
    measured at and holds station against the near or far edge — which is how
    toolbars, sidebars, and status bars actually behave.
    """
    stretched = (origin + start * extent, length * extent)
    if learned is None or learned <= 0 or anchor == "fraction":
        return [stretched]
    fixed = length * learned
    near = (origin + start * learned, fixed)
    far = (origin + extent - (1.0 - start - length) * learned - fixed, fixed)
    if anchor in ("left", "top"):
        return [near]
    if anchor in ("right", "bottom"):
        return [far]
    # A projection landing wholly outside the window refutes itself — the
    # control is in the window — and keeping it would widen every shrunken
    # window's region to nothing useful. The stretched one is inside by
    # construction, so something always survives.
    inside = [
        span
        for span in (near, far)
        if span[0] < origin + extent and span[0] + span[1] > origin
    ]
    return [stretched, *inside]


def _cover(spans: list[tuple[float, float]]) -> tuple[float, float]:
    lo = min(start for start, _ in spans)
    hi = max(start + length for start, length in spans)
    return lo, max(hi - lo, 1.0)


def resolve(landmark: dict[str, Any], window: dict[str, Any]) -> dict[str, Any]:
    """Where to look for a landmark, and how much of that to believe.

    A landmark steers a capture; it does not assert a position. So the region
    returned covers every way the control could have followed the window since
    it was measured, and grows as those hypotheses diverge. Once covering them
    honestly would take most of the window, the landmark is **stale**: the
    whole window comes back instead, because a hint that no longer narrows
    anything should say so rather than aim somewhere plausible.
    """
    rect, frame = landmark["rect"], window["logical_rect"]
    learned = landmark.get("learned_rect")
    anchor = landmark.get("anchor") or {}

    if learned is None:
        return {
            "rect": _pad(to_logical(landmark, window), frame),
            "confidence": "widened",
            "reason": "learned before window geometry was recorded",
        }

    ratios = (frame["w"] / learned["w"], frame["h"] / learned["h"])
    if all(abs(r - 1.0) <= SAME_SIZE for r in ratios):
        # Every hypothesis coincides at the geometry the landmark was measured
        # at, so hedging here would only spend pixels.
        return {
            "rect": to_logical(landmark, window),
            "confidence": "exact",
            "reason": "window is the size this was learned at",
        }

    x, w = _cover(
        _spans(rect["x"], rect["w"], frame["x"], frame["w"], learned["w"],
               anchor.get("x", "auto"))
    )
    y, h = _cover(
        _spans(rect["y"], rect["h"], frame["y"], frame["h"], learned["h"],
               anchor.get("y", "auto"))
    )
    covered = _pad({"x": x, "y": y, "w": w, "h": h}, frame)
    reason = (
        f"window is {round(frame['w'])}x{round(frame['h'])} now, "
        f"learned at {round(learned['w'])}x{round(learned['h'])}"
    )
    if covered["w"] * covered["h"] > USELESS_SHARE * frame["w"] * frame["h"]:
        return {
            "rect": dict(frame),
            "confidence": "stale",
            "reason": f"{reason}; too far to narrow the search",
        }
    return {"rect": covered, "confidence": "widened", "reason": reason}


def _pad(rect: dict[str, float], frame: dict[str, float]) -> dict[str, float]:
    """A margin on a hedged region, kept inside its own window.

    The window is the boundary because the pixels outside it belong to a
    different application, and under a tiling compositor they are always there.
    """
    x = max(rect["x"] - MARGIN * frame["w"], frame["x"])
    y = max(rect["y"] - MARGIN * frame["h"], frame["y"])
    right = min(rect["x"] + rect["w"] + MARGIN * frame["w"], frame["x"] + frame["w"])
    bottom = min(rect["y"] + rect["h"] + MARGIN * frame["h"], frame["y"] + frame["h"])
    return {"x": x, "y": y, "w": max(right - x, 1.0), "h": max(bottom - y, 1.0)}
