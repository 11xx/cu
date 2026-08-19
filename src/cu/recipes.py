"""Recipes: a successful run, kept and made repeatable.

Driving a site well once is worth keeping, and the append-only action log is
already the raw material — a successful run is a sequence of commands that
worked. Promoting one to a recipe is mostly parameterizing it and recording
what it was for.

**Storage is user-global.** These are facts about a website, not about a
repository; the same login flow is worth reusing from any project, so a
per-project journal is the wrong home.

**A recipe is a security boundary, not a cache.** It is generated code that
runs against a real, possibly authenticated browser, so it is reviewable before
its first replay, stored with a content hash, and re-reviewed when that hash
changes. A stored recipe must never be able to change silently.

**Pointer steps are stored window-relative.** An absolute coordinate describes
one arrangement of one desktop at one moment; a fraction of the window survives
that window being moved. It does not survive arbitrary resizing, so pointer
steps also retain the measured window size and refuse when the current window
has drifted beyond the tolerance they can justify.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

#: Steps a recipe may contain. Anything else in the log — a refused command, a
#: dry run — is history rather than procedure.
REPLAYABLE = {"focus", "click", "move", "scroll", "type", "key", "browser", "wait"}

WINDOW_SIZE_TOLERANCE = 0.02

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class RecipeError(RuntimeError):
    pass


class RecipeNotFound(RecipeError):
    pass


class RecipeTimeout(RecipeError):
    pass


def store_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    path = Path(base).expanduser() / "ai-computer-use" / "recipes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_name(name: str) -> str:
    """Names become filenames, so they are constrained rather than sanitised.

    Rewriting a bad name into a usable one would let two different recipes
    collide on one file, which for something replayed against a live browser is
    worse than a refusal.
    """
    if not NAME_PATTERN.match(name):
        raise RecipeError(
            f"{name!r} is not a usable recipe name; use lowercase letters, "
            "digits, dot, dash, and underscore"
        )
    return name


def path_for(name: str) -> Path:
    return store_dir() / f"{validate_name(name)}.json"


def content_hash(recipe: dict[str, Any]) -> str:
    """A digest of what the recipe *does*, ignoring what it has been through.

    Review history and outcomes are excluded deliberately: recording that a
    replay failed must not invalidate the review of steps nobody edited.
    """
    kept = ("name", "site", "task", "steps", "preconditions", "verify")
    body = {key: recipe[key] for key in kept}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def step_from_log(entry: dict[str, Any]) -> dict[str, Any] | None:
    """One log entry as a replayable step, or None if it is not one.

    A pointer step keeps its window-relative position and the class it was
    aimed at, and deliberately drops the frame id: frames live in tmpfs and are
    worthless by the time a recipe is replayed.
    """
    action = entry.get("action")
    if action not in REPLAYABLE or entry.get("dry_run"):
        return None
    if entry.get("typed") is False:
        # A click logged because the typing it belonged to refused. It was
        # part of a failure, not of a working run, and replaying it alone
        # would repeat the click without the text that gave it a purpose.
        return None
    window = entry.get("window") or {}
    step: dict[str, Any] = {"action": action}
    if action in {"click", "move"}:
        if not entry.get("relative") or not window.get("logical_rect"):
            return None  # nothing that would survive the window moving
        step["relative"] = entry["relative"]
        step["window_class"] = window.get("class")
        step["window_size"] = {
            axis: window["logical_rect"][axis] for axis in ("w", "h")
        }
        if action == "click":
            step["button"] = entry.get("button", "left")
            step["count"] = entry.get("count", 1)
    elif action == "scroll":
        step["dy"] = entry.get("dy", 0)
        step["dx"] = entry.get("dx", 0)
        if entry.get("relative") and window.get("logical_rect"):
            step["relative"] = entry["relative"]
            step["window_class"] = window.get("class")
            step["window_size"] = {
                axis: window["logical_rect"][axis] for axis in ("w", "h")
            }
    elif action == "type":
        # The text itself is not in the log and must not be: the log records
        # that typing happened and how much, never what was typed. So a typing
        # step replays as a parameter the caller must supply, which is also
        # what makes one recipe usable for more than one input.
        step["param"] = None  # assigned when the recipe is assembled
        step["length"] = entry.get("length")
        step["method"] = entry.get("method", "clipboard")
        # Aimed typing replays as one step for the same reason it runs as one
        # command: splitting it back into a click and a type would reopen the
        # gap between selecting a field and filling it.
        pointer = entry.get("pointer")
        if pointer is not None:
            window_class = (pointer.get("window") or {}).get("class")
            if not (pointer.get("relative") and pointer.get("window_size") and window_class):
                # Dropping it would leave a recipe that types without aiming,
                # which is a different procedure from the one recorded — so
                # this refuses the promotion rather than quietly changing it.
                raise RecipeError(
                    "a typing action in that slice was aimed at a position "
                    "that cannot be replayed; re-record it"
                )
            step["relative"] = pointer["relative"]
            step["window_class"] = window_class
            step["window_size"] = pointer["window_size"]
            step["button"] = pointer.get("button", "left")
            step["count"] = pointer.get("count", 1)
    elif action == "key":
        step["chord"] = entry.get("chord")
        step["semantic"] = entry.get("semantic")
    elif action == "browser":
        # The action, deliberately not the chord it resolved to this time. A
        # keymap the user changes between recording and replay should move the
        # recipe with it; a frozen chord would keep firing the old binding,
        # which by then belongs to some other command.
        step["semantic"] = entry.get("semantic")
        step["browser"] = entry.get("browser")
    elif action == "focus":
        step["window_class"] = window.get("class")
    elif action == "wait":
        if entry.get("outcome") == "timeout":
            return None  # a wait that never matched is not part of a working run
        step["condition"] = entry.get("condition")
        step["class"] = entry.get("class")
        step["title"] = entry.get("title")
        step["timeout"] = entry.get("timeout", 30.0)
    return step


def from_log(
    entries: list[dict[str, Any]],
    name: str,
    site: str,
    task: str,
    capture_profile: str,
) -> dict[str, Any]:
    steps = [s for s in (step_from_log(e) for e in entries) if s]
    if not steps:
        raise RecipeError("no replayable actions in that slice of the log")
    typed = 0
    for step in steps:
        if step["action"] == "type":
            typed += 1
            step["param"] = f"text{typed}"
    return {
        "name": validate_name(name),
        "site": site,
        "task": task,
        "created_at": time.time(),
        "revision": 1,
        "preconditions": [],
        # Which consumer's budget the replay captures at. Deliberately outside
        # the reviewed hash: pointer steps are window-relative and map through
        # whatever image the capture produced, so the profile changes what a
        # replay costs but never where it clicks. Approval is about the steps.
        "capture_profile": capture_profile,
        "steps": steps,
        # A recipe without a check cannot tell a working replay from a replay
        # that clicked into empty space, so the field is always present and
        # unset rather than absent and forgotten.
        "verify": None,
        "history": [],
        "reviewed_hash": None,
    }


def save(recipe: dict[str, Any]) -> Path:
    """Write a recipe, carrying its previous approval forward untouched.

    Approval attaches to a specific set of steps, and the stored hash is what
    says which. Carrying it forward rather than clearing it means the refusal
    can distinguish "never reviewed" from "reviewed, but not these steps" — and
    a recipe whose steps are unchanged keeps its approval, however many times
    it is re-saved.
    """
    path = path_for(recipe["name"])
    if path.exists():
        previous = load(recipe["name"])
        recipe["revision"] = previous["revision"] + 1
        recipe["history"] = previous.get("history", [])
        recipe["reviewed_hash"] = previous.get("reviewed_hash")
    path.write_text(json.dumps(recipe, indent=2))
    return path


def load(name: str) -> dict[str, Any]:
    path = path_for(name)
    if not path.is_file():
        raise RecipeNotFound(f"no recipe named {name!r}")
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RecipeError(f"{path}: {exc}") from exc


def listing() -> list[dict[str, Any]]:
    out = []
    for path in sorted(store_dir().glob("*.json")):
        try:
            recipe = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            {
                "name": recipe.get("name", path.stem),
                "site": recipe.get("site", ""),
                "task": recipe.get("task", ""),
                "revision": recipe.get("revision", 1),
                "steps": len(recipe.get("steps", [])),
                "reviewed": is_reviewed(recipe),
            }
        )
    return out


def is_reviewed(recipe: dict[str, Any]) -> bool:
    return bool(recipe.get("reviewed_hash")) and recipe["reviewed_hash"] == content_hash(recipe)


def approve(name: str) -> dict[str, Any]:
    recipe = load(name)
    recipe["reviewed_hash"] = content_hash(recipe)
    path_for(name).write_text(json.dumps(recipe, indent=2))
    return recipe


def record_outcome(name: str, outcome: str, note: str = "") -> dict[str, Any]:
    """Append what happened to a replay.

    This history is what makes "you got that wrong last time, do it
    differently" answerable: the next attempt starts from the recorded failure
    rather than from nothing.
    """
    recipe = load(name)
    recipe.setdefault("history", []).append(
        {"at": time.time(), "revision": recipe["revision"], "outcome": outcome, "note": note}
    )
    path_for(name).write_text(json.dumps(recipe, indent=2))
    return recipe


def unreviewed_reason(recipe: dict[str, Any]) -> str | None:
    """Why this recipe may not be replayed, or None if it may."""
    if not recipe.get("reviewed_hash"):
        return "it has never been reviewed"
    if recipe["reviewed_hash"] != content_hash(recipe):
        return "its steps changed since the review that approved them"
    return None


def _window_by_class(window_class: str, snap: dict[str, Any]) -> dict[str, Any]:
    matches = [w for w in snap["windows"] if w["class"].lower() == (window_class or "").lower()]
    if not matches:
        raise RecipeError(f"no window of class {window_class!r} is open")
    # Ties go to the most recently focused, which is the window a person
    # replaying this would mean.
    return min(matches, key=lambda w: w["focus_order"])


def replay(
    name: str,
    cfg: dict[str, Any],
    params: dict[str, str],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Run a stored procedure, refusing anything unreviewed.

    Pointer steps re-enter through the ordinary frame contract: a fresh capture
    of the target window, then a click against that frame. Reusing the contract
    rather than computing coordinates directly means a replay inherits every
    staleness and denylist refusal instead of needing its own.
    """
    from . import actions, observe

    recipe = load(name)
    # Recipes promoted before profiles were recorded fall back to the
    # configured consumer rather than refusing: the profile changes cost, not
    # aim, so an absent one is not a reason to block a reviewed procedure.
    profile_name = recipe.get("capture_profile") or cfg["capture"]["profile"]
    blocked = unreviewed_reason(recipe)
    if blocked and not dry_run:
        raise RecipeError(
            f"refusing to replay {name!r}: {blocked}. "
            f"Read it with `cu recipe show {name}` and approve with `cu recipe review {name}`."
        )

    performed = []
    for index, step in enumerate(recipe["steps"], start=1):
        action = step["action"]
        if action == "focus":
            snap = observe.snapshot()
            window = _window_by_class(step["window_class"], snap)
            performed.append(actions.focus(cfg, window, dry_run=dry_run))
        elif action in {"click", "move"}:
            snap = observe.snapshot()
            window = _window_by_class(step["window_class"], snap)
            frame = _capture_window(window, snap["monitors"], cfg, profile_name)
            at = _point_in_frame(step, window, frame)
            if action == "click":
                performed.append(
                    actions.click(
                        cfg, frame["id"], at,
                        button=step.get("button", "left"),
                        count=step.get("count", 1),
                        dry_run=dry_run,
                    )
                )
            else:
                performed.append(actions.move(cfg, frame["id"], at, dry_run=dry_run))
        elif action == "scroll":
            frame_id = None
            at = None
            if step.get("relative"):
                snap = observe.snapshot()
                window = _window_by_class(step["window_class"], snap)
                frame = _capture_window(window, snap["monitors"], cfg, profile_name)
                frame_id = frame["id"]
                at = _point_in_frame(step, window, frame)
            performed.append(
                actions.scroll(
                    cfg, dy=step.get("dy", 0), dx=step.get("dx", 0),
                    frame_id=frame_id, at=at, dry_run=dry_run,
                )
            )
        elif action == "type":
            param = step.get("param")
            if param not in params:
                raise RecipeError(
                    f"step {index} types the parameter {param!r}, which was not "
                    f"supplied; pass --param {param}=..."
                )
            frame_id = None
            at = None
            if step.get("relative"):
                snap = observe.snapshot()
                window = _window_by_class(step["window_class"], snap)
                frame = _capture_window(window, snap["monitors"], cfg, profile_name)
                frame_id = frame["id"]
                at = _point_in_frame(step, window, frame)
            performed.append(
                actions.type_text(
                    cfg, params[param], method=step.get("method", "clipboard"),
                    frame_id=frame_id, at=at,
                    button=step.get("button", "left"), count=step.get("count", 1),
                    dry_run=dry_run,
                )
            )
        elif action == "key":
            performed.append(
                actions.key(
                    cfg, action=step.get("semantic"), chord=step.get("chord"),
                    dry_run=dry_run,
                )
            )
        elif action == "browser":
            from . import browser as browser_module

            snap = observe.snapshot()
            focused = snap["focused"]
            if focused is None:
                raise RecipeError(f"step {index} drives a browser, but nothing holds focus")
            target = browser_module.for_window_class(focused["class"])
            if target is None:
                raise RecipeError(
                    f"step {index} drives a browser, but the focused window "
                    f"({focused['class']}) is not one"
                )
            performed.append(
                actions.browser_action(cfg, target, step["semantic"], dry_run=dry_run)
            )
        elif action == "wait":
            from . import wait as wait_module

            if dry_run:
                performed.append({"action": "wait", "dry_run": True})
            else:
                try:
                    performed.append(
                        wait_module.until(
                            step["condition"], cls=step.get("class"),
                            title=step.get("title"), timeout=step.get("timeout", 30.0),
                        )
                    )
                except wait_module.WaitTimeout as exc:
                    raise RecipeTimeout(f"step {index}: {exc}") from exc
        else:
            raise RecipeError(f"step {index} has unknown action {action!r}")

    verified = check(recipe.get("verify"))
    return {
        "recipe": name,
        "revision": recipe["revision"],
        "steps": len(performed),
        "verified": verified,
        "dry_run": dry_run,
    }


