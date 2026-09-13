"""Structural observation: what is on screen, without looking at pixels.

The compositor already knows which window is focused and where it sits, so
answering those questions costs a few hundred tokens instead of a screenshot.
Every rectangle here is in logical coordinates, the space Hyprland accepts back.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

from . import hypr


def _rect(x: float, y: float, w: float, h: float) -> dict[str, float]:
    return {"x": x, "y": y, "w": w, "h": h}


def intersection(
    first: dict[str, float], second: dict[str, float]
) -> dict[str, float] | None:
    """Return the positive-area intersection of two logical rectangles."""
    left = max(first["x"], second["x"])
    top = max(first["y"], second["y"])
    right = min(first["x"] + first["w"], second["x"] + second["w"])
    bottom = min(first["y"] + first["h"], second["y"] + second["h"])
    if right <= left or bottom <= top:
        return None
    return _rect(left, top, right - left, bottom - top)


def reachability(
    rect: dict[str, float],
    mons: list[dict[str, Any]],
    *,
    max_gaps: int = 64,
) -> dict[str, Any]:
    """Describe a rectangle's coverage by the active output union.

    The grid uses only the rectangle edges, making its area exact for the
    reported geometry. Interior gap details are bounded while their full count
    and area remain in the result.
    """
    active = [
        monitor
        for monitor in mons
        if not monitor.get("disabled", False) and monitor.get("logical_rect")
    ]
    intersections = [
        {"output": monitor["name"], "logical_rect": clipped}
        for monitor in active
        if (clipped := intersection(rect, monitor["logical_rect"])) is not None
    ]
    total = max(0.0, rect["w"] * rect["h"])
    if not active:
        return {
            "status": "unreachable",
            "rect": rect,
            "intersections": intersections,
            "reachable_area": 0.0,
            "unreachable_area": total,
            "edge_excess": {
                "left": rect["w"], "right": rect["w"],
                "top": rect["h"], "bottom": rect["h"],
            },
            "gaps": [],
            "gap_count": 0,
            "truncated": False,
        }
    xs = {rect["x"], rect["x"] + rect["w"]}
    ys = {rect["y"], rect["y"] + rect["h"]}
    for item in intersections:
        clipped = item["logical_rect"]
        xs.update((clipped["x"], clipped["x"] + clipped["w"]))
        ys.update((clipped["y"], clipped["y"] + clipped["h"]))
    bounds = desktop_bounds(active)
    reachable_area = 0.0
    gaps: list[dict[str, float]] = []
    gap_count = 0
    for left, right in pairwise(sorted(xs)):
        for top, bottom in pairwise(sorted(ys)):
            center_x, center_y = (left + right) / 2, (top + bottom) / 2
            area = (right - left) * (bottom - top)
            if any(contains(monitor["logical_rect"], center_x, center_y) for monitor in active):
                reachable_area += area
            elif contains(bounds, center_x, center_y):
                gap_count += 1
                if len(gaps) < max_gaps:
                    gaps.append(_rect(left, top, right - left, bottom - top))
    edge_excess = {
        "left": max(0.0, bounds["x"] - rect["x"]),
        "right": max(0.0, rect["x"] + rect["w"] - (bounds["x"] + bounds["w"])),
        "top": max(0.0, bounds["y"] - rect["y"]),
        "bottom": max(0.0, rect["y"] + rect["h"] - (bounds["y"] + bounds["h"])),
    }
    unreachable_area = max(0.0, total - reachable_area)
    if unreachable_area == 0:
        status = "fully-reachable"
    elif reachable_area == 0:
        status = "unreachable"
    else:
        status = "partially-reachable"
    return {
        "status": status,
        "rect": rect,
        "intersections": intersections,
        "reachable_area": reachable_area,
        "unreachable_area": unreachable_area,
        "edge_excess": edge_excess,
        "gaps": gaps,
        "gap_count": gap_count,
        "truncated": gap_count > len(gaps),
    }


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
                "id": m.get("id", len(out)),
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
                "special_workspace": m.get("specialWorkspace", {}).get("name", ""),
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

    Monitor IDs are resolved to names; IDs need not be contiguous after hotplug.
    Client visibility also requires a displayed workspace: the client's visible
    flag alone can remain true while another workspace occupies the output.
    """
    mons = mons if mons is not None else monitors()
    by_id = {m.get("id", i): m for i, m in enumerate(mons)}
    out = []
    for c in hypr.query("clients"):
        if not c.get("mapped", True):
            continue
        at, size = c["at"], c["size"]
        monitor = by_id.get(c["monitor"])
        workspace = c["workspace"]["name"]
        displayed = bool(monitor) and not monitor.get("disabled", False) and (
            c.get("pinned", False)
            or workspace == (monitor.get("special_workspace") or monitor.get("active_workspace"))
            or "active_workspace" not in monitor
        )
        out.append(
            {
                "address": c["address"],
                "class": c["class"],
                "title": c["title"],
                "pid": c["pid"],
                "xwayland": c["xwayland"],
                "floating": c["floating"],
                "hidden": c.get("hidden", False),
                "visible": c.get("visible", True) and displayed,
                # Lower means more recently focused, which is the closest thing
                # Hyprland exposes to a stacking order.
                "focus_order": c.get("focusHistoryID", 1 << 30),
                "workspace": c["workspace"]["name"],
                "monitor": monitor["name"] if monitor else str(c["monitor"]),
                "logical_rect": _rect(at[0], at[1], size[0], size[1]),
            }
        )
        out[-1]["reachability"] = reachability(out[-1]["logical_rect"], mons)
    return out


def contains(rect: dict[str, float], x: float, y: float) -> bool:
    return rect["x"] <= x < rect["x"] + rect["w"] and rect["y"] <= y < rect["y"] + rect["h"]


def windows_at(x: float, y: float, wins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Visible windows whose reported geometry covers a logical point."""
    return [
        w for w in wins
        if w["visible"] and not w["hidden"] and contains(w["logical_rect"], x, y)
    ]


def window_at(x: float, y: float, wins: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The heuristic window a click at this logical point would land in.

    Only visible windows are considered, and ties go to the most recently
    focused, which is the best available stand-in for what is actually on top.
    An answer here is what lets the denylist apply to a point on a full-output
    frame rather than only to a window the caller named.
    """
    covering = windows_at(x, y, wins)
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
    rects = [m["logical_rect"] for m in mons if not m.get("disabled", False)]
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
