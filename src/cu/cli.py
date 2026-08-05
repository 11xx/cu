"""Command dispatch.

Every subcommand prints JSON on `--json` and something readable otherwise, and
signals outcome through the codes in `exits` rather than through prose on
stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import capture, checks, exits, frames, observe
from .capture import CaptureError
from .frames import FrameError
from .hypr import HyprError

STATUS_MARK = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL"}


def _num(value: float) -> str:
    """Logical space is continuous, but whole numbers should not read as floats."""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def fmt_rect(r: dict[str, float]) -> str:
    return f"{_num(r['w'])}x{_num(r['h'])}+{_num(r['x'])}+{_num(r['y'])}"


def _emit(data: Any, as_json: bool, render) -> None:
    if as_json:
        json.dump(data, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        render(data)


def cmd_doctor(args: argparse.Namespace) -> int:
    results = checks.run_all()
    overall = checks.worst(results)
    payload = {
        "status": overall,
        "checks": results,
        "exit_codes": {exits.name(c): {"code": c, "response": r} for c, r in exits.RESPONSE.items()},
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


def cmd_observe(args: argparse.Namespace) -> int:
    snap = observe.snapshot()

    def render(s: Any) -> None:
        for m in s["monitors"]:
            focus = " *" if m["focused"] else "  "
            print(
                f"{focus}{m['name']:<8} scale {m['scale']:<5} "
                f"physical {m['physical']['w']}x{m['physical']['h']}  "
                f"logical {fmt_rect(m['logical_rect']):<22} ws {m['active_workspace']}"
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
    loose = [w for w in windows if selector.lower() in w["class"].lower()]
    if not loose:
        raise LookupError(f"no window matches {selector!r}")
    if len(loose) > 1:
        names = ", ".join(f"{w['class']}({w['address']})" for w in loose[:5])
        raise LookupError(f"{selector!r} matches several windows: {names}")
    return loose[0]


def _parse_region(text: str) -> dict[str, float]:
    """Accept grim's own `x,y wxh`, and a plain `x,y,w,h`."""
    cleaned = text.replace("x", " ").replace(",", " ")
    parts = cleaned.split()
    if len(parts) != 4:
        raise ValueError(f"region must be 'x,y wxh' or 'x,y,w,h', got {text!r}")
    x, y, w, h = (float(p) for p in parts)
    if w <= 0 or h <= 0:
        raise ValueError("region width and height must be positive")
    return {"x": x, "y": y, "w": w, "h": h}


def cmd_shot(args: argparse.Namespace) -> int:
    snap = observe.snapshot()
    monitors = snap["monitors"]
    source: dict[str, Any]
    window = None

    if args.output:
        monitor = next((m for m in monitors if m["name"] == args.output), None)
        if monitor is None:
            print(f"cu: no such output: {args.output}", file=sys.stderr)
            return exits.TARGET_NOT_FOUND
        rect = monitor["logical_rect"]
        source = {"type": "output", "name": monitor["name"]}
    elif args.region:
        rect = _parse_region(args.region)
        monitor = capture.sole_monitor(rect, monitors)
        rect = capture.clamp(rect, monitor["logical_rect"])
        source = {"type": "region", "name": monitor["name"]}
    else:
        window = _resolve_window(args.window or "active", snap)
        rect = window["logical_rect"]
        monitor = capture.sole_monitor(rect, monitors)
        rect = capture.clamp(rect, monitor["logical_rect"])
        source = {"type": "window", "name": window["class"]}

    frames.collect()
    profile = capture.PROFILES[args.profile]
    scratch = frames.store_dir() / ".pending.png"
    scale = capture.scale_for(rect, monitor["scale"], profile["max_long_edge"])
    native = {
        "w": round(rect["w"] * monitor["scale"]),
        "h": round(rect["h"] * monitor["scale"]),
    }

    if source["type"] == "output":
        capture.grab_output(source["name"], scratch, scale)
    else:
        capture.grab_region(rect, scratch, scale)

    digest = capture.digest(scratch)

    if args.if_changed:
        previous = _last_frame_like(source, rect)
        if previous and previous.get("digest_native") == digest:
            scratch.unlink(missing_ok=True)
            payload = {**previous, "changed": False}
            _emit(payload, args.json, lambda p: print(f"unchanged; reusing {p['id']}"))
            return exits.OK

    image_size = capture.size_of(scratch)
    frame = frames.new(
        source=source,
        logical_rect=rect,
        image_size=image_size,
        image_path=scratch,  # replaced below once the id exists
        native_size=native,
        digest_native=digest,
        monitor={"name": monitor["name"], "scale": monitor["scale"]},
        profile=args.profile,
        cursor_logical=snap["cursor_logical"],
        window=(
            {
                "address": window["address"],
                "class": window["class"],
                "title": window["title"],
                "logical_rect": window["logical_rect"],
            }
            if window
            else None
        ),
    )
    final = frames.path_for(frame["id"], ".png")
    scratch.replace(final)
    frame["path"] = str(final)
    frames.save(frame)
    frame["changed"] = True

    def render(f: Any) -> None:
        i = f["image_size"]
        print(f"{f['id']}  {f['path']}")
        print(
            f"  depicts {source['type']} {source['name']} "
            f"logical {fmt_rect(f['logical_rect'])}"
        )
        print(
            f"  image {i['w']}x{i['h']} (native {native['w']}x{native['h']}, "
            f"profile {args.profile})"
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
        if candidate.get("source") == source and candidate.get("logical_rect") == rect:
            if candidate["captured_at"] > newest_at:
                newest, newest_at = candidate, candidate["captured_at"]
    return newest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cu", description="Computer use for Hyprland.")
    parser.add_argument("--json", action="store_true", help="emit JSON on stdout")
    sub = parser.add_subparsers(dest="command", required=True)

    # Repeating --json per subcommand accepts both `cu --json observe` and the
    # `cu observe --json` an agent is far more likely to write. SUPPRESS keeps
    # an omitted flag from overwriting the value parsed before the subcommand.
    def add(name: str, help: str, run) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help)
        p.add_argument(
            "--json", action="store_true", default=argparse.SUPPRESS, help="emit JSON"
        )
        p.set_defaults(run=run)
        return p

    add("doctor", "verify every environment precondition", cmd_doctor)
    add("observe", "monitors, windows, focus, cursor", cmd_observe)

    shot = add("shot", "capture a frame", cmd_shot)
    target = shot.add_mutually_exclusive_group()
    target.add_argument(
        "--window",
        metavar="SELECTOR",
        help="'active' (default), a window address, or a class substring",
    )
    target.add_argument("--output", metavar="NAME", help="a whole monitor")
    target.add_argument("--region", metavar="'x,y wxh'", help="an arbitrary strip")
    shot.add_argument(
        "--for",
        dest="profile",
        choices=sorted(capture.PROFILES),
        default="claude",
        help="downscale budget of the consuming model (default: claude)",
    )
    shot.add_argument(
        "--if-changed",
        action="store_true",
        help="reuse the previous frame when the pixels are identical",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except HyprError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.PRECONDITION_FAILED
    except CaptureError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.DEGENERATE_AIM
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
        return exits.OK
    except KeyboardInterrupt:
        return exits.ERROR


if __name__ == "__main__":
    sys.exit(main())
