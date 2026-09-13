"""Screen capture, budgeted rather than literal.

Per-output capture is the primitive. Whole-desktop capture is never used:
Hyprland composites it at the largest scale in play, which upscales every other
output into an image that is both larger and blurrier than capturing each one
natively.

`grim -g` takes logical coordinates — the same space frames describe — so a
frame's rectangle is directly usable as its own capture argument.
"""

from __future__ import annotations

import hashlib
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import ConfigError

#: The name every profile inherits from, and the one that must always exist.
BASE_PROFILE = "generic"

#: Fields a profile may declare. A consumer is described by capability rather
#: than by a single number because the vendors do not share a unit: an image
#: costs whole patches, and the patch is 28px on one side and 32px on another,
#: so one long edge cannot express both. `generic` holds the most conservative
#: value for each, and a named profile states only what it changes — which is
#: what makes adding a consumer configuration rather than code.
PROFILE_FIELDS = {
    # Long edge cu downscales to. The figure that actually drives token cost.
    "max_long_edge": int,
    # The largest long edge the consumer accepts before downscaling itself.
    "ceiling": int,
    # Side of the square the consumer bills in. Anthropic bills 28px patches,
    # OpenAI 32px. Carrying it is what makes a cost estimate computable rather
    # than guessed.
    "patch_px": int,
    # The consumer's own cap on patches per image. Zero means it states none,
    # which is how a model that resizes nothing says so — TOML has no null, and
    # a budget of zero patches is not a thing anyone could otherwise mean.
    "token_budget": int,
    # Some models bill a patch at a rate rather than one-for-one.
    "token_multiplier": float,
    "max_bytes": int,
    "max_images_per_request": int,
}

#: The one field zero is a value for rather than a mistake: it is how a
#: consumer states no cap of its own. Everywhere else zero would divide, scale,
#: or size something into nothing.
UNBOUNDED_AT_ZERO = frozenset({"token_budget"})


