"""Command dispatch.

Every subcommand prints JSON on `--json` and something readable otherwise, and
signals outcome through the codes in `exits` rather than through prose on
stderr.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from . import (
    actions,
    browser,
    browser_read_cli,
    capture,
    checks,
    config,
    context,
    desktop,
    evidence,
    exits,
    frames,
    guide,
    knowledge,
    metrics,
    native_playground,
    observe,
    png,
    recipes,
    safety,
    sandbox,
    status,
    uinput,
    wait,
)
from .actions import ClipboardError
from .browser import BrowserError
from .capture import CaptureAimError, CaptureError
from .compositor import QuietError
from .config import ConfigError
from .frames import FrameError
from .hypr import HyprError
from .knowledge import KnowledgeError
from .recipes import RecipeError, RecipeNotFound, RecipeTimeout
from .safety import Refused
from .sandbox import SandboxError
from .uinput import UinputError


class _Formatter(argparse.HelpFormatter):
    """Two-column help with room for a sentence, not a fragment.

    argparse's default wraps descriptions at whatever the flag names leave
    over, which for a command set of any size is a ragged column two words
    wide. A fixed, generous split reads like the help of the sibling tools.
    """

    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=32, width=96)

    def _format_action(self, action: argparse.Action) -> str:
        # The subcommand group prints its own metavar above the commands it
        # holds, which says nothing the commands do not. Render the children
        # and drop the header.
        if isinstance(action, argparse._SubParsersAction):
            return "".join(
                super(_Formatter, self)._format_action(child)
                for child in action._get_subactions()
            )
        return super()._format_action(action)


class _Parser(argparse.ArgumentParser):
    """An argparse parser that renders the way the sibling tools do.

    The description leads, the usage line follows it, and the sections are
    named for what they hold rather than for argparse's internal categories.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("formatter_class", _Formatter)
        super().__init__(*args, **kwargs)
        self._optionals.title = "Options"
        for action in self._actions:
            if isinstance(action, argparse._HelpAction):
                action.help = "Print help"

    def format_help(self) -> str:
        # A parser's positionals are its subcommands or its arguments, never
        # both, so the group is named for whichever it turned out to hold.
        self._positionals.title = (
            "Commands"
            if any(isinstance(a, argparse._SubParsersAction) for a in self._actions)
            else "Arguments"
        )
        formatter = self._get_formatter()
        if self.description:
            formatter.add_text(self.description)
        formatter.add_usage(
            self.usage, self._actions, self._mutually_exclusive_groups, prefix="Usage: "
        )
        for group in self._action_groups:
            if not group._group_actions:
                continue
            formatter.start_section(group.title)
            formatter.add_text(group.description)
            formatter.add_arguments(group._group_actions)
            formatter.end_section()
        formatter.add_text(self.epilog)
        return formatter.format_help()


STATUS_MARK = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL"}
MAX_MAGNIFIED_LONG_EDGE = 8192
MAX_HOVER_WAIT = 10.0

# These commands do not make the desktop look agent-driven. Their parsers also
# refuse the activity-only options whose values would otherwise be ignored.
NON_PUBLISHING_COMMANDS = frozenset({"status", "session", "abort", "export", "playground"})


def _num(value: float) -> str:
    """Logical space is continuous, but whole numbers should not read as floats."""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def fmt_rect(r: dict[str, float]) -> str:
    return f"{_num(r['w'])}x{_num(r['h'])}+{_num(r['x'])}+{_num(r['y'])}"


