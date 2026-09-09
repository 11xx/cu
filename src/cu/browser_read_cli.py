"""CLI boundary for optional, explicitly granted browser evidence."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

from . import browser_read as transport
from . import exits
from .safety import Refused


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="web_command", required=True)
    for name, description in (
        (
            "setup",
            "Install the Firefox native-host manifest; does not install or grant the extension",
        ),
        ("hosts", "List live browser read helpers and their explicit tab grants"),
        ("read", "Read bounded page evidence; never aim or inject input"),
    ):
        child = commands.add_parser(name, help=description, description=description)
        child.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        if name == "read":
            child.add_argument("--helper", help="Socket path from hosts; required if several exist")
            child.add_argument("--tab", type=int, help="Granted browser tab ID")
            child.add_argument("--document", help="Expected document ID; mismatch refuses")
            child.add_argument("--generation", type=int, help="Expected observation generation")
            child.add_argument("--query", help="Case-insensitive text/name search")
            child.add_argument("--role", help="Filter approximate semantic role")
            child.add_argument("--page", type=int, default=0)
            child.add_argument("--limit", type=int, default=20)
            child.add_argument(
                "--values", action="store_true", help="Include nonsecret field values"
            )
            child.add_argument("--timeout", type=float, default=transport.DEFAULT_TIMEOUT)


def install_host() -> dict:
    data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    launcher = data / "cu" / "browser-native-host"
    if not launcher.is_absolute():
        raise ValueError("XDG_DATA_HOME must be absolute")
    manifest = Path.home() / ".mozilla/native-messaging-hosts/org.cu.read.json"
    script = (
        "#!/bin/sh\n# CU Firefox read host\n"
        f"exec {shlex.quote(sys.executable)} -m cu.browser_read host\n"
    )
    definition = {
        "name": "org.cu.read",
        "description": "CU passive browser read bridge",
        "path": str(launcher),
        "type": "stdio",
        "allowed_extensions": ["cu-browser-read@local"],
    }
    files = [(launcher, script, 0o700), (manifest, json.dumps(definition, indent=2) + "\n", 0o600)]
    for path, content, _ in files:
        if path.exists() and (path.is_symlink() or path.read_text() != content):
            raise Refused(
                exits.BROWSER_READ_REFUSED, f"will not overwrite existing host setup: {path}"
            )
    for path, content, mode in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with path.open("x") as handle:
                handle.write(content)
        path.chmod(mode)
    return {
        "manifest": str(manifest),
        "launcher": str(launcher),
        "extension_installed": False,
        "tab_granted": False,
    }


def _read(args: argparse.Namespace) -> dict:
    try:
        payload = transport.validate_payload(
            "query",
            {
                "search": args.query or "",
                "role": args.role,
                "page": args.page,
                "page_size": args.limit,
                "include_values": args.values,
            },
        )
    except transport.ProtocolError as exc:
        raise ValueError(str(exc)) from exc
    paths = [Path(args.helper)] if args.helper else transport.discover_socket_paths()
    if not paths:
        raise transport.StaleHelper("enable a tab in the Firefox CU extension first")
    if len(paths) != 1:
        raise Refused(exits.BROWSER_READ_AMBIGUOUS, "use web hosts and select --helper")
    with transport.BrowserReadClient.connect(paths[0], args.timeout) as client:
        grants = [
            g for g in client.metadata["grants"] if args.tab is None or g["tab_id"] == args.tab
        ]
        if not grants:
            raise Refused(exits.BROWSER_READ_REFUSED, "the requested tab has no read grant")
        if len(grants) != 1:
            raise Refused(exits.BROWSER_READ_AMBIGUOUS, "select one granted --tab")
        grant = grants[0]
        if args.document is not None and args.document != grant["document_id"]:
            raise Refused(exits.BROWSER_DOCUMENT_STALE, "the granted document changed")
        identity = {key: grant[key] for key in ("session", "tab_id", "document_id")}
        result = client.request(
            "query",
            **identity,
            generation=args.generation,
            payload=payload,
        )
    if not result["ok"]:
        reason = result["error"]["code"]
        code = (
            exits.BROWSER_DOCUMENT_STALE
            if reason in {"stale-document", "stale-generation"}
            else exits.BROWSER_READ_BUDGET
            if reason == "observation-too-large"
            else exits.BROWSER_READ_REFUSED
        )
        raise Refused(code, f"browser read refused: {reason}")
    return {"evidence_only": True, "untrusted_page_content": True, **result}


def run(args: argparse.Namespace) -> int:
    try:
        if args.web_command == "setup":
            result = install_host()
        elif args.web_command == "hosts":
            paths = transport.discover_socket_paths()
            helpers = []
            for path in paths[:16]:
                try:
                    with transport.BrowserReadClient.connect(path, 0.2) as client:
                        helpers.append(client.metadata)
                except transport.BrowserReadError:
                    helpers.append({"socket": str(path), "state": "unavailable"})
            result = {"helpers": helpers, "truncated": len(paths) > 16}
        else:
            result = _read(args)
        print(json.dumps(result, indent=None if args.json else 2))
        return exits.OK
    except transport.BrowserReadTimeout as exc:
        raise Refused(exits.BROWSER_READ_TIMEOUT, str(exc)) from exc
    except (transport.StaleHelper, transport.HelperUnavailable) as exc:
        raise Refused(exits.BROWSER_READ_UNAVAILABLE, str(exc)) from exc
    except transport.OversizeMessage as exc:
        raise Refused(exits.BROWSER_READ_BUDGET, str(exc)) from exc
    except transport.ProtocolError as exc:
        raise Refused(exits.BROWSER_READ_PROTOCOL, str(exc)) from exc