def _point_in_frame(
    step: dict[str, Any], window: dict[str, Any], frame: dict[str, Any]
) -> tuple[float, float]:
    """Map a recorded window fraction into the visible frame, refusing resize drift."""
    measured = step.get("window_size")
    if not measured:
        raise RecipeError("pointer step predates window-size checks; record and review it again")
    current = window["logical_rect"]
    for axis in ("w", "h"):
        if measured[axis] <= 0:
            raise RecipeError(f"pointer step has an invalid measured {axis}")
        drift = abs(current[axis] / measured[axis] - 1.0)
        if drift > WINDOW_SIZE_TOLERANCE:
            raise RecipeError(
                f"{window['class']} is {round(current['w'])}x{round(current['h'])}, "
                f"but the pointer step was recorded at "
                f"{round(measured['w'])}x{round(measured['h'])}; "
                "resize it or record and review the recipe again"
            )

    relative = step["relative"]
    logical = {
        "x": current["x"] + relative["x"] * current["w"],
        "y": current["y"] + relative["y"] * current["h"],
    }
    depicted = frame["logical_rect"]
    if not (
        depicted["x"] <= logical["x"] < depicted["x"] + depicted["w"]
        and depicted["y"] <= logical["y"] < depicted["y"] + depicted["h"]
    ):
        raise RecipeError("the recorded pointer target is outside the visible part of the window")
    image = frame["image_size"]
    return (
        (logical["x"] - depicted["x"]) * image["w"] / depicted["w"],
        (logical["y"] - depicted["y"]) * image["h"] / depicted["h"],
    )