@metrics.timed("output_publication")
def _emit(data: Any, as_json: bool, render) -> None:
    if isinstance(data, dict) and context.should_correlate(data):
        data = {**data, **context.metadata()}
    metrics.frame(data)
    if as_json:
        json.dump(data, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        render(data)
    # Flushed here so that a reader who has gone away — `cu observe | head` —
    # fails inside the handler below rather than during interpreter shutdown,
    # where the only possible report is a message onto stderr.
    sys.stdout.flush()


def cmd_doctor(args: argparse.Namespace) -> int:
    results = checks.run_all()
    overall = checks.worst(results)
    payload = {
        "status": overall,
        "checks": results,
        "exit_codes": {
            exits.name(c): {"code": c, "response": r} for c, r in exits.RESPONSE.items()
        },
    }

    def render(_: Any) -> None:
        for r in results:
            line = f"{STATUS_MARK[r['status']]}  {r['name']:<20} {r['detail']}"
            print(line)
            if r["status"] != checks.OK and r["remedy"]:
                print(f"      {'':<20} → {r['remedy']}")
        print(f"\n{overall}")

    _emit(payload, args.json, render)
    return exits.PRECONDITION_FAILED if overall == checks.FAIL else exits.OK


def cmd_profiles(args: argparse.Namespace) -> int:
    """What each configured consumer costs, resolved rather than as written.

    A profile states only its deltas, so the file does not show what any of
    them actually resolves to. This does, which is what makes a new stanza
    checkable without reading the merge.
    """
    cfg = config.load()
    # Resolved first: a default naming no stanza would otherwise print a table
    # with no marker on it, which reads as "none is the default" rather than as
    # the misconfiguration every other command is about to hit.
    default = capture.default_name(cfg)
    rows = [
        {"name": n, "default": n == default, **capture.resolve(n, cfg)}
        for n in capture.names(cfg)
    ]

    def render(data: Any) -> None:
        for row in data["profiles"]:
            mark = "*" if row["default"] else " "
            budget = row["token_budget"] or "none"
            print(
                f"{mark} {row['name']:<10} long edge {row['max_long_edge']:>5} "
                f"(ceiling {row['ceiling']})  {row['patch_px']}px patches  "
                f"budget {budget}"
            )
        print(f"\nconfigure under [capture.profiles] in {config.config_path()}")

    _emit({"profiles": rows}, args.json, render)
    return exits.OK


def cmd_observe(args: argparse.Namespace) -> int:
    snap = observe.snapshot()

    def render(s: Any) -> None:
        for m in s["monitors"]:
            focus = " *" if m["focused"] else "  "
            reserved = m["reserved"]
            edges = ""
            if any(reserved.values()):
                edges = (
                    f" reserved left {reserved['left']} top {reserved['top']} "
                    f"right {reserved['right']} bottom {reserved['bottom']}"
                )
            print(
                f"{focus}{m['name']:<8} scale {m['scale']:<5} "
                f"physical {m['physical']['w']}x{m['physical']['h']}  "
                f"logical {fmt_rect(m['logical_rect']):<22} ws {m['active_workspace']}{edges}"
            )
        print(f"  desktop  logical {fmt_rect(s['desktop_logical_bounds'])}")
        c = s["cursor_logical"]
        print(f"  cursor   logical {c['x']},{c['y']}\n")
        for w in s["windows"]:
            focus = " *" if w["focused"] else "  "
            xw = "xwayland" if w["xwayland"] else ""
            print(
                f"{focus}{w['class']:<18} {fmt_rect(w['logical_rect']):<22} "
                f"ws {w['workspace']:<4} {xw:<9} {w['title'][:44]}"
            )
            reachability = w.get("reachability")
            if reachability and reachability["status"] != "fully-reachable":
                print(
                    f"    reachability {reachability['status']} "
                    f"unreachable area {reachability['unreachable_area']:g}; "
                    f"gaps {reachability['gap_count']}"
                )
        for surface in s["layer_surfaces"]:
            print(
                f"  layer {surface['namespace']:<18} "
                f"{fmt_rect(surface['logical_rect']):<22} "
                f"monitor {surface['monitor']:<8} level {surface['level']}"
            )

    _emit(snap, args.json, render)
    return exits.OK


def _resolve_window(selector: str, snap: dict[str, Any]) -> dict[str, Any]:
    windows = snap["windows"]
    if selector == "active":
        match = next((w for w in windows if w["focused"]), None)
        if match is None:
            raise LookupError("no window is focused")
        return match
    exact = [w for w in windows if w["address"] == selector]
    if exact:
        return exact[0]
    # Class first, then title. Several windows of one application share a
    # class, so the title is often the only thing that separates them.
    for field in ("class", "title"):
        loose = [w for w in windows if selector.lower() in w[field].lower()]
        if len(loose) == 1:
            return loose[0]
        if len(loose) > 1:
            names = ", ".join(f"{w['class']}:{w['title'][:24]!r}" for w in loose[:5])
            raise LookupError(
                f"{selector!r} matches several windows by {field}: {names}. "
                "Use an address, or a fragment that appears in only one title."
            )
    raise LookupError(f"no window matches {selector!r}")


def _parse_region(text: str) -> dict[str, float]:
    """Accept grim's own `x,y wxh`, and a plain `x,y,w,h`."""
    cleaned = text.replace("x", " ").replace(",", " ")
    parts = cleaned.split()
    if len(parts) != 4:
        raise ValueError(f"region must be 'x,y wxh' or 'x,y,w,h', got {text!r}")
    x, y, w, h = (float(p) for p in parts)
    if not all(math.isfinite(value) for value in (x, y, w, h)):
        raise ValueError("region coordinates and dimensions must be finite")
    if w <= 0 or h <= 0:
        raise ValueError("region width and height must be positive")
    return {"x": x, "y": y, "w": w, "h": h}


def _parse_watch(text: str, image_size: dict[str, int]) -> dict[str, int]:
    if text == "full":
        return {"x": 0, "y": 0, **image_size}
    parsed = _parse_region(text)
    if any(not value.is_integer() for value in parsed.values()):
        raise ValueError("--watch coordinates and dimensions must be whole image pixels")
    box = {name: int(value) for name, value in parsed.items()}
    if (
        box["x"] < 0
        or box["y"] < 0
        or box["x"] + box["w"] > image_size["w"]
        or box["y"] + box["h"] > image_size["h"]
    ):
        raise ValueError(
            f"--watch is outside the reference image "
            f"({image_size['w']}x{image_size['h']})"
        )
    return box


@metrics.timed("crop_compare")
def _watched_pixels(rgba: bytes, image_width: int, box: dict[str, int]) -> bytes:
    row_bytes = image_width * 4
    left = box["x"] * 4
    right = (box["x"] + box["w"]) * 4
    return b"".join(
        rgba[row * row_bytes + left : row * row_bytes + right]
        for row in range(box["y"], box["y"] + box["h"])
    )


def _current_window(
    captured: dict[str, Any] | None, windows: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The window a frame depicts, re-read at capture time.

    A frame records what it depicts so a later action can tell that the window
    now shows something else. Copying a reference frame's record into a fresh
    capture would carry that identity past the moment it was true, and the
    picture — which is current — would then be refused as describing content
    that has changed.

    Only the identity is refreshed. Geometry belongs to the pixels: the image
    depicts the rectangle the capture was aimed at, so adopting a newer one
    would let a window that moved during the capture pass the staleness check
    it should fail.

    Every identity the observer can supply is re-read, `content_id` included.
    Keeping the older one would label a current picture with what the window
    used to show, and every action through it would be refused.
    """
    if captured is None:
        return None
    live = next((w for w in windows if w["address"] == captured["address"]), None)
    if live is None:
        return captured
    refreshed = {**captured, "class": live["class"], "title": live["title"]}
    if live.get("content_id"):
        refreshed["content_id"] = live["content_id"]
    return refreshed


def _window_descriptor(window: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only the stable identity a frame can recheck later."""
    if window is None:
        return None
    descriptor = {
        "address": window["address"],
        "class": window["class"],
        "title": window["title"],
        "logical_rect": dict(window["logical_rect"]),
    }
    if window.get("content_id"):
        descriptor["content_id"] = window["content_id"]
    return descriptor


def _frame_capture_scale(frame: dict[str, Any]) -> float:
    if "capture_scale" in frame:
        return float(frame["capture_scale"])
    try:
        rect = frame["logical_rect"]
        image = frame["image_size"]
        scales = (image["w"] / rect["w"], image["h"] / rect["h"])
    except (KeyError, TypeError, ZeroDivisionError) as exc:
        raise FrameError(f"frame {frame['id']} has no comparable capture scale") from exc
    # Older descriptors did not record grim's scale. One output pixel of
    # rounding on each axis is the most that can separate these two ratios.
    tolerance = (1 / rect["w"]) + (1 / rect["h"])
    if abs(scales[0] - scales[1]) > tolerance:
        raise FrameError(f"frame {frame['id']} has no comparable capture scale")
    return sum(scales) / len(scales)


@metrics.timed("acquisition")
def _grab_shot(
    source: dict[str, Any], rect: dict[str, float], scratch: Path, scale: float
) -> None:
    if source["type"] == "output":
        capture.grab_output(source["name"], scratch, scale)
    else:
        capture.grab_region(rect, scratch, scale)


def _confirm_captured_window(
    window: dict[str, Any], monitors: list[dict[str, Any]]
) -> None:
    """Establish that grim captured the geometry selected before it ran."""
    live = next(
        (w for w in observe.windows(monitors) if w["address"] == window["address"]),
        None,
    )
    if live is None:
        raise FrameError(
            f"window {window['class']} ({window['address']}) disappeared during capture"
        )
    if live["logical_rect"] != window["logical_rect"]:
        raise FrameError(f"window {window['class']} moved or resized during capture")


def _validate_after_args(args: argparse.Namespace) -> None:
    if args.watch and not args.after:
        raise ValueError("--watch requires --after")
    if not args.after:
        return
    conflicting = [
        flag
        for flag, value in (
            ("--window", args.window),
            ("--output", args.output),
            ("--region", args.region),
            ("--landmark", args.landmark),
            ("--target-window", getattr(args, "target_window", None)),
        )
        if value
    ]
    if conflicting:
        raise ValueError(
            f"--after cannot combine with {', '.join(conflicting)}; "
            "the reference frame already names the region"
        )
    if not args.watch:
        raise ValueError(
            "--after requires --watch; unrelated pixels such as a ticking clock "
            "must not satisfy the wait"
        )
    if args.profile:
        raise ValueError("--after cannot combine with --for; it reuses the reference scale")
    if args.if_changed:
        raise ValueError("--after cannot combine with --if-changed")
    if not all(math.isfinite(value) for value in (args.poll_ms, args.quiet_ms, args.timeout)):
        raise ValueError("wait timing values must be finite")
    if args.poll_ms <= 0:
        raise ValueError("--poll-ms must be positive")
    if args.quiet_ms < 0:
        raise ValueError("--quiet-ms cannot be negative")
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")


def _check_recapture(reference: dict[str, Any], snap: dict[str, Any]) -> None:
    why = frames.staleness(reference, snap["windows"], snap["monitors"], max_age=float("inf"))
    if why:
        raise FrameError(why)
    if reference.get("window"):
        capture.require_visible(reference["window"], reference["logical_rect"], snap)


def _shot_after(args: argparse.Namespace, snap: dict[str, Any], cfg: dict[str, Any]) -> int:
    reference = frames.load(args.after)
    _check_recapture(reference, snap)
    expected_size = reference["image_size"]
    watch = _parse_watch(args.watch, expected_size)
    try:
        with metrics.span("reference_decode"):
            width, height, reference_rgba = png.decode(Path(reference["path"]).read_bytes())
    except (OSError, ValueError, KeyError) as exc:
        raise FrameError(f"cannot read frame {reference['id']}: {exc}") from exc
    decoded_size = {"w": width, "h": height}
    if decoded_size != expected_size:
        raise FrameError(
            f"frame {reference['id']} descriptor says {expected_size['w']}x"
            f"{expected_size['h']}, but its PNG is {width}x{height}"
        )

    source = reference["source"]
    rect = reference["logical_rect"]
    scale = _frame_capture_scale(reference)
    baseline = _watched_pixels(reference_rgba, width, watch)
    previous = baseline
    changed = False
    quiet_since = 0.0
    probes = 0
    started = time.monotonic()
    deadline = started + args.timeout
    poll_seconds = args.poll_ms / 1000
    quiet_seconds = args.quiet_ms / 1000

    while True:
        if probes and time.monotonic() >= deadline:
            elapsed = time.monotonic() - started
            print(
                f"cu: timed out after {elapsed:.3f}s and {probes} probes; "
                "watched pixels did not change and settle",
                file=sys.stderr,
            )
            return exits.TIMED_OUT

        _check_recapture(reference, observe.snapshot())
        with frames.capture_scratch() as scratch:
            _grab_shot(source, rect, scratch, scale)
            captured_snap = observe.snapshot()
            _check_recapture(reference, captured_snap)
            probes += 1
            try:
                with metrics.span("probe_decode"):
                    probe_width, probe_height, probe_rgba = png.decode(scratch.read_bytes())
            except (OSError, ValueError) as exc:
                raise FrameError(f"cannot compare probe {probes}: {exc}") from exc
            probe_size = {"w": probe_width, "h": probe_height}
            if probe_size != expected_size:
                raise FrameError(
                    f"probe {probes} is {probe_width}x{probe_height}, but reference "
                    f"{reference['id']} is {expected_size['w']}x{expected_size['h']}; "
                    "capture conditions changed"
                )

            now = time.monotonic()
            current = _watched_pixels(probe_rgba, probe_width, watch)
            with metrics.span("crop_compare"):
                if not changed:
                    if current != baseline:
                        changed = True
                        quiet_since = now
                elif current != previous:
                    quiet_since = now
                previous = current

            if changed and now - quiet_since >= quiet_seconds:
                elapsed = now - started
                profile_name = reference.get("profile") or capture.default_name(cfg)
                profile = capture.resolve(profile_name, cfg)
                capture.require_budget(scratch, profile)
                digest = capture.digest(scratch)
                frame = frames.new(
                    source=source,
                    logical_rect=rect,
                    image_size=probe_size,
                    scratch=scratch,
                    native_size=reference.get("native_size"),
                    digest_native=digest,
                    monitor=reference.get("monitor"),
                    profile=profile_name,
                    capture_scale=scale,
                    estimator=capture.estimator(profile),
                    visual_tokens=capture.visual_tokens(
                        probe_width, probe_height, profile
                    ),
                    cursor_logical=observe.cursor(),
                    window=_current_window(
                        reference.get("window"), captured_snap["windows"]
                    ),
                    target_window=_current_window(
                        reference.get("target_window"), captured_snap["windows"]
                    ),
                    wait={
                        "after": reference["id"],
                        "watch": watch,
                        "elapsed_seconds": elapsed,
                        "probes": probes,
                        "poll_ms": args.poll_ms,
                        "quiet_ms": args.quiet_ms,
                        "timeout_seconds": args.timeout,
                    },
                )
                frame["changed"] = True

                def render(f: Any) -> None:
                    waited = f["wait"]
                    depicted = f["source"]
                    print(f"{f['id']}  {f['path']}")
                    print(
                        f"  waited {waited['elapsed_seconds']:.3f}s across "
                        f"{waited['probes']} probes; "
                        "watched pixels changed and settled"
                    )
                    print(
                        f"  depicts {depicted['type']} {depicted['name']} "
                        f"logical {fmt_rect(f['logical_rect'])}"
                    )

                _emit(frame, args.json, render)
                return exits.OK

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            continue
        with metrics.span("intentional_wait"):
            time.sleep(min(poll_seconds, remaining))


def _hover_frame_from_probe(
    reference: dict[str, Any],
    cfg: dict[str, Any],
    scratch: Path,
    captured_snap: dict[str, Any],
    image_size: dict[str, int],
) -> dict[str, Any]:
    expected_size = reference["image_size"]
    if image_size != expected_size:
        raise FrameError(
            f"hover capture is {image_size['w']}x{image_size['h']}, but reference "
            f"{reference['id']} is {expected_size['w']}x{expected_size['h']}; "
            "capture conditions changed"
        )
    profile_name = reference.get("profile") or capture.default_name(cfg)
    profile = capture.resolve(profile_name, cfg)
    capture.require_budget(scratch, profile)
    return frames.new(
        source=reference["source"],
        logical_rect=reference["logical_rect"],
        image_size=image_size,
        scratch=scratch,
        native_size=reference.get("native_size"),
        digest_native=capture.digest(scratch),
        monitor=reference.get("monitor"),
        profile=profile_name,
        capture_scale=_frame_capture_scale(reference),
        estimator=capture.estimator(profile),
        visual_tokens=capture.visual_tokens(image_size["w"], image_size["h"], profile),
        cursor_logical=observe.cursor(),
        window=_current_window(reference.get("window"), captured_snap["windows"]),
        target_window=_current_window(
            reference.get("target_window"), captured_snap["windows"]
        ),
    )


def _check_hover_interruption(
    cfg: dict[str, Any],
    expected_cursor: dict[str, float],
    expected_focus: dict[str, Any] | None,
) -> None:
    """Recheck user-owned interlocks before accepting a hover observation."""
    safety.check_abort()
    safety.confirm_pointer(
        cfg,
        (expected_cursor["x"], expected_cursor["y"]),
        observe.cursor(),
    )
    if expected_focus is not None:
        safety.confirm_focus(expected_focus, observe.focused())


def _hover_observe(
    reference: dict[str, Any],
    cfg: dict[str, Any],
    watch: dict[str, int],
    *,
    timeout: float,
    poll_ms: float,
    quiet_ms: float,
    expected_cursor: dict[str, float],
    expected_focus: dict[str, Any] | None,
) -> dict[str, Any]:
    try:
        width, height, baseline_rgba = png.decode(Path(reference["path"]).read_bytes())
    except (OSError, ValueError, KeyError) as exc:
        raise FrameError(f"cannot read frame {reference['id']}: {exc}") from exc
    expected_size = reference["image_size"]
    if {"w": width, "h": height} != expected_size:
        raise FrameError(f"frame {reference['id']} PNG does not match its descriptor")
    baseline = _watched_pixels(baseline_rgba, width, watch)
    previous = baseline
    changed = False
    quiet_since = 0.0
    probes = 0
    started = time.monotonic()
    deadline = started + timeout
    poll_seconds = poll_ms / 1000
    quiet_seconds = quiet_ms / 1000
    scale = _frame_capture_scale(reference)

    while True:
        _check_hover_interruption(cfg, expected_cursor, expected_focus)
        if probes and time.monotonic() >= deadline:
            return {
                "status": "timed-out",
                "watch": watch,
                "probes": probes,
                "elapsed_seconds": time.monotonic() - started,
                "frame": None,
            }
        _check_recapture(reference, observe.snapshot())
        with frames.capture_scratch() as scratch:
            _grab_shot(reference["source"], reference["logical_rect"], scratch, scale)
            captured_snap = observe.snapshot()
            _check_recapture(reference, captured_snap)
            probes += 1
            try:
                probe_width, probe_height, probe_rgba = png.decode(scratch.read_bytes())
            except (OSError, ValueError) as exc:
                raise FrameError(f"cannot compare hover probe {probes}: {exc}") from exc
            if {"w": probe_width, "h": probe_height} != expected_size:
                raise FrameError("hover capture conditions changed")
            now = time.monotonic()
            current = _watched_pixels(probe_rgba, probe_width, watch)
            if not changed:
                if current != baseline:
                    changed = True
                    quiet_since = now
            elif current != previous:
                quiet_since = now
            previous = current
            if changed and now - quiet_since >= quiet_seconds:
                _check_hover_interruption(cfg, expected_cursor, expected_focus)
                frame = _hover_frame_from_probe(
                    reference,
                    cfg,
                    scratch,
                    captured_snap,
                    {"w": probe_width, "h": probe_height},
                )
                frame["changed"] = True
                frame["hover_watch"] = {
                    "watch": watch,
                    "probes": probes,
                    "elapsed_seconds": now - started,
                    "poll_ms": poll_ms,
                    "quiet_ms": quiet_ms,
                    "timeout_seconds": timeout,
                }
                return {
                    "status": "changed",
                    "watch": watch,
                    "probes": probes,
                    "elapsed_seconds": now - started,
                    "frame": frame,
                }
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_seconds, remaining))


