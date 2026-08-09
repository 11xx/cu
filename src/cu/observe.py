"""Structural observation: what is on screen, without looking at pixels.

The compositor already knows which window is focused and where it sits, so
answering those questions costs a few hundred tokens instead of a screenshot.
Every rectangle here is in logical coordinates, the space Hyprland accepts back.
"""

from __future__ import annotations

from typing import Any

from . import hypr


def _rect(x: float, y: float, w: float, h: float) -> dict[str, float]:
    return {"x": x, "y": y, "w": w, "h": h}


def monitors() -> list[dict[str, Any]]:
    """Monitors with both coordinate spaces stated explicitly.

    `width`/`height` from Hyprland are physical pixels while `x`/`y` are a
    logical origin; mixing them is the bug this whole project exists to avoid,
    so both forms are named rather than left to the caller to derive.
    """
    out = []
    for m in hypr.query("monitors"):
        scale = m["scale"]
        left, top, right, bottom = m["reserved"]
        out.append(
            {
                "name": m["name"],
                "scale": scale,
                "physical": {"w": m["width"], "h": m["height"]},
                "logical_rect": _rect(
                    m["x"], m["y"], round(m["width"] / scale), round(m["height"] / scale)
                ),
                "reserved": {
                    "left": left,
                    "top": top,
                    "right": right,
                    "bottom": bottom,
                },
                "focused": m["focused"],
                "active_workspace": m["activeWorkspace"]["name"],
                "disabled": m.get("disabled", False),
            }
        )
    return out


def layer_surfaces() -> list[dict[str, Any]]:
    """Layer-shell surfaces in desktop-absolute logical coordinates."""
    out = []
    for monitor, layers in hypr.query("layers").items():
        for level, surfaces in layers["levels"].items():
            for surface in surfaces:
                out.append(
                    {
                        "address": surface["address"],
                        "namespace": surface["namespace"],
                        "monitor": monitor,
                        "level": int(level),
                        "logical_rect": _rect(
                            surface["x"], surface["y"], surface["w"], surface["h"]
                        ),
                    }
                )
    return out


def windows(mons: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Mapped windows, with the monitor named rather than indexed.

    Hyprland reports `monitor` as a position in its own output list, which is
    not stable across hotplug and means nothing to a caller holding an output
    name. Resolving it here keeps every rectangle in this module addressable by
    the same names `shot --output` accepts.
    """
    by_index = {i: m["name"] for i, m in enumerate(mons or monitors())}
    out = []
    for c in hypr.query("clients"):
        if not c.get("mapped", True):
            continue
        at, size = c["at"], c["size"]
        out.append(
            {
                "address": c["address"],
                "class": c["class"],
                "title": c["title"],
                "pid": c["pid"],
                "xwayland": c["xwayland"],
                "floating": c["floating"],
                "hidden": c.get("hidden", False),
                "visible": c.get("visible", True),
                # Lower means more recently focused, which is the closest thing
                # Hyprland exposes to a stacking order.
                "focus_order": c.get("focusHistoryID", 1 << 30),
                "workspace": c["workspace"]["name"],
                "monitor": by_index.get(c["monitor"], str(c["monitor"])),
                "logical_rect": _rect(at[0], at[1], size[0], size[1]),
            }
        )
    return out


def contains(rect: dict[str, float], x: float, y: float) -> bool:
    return rect["x"] <= x < rect["x"] + rect["w"] and rect["y"] <= y < rect["y"] + rect["h"]


def window_at(x: float, y: float, wins: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The window a click at this logical point would land in.

    Only visible windows are considered, and ties go to the most recently
    focused, which is the best available stand-in for what is actually on top.
    An answer here is what lets the denylist apply to a point on a full-output
    frame rather than only to a window the caller named.
    """
    covering = [
        w for w in wins if w["visible"] and not w["hidden"] and contains(w["logical_rect"], x, y)
    ]
    if not covering:
        return None
    return min(covering, key=lambda w: w["focus_order"])


def layer_surface_at(
    x: float, y: float, surfaces: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The highest layer level whose geometry covers this logical point."""
    covering = [surface for surface in surfaces if contains(surface["logical_rect"], x, y)]
    return max(covering, key=lambda surface: surface["level"], default=None)


def focused() -> dict[str, Any] | None:
    active = hypr.query("activewindow")
    if not active or "address" not in active:
        return None
    return {
        "address": active["address"],
        "class": active["class"],
        "title": active["title"],
        "pid": active["pid"],
    }


def cursor() -> dict[str, float]:
    pos = hypr.query("cursorpos")
    return {"x": pos["x"], "y": pos["y"]}


def desktop_bounds(mons: list[dict[str, Any]]) -> dict[str, float]:
    """The logical bounding box of all enabled outputs."""
    rects = [m["logical_rect"] for m in mons if not m["disabled"]]
    if not rects:
        return _rect(0, 0, 0, 0)
    x0 = min(r["x"] for r in rects)
    y0 = min(r["y"] for r in rects)
    x1 = max(r["x"] + r["w"] for r in rects)
    y1 = max(r["y"] + r["h"] for r in rects)
    return _rect(x0, y0, x1 - x0, y1 - y0)


def snapshot() -> dict[str, Any]:
    mons = monitors()
    focus = focused()
    wins = windows(mons)
    for w in wins:
        w["focused"] = focus is not None and w["address"] == focus["address"]
    return {
        "monitors": mons,
        "desktop_logical_bounds": desktop_bounds(mons),
        "windows": wins,
        "layer_surfaces": layer_surfaces(),
        "focused": focus,
        "cursor_logical": cursor(),
    }
