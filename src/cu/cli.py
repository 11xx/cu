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

from . import actions, capture, checks, config, exits, frames, observe, uinput, wait
from .capture import CaptureError
from .config import ConfigError
from .frames import FrameError
from .hypr import HyprError
from .safety import Refused
from .uinput import UinputError

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


def _parse_at(text: str) -> tuple[float, float]:
    parts = [p for p in text.replace(",", " ").split() if p]
    if len(parts) != 2:
        raise ValueError(f"--at takes 'x,y' in image pixels, got {text!r}")
    return (float(parts[0]), float(parts[1]))


def _render_action(result: dict[str, Any]) -> None:
    mark = "would " if result["dry_run"] else ""
    window = result.get("window")
    where = f"{window['class']}" if window else "no window"
    if result["action"] in {"move", "click"}:
        point = result["logical"]
        detail = f"at image {result['at']['x']:g},{result['at']['y']:g} "
        detail += f"→ logical {point['x']:.0f},{point['y']:.0f}"
        if result["action"] == "click":
            detail = f"{result['button']}x{result['count']} {detail}"
    elif result["action"] == "scroll":
        detail = f"dy {result['dy']} dx {result['dx']}"
    elif result["action"] == "type":
        detail = f"{result['length']} chars via {result['method']}"
        if result["chord"]:
            detail += f" ({result['chord']})"
    else:
        detail = f"{result['semantic'] or 'chord'} → {result['chord']}"
        if result["repeat"] > 1:
            detail += f" x{result['repeat']}"
    print(f"{mark}{result['action']} {detail}  [{where}]")


def cmd_wait(args: argparse.Namespace) -> int:
    try:
        result = wait.until(
            args.condition,
            cls=getattr(args, "class"),
            title=args.title,
            timeout=args.timeout,
        )
    except wait.WaitTimeout as exc:
        print(f"cu: {exc}", file=sys.stderr)
        print(f"cu: {exits.RESPONSE[exits.TIMED_OUT]}", file=sys.stderr)
        return exits.TIMED_OUT

    def render(r: Any) -> None:
        matched = r["matched"]
        how = "waited for" if r["waited"] else "already true:"
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
    result = actions.move(cfg, args.frame, _parse_at(args.at), dry_run=args.dry_run)
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
    result = actions.type_text(
        cfg,
        text,
        method=args.method,
        restore_clipboard=not args.no_restore,
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
        dry_run=args.dry_run,
    )
    _emit(result, args.json, _render_action)
    return exits.OK


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

    waiting = add("wait", "block until a compositor event happens", cmd_wait)
    waiting.add_argument(
        "condition",
        choices=sorted(wait.EVENTS),
        help="what to wait for; checked once against the current state first",
    )
    waiting.add_argument("--class", metavar="SUBSTRING", help="restrict to a window class")
    waiting.add_argument("--title", metavar="SUBSTRING", help="restrict to a window title")
    waiting.add_argument(
        "--timeout", type=float, default=30.0, help="seconds before giving up (default: 30)"
    )

    # Everything below injects. --dry-run runs every interlock and writes the
    # same log entry without emitting a single event, which is what makes an
    # agent's plan inspectable before it reaches the desktop.
    def add_injecting(name: str, help: str, run) -> argparse.ArgumentParser:
        p = add(name, help, run)
        p.add_argument(
            "--dry-run",
            action="store_true",
            help="check and log the action without injecting it",
        )
        return p

    def add_aim(p: argparse.ArgumentParser, required: bool) -> None:
        p.add_argument("--frame", metavar="ID", required=required, help="frame the coordinate came from")
        p.add_argument("--at", metavar="X,Y", required=required, help="coordinate in image pixels")

    focus = add_injecting("focus", "give a window keyboard focus", cmd_focus)
    focus.add_argument(
        "selector",
        help="'active', a window address, or a class substring",
    )

    move = add_injecting("move", "warp the pointer to a frame coordinate", cmd_move)
    add_aim(move, required=True)

    click = add_injecting("click", "warp, assert focus, press and release", cmd_click)
    add_aim(click, required=True)
    click.add_argument(
        "--button", choices=sorted(uinput.BUTTONS), default="left", help="which button"
    )
    click.add_argument("--count", type=int, default=1, help="click count (2 for double)")

    scroll = add_injecting("scroll", "wheel events, optionally aimed first", cmd_scroll)
    add_aim(scroll, required=False)
    scroll.add_argument("--dy", type=int, default=0, help="vertical detents, negative scrolls down")
    scroll.add_argument("--dx", type=int, default=0, help="horizontal detents")

    typing = add_injecting("type", "enter text into the focused window", cmd_type)
    typing.add_argument("text", help="the text, or '-' to read stdin")
    typing.add_argument(
        "--method",
        choices=("clipboard", "keys"),
        default="clipboard",
        help="clipboard is layout-independent; keys synthesizes each character",
    )
    typing.add_argument(
        "--no-restore",
        action="store_true",
        help="leave the text on the clipboard instead of restoring what was there",
    )

    key = add_injecting("key", "a semantic action, or a literal chord", cmd_key)
    target = key.add_mutually_exclusive_group(required=True)
    target.add_argument("--action", metavar="NAME", help="a semantic action, resolved per application")
    target.add_argument("--chord", metavar="CHORD", help="a literal chord such as 'ctrl+alt+shift+f4'")
    key.add_argument("--repeat", type=int, default=1, help="how many times to send it")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except Refused as exc:
        # An interlock, not a fault. The code is what the caller acts on, so
        # the response the taxonomy prescribes is printed alongside it.
        print(f"cu: refused: {exc.reason}", file=sys.stderr)
        print(f"cu: {exits.RESPONSE[exc.code]}", file=sys.stderr)
        return exc.code
    except UinputError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.PRECONDITION_FAILED
    except ConfigError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.PRECONDITION_FAILED
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