def _capture_window(
    window: dict[str, Any],
    monitors: list[dict[str, Any]],
    cfg: dict[str, Any],
    profile_name: str,
) -> dict[str, Any]:
    from . import capture, frames, observe

    # Replays capture per pointer step, so without this the store grows for the
    # length of the replay and is only ever trimmed by the next `cu shot`.
    frames.collect()
    rect = capture.clamp(
        window["logical_rect"],
        capture.sole_monitor(window["logical_rect"], monitors)["logical_rect"],
    )
    monitor = capture.sole_monitor(rect, monitors)
    profile = capture.resolve(profile_name, cfg)
    scale = capture.scale_for(rect, monitor["scale"], profile["max_long_edge"])
    with frames.capture_scratch() as scratch:
        capture.grab_region(rect, scratch, scale)
        live = next(
            (
                candidate
                for candidate in observe.windows(monitors)
                if candidate["address"] == window["address"]
            ),
            None,
        )
        if live is None:
            raise frames.FrameError(
                f"window {window['class']} ({window['address']}) disappeared during capture"
            )
        if live["logical_rect"] != window["logical_rect"]:
            raise frames.FrameError(
                f"window {window['class']} moved or resized during capture"
            )
        image_size = capture.size_of(scratch)
        return frames.new(
            source={"type": "window", "name": window["class"]},
            logical_rect=rect,
            image_size=image_size,
            scratch=scratch,
            native_size={
                "w": round(rect["w"] * monitor["scale"]),
                "h": round(rect["h"] * monitor["scale"]),
            },
            digest_native=capture.digest(scratch),
            monitor={
                "name": monitor["name"],
                "scale": monitor["scale"],
                "logical_rect": dict(monitor["logical_rect"]),
            },
            profile=profile_name,
            visual_tokens=capture.visual_tokens(
                image_size["w"], image_size["h"], profile
            ),
            # The identity is re-read after the capture rather than reused
            # from before it, since the title may have moved on while the shot
            # was taken. The geometry stays the one the pixels depict: it was
            # already asserted unchanged above.
            window={
                "address": window["address"],
                "class": live["class"],
                "title": live["title"],
                "logical_rect": window["logical_rect"],
                # Carried when the observer can name the document itself. A
                # frame that fell back to the title would be checked by the
                # weaker identity of the two for the rest of its life.
                **({"content_id": live["content_id"]} if live.get("content_id") else {}),
            },
        )


def check(verify: dict[str, Any] | None) -> bool | None:
    """Decide whether a replay actually worked.

    None means the recipe states no check, which is not the same as success —
    a replay that clicked into empty space looks identical to one that worked
    unless something says otherwise.
    """
    if not verify:
        return None
    kind = verify.get("kind")
    field = {"window-title": "title", "window-class": "class"}.get(kind)
    if field is None:
        # Checked before the compositor is asked anything. A recipe naming a
        # check cu does not implement is wrong however the screen looks, and
        # reading the screen first only delays saying so.
        raise RecipeError(f"unknown verification kind {kind!r}")

    from . import observe

    needle = verify.get("contains", "").lower()
    return any(needle in w[field].lower() for w in observe.snapshot()["windows"])