def _validate_hover_wait(args: argparse.Namespace) -> None:
    if not all(math.isfinite(value) for value in (args.poll_ms, args.quiet_ms, args.timeout)):
        raise ValueError("hover wait timing values must be finite")
    if args.poll_ms <= 0:
        raise ValueError("--poll-ms must be positive")
    if args.quiet_ms < 0:
        raise ValueError("--quiet-ms cannot be negative")
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")
    if args.timeout > MAX_HOVER_WAIT:
        raise ValueError(f"--timeout must be at most {MAX_HOVER_WAIT:g}s")


def _render_hover(result: dict[str, Any]) -> None:
    mark = "would " if result["dry_run"] else ""
    condition = result["condition"]["status"]
    frame = result.get("observation")
    where = f"frame {frame['id']}" if frame else "no final frame"
    print(
        f"{mark}hovered at {result['at']['x']:g},{result['at']['y']:g}; "
        f"dwell {result['dwell_elapsed']:.3f}s; condition {condition}; {where}"
    )


def cmd_hover(args: argparse.Namespace) -> int:
    _validate_hover_wait(args)
    cfg = config.load()
    reference = frames.load(args.frame)
    watch = _parse_watch(args.watch, reference["image_size"]) if args.watch else None
    expected_focus = observe.focused()
    result = actions.hover(
        cfg,
        args.frame,
        _parse_at(args.at),
        duration=args.duration,
        hz=args.hz,
        move_timeout=args.move_timeout,
        dwell=args.dwell,
        dry_run=args.dry_run,
    )
    if result["dry_run"]:
        _emit(result, args.json, _render_hover)
        return exits.OK

    if watch is None:
        with frames.capture_scratch() as scratch:
            _grab_shot(
                reference["source"],
                reference["logical_rect"],
                scratch,
                _frame_capture_scale(reference),
            )
            captured_snap = observe.snapshot()
            _check_recapture(reference, captured_snap)
            frame = _hover_frame_from_probe(
                reference,
                cfg,
                scratch,
                captured_snap,
                capture.size_of(scratch),
            )
        _check_hover_interruption(cfg, result["entry"]["cursor"], expected_focus)
        result["observation"] = frame
        result["status"] = "observed"
    else:
        condition = _hover_observe(
            reference,
            cfg,
            watch,
            timeout=args.timeout,
            poll_ms=args.poll_ms,
            quiet_ms=args.quiet_ms,
            expected_cursor=result["entry"]["cursor"],
            expected_focus=expected_focus,
        )
        result["condition"] = {
            "kind": "pixels",
            "status": condition["status"],
            "watch": watch,
            "probes": condition["probes"],
            "elapsed_seconds": condition["elapsed_seconds"],
        }
        result["observation"] = condition["frame"]
        result["status"] = "observed" if condition["frame"] else "timed-out"
    _check_hover_interruption(cfg, result["entry"]["cursor"], expected_focus)
    result["reveal"] = {
        "status": "unknown",
        "meaning": "a frame or pixel change does not establish application state",
    }
    safety.record(cfg, result)
    _emit(result, args.json, _render_hover)
    if result["status"] == "timed-out":
        print(
            f"cu: hover timed out after {result['condition']['elapsed_seconds']:.3f}s; "
            "pixels did not change and settle",
            file=sys.stderr,
        )
        return exits.TIMED_OUT
    return exits.OK


def cmd_shot(args: argparse.Namespace) -> int:
    _validate_after_args(args)
    if args.window and (args.output or args.region):
        raise ValueError("--window cannot combine with --output or --region")
    target_selector = getattr(args, "target_window", None)
    if target_selector and not (args.output or args.region):
        raise ValueError("--target-window requires --output or --region")
    if target_selector and (args.window or args.landmark):
        raise ValueError("--target-window cannot combine with --window or --landmark")

    cfg = config.load()
    snap = observe.snapshot()
    if args.after:
        frames.collect()
        return _shot_after(args, snap, cfg)
    monitors = snap["monitors"]
    source: dict[str, Any]
    window = None
    intended_window = None

    if args.landmark and (args.output or args.region):
        raise ValueError(
            "--landmark aims within a window; it cannot combine with --output or --region"
        )

    if args.output and not args.landmark:
        monitor = next((m for m in monitors if m["name"] == args.output), None)
        if monitor is None:
            print(f"cu: no such output: {args.output}", file=sys.stderr)
            return exits.TARGET_NOT_FOUND
        if args.region:
            rect = _parse_region(args.region)
            bounds = monitor["logical_rect"]
            rect["x"] += bounds["x"]
            rect["y"] += bounds["y"]
            rect = capture.clamp(rect, bounds)
            source = {"type": "region", "name": monitor["name"]}
        else:
            rect = monitor["logical_rect"]
            source = {"type": "output", "name": monitor["name"]}
    elif args.region and not args.landmark:
        rect = _parse_region(args.region)
        monitor = capture.sole_monitor(rect, monitors)
        rect = capture.clamp(rect, monitor["logical_rect"])
        source = {"type": "region", "name": monitor["name"]}
    elif args.landmark:
        # A landmark aims the capture and nothing else. It resolves against the
        # window's geometry *now* and hedges by however much that geometry has
        # moved on, so what comes back is a region worth reading rather than a
        # confident guess — and the model still reads the pixels before
        # anything acts on them.
        app, _, name = args.landmark.partition("/")
        if not name:
            raise ValueError("--landmark takes 'app/name', e.g. 'zen/urlbar'")
        # With several windows of one application open, the one being worked
        # in is the one that holds focus — so an unqualified landmark means
        # "this window", not "some window of this class", which would be
        # ambiguous exactly when it matters.
        focused = snap["focused"]
        if args.window:
            window = _resolve_window(args.window, snap)
        elif focused and focused["class"].lower() == app.lower():
            window = _resolve_window(focused["address"], snap)
        else:
            window = _resolve_window(app, snap)
        mark = knowledge.get_landmark(app, name, args.context)
        aim = knowledge.resolve(mark, window)
        rect = aim["rect"]
        monitor = capture.sole_monitor(rect, monitors)
        rect = capture.clamp(rect, monitor["logical_rect"])
        source = {
            "type": "landmark",
            "name": f"{app}/{name}",
            "confidence": aim["confidence"],
            "why": aim["reason"],
        }
        if aim["confidence"] != "exact":
            # On stderr because the frame is still the answer: a hedged or
            # abandoned landmark changes how much of the image to search, not
            # whether the capture happened.
            print(
                f"cu: landmark {app}/{name} {aim['confidence']} — {aim['reason']}",
                file=sys.stderr,
            )
    else:
        window = _resolve_window(args.window or "active", snap)
        rect = window["logical_rect"]
        monitor = capture.sole_monitor(rect, monitors)
        rect = capture.clamp(rect, monitor["logical_rect"])
        source = {"type": "window", "name": window["class"]}

    if target_selector:
        intended_window = _resolve_window(target_selector, snap)
        if not intended_window.get("visible", True) or intended_window.get("hidden", False):
            raise capture.CaptureVisibilityError(
                "the intended target window is not on a visible workspace"
            )
        if not capture.intersects(intended_window["logical_rect"], rect):
            raise CaptureAimError(
                f"target window {intended_window['class']} "
                f"({intended_window['address']}) does not intersect the capture region"
            )

    frames.collect()
    name = args.profile or capture.default_name(cfg)
    profile = capture.resolve(name, cfg)
    scale = capture.scale_for(rect, monitor["scale"], profile["max_long_edge"])
    native = {
        "w": round(rect["w"] * monitor["scale"]),
        "h": round(rect["h"] * monitor["scale"]),
    }

    if window is not None:
        capture.require_visible(window, rect, snap)
    with frames.capture_scratch() as scratch:
        _grab_shot(source, rect, scratch, scale)
        if window is not None:
            _confirm_captured_window(window, monitors)
            capture.require_visible(window, rect, observe.snapshot())
        if intended_window is not None:
            _confirm_captured_window(intended_window, monitors)

        capture.require_budget(scratch, profile)
        digest = capture.digest(scratch)

        if args.if_changed:
            previous = _last_frame_like(source, rect)
            if (
                previous
                and previous.get("digest_native") == digest
                and (previous.get("window") or {}).get("address")
                == (window or {}).get("address")
                and previous.get("profile") == name
                and previous.get("capture_scale") == scale
                and (previous.get("target_window") or {}).get("address")
                == (intended_window or {}).get("address")
            ):
                # The pixels were just re-read and match, which is the evidence
                # staleness is a proxy for. Keeping the original timestamp would
                # let a frame proven current a moment ago be refused as too old.
                previous["captured_at"] = time.time()
                previous["cursor_logical"] = observe.cursor()
                # Identical pixels are proof the picture is current, so the
                # identity it depicts is re-read with it. Keeping the old one
                # would refuse a frame just re-proven against the screen.
                previous["window"] = _current_window(
                    previous.get("window"), observe.windows(snap["monitors"])
                )
                previous["target_window"] = _current_window(
                    previous.get("target_window"), observe.windows(snap["monitors"])
                )
                frames.save(previous)
                payload = {**previous, "changed": False}
                _emit(payload, args.json, lambda p: print(f"unchanged; reusing {p['id']}"))
                return exits.OK

        image_size = capture.size_of(scratch)
        frame = frames.new(
            source=source,
            logical_rect=rect,
            image_size=image_size,
            scratch=scratch,
            native_size=native,
            digest_native=digest,
            monitor={
                "name": monitor["name"],
                "scale": monitor["scale"],
                "logical_rect": dict(monitor["logical_rect"]),
            },
            profile=name,
            capture_scale=scale,
            estimator=capture.estimator(profile),
            visual_tokens=capture.visual_tokens(
                image_size["w"], image_size["h"], profile
            ),
            cursor_logical=snap["cursor_logical"],
            window=_window_descriptor(window),
            target_window=_window_descriptor(intended_window),
        )
    frame["changed"] = True

    def render(f: Any) -> None:
        i = f["image_size"]
        print(f"{f['id']}  {f['path']}")
        hedge = f" ({source['confidence']})" if "confidence" in source else ""
        print(
            f"  depicts {source['type']} {source['name']}{hedge} "
            f"logical {fmt_rect(f['logical_rect'])}"
        )
        print(
            f"  image {i['w']}x{i['h']} (native {native['w']}x{native['h']}, "
            f"profile {name}, ~{f['visual_tokens']} visual tokens)"
        )

    _emit(frame, args.json, render)
    return exits.OK


def _last_frame_like(
    source: dict[str, Any], rect: dict[str, float]
) -> dict[str, Any] | None:
    newest, newest_at = None, 0.0
    for meta in frames.store_dir().glob("f*.json"):
        try:
            candidate = json.loads(meta.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if (
            candidate.get("source") == source
            and candidate.get("logical_rect") == rect
            and candidate["captured_at"] > newest_at
        ):
            newest, newest_at = candidate, candidate["captured_at"]
    return newest


def _parse_at(text: str) -> tuple[float, float]:
    parts = [p for p in text.replace(",", " ").split() if p]
    if len(parts) != 2:
        raise ValueError(f"--at takes 'x,y' in image pixels, got {text!r}")
    return (float(parts[0]), float(parts[1]))


def _parse_pixel_at(text: str) -> tuple[int, int]:
    parts = [part for part in text.replace(",", " ").split() if part]
    if len(parts) != 2:
        raise ValueError(f"--at takes 'x,y' in image pixels, got {text!r}")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f"--at takes integer image pixels, got {text!r}") from exc


