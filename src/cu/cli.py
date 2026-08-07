"""Command dispatch.

Every subcommand prints JSON on `--json` and something readable otherwise, and
signals outcome through the codes in `exits` rather than through prose on
stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

from . import (
    actions,
    browser,
    capture,
    checks,
    config,
    exits,
    frames,
    knowledge,
    observe,
    recipes,
    safety,
    sandbox,
    status,
    uinput,
    wait,
)
from .actions import ClipboardError
from .browser import BrowserError
from .capture import CaptureError
from .compositor import QuietError
from .config import ConfigError
from .frames import FrameError
from .hypr import HyprError
from .knowledge import KnowledgeError
from .recipes import RecipeError
from .safety import Refused
from .sandbox import SandboxError
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

    if args.landmark and (args.output or args.region):
        raise ValueError(
            "--landmark aims within a window; it cannot combine with --output or --region"
        )

    if args.output and not args.landmark:
        monitor = next((m for m in monitors if m["name"] == args.output), None)
        if monitor is None:
            print(f"cu: no such output: {args.output}", file=sys.stderr)
            return exits.TARGET_NOT_FOUND
        rect = monitor["logical_rect"]
        source = {"type": "output", "name": monitor["name"]}
    elif args.region and not args.landmark:
        rect = _parse_region(args.region)
        monitor = capture.sole_monitor(rect, monitors)
        rect = capture.clamp(rect, monitor["logical_rect"])
        source = {"type": "region", "name": monitor["name"]}
    elif args.landmark:
        # A landmark aims the capture and nothing else. It resolves against the
        # window's geometry *now*, so a moved window yields a correctly-aimed
        # strip rather than a confidently wrong one, and the model still reads
        # the pixels before anything acts on them.
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
        rect = knowledge.to_logical(mark, window)
        monitor = capture.sole_monitor(rect, monitors)
        rect = capture.clamp(rect, monitor["logical_rect"])
        source = {"type": "landmark", "name": f"{app}/{name}"}
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
            # The pixels were just re-read and match, which is the evidence
            # staleness is a proxy for. Keeping the original timestamp would
            # let a frame proven current a moment ago be refused as too old.
            previous["captured_at"] = time.time()
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
        entries = _read_log(cfg, args.last)
        recipe = recipes.from_log(entries, args.name, args.site or "", args.task or "")
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
        return exits.OK

    return exits.USAGE


def _verdict(verified: bool | None) -> str:
    """Three outcomes, because "not stated" is not the same as "failed"."""
    return {True: "passed", False: "failed", None: "not stated"}[verified]


def _describe_step(step: dict[str, Any]) -> str:
    action = step["action"]
    if action in {"click", "move"}:
        rel = step["relative"]
        pressed = f"{step.get('button', 'left')} x{step.get('count', 1)}"
        how = action if action == "move" else pressed
        return f"{how} at {rel['x']:.3f},{rel['y']:.3f} of {step['window_class']}"
    if action == "scroll":
        return f"scroll dy {step.get('dy',0)} dx {step.get('dx',0)}"
    if action == "type":
        return (
            f"type parameter {step.get('param')!r} "
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
                print(
                    f"  {key:<28} {rect['x']:.3f},{rect['y']:.3f} "
                    f"{rect['w']:.3f}x{rect['h']:.3f}  {seen:<8} {mark.get('note', '')}"
                )
            for note in r.get("notes", []):
                scope = f"[{note['context']}] " if note.get("context") else ""
                print(f"  note: {scope}{note['text']}")

        _emit(record, args.json, render)
        return exits.OK

    if args.subcommand == "set":
        rect = _parse_landmark_rect(args.rect)
        mark = knowledge.set_landmark(
            args.app, args.name, rect, context=args.context, note=args.note or ""
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


def _parse_landmark_rect(text: str) -> dict[str, float]:
    """Accept a point or a box, both as fractions of the window."""
    parts = [float(p) for p in text.replace(",", " ").split() if p]
    if len(parts) == 2:
        # A point becomes a small box, because a capture needs an area and a
        # landmark is a place to look rather than a pixel to hit.
        x, y = parts
        return {"x": max(x - 0.06, 0.0), "y": max(y - 0.03, 0.0), "w": 0.12, "h": 0.06}
    if len(parts) == 4:
        x, y, w, h = parts
        return {"x": x, "y": y, "w": w, "h": h}
    raise ValueError(f"--rect takes 'x,y' or 'x,y,w,h' as fractions, got {text!r}")


def cmd_browser(args: argparse.Namespace) -> int:
    cfg = config.load()
    browsers = browser.discover()

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
        window = None
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
        payload = {"browser": target["name"], "family": target["family"], "actions": keymap}

        def render(data: Any) -> None:
            print(f"{data['browser']} ({data['family']})")
            if not data["actions"]:
                print("  no actions resolvable — this browser keeps no readable keymap;")
                print("  state them under [keys] in cu's config to drive it")
            for action, chord in sorted(data["actions"].items()):
                print(f"  {action:<18} {chord}")

        _emit(payload, args.json, render)
        return exits.OK

    chord, _source = browser.resolve(target, args.action, cfg)
    if chord is None:
        known = sorted(browser.keymap_for(target)) or ["none"]
        print(
            f"cu: {target['name']} has no binding for {args.action!r}. "
            f"Known: {', '.join(known)}.",
            file=sys.stderr,
        )
        print(f"cu: {exits.RESPONSE[exits.NOT_MAPPED]}", file=sys.stderr)
        return exits.NOT_MAPPED

    if window is None:
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
            f"{r['chord']}  [{r['browser']}, via {r['resolved_from']}]"
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
        backend=args.backend,
        verify=args.verify,
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
    """What cu is doing, for whatever shows the user things.

    Prints nothing at all when idle, because that is what a status bar wants to
    render for idle — a module whose command outputs an empty string takes up
    no room.
    """
    record = status.read()
    _emit(record, args.json, lambda r: print(status.describe(r)) if r.get("active") else None)
    return exits.OK


def cmd_session(args: argparse.Namespace) -> int:
    """Bracket a stretch of work, so the indicator does not blink between steps.

    A single action's record expires on its own, which is what keeps a killed
    process from claiming the desktop forever. But a model deciding what to do
    next routinely takes longer than that, and an indicator that clears while
    the agent is still working reads as "it finished" at the moment the user
    most wants the truth.
    """
    if args.subcommand == "begin":
        record = status.begin(args.detail, pid=args.pid)
        _emit(record, args.json, lambda r: print(f"working: {r['detail']}"))
        return exits.OK
    record = status.end()
    _emit(record, args.json, lambda _r: print("idle"))
    return exits.OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cu", description="Computer use for Hyprland.")
    parser.add_argument("--json", action="store_true", help="emit JSON on stdout")
    parser.add_argument("--why", metavar="TEXT", default="", help="what this is for")
    sub = parser.add_subparsers(dest="command", required=True)

    # Repeating --json per subcommand accepts both `cu --json observe` and the
    # `cu observe --json` an agent is far more likely to write. SUPPRESS keeps
    # an omitted flag from overwriting the value parsed before the subcommand.
    def add(name: str, help: str, run) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help)
        p.add_argument(
            "--json", action="store_true", default=argparse.SUPPRESS, help="emit JSON"
        )
        # What the user sees while this runs. A command name says an agent is
        # acting; a sentence says what it is trying to do, which is the part
        # worth interrupting over or leaving alone.
        p.add_argument(
            "--why",
            metavar="TEXT",
            default=argparse.SUPPRESS,
            help="a short description of the intent, shown to the user",
        )
        p.set_defaults(run=run)
        return p

    add("status", "what cu is doing, for a status bar to poll", cmd_status)

    session = add("session", "bracket a stretch of work as one activity", cmd_session)
    ssub2 = session.add_subparsers(dest="subcommand", required=True)
    begin = ssub2.add_parser("begin", help="say an agent has started working")
    begin.add_argument("detail", help="a short description the user will see")
    begin.add_argument(
        "--pid", type=int,
        help="end the session when this process does; omit if there is none",
    )
    begin.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    ended = ssub2.add_parser("end", help="say it has stopped")
    ended.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

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
    # --landmark is deliberately outside the mutually exclusive group: it names
    # *what region* while --window names *which window*, so they compose. With
    # several windows of one class open, that pairing is the only way to say
    # which one the landmark applies to.
    shot.add_argument(
        "--landmark",
        metavar="APP/NAME",
        help="a remembered region of a window; aims the capture, never a click",
    )
    shot.add_argument("--context", metavar="NAME", help="scope a landmark, e.g. a site")
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

    sbox = add("sandbox", "create and remove headless outputs, explicitly", cmd_sandbox)
    ssub = sbox.add_subparsers(dest="subcommand", required=True)
    for name, help_text in (
        ("create", "add a headless output nobody can see"),
        ("list", "headless outputs that exist"),
        ("remove", "take one away; refuses anything but a headless output"),
    ):
        sp = ssub.add_parser(name, help=help_text)
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        if name == "remove":
            sp.add_argument("name", nargs="?", help="which output")
            sp.add_argument("--all", action="store_true", help="every headless output")

    know = add("know", "what past runs learned about an application", cmd_know)
    ksub = know.add_subparsers(dest="subcommand", required=True)
    for name, help_text in (
        ("list", "applications with stored knowledge"),
        ("show", "landmarks and notes for one application"),
        ("set", "record where something is, as a fraction of the window"),
        ("note", "record a fact that has no coordinate"),
        ("confirm", "the landmark pointed at the right thing"),
        ("miss", "it did not"),
        ("forget", "drop a landmark, or everything for an application"),
    ):
        kp = ksub.add_parser(name, help=help_text)
        kp.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        if name != "list":
            kp.add_argument("app", help="window class, or any name you key it by")
        if name in {"set", "confirm", "miss"}:
            kp.add_argument("name", help="what the landmark is called")
        if name == "forget":
            kp.add_argument("name", nargs="?", help="omit to forget the application")
        if name == "set":
            kp.add_argument("--rect", required=True, metavar="X,Y[,W,H]",
                            help="fractions of the window")
            kp.add_argument("--note", help="what it is")
        if name == "note":
            kp.add_argument("text", help="the fact worth keeping")
        kp.add_argument("--context", metavar="NAME", help="scope, e.g. a site")

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
        p.add_argument(
            "--frame", metavar="ID", required=required,
            help="frame the coordinate came from",
        )
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
        "--verify",
        action="store_true",
        help="read the field back and confirm the text arrived intact",
    )
    typing.add_argument(
        "--backend",
        choices=("uinput", "compositor"),
        help="compositor injection leaves sleeping monitors asleep",
    )
    typing.add_argument(
        "--no-restore",
        action="store_true",
        help="leave the text on the clipboard instead of restoring what was there",
    )

    key = add_injecting("key", "a semantic action, or a literal chord", cmd_key)
    target = key.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--action", metavar="NAME", help="a semantic action, resolved per application"
    )
    target.add_argument(
        "--chord", metavar="CHORD", help="a literal chord such as 'ctrl+alt+shift+f4'"
    )
    key.add_argument("--repeat", type=int, default=1, help="how many times to send it")
    key.add_argument(
        "--backend",
        choices=("uinput", "compositor"),
        help="compositor injection leaves sleeping monitors asleep",
    )

    recipe = add_injecting("recipe", "save, review, and replay a procedure", cmd_recipe)
    rsub = recipe.add_subparsers(dest="subcommand", required=True)

    def add_recipe(name: str, help_text: str) -> argparse.ArgumentParser:
        """Nested subcommands repeat the global flags for the same reason the
        top level does: `cu recipe list --json` is the form an agent writes,
        and argparse accepts a flag only on the parser that declares it."""
        p = rsub.add_parser(name, help=help_text)
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        p.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
        return p

    add_recipe("list", "every stored recipe and whether it is reviewed")
    rsave = add_recipe("save", "promote recent actions from the log")
    rsave.add_argument("name", help="lowercase name; it becomes the filename")
    rsave.add_argument("--last", type=int, default=20, help="how many log entries to promote")
    rsave.add_argument("--site", help="what site this is for")
    rsave.add_argument("--task", help="what it accomplishes")
    for name, help_text in (
        ("show", "print every step"),
        ("review", "read the steps, and approve them with --approve"),
        ("replay", "run a reviewed recipe"),
    ):
        sub_parser = add_recipe(name, help_text)
        sub_parser.add_argument("name")
        if name == "review":
            sub_parser.add_argument(
                "--approve", action="store_true", help="record that you have read every step"
            )
        if name == "replay":
            sub_parser.add_argument(
                "--param", action="append", metavar="NAME=VALUE",
                help="supply a typed value; recipes never store the text itself",
            )

    browsing = add_injecting(
        "browser", "a semantic browser action, resolved per browser", cmd_browser
    )
    browsing.add_argument(
        "action",
        help="an action such as focus-urlbar or close-tab; "
        "'list' shows installed browsers and 'actions' shows what one resolves",
    )
    browsing.add_argument(
        "--browser", metavar="NAME", help="which browser (default: the focused window's)"
    )
    browsing.add_argument(
        "--backend",
        choices=("uinput", "compositor"),
        help="compositor injection leaves sleeping monitors asleep",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Published before the command runs rather than after, because the point is
    # to say what is happening while it happens. `status` is excluded: a reader
    # asking what is going on is not itself something going on.
    if args.command not in {"status", "session"}:
        status.publish(args.command, detail=getattr(args, "why", "") or "")
    try:
        return args.run(args)
    except Refused as exc:
        # An interlock, not a fault. The code is what the caller acts on, so
        # the response the taxonomy prescribes is printed alongside it.
        print(f"cu: refused: {exc.reason}", file=sys.stderr)
        print(f"cu: {exits.RESPONSE[exc.code]}", file=sys.stderr)
        return exc.code
    except (KnowledgeError, SandboxError) as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.TARGET_NOT_FOUND
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
    except CaptureError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.DEGENERATE_AIM
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
    except RuntimeError as exc:
        # Anything this taxonomy has not been taught. A traceback would leave
        # the caller with no code to branch on, which for a CLI whose contract
        # *is* its exit codes is worse than an unhelpful one.
        print(f"cu: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"cu: {exits.RESPONSE[exits.ERROR]}", file=sys.stderr)
        return exits.ERROR


if __name__ == "__main__":
    sys.exit(main())
