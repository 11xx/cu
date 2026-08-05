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

from . import checks, exits, observe
from .hypr import HyprError

STATUS_MARK = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL"}


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
        def geom(r: dict[str, float]) -> str:
            return f"{r['w']}x{r['h']}+{r['x']}+{r['y']}"

        for m in s["monitors"]:
            focus = " *" if m["focused"] else "  "
            print(
                f"{focus}{m['name']:<8} scale {m['scale']:<5} "
                f"physical {m['physical']['w']}x{m['physical']['h']}  "
                f"logical {geom(m['logical_rect']):<22} ws {m['active_workspace']}"
            )
        print(f"  desktop  logical {geom(s['desktop_logical_bounds'])}")
        c = s["cursor_logical"]
        print(f"  cursor   logical {c['x']},{c['y']}\n")
        for w in s["windows"]:
            focus = " *" if w["focused"] else "  "
            xw = "xwayland" if w["xwayland"] else ""
            print(
                f"{focus}{w['class']:<18} {geom(w['logical_rect']):<22} "
                f"ws {w['workspace']:<4} {xw:<9} {w['title'][:44]}"
            )

    _emit(snap, args.json, render)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except HyprError as exc:
        print(f"cu: {exc}", file=sys.stderr)
        return exits.PRECONDITION_FAILED
    except BrokenPipeError:
        return exits.OK
    except KeyboardInterrupt:
        return exits.ERROR


if __name__ == "__main__":
    sys.exit(main())