def _frame_pixels(frame: dict[str, Any]) -> tuple[int, int, bytes]:
    try:
        data = Path(frame["path"]).read_bytes()
        width, height, pixels = png.decode(data)
        recorded = frame["image_size"]
    except (KeyError, OSError, ValueError) as exc:
        raise FrameError(f"cannot read frame {frame['id']}: {exc}") from exc
    if recorded != {"w": width, "h": height}:
        if isinstance(recorded, dict):
            recorded_size = f"{recorded.get('w')}x{recorded.get('h')}"
        else:
            recorded_size = repr(recorded)
        raise FrameError(
            f"frame {frame['id']} records {recorded_size} pixels, but its PNG is "
            f"{width}x{height}"
        )
    return width, height, pixels


def cmd_pixel(args: argparse.Namespace) -> int:
    frame = frames.load(args.frame)
    width, height, pixels = _frame_pixels(frame)
    x, y = _parse_pixel_at(args.at)
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError(
            f"({x},{y}) is outside frame {frame['id']} ({width}x{height})"
        )
    if args.radius < 0:
        raise ValueError("--radius must be at least 0")
    if x - args.radius < 0 or y - args.radius < 0:
        raise ValueError(f"--radius {args.radius} extends outside frame {frame['id']}")
    if x + args.radius >= width or y + args.radius >= height:
        raise ValueError(f"--radius {args.radius} extends outside frame {frame['id']}")

    samples = []
    for sample_y in range(y - args.radius, y + args.radius + 1):
        for sample_x in range(x - args.radius, x + args.radius + 1):
            offset = (sample_y * width + sample_x) * 4
            samples.append(pixels[offset : offset + 4])
    middle = len(samples) // 2
    colour = tuple(
        sorted(sample[channel] for sample in samples)[middle] for channel in range(4)
    )
    hexadecimal = "#" + "".join(f"{component:02X}" for component in colour)
    payload = {
        "frame": frame["id"],
        "at": {"x": x, "y": y},
        "radius": args.radius,
        "r": colour[0],
        "g": colour[1],
        "b": colour[2],
        "a": colour[3],
        "hex": hexadecimal,
    }
    _emit(
        payload,
        args.json,
        lambda value: print(
            f"rgba {value['r']},{value['g']},{value['b']},{value['a']}  {value['hex']}"
        ),
    )
    return exits.OK


def _magnified_pixels(width: int, height: int, pixels: bytes, scale: int) -> bytes:
    enlarged = bytearray()
    stride = width * 4
    for row_start in range(0, height * stride, stride):
        row = pixels[row_start : row_start + stride]
        enlarged_row = b"".join(
            row[index : index + 4] * scale for index in range(0, stride, 4)
        )
        enlarged.extend(enlarged_row * scale)
    return bytes(enlarged)


def cmd_magnify(args: argparse.Namespace) -> int:
    if args.scale < 2:
        raise ValueError("--scale must be an integer at least 2")
    source = frames.load(args.frame)
    width, height, pixels = _frame_pixels(source)
    long_edge = max(width, height) * args.scale
    if long_edge > MAX_MAGNIFIED_LONG_EDGE:
        raise ValueError(
            f"--scale {args.scale} would make a {long_edge}px long edge; "
            f"the limit is {MAX_MAGNIFIED_LONG_EDGE}px"
        )

    try:
        source_kind = source["source"]
        logical_rect = source["logical_rect"]
        captured_at = source["captured_at"]
    except KeyError as exc:
        raise FrameError(f"frame {source['id']} has no {exc.args[0]}") from exc
    cfg = config.load()
    profile_name = source.get("profile") or capture.default_name(cfg)
    profile = capture.resolve(profile_name, cfg)
    image_size = {"w": width * args.scale, "h": height * args.scale}
    enlarged = _magnified_pixels(width, height, pixels, args.scale)
    with frames.capture_scratch() as scratch:
        scratch.write_bytes(png.encode(image_size["w"], image_size["h"], enlarged))
        capture.require_budget(scratch, profile)
        frame = frames.new(
            source=source_kind,
            logical_rect=logical_rect,
            image_size=image_size,
            scratch=scratch,
            captured_at=captured_at,
            derived_from=source["id"],
            window=source.get("window"),
            target_window=source.get("target_window"),
            monitor=source.get("monitor"),
            profile=profile_name,
            estimator=capture.estimator(profile),
            visual_tokens=capture.visual_tokens(
                image_size["w"], image_size["h"], profile
            ),
        )

    def render(value: Any) -> None:
        size = value["image_size"]
        print(f"{value['id']}  {value['path']}")
        print(
            f"  magnified from {value['derived_from']} by {args.scale}x; "
            f"image {size['w']}x{size['h']} "
            f"(~{value['visual_tokens']} visual tokens)"
        )

    _emit(frame, args.json, render)
    return exits.OK


def _render_action(result: dict[str, Any]) -> None:
    mark = "would " if result["dry_run"] else ""
    window = result.get("window")
    where = f"{window['class']}" if window else "no window"
    layer_surface = result.get("layer_surface")
    if result["action"] == "click" and window is None and layer_surface is not None:
        where += f"; layer '{layer_surface['namespace']}' covers this point"
    if result["action"] in {"move", "warp", "click"}:
        point = result["logical"]
        detail = f"at image {result['at']['x']:g},{result['at']['y']:g} "
        detail += f"→ logical {point['x']:.0f},{point['y']:.0f}"
        if result["action"] == "move":
            detail = f"native {detail} over {result['duration_requested']:.3f}s"
            detail += (
                f"; {result['status']} at "
                f"{result['cursor']['x']:.0f},{result['cursor']['y']:.0f}"
            )
        elif result["action"] == "click":
            detail = f"{result['button']}x{result['count']} {detail}"
    elif result["action"] == "scroll":
        detail = f"dy {result['dy']} dx {result['dx']}"
    elif result["action"] == "type":
        detail = ""
        pointer = result.get("pointer")
        if pointer:
            detail = f"at image {pointer['at']['x']:g},{pointer['at']['y']:g} → "
        detail += f"{result['length']} chars via {result['method']}"
        if result["chord"]:
            detail += f" ({result['chord']})"
        detail += f" [{result['backend']}]"
        checked = result.get("verified")
        if checked:
            detail += f" — {checked['status']}: {checked['detail']}"
    else:
        detail = f"{result['semantic'] or 'chord'} → {result['chord']}"
        if result["repeat"] > 1:
            detail += f" x{result['repeat']}"
    print(f"{mark}{result['action']} {detail}  [{where}]")


def _read_log(cfg: dict[str, Any], count: int) -> list[dict[str, Any]]:
    path = config.log_path(cfg)
    if not path.is_file():
        raise LookupError(f"no action log at {path}; nothing to promote")
    entries = []
    for line in path.read_text().splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries[-count:]


def cmd_recipe(args: argparse.Namespace) -> int:
    cfg = config.load()

    if args.subcommand == "list":
        rows = recipes.listing()

        def render(data: Any) -> None:
            if not data["recipes"]:
                print("no recipes stored")
            for r in data["recipes"]:
                mark = "reviewed" if r["reviewed"] else "UNREVIEWED"
                print(f"{r['name']:<24} r{r['revision']:<3} {r['steps']:>2} steps  "
                      f"{mark:<11} {r['site']} — {r['task']}")

        _emit({"recipes": rows}, args.json, render)
        return exits.OK

    if args.subcommand == "save":
        # Resolved before anything is written: the name is stored verbatim and
        # every replay resolves it again, so a profile that does not resolve
        # turns into a recipe that refuses at the moment it is relied on.
        profile = args.profile or capture.default_name(cfg)
        capture.resolve(profile, cfg)
        entries = _read_log(cfg, args.last)
        recipe = recipes.from_log(
            entries,
            args.name,
            args.site or "",
            args.task or "",
            profile,
        )
        path = recipes.save(recipe)
        _emit(
            recipe,
            args.json,
            lambda r: print(
                f"saved {r['name']} r{r['revision']} — {len(r['steps'])} steps → {path}\n"
                f"It is UNREVIEWED and will not replay until you read it "
                f"(`cu recipe show {r['name']}`) and approve it."
            ),
        )
        return exits.OK

    recipe = recipes.load(args.name)

    if args.subcommand == "show":
        def render(r: Any) -> None:
            state = "reviewed" if recipes.is_reviewed(r) else "UNREVIEWED"
            print(f"{r['name']}  r{r['revision']}  [{state}]")
            print(f"  site {r['site']!r}  task {r['task']!r}")
            for index, step in enumerate(r["steps"], start=1):
                print(f"  {index:>2}. {_describe_step(step)}")
            if r.get("verify"):
                print(f"  verify: {r['verify']}")
            else:
                print("  verify: none — a replay cannot report whether it worked")
            for event in r.get("history", []):
                print(f"  history: r{event['revision']} {event['outcome']} {event.get('note','')}")

        _emit(recipe, args.json, render)
        return exits.OK

    if args.subcommand == "review":
        blocked = recipes.unreviewed_reason(recipe)
        if not args.approve:
            print(f"{recipe['name']} r{recipe['revision']}: "
                  f"{blocked or 'reviewed and replayable'}")
            for index, step in enumerate(recipe["steps"], start=1):
                print(f"  {index:>2}. {_describe_step(step)}")
            if blocked:
                print("\nRe-run with --approve once you have read every step above.")
            return exits.OK
        recipes.approve(args.name)
        print(f"approved {recipe['name']} r{recipe['revision']}")
        return exits.OK

    if args.subcommand == "replay":
        params = {}
        for item in args.param or []:
            key, _, value = item.partition("=")
            if not _:
                print(f"cu: --param takes name=value, got {item!r}", file=sys.stderr)
                return exits.USAGE
            params[key] = value
        result = recipes.replay(args.name, cfg, params, dry_run=args.dry_run)
        if not args.dry_run:
            outcome = {True: "verified", False: "failed", None: "unverified"}[result["verified"]]
            recipes.record_outcome(args.name, outcome)
        _emit(
            result,
            args.json,
            lambda r: print(
                f"{'would replay' if r['dry_run'] else 'replayed'} {r['recipe']} "
                f"r{r['revision']}: {r['steps']} steps, "
                f"verification {_verdict(r['verified'])}"
            ),
        )
        return exits.VERIFY_FAILED if result["verified"] is False else exits.OK

    return exits.USAGE


def _verdict(verified: bool | None) -> str:
    """Three outcomes, because "not stated" is not the same as "failed"."""
    return {True: "passed", False: "failed", None: "not stated"}[verified]


def _describe_step(step: dict[str, Any]) -> str:
    action = step["action"]
    if action in {"click", "warp", "move"}:
        rel = step["relative"]
        pressed = f"{step.get('button', 'left')} x{step.get('count', 1)}"
        if action == "move":
            how = f"native move {step.get('duration', '?')}s"
        elif action == "warp":
            how = "warp"
        else:
            how = pressed
        return f"{how} at {rel['x']:.3f},{rel['y']:.3f} of {step['window_class']}"
    if action == "scroll":
        return f"scroll dy {step.get('dy',0)} dx {step.get('dx',0)}"
    if action == "type":
        aimed = ""
        if step.get("relative"):
            rel = step["relative"]
            aimed = f"into {rel['x']:.3f},{rel['y']:.3f} of {step['window_class']}, "
        return (
            f"type parameter {step.get('param')!r} {aimed}"
            f"({step.get('length')} chars via {step.get('method')})"
        )
    if action == "key":
        return f"key {step.get('semantic') or 'chord'} → {step.get('chord')}"
    if action == "browser":
        return f"browser action {step.get('semantic')!r} (re-resolved at replay)"
    if action == "focus":
        return f"focus a {step['window_class']} window"
    if action == "wait":
        filters = " ".join(
            f"{k} {v!r}" for k, v in (("class", step.get("class")), ("title", step.get("title")))
            if v
        )
        return f"wait for {step['condition']} {filters} (up to {step.get('timeout', 30):g}s)"
    return action