def _check_field(profile: str, field: str, value: Any) -> None:
    """A configured value has to survive the arithmetic, not merely be present.

    Booleans are refused explicitly because `True` is an `int` in Python: a
    patch size of one pixel is not a refusal anyone would recognise as one.
    Non-finite floats are refused for the same reason one step further on: TOML
    spells `nan` and `inf`, and both pass an ordering check — `nan` compares
    false against every bound — before failing inside `round()`.
    """
    accepted = int if PROFILE_FIELDS[field] is int else (int, float)
    if isinstance(value, bool) or not isinstance(value, accepted):
        raise ConfigError(
            f"capture profile {profile!r} sets {field} to {value!r}; "
            f"it must be {'an integer' if accepted is int else 'a number'}"
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigError(
            f"capture profile {profile!r} sets {field} to {value!r}; "
            "it must be a finite number"
        )
    floor = 0 if field in UNBOUNDED_AT_ZERO else 1
    if value < floor:
        raise ConfigError(
            f"capture profile {profile!r} sets {field} to {value!r}; "
            f"it must be at least {floor}"
        )


def resolve(name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """One consumer's capabilities: the base, with that consumer's deltas.

    A field the base does not carry cannot be introduced by a profile, so a
    typo in a user's config is a refusal rather than a silently ignored key.
    Values are checked here too: every later caller divides, scales, or budgets
    by them, where a string or a zero is an exception with no exit code rather
    than a configuration the user can be told to fix.
    """
    table = cfg["capture"]["profiles"]
    if name not in table:
        # A name the caller typed, so this is a usage error rather than a
        # capture that went wrong — the two ask different things of the caller.
        raise ValueError(
            f"unknown capture profile: {name!r}; configured: {', '.join(sorted(table))}"
        )
    resolved = {**table[BASE_PROFILE], **table[name]}
    unknown = set(resolved) - set(PROFILE_FIELDS)
    if unknown:
        raise ConfigError(
            f"capture profile {name!r} declares unknown field(s): "
            f"{', '.join(sorted(unknown))}"
        )
    missing = set(PROFILE_FIELDS) - set(resolved)
    if missing:
        raise ConfigError(
            f"capture profile {BASE_PROFILE!r} is missing {', '.join(sorted(missing))}; "
            "every field must have a conservative default to inherit"
        )
    for field in PROFILE_FIELDS:
        _check_field(name, field, resolved[field])
    if resolved["max_long_edge"] > resolved["ceiling"]:
        raise ConfigError(
            f"capture profile {name!r} downscales to {resolved['max_long_edge']}px, "
            f"past the {resolved['ceiling']}px the consumer accepts"
        )
    return resolved


def default_name(cfg: dict[str, Any]) -> str:
    """The profile that applies when the caller names none.

    Unknown here is a configuration error rather than the usage error `resolve`
    raises: nobody typed this name on the command line, so no rewording of the
    command reaches a working one.
    """
    name = cfg["capture"]["profile"]
    table = cfg["capture"]["profiles"]
    if name not in table:
        raise ConfigError(
            f"configured default capture profile {name!r} has no stanza under "
            f"[capture.profiles]; configured: {', '.join(sorted(table))}"
        )
    return name


def names(cfg: dict[str, Any]) -> list[str]:
    return sorted(cfg["capture"]["profiles"])


def visual_tokens(width: int, height: int, profile: dict[str, Any]) -> int:
    """What an image of this size costs the consuming model.

    An image is billed in whole patches, so a pixel past a boundary costs a
    whole row. Reported rather than enforced: cu has already downscaled to the
    profile's long edge, and a consumer that resizes further does so on its own
    terms — the number is here so a caller can see what a capture spends.
    """
    side = profile["patch_px"]
    patches = math.ceil(width / side) * math.ceil(height / side)
    budget = profile["token_budget"]
    if budget:
        patches = min(patches, budget)
    return round(patches * profile["token_multiplier"])


def estimator(profile: dict[str, Any]) -> dict[str, Any]:
    return {"algorithm": "cu-patches-v1", **{key: profile[key] for key in (
        "patch_px", "token_budget", "token_multiplier",
    )}}


class CaptureError(RuntimeError):
    pass


class CaptureAimError(CaptureError):
    pass


class CaptureToolError(CaptureError):
    pass


class CaptureVisibilityError(CaptureError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class CaptureBudgetError(CaptureError):
    pass


# These are deliberately small because a refusal is consumed by a caller that
# needs a decision, not by a window inventory tool. Counts retain completeness
# without placing an unbounded compositor list in stdout or stderr.
MAX_WINDOW_DETAILS = 8
MAX_LAYER_DETAILS = 8
MAX_DETAIL_TEXT = 160


def require_budget(path: Path, profile: dict[str, Any]) -> None:
    size = path.stat().st_size
    if size > profile["max_bytes"]:
        raise CaptureBudgetError(
            f"encoded image is {size} bytes; profile permits {profile['max_bytes']}"
        )


def require_visible(window: dict[str, Any], rect: dict[str, float], snap: dict[str, Any]) -> None:
    """Refuse window identity when reported geometry cannot establish clear pixels.

    Focus history is not a stacking order. Any overlapping visible window is
    ambiguous, even when the requested window was focused most recently.
    Output/region capture remains available without asserting a window identity.
    """
    live = next((w for w in snap["windows"] if w["address"] == window["address"]), None)
    if live is None or not live.get("visible", True) or live.get("hidden", False):
        details = _visibility_details(window, rect, snap, [], [])
        details["visibility"] = {
            "status": "gone" if live is None else "not-visible",
            "reason": "the requested window is not on a visible workspace",
        }
        raise CaptureVisibilityError(
            "the requested window is not on a visible workspace", details=details
        )
    target = live
    overlaps = []
    for other in snap["windows"]:
        if (
            other["address"] != target["address"] and other.get("visible", True)
            and not other.get("hidden", False)
            and intersects(rect, other["logical_rect"])
        ):
            overlap = _intersection(rect, other["logical_rect"])
            overlaps.append(_window_detail(target, other, overlap))
    layers = []
    for surface in snap.get("layer_surfaces", []):
        if (
            surface.get("level", 0) >= 2
            and intersects(rect, surface["logical_rect"])
        ):
            overlap = _intersection(rect, surface["logical_rect"])
            layers.append(_layer_detail(surface, overlap))
    if overlaps or layers:
        details = _visibility_details(target, rect, snap, overlaps, layers)
        messages = []
        if extent_message := _extent_message(details):
            messages.append(extent_message)
        shown_windows = details["windows"]
        if shown_windows:
            names = ", ".join(
                _window_message(item) for item in shown_windows
            )
            suffix = _more_suffix(details["window_count"], len(shown_windows))
            messages.append(f"another window overlaps the requested capture: {names}{suffix}")
        shown_layers = details["layers"]
        if shown_layers:
            names = ", ".join(_layer_message(item) for item in shown_layers)
            suffix = _more_suffix(details["layer_count"], len(shown_layers))
            messages.append(f"top or overlay layers overlap the requested capture: {names}{suffix}")
        messages.append("stacking unsupported")
        raise CaptureVisibilityError("; ".join(messages), details=details)


def _bounded_text(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    if len(text) <= MAX_DETAIL_TEXT:
        return text
    return text[: MAX_DETAIL_TEXT - 1] + "…"


def _contains_rect(container: dict[str, float], inner: dict[str, float]) -> bool:
    return (
        container["x"] <= inner["x"]
        and container["y"] <= inner["y"]
        and container["x"] + container["w"] >= inner["x"] + inner["w"]
        and container["y"] + container["h"] >= inner["y"] + inner["h"]
    )


def _containment(target: dict[str, float], other: dict[str, float]) -> str:
    if _contains_rect(other, target):
        return "other-contains-target"
    if _contains_rect(target, other):
        return "target-contains-other"
    return "overlap-only"


def _transient_relation(
    target: dict[str, Any], other: dict[str, Any]
) -> dict[str, Any]:
    """Report ownership only when the observed client data says it exists."""
    owner = target.get("transient_owner")
    if not isinstance(owner, dict):
        return {
            "status": "unsupported",
            "reason": "the compositor client data did not expose transient ownership",
        }
    result = dict(owner)
    if owner.get("status") == "known":
        result["relation"] = (
            "target-transient-owner"
            if owner.get("address") == other.get("address")
            else "different-transient-owner"
        )
    return result


def _window_detail(
    target: dict[str, Any], other: dict[str, Any], overlap: dict[str, float]
) -> dict[str, Any]:
    target_rect = target["logical_rect"]
    other_rect = other["logical_rect"]
    return {
        "class": _bounded_text(other.get("class")),
        "title": _bounded_text(other.get("title")),
        "address": _bounded_text(other.get("address")),
        # logical_rect remains the established overlap field. The full window
        # and the explicit intersection are separate so containment cannot be
        # mistaken for the pixels that caused the refusal.
        "logical_rect": overlap,
        "window_rect": dict(other_rect),
        "intersection": overlap,
        "containment": _containment(target_rect, other_rect),
        "transient_owner": _transient_relation(target, other),
    }


def _layer_detail(surface: dict[str, Any], overlap: dict[str, float]) -> dict[str, Any]:
    return {
        "namespace": _bounded_text(surface.get("namespace")),
        "address": _bounded_text(surface.get("address")),
        "level": surface.get("level"),
        "logical_rect": overlap,
        "surface_rect": dict(surface["logical_rect"]),
        "intersection": overlap,
        "input_authority": {
            "status": "unknown",
            "reason": "layer geometry does not establish event routing",
        },
    }


def _visibility_details(
    window: dict[str, Any],
    rect: dict[str, float],
    snap: dict[str, Any],
    overlaps: list[dict[str, Any]],
    layers: list[dict[str, Any]],
) -> dict[str, Any]:
    shown_windows = overlaps[:MAX_WINDOW_DETAILS]
    shown_layers = layers[:MAX_LAYER_DETAILS]
    window_truncated = len(overlaps) > len(shown_windows)
    layer_truncated = len(layers) > len(shown_layers)
    # Import lazily so capture remains usable as the low-level primitive while
    # sharing the observer's explicit outer-bounds versus union distinction.
    from . import observe

    target_rect = window.get("logical_rect", rect)
    target_extent = observe.extent(target_rect, snap.get("monitors", []))
    return {
        "target": {
            "address": _bounded_text(window.get("address")),
            "class": _bounded_text(window.get("class")),
            "title": _bounded_text(window.get("title")),
            "logical_rect": dict(target_rect),
            "extent": target_extent,
            "reachability": observe.reachability(target_rect, snap.get("monitors", [])),
        },
        "windows": shown_windows,
        "window_count": len(overlaps),
        "window_limit": MAX_WINDOW_DETAILS,
        "windows_truncated": window_truncated,
        "layers": shown_layers,
        "layer_count": len(layers),
        "layer_limit": MAX_LAYER_DETAILS,
        "layers_truncated": layer_truncated,
        "truncated": window_truncated or layer_truncated,
        "stacking": {
            "status": "unsupported",
            "reason": "focus history and geometry do not establish pixel stacking",
        },
    }


def _more_suffix(total: int, shown: int) -> str:
    remaining = total - shown
    return f"; {remaining} more" if remaining else ""


def _window_message(item: dict[str, Any]) -> str:
    relation = item["containment"]
    owner = item["transient_owner"]
    ownership = owner.get("status", "unknown")
    return (
        f"{item['class']} {item['title']!r} ({item['address']}) at "
        f"{item['intersection']} (window {item['window_rect']}) "
        f"[{relation}; transient ownership {ownership}]"
    )


def _extent_message(details: dict[str, Any]) -> str | None:
    target = details.get("target", {})
    extent = target.get("extent", {})
    if extent.get("status") in {None, "inside"}:
        return None
    edges = extent.get("edge_excess") or {}
    edge_text = ", ".join(
        f"{name} {edges[name]:g}"
        for name in ("left", "right", "top", "bottom")
        if edges.get(name, 0)
    ) or "none"
    reachability = target.get("reachability", {})
    status = reachability.get("status", "unknown")
    return f"target extent {extent.get('status')} ({edge_text}); reachability {status}"


def _layer_message(item: dict[str, Any]) -> str:
    name = item["namespace"] or item["address"] or "unknown"
    return (
        f"{name} (level {item['level']}) at {item['intersection']} "
        f"(surface {item['surface_rect']}; input authority unknown)"
    )


def _run(args: list[str]) -> None:
    if not shutil.which(args[0]):
        raise CaptureToolError(f"{args[0]} is not installed")
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CaptureToolError(f"{args[0]} could not complete capture: {exc}") from exc
    if proc.returncode != 0:
        raise CaptureToolError(f"{' '.join(args[:2])}: {proc.stderr.strip()}")


def intersects(rect: dict[str, float], other: dict[str, float]) -> bool:
    return not (
        rect["x"] + rect["w"] <= other["x"]
        or other["x"] + other["w"] <= rect["x"]
        or rect["y"] + rect["h"] <= other["y"]
        or other["y"] + other["h"] <= rect["y"]
    )


def _intersection(
    rect: dict[str, float], other: dict[str, float]
) -> dict[str, float]:
    left = max(rect["x"], other["x"])
    top = max(rect["y"], other["y"])
    right = min(rect["x"] + rect["w"], other["x"] + other["w"])
    bottom = min(rect["y"] + rect["h"], other["y"] + other["h"])
    return {"x": left, "y": top, "w": right - left, "h": bottom - top}


def sole_monitor(
    rect: dict[str, float], monitors: list[dict[str, Any]]
) -> dict[str, Any]:
    """The one monitor a rectangle lies on.

    A region spanning outputs is refused rather than captured: Hyprland would
    composite it at the larger scale, silently upscaling the other half.
    """
    touched = [m for m in monitors if intersects(rect, m["logical_rect"])]
    if not touched:
        raise CaptureAimError("region falls outside every output")
    if len(touched) > 1:
        names = ", ".join(m["name"] for m in touched)
        raise CaptureAimError(
            f"region spans {names}; capture one output at a time to avoid upscaling"
        )
    return touched[0]


def clamp(rect: dict[str, float], bounds: dict[str, float]) -> dict[str, float]:
    """The capturable rectangle: inside the output, and on whole pixels.

    Both halves are one operation because a caller has no use for either
    without the other. `grim -g` takes integers, so a fractional rectangle —
    which is what a window-relative landmark resolves to — is truncated on the
    way in while the frame keeps the fraction. Every coordinate later mapped
    back through that frame inherits the difference. Rounding outward here
    means the frame records exactly the region that was captured.
    """
    x0 = math.floor(max(rect["x"], bounds["x"]))
    y0 = math.floor(max(rect["y"], bounds["y"]))
    x1 = math.ceil(min(rect["x"] + rect["w"], bounds["x"] + bounds["w"]))
    y1 = math.ceil(min(rect["y"] + rect["h"], bounds["y"] + bounds["h"]))
    # Rounding outward can leave the output, so the bounds get the last word.
    x0, y0 = max(x0, math.ceil(bounds["x"])), max(y0, math.ceil(bounds["y"]))
    x1 = min(x1, math.floor(bounds["x"] + bounds["w"]))
    y1 = min(y1, math.floor(bounds["y"] + bounds["h"]))
    if x1 <= x0 or y1 <= y0:
        raise CaptureAimError("region is empty after clamping to its output")
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def scale_for(
    rect: dict[str, float], monitor_scale: float, max_long_edge: int
) -> float:
    """The grim scale factor that lands inside the budget.

    `grim -s` multiplies the *logical* size, so the factor is a ratio against
    the logical rectangle rather than against pixels. It is capped at the
    output's own scale: beyond that grim would upsample, spending tokens on
    pixels carrying no additional detail.
    """
    longest = max(rect["w"], rect["h"])
    if longest <= 0:
        raise CaptureAimError("cannot scale an empty region")
    return min(max_long_edge / longest, monitor_scale)


def grab_output(name: str, dest: Path, scale: float) -> None:
    _run(["grim", "-o", name, "-s", f"{scale:.6f}", str(dest)])


def grab_region(rect: dict[str, float], dest: Path, scale: float) -> None:
    """Capture a rectangle that `clamp` has already put on whole pixels.

    Rounding here instead would be the frame contract's own bug: grim would
    capture one rectangle while the frame described another.
    """
    geometry = f"{round(rect['x'])},{round(rect['y'])} {round(rect['w'])}x{round(rect['h'])}"
    _run(["grim", "-g", geometry, "-s", f"{scale:.6f}", str(dest)])


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def size_of(path: Path) -> dict[str, int]:
    """Read the dimensions out of the PNG header.

    Spawning `magick identify` to learn two integers cost ~80 ms per call and
    was invoked three times per capture; the IHDR chunk is at a fixed offset.
    """
    header = path.read_bytes()[:24]
    if len(header) < 24 or not header.startswith(PNG_MAGIC):
        raise CaptureToolError(f"{path} is not a PNG")
    return {
        "w": int.from_bytes(header[16:20], "big"),
        "h": int.from_bytes(header[20:24], "big"),
    }


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