def cmd_sandbox(args: argparse.Namespace) -> int:
    """Headless outputs, created and removed explicitly.

    `cu` works on the visible desktop by default and this is the only way to
    ask for anywhere else, which keeps "nobody can see this" a deliberate act
    rather than something an agent drifts into.
    """
    if args.subcommand == "create":
        monitor = sandbox.create()
        _emit(
            monitor,
            args.json,
            lambda m: print(
                f"{m['name']}  scale {m['scale']}  logical "
                f"{fmt_rect(m['logical_rect'])}  workspace {m['active_workspace']}\n"
                f"  Nothing on this output is visible to the user. Remove it when done."
            ),
        )
        return exits.OK

    if args.subcommand == "list":
        found = sandbox.existing()
        _emit(
            {"headless": found},
            args.json,
            lambda d: print(
                "\n".join(
                    f"{m['name']}  scale {m['scale']}  logical {fmt_rect(m['logical_rect'])}"
                    for m in d["headless"]
                )
                or "no headless outputs"
            ),
        )
        return exits.OK

    # Reported after the fact, never before it: `sandbox.remove` refuses
    # anything that is not a headless output, and a list printed ahead of that
    # refusal would claim a removal that never happened.
    if args.all:
        if args.name:
            raise ValueError(f"--all removes every headless output; drop {args.name!r}")
        removed = sandbox.remove_all()
    elif args.name:
        sandbox.remove(args.name)
        removed = [args.name]
    else:
        print("cu: name an output to remove, or pass --all", file=sys.stderr)
        return exits.USAGE
    _emit(
        {"removed": removed},
        args.json,
        lambda d: print(f"removed {', '.join(d['removed']) or 'nothing'}"),
    )
    return exits.OK


def cmd_know(args: argparse.Namespace) -> int:
    if args.subcommand == "host":
        if args.text:
            entry = knowledge.add_host_note(args.text)
            _emit(entry, args.json, lambda e: print(f"noted about this host: {e['text']}"))
            return exits.OK
        if args.forget is not None:
            # 0 is the "all" sentinel: an index the listing never prints, so it
            # cannot be reached by miscounting a line.
            outcome = knowledge.forget_host(args.forget or None)
            _emit({"outcome": outcome}, args.json, lambda d: print(d["outcome"]))
            return exits.OK
        record = knowledge.load_host()

        def render(r: Any) -> None:
            if not r["notes"]:
                # What cu measures about the machine is `cu doctor`'s answer,
                # and saying so here stops an empty store reading as a claim
                # that nothing is known about the host at all.
                print("nothing learned about this host yet")
                print("what cu can probe for itself is in `cu doctor`")
                return
            for index, note in enumerate(r["notes"], start=1):
                print(f"{index:>3}. {note['text']}")

        _emit(record, args.json, render)
        return exits.OK

    if args.subcommand == "list":
        rows = knowledge.listing()
        _emit(
            {"applications": rows},
            args.json,
            lambda d: print(
                "\n".join(
                    f"{r['app']:<24} {r['landmarks']:>3} landmarks  {r['notes']:>3} notes"
                    for r in d["applications"]
                )
                or "nothing learned yet"
            ),
        )
        return exits.OK

    if args.subcommand == "show":
        record = knowledge.load(args.app)

        def render(r: Any) -> None:
            print(f"{r['app']}")
            if not r.get("landmarks") and not r.get("notes"):
                # Otherwise a bare name is the whole answer, and "never seen"
                # reads exactly like "seen, and nothing was worth keeping".
                print("  nothing learned about it yet")
            for key, mark in sorted(r.get("landmarks", {}).items()):
                rect = mark["rect"]
                seen = f"{mark.get('confirmed', 0)}✓/{mark.get('missed', 0)}✗"
                learned = mark.get("learned_rect")
                at = (
                    f"@{round(learned['w'])}x{round(learned['h'])}"
                    if learned
                    else "@unknown"
                )
                print(
                    f"  {key:<28} {rect['x']:.3f},{rect['y']:.3f} "
                    f"{rect['w']:.3f}x{rect['h']:.3f} {at:<12} {seen:<8} "
                    f"{mark.get('note', '')}"
                )
            for note in r.get("notes", []):
                scope = f"[{note['context']}] " if note.get("context") else ""
                print(f"  note: {scope}{note['text']}")

        _emit(record, args.json, render)
        return exits.OK

    if args.subcommand == "set":
        rect = _parse_landmark_rect(args.rect)
        mark = knowledge.set_landmark(
            args.app,
            args.name,
            rect,
            context=args.context,
            note=args.note or "",
            learned_rect=_window_geometry(args.window or args.app),
            anchor_x=args.anchor_x,
            anchor_y=args.anchor_y,
        )
        _emit(
            mark,
            args.json,
            lambda m: print(f"learned {args.app}/{args.name}: {m['rect']}"),
        )
        return exits.OK

    if args.subcommand == "note":
        entry = knowledge.add_note(args.app, args.text, context=args.context)
        _emit(entry, args.json, lambda e: print(f"noted for {args.app}: {e['text']}"))
        return exits.OK

    if args.subcommand in {"confirm", "miss"}:
        mark = knowledge.score(
            args.app, args.name, args.subcommand == "confirm", context=args.context
        )
        _emit(
            mark,
            args.json,
            lambda m: print(
                f"{args.app}/{args.name}: {m.get('confirmed', 0)} confirmed, "
                f"{m.get('missed', 0)} missed"
            ),
        )
        return exits.OK

    if args.subcommand == "forget":
        message = knowledge.forget(args.app, args.name, context=args.context)
        _emit({"message": message}, args.json, lambda d: print(d["message"]))
        return exits.OK

    return exits.USAGE


def _window_geometry(selector: str) -> dict[str, float] | None:
    """The window a fraction was measured against, if it can still be found.

    A landmark without it is hedged forever, which is a worse hint but a hint —
    so a window that has since closed costs precision rather than the record.
    """
    try:
        return _resolve_window(selector, observe.snapshot())["logical_rect"]
    except (LookupError, OSError, RuntimeError):
        print(
            f"cu: no window matches {selector!r}; storing the landmark without "
            "the geometry it was measured at, so it can only ever be widened",
            file=sys.stderr,
        )
        return None


def _parse_landmark_rect(text: str) -> dict[str, float]:
    """Accept a point or a box, both as fractions of the window."""
    parts = [float(p) for p in text.replace(",", " ").split() if p]
    if len(parts) == 2:
        # A point becomes a small box, because a capture needs an area and a
        # landmark is a place to look rather than a pixel to hit.
        x, y = parts
        return {
            "x": min(max(x - 0.06, 0.0), 0.88),
            "y": min(max(y - 0.03, 0.0), 0.94),
            "w": 0.12,
            "h": 0.06,
        }
    if len(parts) == 4:
        x, y, w, h = parts
        return {"x": x, "y": y, "w": w, "h": h}
    raise ValueError(f"--rect takes 'x,y' or 'x,y,w,h' as fractions, got {text!r}")


def cmd_browser(args: argparse.Namespace) -> int:
    cfg = config.load()
    browsers = browser.discover()

    if args.action == "products":
        products = browser.product_candidates()

        def render_products(data: Any) -> None:
            for product in data["products"]:
                state = "available" if product["available"] else "unavailable"
                print(
                    f"{product['name']}: {state}; binary {product['resolved'] or 'missing'}; "
                    f"profiles {len(product['profiles'])}"
                )

        _emit({"products": products}, args.json, render_products)
        return exits.OK

    if args.action == "list":
        def render(data: Any) -> None:
            for b in data["browsers"]:
                installed = "" if b["resolved"] else "  (not installed)"
                print(
                    f"{b['name']:<20} {b['family']:<9} "
                    f"class {b['window_class'] or '?'!s:<16} "
                    f"{len(b['actions'])} actions{installed}"
                )
        payload = {
            "browsers": [{**b, "actions": sorted(browser.keymap_for(b))} for b in browsers]
        }
        _emit(payload, args.json, render)
        return exits.OK

    # Which browser is being driven: the one named, or the one holding focus.
    snap = observe.snapshot()
    focused = snap["focused"]
    if args.browser:
        target = browser.find(args.browser, browsers)
        focused_browser = browser.for_window_class(focused["class"], browsers) if focused else None
        window = focused if focused_browser and focused_browser["id"] == target["id"] else None
    else:
        if focused is None:
            print("cu: no window is focused; name a browser with --browser", file=sys.stderr)
            return exits.TARGET_NOT_FOUND
        target = browser.for_window_class(focused["class"], browsers)
        window = focused
        if target is None:
            print(
                f"cu: the focused window ({focused['class']}) is not a known browser",
                file=sys.stderr,
            )
            return exits.TARGET_NOT_FOUND

    if args.action == "actions":
        keymap = browser.keymap_for(target)
        payload = {
            "browser": target["name"],
            "family": target["family"],
            "window_class": target["window_class"],
            "actions": keymap,
            # An installed, focused browser that resolves nothing is a
            # configuration gap, and saying which gap is the difference
            # between a caller retrying and a caller fixing it.
            "diagnostic": None if keymap else browser.unmapped_reason(target),
        }

        def render(data: Any) -> None:
            print(f"{data['browser']} ({data['family']})")
            if data["diagnostic"]:
                print(f"  no actions resolvable — {data['diagnostic']}")
            for action, chord in sorted(data["actions"].items()):
                print(f"  {action:<18} {chord}")

        _emit(payload, args.json, render)
        return exits.OK

    chord, source = browser.resolve(target, args.action, cfg)
    if chord is None:
        keymap = browser.keymap_for(target)
        known = sorted(keymap) or ["none"]
        print(
            f"cu: {target['name']} has no binding for {args.action!r}. "
            f"Known: {', '.join(known)}.",
            file=sys.stderr,
        )
        if not keymap:
            print(f"cu: {browser.unmapped_reason(target)}", file=sys.stderr)
        print(f"cu: {exits.RESPONSE[exits.NOT_MAPPED]}", file=sys.stderr)
        return exits.NOT_MAPPED

    if window is None:
        # A dry run sends nothing, so it can answer about the browser rather
        # than about which window happens to hold focus — which is what makes
        # it usable for checking a resolution before arranging the desktop.
        if args.dry_run:
            _emit(
                {
                    "action": "browser",
                    "semantic": args.action,
                    "browser": target["name"],
                    "chord": chord,
                    "resolved_from": source,
                    "delivery": "not-dispatched",
                    "effect": {
                        "status": "unknown",
                        "meaning": "no browser window held focus for this dry run",
                    },
                    "dry_run": True,
                    "focused": False,
                },
                args.json,
                lambda r: print(
                    f"would send {r['semantic']} → {r['chord']}  "
                    f"[{r['browser']}, via {r['resolved_from']}, not focused]"
                ),
            )
            return exits.OK
        print(
            "cu: --browser names a browser but no window of it holds focus; "
            "focus one first",
            file=sys.stderr,
        )
        return exits.TARGET_NOT_FOUND

    result = actions.browser_action(
        cfg, target, args.action, backend=args.backend, dry_run=args.dry_run
    )
    _emit(
        result,
        args.json,
        lambda r: print(
            f"{'would send' if r['dry_run'] else 'sent'} {r['semantic']} → "
            f"{r['chord']}  [{r['browser']}, via {r['resolved_from']}; "
            f"effect {r.get('effect', {}).get('status', 'unknown')}]"
        ),
    )
    return exits.OK


def cmd_wait(args: argparse.Namespace) -> int:
    cfg = config.load()
    # Waiting is logged even though it injects nothing: a procedure that does
    # not wait for the page it just asked for is the commonest way a replay
    # acts on a screen that has not caught up yet.
    record = {
        "action": "wait",
        "condition": args.condition,
        "class": getattr(args, "class"),
        "title": args.title,
        "timeout": args.timeout,
        "dry_run": False,
    }
    try:
        result = wait.until(
            args.condition,
            cls=getattr(args, "class"),
            title=args.title,
            timeout=args.timeout,
        )
    except wait.WaitTimeout as exc:
        safety.record(cfg, {**record, "outcome": "timeout"})
        print(f"cu: {exc}", file=sys.stderr)
        print(f"cu: {exits.RESPONSE[exits.TIMED_OUT]}", file=sys.stderr)
        return exits.TIMED_OUT

    safety.record(cfg, {**record, "outcome": "matched", "waited": result["waited"]})

    def render(r: Any) -> None:
        matched = r["matched"]
        how = "waited for" if r["waited"] else "already true:"
        if "workspace" in matched:
            print(f"{how} {r['condition']}  workspace {matched['workspace']}")
            return
        described = matched.get("class", "?")
        title = matched.get("title", "")
        print(f"{how} {r['condition']}  {described}  {title[:56]}")

    _emit(result, args.json, render)
    return exits.OK


def cmd_focus(args: argparse.Namespace) -> int:
    cfg = config.load()
    snap = observe.snapshot()
    window = _resolve_window(args.selector, snap)
    result = actions.focus(cfg, window, dry_run=args.dry_run)
    _emit(
        result,
        args.json,
        lambda r: print(
            f"{'would focus' if r['dry_run'] else 'focused'} "
            f"{r['window']['class']}  {r['window']['title'][:60]}"
        ),
    )
    return exits.OK


def cmd_move(args: argparse.Namespace) -> int:
    cfg = config.load()
    result = actions.move(
        cfg,
        args.frame,
        _parse_at(args.at),
        duration=args.duration,
        hz=args.hz,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    _emit(result, args.json, _render_action)
    return exits.OK


def cmd_warp(args: argparse.Namespace) -> int:
    cfg = config.load()
    result = actions.warp_to(cfg, args.frame, _parse_at(args.at), dry_run=args.dry_run)
    _emit(result, args.json, _render_action)
    return exits.OK


def cmd_click(args: argparse.Namespace) -> int:
    cfg = config.load()
    result = actions.click(
        cfg,
        args.frame,
        _parse_at(args.at),
        button=args.button,
        count=args.count,
        dry_run=args.dry_run,
    )
    _emit(result, args.json, _render_action)
    return exits.OK


def cmd_scroll(args: argparse.Namespace) -> int:
    if args.dx == 0 and args.dy == 0:
        print("cu: --dx and --dy cannot both be zero", file=sys.stderr)
        return exits.USAGE
    cfg = config.load()
    if (args.frame is None) != (args.at is None):
        print("cu: --frame and --at are given together or not at all", file=sys.stderr)
        return exits.USAGE
    result = actions.scroll(
        cfg,
        dy=args.dy,
        dx=args.dx,
        frame_id=args.frame,
        at=_parse_at(args.at) if args.at else None,
        dry_run=args.dry_run,
    )
    _emit(result, args.json, _render_action)
    return exits.OK


def cmd_type(args: argparse.Namespace) -> int:
    cfg = config.load()
    text = sys.stdin.read() if args.text == "-" else args.text
    if (args.frame is None) != (args.at is None):
        raise ValueError("--frame and --at are given together or not at all")
    result = actions.type_text(
        cfg,
        text,
        method=args.method,
        restore_clipboard=not args.no_restore,
        backend=args.backend,
        verify=args.verify,
        replace=args.replace,
        frame_id=args.frame,
        at=_parse_at(args.at) if args.at else None,
        button=args.button,
        count=args.count,
        dry_run=args.dry_run,
    )
    _emit(result, args.json, _render_action)
    return exits.OK


def cmd_key(args: argparse.Namespace) -> int:
    cfg = config.load()
    result = actions.key(
        cfg,
        action=args.action,
        chord=args.chord,
        repeat=args.repeat,
        backend=args.backend,
        dry_run=args.dry_run,
    )
    _emit(result, args.json, _render_action)
    return exits.OK


def cmd_status(args: argparse.Namespace) -> int:
    """Who is driving the desktop, for whatever shows the user things.

    Prints nothing at all when nobody is, because that is what a status bar
    wants to render for idle — a module whose command outputs an empty string
    takes up no room. One line per driver, since two agents on one desktop is
    a thing the user needs to see rather than a case to average away.
    """
    if not args.follow:
        record = status.read()
        _emit(record, args.json, lambda r: print(status.summarize(r)) if r.get("active") else None)
        return exits.OK

    if not args.json:
        args.parser.error(
            "cu status --follow requires --json: a feed is machine-readable by definition"
        )

    def stop_feed(_signum, _frame) -> None:
        raise KeyboardInterrupt

    previous_handlers = {
        signum: signal.signal(signum, stop_feed) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        for record in status.follow():
            json.dump(record, sys.stdout, separators=(",", ":"))
            sys.stdout.write("\n")
            sys.stdout.flush()
    except KeyboardInterrupt:
        return exits.OK
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return exits.OK


def cmd_abort(args: argparse.Namespace) -> int:
    """Report or change the user's host-wide abort interlock."""
    if args.set or args.clear:
        if args.session is not None or status.current_session() is not None:
            raise Refused(
                exits.ABORT_CONTROLLED_BY_USER,
                "the abort flag belongs to the user; ask them to change it",
            )
        if args.set:
            config.set_abort()
        else:
            config.clear_abort()
    _emit(
        {"aborted": config.aborted()},
        args.json,
        lambda state: print(f"abort: {'set' if state['aborted'] else 'clear'}"),
    )
    return exits.OK


def cmd_playground(args: argparse.Namespace) -> int:
    native_playground.serve(args.port, as_json=args.json)
    return exits.OK


def cmd_export(args: argparse.Namespace) -> int:
    """Export validated frame evidence without touching the desktop."""
    frame = frames.load(args.frame)
    compare = frames.load(args.compare) if args.compare else None
    envelope = evidence.export_frame(
        frame,
        compare=compare,
        retain=Path(args.retain) if args.retain else None,
    )

    def render(value: Any) -> None:
        print(f"evidence {value['status']}  frame {value['frame'].get('id')}")
        print(f"  runtime bytes: {value['availability']['runtime']}")
        for derived in value.get("derived", []):
            print(f"  {derived['kind']}: {derived['status']}")

    _emit(envelope, args.json, render)
    return exits.OK if envelope["status"] == "ok" else exits.TARGET_NOT_FOUND


def cmd_session(args: argparse.Namespace) -> int:
    """Bracket a stretch of work, so the indicator does not blink between steps.

    A single action's entry expires on its own, which is what keeps a killed
    process from claiming the desktop forever. But a model deciding what to do
    next routinely takes longer than that, and an indicator that clears while
    the agent is still working reads as "it finished" at the moment the user
    most wants the truth.

    `begin` prints the session id and nothing else, so the shell form that
    binds every later command to it is the obvious one:
    `export CU_SESSION=$(cu session begin --why 'booking a flight')`.
    """
    if args.subcommand == "begin":
        if (args.detail is None) == (args.purpose is None):
            args.parser.error("session begin requires exactly one of PURPOSE or --why TEXT")
        purpose = args.detail if args.detail is not None else args.purpose
        record = status.begin(purpose, pid=args.pid, exclusive=args.exclusive)
        _emit(record, args.json, lambda r: print(r["id"]))
        return exits.OK
    record = status.end(getattr(args, "session", None))
    _emit(
        record,
        args.json,
        # A session that had already expired reports that, rather than the
        # "ended" of one that was live a moment ago. They mean different things
        # to whoever is reading: one of them says the bracket was too short.
        lambda r: print(f"ended {r['ended']}" if r["was_open"] else f"{r['ended']} was not open"),
    )
    return exits.OK


MAX_BATCH_STEPS = 32
MAX_BATCH_BYTES = 65536
BATCH_COMMANDS = frozenset({"focus", "warp", "move", "click", "scroll", "type", "key", "browser"})


def cmd_batch(args: argparse.Namespace) -> int:
    """Execute a bounded, pre-parsed sequence under the caller's desktop hold."""
    try:
        with contextlib.nullcontext(sys.stdin) if args.file == "-" else open(args.file) as handle:
            raw = handle.read(MAX_BATCH_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"cannot read batch: {exc}") from exc
    if len(raw.encode()) > MAX_BATCH_BYTES:
        raise ValueError(f"batch exceeds {MAX_BATCH_BYTES} bytes")
    steps = json.loads(raw)
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_BATCH_STEPS:
        raise ValueError(f"batch must contain 1..{MAX_BATCH_STEPS} argv arrays")
    parsed = []
    for index, argv in enumerate(steps, 1):
        if (
            not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv)
            or argv[0] not in BATCH_COMMANDS
            or any(a.split("=", 1)[0] in {"--session", "--why", "--dry-run", "--help", "-h"}
                   for a in argv)
            or (argv[0] == "type" and len(argv) > 1 and argv[1] == "-")
        ):
            raise ValueError(f"batch step {index} is not an allowed action argv array")
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                step = build_parser().parse_args(argv)
        except SystemExit as exc:
            raise ValueError(
                f"batch step {index} has invalid arguments; see command --help"
            ) from exc
        step.json = True
        step.dry_run = args.dry_run
        parsed.append(step)

    cfg = config.load()
    snap = observe.snapshot()
    expected = next((w for w in snap["windows"] if snap["focused"]
                     and w["address"] == snap["focused"]["address"]), None)
    completed = []
    failure = None
    for index, step in enumerate(parsed, 1):
        output, errors = io.StringIO(), io.StringIO()
        def invoke(step=step, expected=expected):
            safety.check_abort()
            status.check_exclusive(getattr(args, "session", None))
            if expected is not None:
                current = observe.snapshot()
                safety.confirm_focus(expected, current["focused"])
                safety.check_frame(cfg, {
                    "id": "batch-context", "captured_at": time.time(), "window": expected,
                }, current["windows"], current["monitors"])
            return step.run(step)
        with (
            context.batch_step(index),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            code = _safe_call(invoke)
        result = json.loads(output.getvalue()) if output.getvalue().strip() else None
        if (code == exits.OK and isinstance(result, dict)
                and result.get("verified", {}).get("status") == "unverified"):
            code = exits.VERIFY_UNAVAILABLE
        item = {"step": index, "command": step.command, "code": code, "result": result}
        if code != exits.OK:
            failure = {**item, "detail": errors.getvalue().strip() or exits.RESPONSE[code],
                       "may_have_acted": not args.dry_run}
            break
        completed.append(item)
        if (not args.dry_run and step.command in {"focus", "click", "type"}
                and result.get("window")):
            expected = result["window"]
    payload = {"completed": completed, "failed": failure, "dry_run": args.dry_run,
               "rollback": False, "code": failure["code"] if failure else exits.OK}
    _emit(payload, args.json, lambda p: print(json.dumps(p, indent=2)))
    return payload["code"]


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="cu",
        description="Computer use for Hyprland.",
        epilog="Run `cu` with no arguments for the workflow guide.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON on stdout")
    parser.add_argument("--why", metavar="TEXT", default="", help="What this is for")
    # Optional, so a bare `cu` is a request for the guide rather than a usage
    # error. Every other malformed invocation still fails at 2: an unknown
    # command, an unrecognized flag, and a subcommand missing a required
    # argument are all rejected by argparse before this matters.
    sub = parser.add_subparsers(
        dest="command", required=False, metavar="<command>", parser_class=_Parser
    )

    # Repeating --json per subcommand accepts both `cu --json observe` and the
    # `cu observe --json` an agent is far more likely to write. SUPPRESS keeps
    # an omitted flag from overwriting the value parsed before the subcommand.
    def add(name: str, help: str, run) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help, description=help)
        p.add_argument(
            "--json", action="store_true", default=argparse.SUPPRESS, help="Emit JSON"
        )
        if name in NON_PUBLISHING_COMMANDS:
            p.set_defaults(run=run, injects=False)
            return p
        # What the user sees while this runs. A command name says an agent is
        # acting; a sentence says what it is trying to do, which is the part
        # worth interrupting over or leaving alone.
        p.add_argument(
            "--why",
            metavar="TEXT",
            default=argparse.SUPPRESS,
            help="A short description of the intent, shown to the user",
        )
        # Which stretch of work this command belongs to. Never inferred: with
        # two agents on one desktop, guessing from process ancestry files one
        # agent's action under the other's intent, which reads as true.
        p.add_argument(
            "--session",
            metavar="ID",
            default=argparse.SUPPRESS,
            help=f"Session from `cu session begin` (default: ${status.SESSION_ENV})",
        )
        p.set_defaults(run=run, injects=False)
        return p

    command = add("status", "Who is driving the desktop, for a status bar", cmd_status)
    command.add_argument(
        "--follow", action="store_true", help="Emit changed status objects as a JSON feed"
    )
    command.set_defaults(parser=command)

    session = add("session", "Bracket a stretch of work as one activity", cmd_session)
    ssub2 = session.add_subparsers(dest="subcommand", required=True)
    begin = ssub2.add_parser(
        "begin",
        help="Say an agent has started working",
        description="Say an agent has started working",
    )
    begin.add_argument(
        "detail",
        nargs="?",
        default=None,
        metavar="PURPOSE",
        help="Standing purpose, not a command's --why intent",
    )
    begin.add_argument(
        "--why",
        dest="purpose",
        metavar="TEXT",
        default=None,
        help="Standing purpose, not a command's --why intent",
    )
    begin.add_argument(
        "--pid", type=int,
        help="End the session when this process does; omit if there is none",
    )
    begin.add_argument(
        "--exclusive",
        action="store_true",
        help="Reserve injection for commands naming this session",
    )
    begin.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    begin.set_defaults(parser=begin)
    ended = ssub2.add_parser(
        "end", help="Say it has stopped", description="Say it has stopped"
    )
    ended.add_argument(
        "--session",
        metavar="ID",
        default=argparse.SUPPRESS,
        help=f"Which session to end (default: ${status.SESSION_ENV}, "
        "or the only one open)",
    )
    ended.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    abort = add("abort", "Report or change the user's abort interlock", cmd_abort)
    mutation = abort.add_mutually_exclusive_group()
    mutation.add_argument("--set", action="store_true", help="Set the abort flag")
    mutation.add_argument("--clear", action="store_true", help="Clear the abort flag")
    abort.add_argument(
        "--session",
        metavar="ID",
        default=None,
        help=f"Agent session; mutations refuse (default: ${status.SESSION_ENV})",
    )

    lab = add(
        "playground", "Serve the packaged native playground without an extension", cmd_playground
    )
    lab.add_argument("--port", type=int, default=0,
                     help="Loopback port; 0 selects a free port (default)")
    add("doctor", "Verify every environment precondition", cmd_doctor)
    add("profiles", "What a capture costs each configured consumer", cmd_profiles)
    add("observe", "Monitors, windows, focus, cursor", cmd_observe)

    export = add("export", "Export bounded evidence from a stored frame", cmd_export)
    export.add_argument("--frame", metavar="ID", required=True, help="Frame to export")
    export.add_argument(
        "--compare", metavar="ID", help="Frame with matching coverage for pixel change"
    )
    export.add_argument("--retain", metavar="PATH", help="Copy validated PNG bytes to a new path")

    shot = add("shot", "Capture a frame", cmd_shot)
    shot.add_argument(
        "--timings", action="store_true",
        help="Emit bounded timing and frame-cost JSON on stderr, including failure paths",
    )
    shot.add_argument(
        "--window",
        metavar="SELECTOR",
        help="'active' (default), a window address, or a class substring",
    )
    shot.add_argument("--output", metavar="NAME", help="A whole monitor")
    shot.add_argument(
        "--region",
        metavar="'x,y wxh'",
        help=(
            "An arbitrary strip; with --output, x,y are output-relative and "
            "logical_rect is absolute"
        ),
    )
    shot.add_argument(
        "--target-window",
        metavar="SELECTOR",
        help=(
            "Assert an output or region point belongs to this exact window; "
            "the window may be obstructed at capture time"
        ),
    )
    # --landmark is deliberately outside target selection: it names
    # *what region* while --window names *which window*, so they compose. With
    # several windows of one class open, that pairing is the only way to say
    # which one the landmark applies to.
    shot.add_argument(
        "--landmark",
        metavar="APP/NAME",
        help="A remembered region of a window; aims the capture, never a click",
    )
    shot.add_argument("--context", metavar="NAME", help="Scope a landmark, e.g. a site")
    # Choices come from the config rather than the code, so a profile added
    # there is selectable without touching this file. Default is None so the
    # configured profile is what applies when --for is absent.
    shot.add_argument(
        "--for",
        dest="profile",
        metavar="CONSUMER",
        help="Capture budget of the consuming model; see `cu profiles`",
    )
    shot.add_argument(
        "--if-changed",
        action="store_true",
        help="Reuse the previous frame when the pixels are identical",
    )
    shot.add_argument(
        "--after",
        metavar="FRAME_ID",
        help="Recapture a frame's region after watched pixels change and settle",
    )
    shot.add_argument(
        "--watch",
        metavar="'x,y wxh'|full",
        help="Required with --after; box in the reference frame's image pixels",
    )
    shot.add_argument(
        "--poll-ms",
        type=float,
        default=100,
        help="Milliseconds between --after probes (default: 100)",
    )
    shot.add_argument(
        "--quiet-ms",
        type=float,
        default=200,
        help="Unchanged milliseconds before --after settles (default: 200)",
    )
    shot.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Seconds before --after gives up (default: 10)",
    )

    pixel = add("pixel", "Read a colour from an existing frame", cmd_pixel)
    pixel.add_argument("--frame", metavar="ID", required=True, help="Frame to read")
    pixel.add_argument(
        "--at", metavar="X,Y", required=True, help="Integer coordinate in image pixels"
    )
    pixel.add_argument(
        "--radius",
        type=int,
        default=0,
        metavar="N",
        help="Median in a square of radius N around the point (default: 0)",
    )

    magnify = add("magnify", "Upscale an existing frame without recapturing", cmd_magnify)
    magnify.add_argument("--frame", metavar="ID", required=True, help="Frame to derive from")
    magnify.add_argument(
        "--scale",
        type=int,
        required=True,
        metavar="N",
        help=f"Integer factor at least 2; maximum output long edge {MAX_MAGNIFIED_LONG_EDGE}px",
    )

    sbox = add("sandbox", "Create and remove headless outputs, explicitly", cmd_sandbox)
    ssub = sbox.add_subparsers(dest="subcommand", required=True)
    for name, help_text in (
        ("create", "Add a headless output nobody can see"),
        ("list", "Headless outputs that exist"),
        ("remove", "Take one away; refuses anything but a headless output"),
    ):
        sp = ssub.add_parser(name, help=help_text, description=help_text)
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        if name == "remove":
            sp.add_argument("name", nargs="?", help="Which output")
            sp.add_argument("--all", action="store_true", help="Every headless output")

    know = add("know", "What past runs learned about an application", cmd_know)
    ksub = know.add_subparsers(dest="subcommand", required=True)
    for name, help_text in (
        ("list", "Applications with stored knowledge"),
        ("show", "Landmarks and notes for one application"),
        ("set", "Record where something is, as a fraction of the window"),
        ("note", "Record a fact that has no coordinate"),
        ("confirm", "The landmark pointed at the right thing"),
        ("miss", "It did not"),
        ("forget", "Drop a landmark, or everything for an application"),
        ("host", "What runs learned about this machine, not about an application"),
    ):
        kp = ksub.add_parser(name, help=help_text, description=help_text)
        kp.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        if name not in {"list", "host"}:
            kp.add_argument("app", help="Window class, or any name you key it by")
        if name in {"set", "confirm", "miss"}:
            kp.add_argument("name", help="What the landmark is called")
        if name == "forget":
            kp.add_argument("name", nargs="?", help="Omit to forget the application")
        if name == "set":
            kp.add_argument("--rect", required=True, metavar="X,Y[,W,H]",
                            help="Fractions of the window")
            kp.add_argument("--note", help="What it is")
            kp.add_argument("--window", metavar="SELECTOR",
                            help="The window measured against (default: the app)")
            # Naming how a control follows its window narrows what a later
            # capture has to cover. Unnamed is the honest default: the run that
            # learned a fraction usually cannot tell either, and covering every
            # possibility costs pixels rather than correctness.
            kp.add_argument("--anchor-x", choices=knowledge.ANCHORS["x"],
                            default="auto", help="How it follows a width change")
            kp.add_argument("--anchor-y", choices=knowledge.ANCHORS["y"],
                            default="auto", help="How it follows a height change")
        if name == "note":
            kp.add_argument("text", help="The fact worth keeping")
        if name == "host":
            # Host facts are notes only, and are addressed by the position the
            # listing prints: a machine has no name to key a fact by. Adding and
            # dropping are exclusive because the command does one or the other:
            # a caller who wrote both meant one of them, and argparse refusing
            # is the only outcome that does not silently discard the other.
            host_action = kp.add_mutually_exclusive_group()
            host_action.add_argument("text", nargs="?", help="A fact about this machine")
            host_action.add_argument(
                "--forget", nargs="?", type=int, const=0, metavar="N",
                help="Drop note N, or every host note when given no number",
            )
        else:
            kp.add_argument("--context", metavar="NAME", help="Scope, e.g. a site")

    waiting = add("wait", "Block until a compositor event happens", cmd_wait)
    waiting.add_argument(
        "condition",
        choices=sorted(wait.EVENTS),
        help="What to wait for; checked once against the current state first",
    )
    waiting.add_argument("--class", metavar="SUBSTRING", help="Restrict to a window class")
    waiting.add_argument("--title", metavar="SUBSTRING", help="Restrict to a window title")
    waiting.add_argument(
        "--timeout", type=float, default=30.0, help="Seconds before giving up (default: 30)"
    )

    # Everything below injects. --dry-run runs every interlock and writes the
    # same log entry without emitting a single event, which is what makes an
    # agent's plan inspectable before it reaches the desktop.
    def add_injecting(name: str, help: str, run) -> argparse.ArgumentParser:
        p = add(name, help, run)
        p.add_argument(
            "--dry-run",
            action="store_true",
            help="Check and log the action without injecting it",
        )
        # Marks the commands that reach the shared pointer, focus, or
        # clipboard, and so must hold the desktop while they run. `main`
        # reads it; nothing else needs to know which commands these are.
        p.set_defaults(injects=True)
        return p

    def add_aim(p: argparse.ArgumentParser, required: bool) -> None:
        p.add_argument(
            "--frame", metavar="ID", required=required,
            help="Frame the coordinate came from",
        )
        p.add_argument("--at", metavar="X,Y", required=required, help="Coordinate in image pixels")

    batch = add_injecting(
        "batch", "Run bounded action argv arrays; stop on the first refusal", cmd_batch
    )
    batch.add_argument("file", help="JSON file or '-' for stdin; at most 32 actions / 64 KiB")

    focus = add_injecting("focus", "Give a window keyboard focus", cmd_focus)
    focus.add_argument(
        "selector",
        help="'active', a window address, or a class substring",
    )

    move = add_injecting("move", "Move the pointer natively to a frame coordinate", cmd_move)
    add_aim(move, required=True)
    move.add_argument(
        "--duration", type=float, default=actions.DEFAULT_MOVE_DURATION,
        help="Seconds for the bounded native segment (default: 0.25)",
    )
    move.add_argument(
        "--hz", type=float, default=actions.DEFAULT_MOVE_HZ,
        help="Native motion samples per second (default: 60)",
    )
    move.add_argument(
        "--timeout", type=float, default=actions.DEFAULT_MOVE_TIMEOUT,
        help="Maximum seconds for movement and convergence (default: 2)",
    )

    warp = add_injecting(
        "warp", "Place the compositor pointer at a frame coordinate", cmd_warp
    )
    add_aim(warp, required=True)

    hover = add_injecting(
        "hover", "Enter a target natively, dwell, and return an observation", cmd_hover
    )
    add_aim(hover, required=True)
    hover.add_argument(
        "--duration", type=float, default=actions.DEFAULT_MOVE_DURATION,
        help="Seconds for native entry (default: 0.25)",
    )
    hover.add_argument(
        "--hz", type=float, default=actions.DEFAULT_MOVE_HZ,
        help="Native entry samples per second (default: 60)",
    )
    hover.add_argument(
        "--move-timeout", type=float, default=actions.DEFAULT_MOVE_TIMEOUT,
        help="Maximum seconds for entry convergence (default: 2)",
    )
    hover.add_argument(
        "--dwell", type=float, default=actions.DEFAULT_HOVER_DWELL,
        help="Seconds to hold the endpoint before observing (default: 0.2)",
    )
    hover.add_argument(
        "--watch", metavar="'x,y wxh'|full",
        help="Wait for watched frame pixels to change and settle",
    )
    hover.add_argument(
        "--poll-ms", type=float, default=100,
        help="Milliseconds between watched probes (default: 100)",
    )
    hover.add_argument(
        "--quiet-ms", type=float, default=200,
        help="Unchanged milliseconds before a watched reveal settles (default: 200)",
    )
    hover.add_argument(
        "--timeout", type=float, default=3.0,
        help="Seconds to wait for watched pixels, at most 10 (default: 3)",
    )

    click = add_injecting("click", "Warp, assert focus, press and release", cmd_click)
    add_aim(click, required=True)
    click.add_argument(
        "--button", choices=sorted(uinput.BUTTONS), default="left", help="Which button"
    )
    click.add_argument("--count", type=int, default=1, help="Click count (2 for double)")

    scroll = add_injecting("scroll", "Wheel events, optionally aimed first", cmd_scroll)
    add_aim(scroll, required=False)
    scroll.add_argument("--dy", type=int, default=0, help="Vertical detents, negative scrolls down")
    scroll.add_argument("--dx", type=int, default=0, help="Horizontal detents")

    typing = add_injecting("type", "Enter text into a field, aimed or focused", cmd_type)
    typing.add_argument("text", help="The text, or '-' to read stdin")
    # Aiming is optional: typing into whatever holds focus is the ordinary
    # case. Supplying a frame and a point makes the click part of this
    # command's contract instead of a separate command with a gap after it.
    add_aim(typing, required=False)
    typing.add_argument(
        "--button", choices=sorted(uinput.BUTTONS), default="left",
        help="Which button aims the field, with --frame",
    )
    typing.add_argument(
        "--count", type=int, default=1,
        help="Click count when aiming (2 selects a word)",
    )
    typing.add_argument(
        "--method",
        choices=("clipboard", "keys"),
        default="clipboard",
        help="Clipboard is layout-independent; keys synthesizes each character",
    )
    typing.add_argument(
        "--replace", action="store_true",
        help="Select the entire field before nonempty text; --verify requires exact equality",
    )
    typing.add_argument(
        "--verify",
        action="store_true",
        help="Read the field back and confirm the text arrived intact",
    )
    typing.add_argument(
        "--backend",
        choices=("uinput", "compositor"),
        help="Compositor injection leaves sleeping monitors asleep",
    )
    typing.add_argument(
        "--no-restore",
        action="store_true",
        help="Leave the text on the clipboard instead of restoring what was there",
    )

    key = add_injecting("key", "A semantic action, or a literal chord", cmd_key)
    target = key.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--action", metavar="NAME", help="A semantic action, resolved per application"
    )
    target.add_argument(
        "--chord", metavar="CHORD", help="A literal chord such as 'ctrl+alt+shift+f4'"
    )
    key.add_argument("--repeat", type=int, default=1, help="How many times to send it")
    key.add_argument(
        "--backend",
        choices=("uinput", "compositor"),
        help="Compositor injection leaves sleeping monitors asleep",
    )

    recipe = add_injecting("recipe", "Save, review, and replay a procedure", cmd_recipe)
    # Only `replay` injects; listing and reading recipes must not queue behind
    # an agent that is driving the desktop. The subparser's default wins over
    # the one add_injecting set here.
    recipe.set_defaults(injects=False)
    rsub = recipe.add_subparsers(dest="subcommand", required=True)

    def add_recipe(name: str, help_text: str) -> argparse.ArgumentParser:
        """Nested subcommands repeat the global flags for the same reason the
        top level does: `cu recipe list --json` is the form an agent writes,
        and argparse accepts a flag only on the parser that declares it."""
        p = rsub.add_parser(name, help=help_text, description=help_text)
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        p.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
        return p

    add_recipe("list", "Every stored recipe and whether it is reviewed")
    rsave = add_recipe("save", "Promote recent actions from the log")
    rsave.add_argument("name", help="Lowercase name; it becomes the filename")
    rsave.add_argument("--last", type=int, default=20, help="How many log entries to promote")
    rsave.add_argument("--site", help="What site this is for")
    rsave.add_argument("--task", help="What it accomplishes")
    rsave.add_argument(
        "--for",
        dest="profile",
        metavar="CONSUMER",
        help="Capture budget the replay should use; see `cu profiles`",
    )
    for name, help_text in (
        ("show", "Print every step"),
        ("review", "Read the steps, and approve them with --approve"),
        ("replay", "Run a reviewed recipe"),
    ):
        sub_parser = add_recipe(name, help_text)
        sub_parser.add_argument("name")
        if name == "review":
            sub_parser.add_argument(
                "--approve", action="store_true", help="Record that you have read every step"
            )
        if name == "replay":
            sub_parser.set_defaults(injects=True)
            sub_parser.add_argument(
                "--param", action="append", metavar="NAME=VALUE",
                help="Supply a typed value; recipes never store the text itself",
            )

    browsing = add_injecting(
        "browser", "A semantic browser action, resolved per browser", cmd_browser
    )
    browsing.add_argument(
        "action",
        help="An action such as focus-urlbar or close-tab; "
        "'list' shows installed browsers, 'products' shows Chrome/Chromium candidates, "
        "and 'actions' shows what one resolves",
    )
    browsing.add_argument(
        "--browser", metavar="NAME", help="Which browser (default: the focused window's)"
    )
    browsing.add_argument(
        "--backend",
        choices=("uinput", "compositor"),
        help="Compositor injection leaves sleeping monitors asleep",
    )

    web = add(
        "web", "Read granted browser semantics without input delivery", browser_read_cli.run
    )
    browser_read_cli.configure(web)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        # Asking how to use cu is not something happening on the desktop, so
        # this returns before anything is published and before dispatch, which
        # has no command to dispatch to.
        print(guide.render(), end="")
        return exits.OK
    if args.command in NON_PUBLISHING_COMMANDS and getattr(args, "why", ""):
        parser.error(f"cu {args.command} does not accept --why")
    why = getattr(args, "why", "") or ""
    session = getattr(args, "session", None)
    with context.active(session):
        # Published before the command runs rather than after, because the point is
        # to say what is happening while it happens. Non-publishing commands are
        # readers or user-owned controls, not agents driving the desktop.
        measurement = metrics.Measurement() if getattr(args, "timings", False) else None
        with metrics.collecting(measurement):
            if args.command not in NON_PUBLISHING_COMMANDS:
                status.publish(args.command, detail=why, session=session)
            code = _safe_call(lambda: _dispatch(args, why, session))
        if measurement is not None:
            print("cu metrics: " + json.dumps(measurement.report(code)), file=sys.stderr)
        return code


def _dispatch(args: argparse.Namespace, why: str, session: str | None) -> int:
    # One hold spans every step that touches pointer, focus or clipboard.
    injecting = getattr(args, "injects", False)
    exclusive_begin = (
        args.command == "session" and args.subcommand == "begin" and args.exclusive
    )
    if injecting or exclusive_begin:
        busy_wait = config.load()["safety"]["busy_wait_ms"] / 1000
        lock_detail = why
        if exclusive_begin:
            lock_detail = args.purpose if args.purpose is not None else args.detail or ""
        with desktop.held(args.command, lock_detail, wait=busy_wait):
            if injecting:
                status.check_exclusive(session)
            return args.run(args)
    return args.run(args)


def _safe_call(call) -> int:
    try:
        return call()
    except Refused as exc:
        # An interlock, not a fault. The code is what the caller acts on, so
        # the response the taxonomy prescribes is printed alongside it.
        print(f"cu: refused: {exc.reason}", file=sys.stderr)
        print(f"cu: {exits.RESPONSE[exc.code]}", file=sys.stderr)
        return exc.code
    except (KnowledgeError, SandboxError) as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.TARGET_NOT_FOUND
    except RecipeNotFound as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.TARGET_NOT_FOUND
    except RecipeTimeout as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.TIMED_OUT
    except RecipeError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.DENIED_BY_POLICY
    except BrowserError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.TARGET_NOT_FOUND
    except (UinputError, ClipboardError, QuietError) as exc:
        # A path that cannot do the job. Each message names the other one —
        # `--method keys` for a missing clipboard, `--backend uinput` for a
        # chord the compositor cannot spell — so retrying as-is will not help.
        print(f"cu: {exc}", file=sys.stderr)
        return exits.PRECONDITION_FAILED
    except ConfigError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.PRECONDITION_FAILED
    except HyprError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.PRECONDITION_FAILED
    except CaptureAimError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.DEGENERATE_AIM
    except capture.CaptureBudgetError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        print(f"cu: {exits.RESPONSE[exits.CAPTURE_BUDGET_EXCEEDED]}", file=sys.stderr)
        return exits.CAPTURE_BUDGET_EXCEEDED
    except capture.CaptureVisibilityError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        print(f"cu: {exits.RESPONSE[exits.TARGET_NOT_VISIBLE]}", file=sys.stderr)
        return exits.TARGET_NOT_VISIBLE
    except CaptureError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.PRECONDITION_FAILED
    except evidence.ExportError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.PRECONDITION_FAILED
    except wait.WaitTimeout as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.TIMED_OUT
    except LookupError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.TARGET_NOT_FOUND
    except FrameError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.STALE_FRAME
    except ValueError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.USAGE
    except BrokenPipeError:
        # The reader is gone and that is not an error. Python flushes stdout
        # once more on the way out, so it is given a sink to flush into.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return exits.OK
    except KeyboardInterrupt:
        return exits.ERROR
    except (RuntimeError, OSError) as exc:
        # Anything this taxonomy has not been taught. A traceback would leave
        # the caller with no code to branch on, which for a CLI whose contract
        # *is* its exit codes is worse than an unhelpful one.
        print(f"cu: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"cu: {exits.RESPONSE[exits.ERROR]}", file=sys.stderr)
        return exits.ERROR


if __name__ == "__main__":
    sys.exit(main())
