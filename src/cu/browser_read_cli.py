"""CLI boundary for optional Firefox browser evidence and interaction."""

from __future__ import annotations

import argparse
import base64
import binascii
import fcntl
import json
import math
import os
import secrets
import shlex
import stat
import struct
import sys
from pathlib import Path
from typing import Any

from . import browser, config, exits, observe, png, safety
from . import browser_read as transport
from .safety import Refused


def _common(child: argparse.ArgumentParser) -> None:
    child.add_argument("--helper", help="Socket path from hosts; required if several exist")
    child.add_argument("--tab", type=int, help="Granted browser tab ID")
    child.add_argument("--document", help="Expected document ID; mismatch refuses")
    child.add_argument("--generation", type=int, help="Expected observation generation")
    child.add_argument("--timeout", type=float, default=transport.DEFAULT_TIMEOUT)


def _context(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session", default=argparse.SUPPRESS, help="CU driving session ID")
    parser.add_argument("--why", default=argparse.SUPPRESS, metavar="TEXT", help="What this is for")


#: Derived from the public key in browser-extension/chromium/manifest.json, which
#: pins the unpacked extension's ID; a test holds the two together.
CHROMIUM_EXTENSION_ID = "eneedbaolcjmmpmfjohalmpbfhlmfoip"
CHROMIUM_SETUP = tuple(
    product for product, definition in browser.CHROMIUM_PRODUCTS.items()
    if definition.get("native_host")
)


def configure(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="web_command", required=True)
    setup = commands.add_parser(
        "setup",
        help="Register the CU native host for one browser; it grants no tab access",
        description="Register the CU native host for one browser; it grants no tab access",
    )
    setup.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    setup.add_argument(
        "--browser",
        choices=("firefox", *CHROMIUM_SETUP),
        default="firefox",
        help="Whose native-messaging lookup to register in",
    )
    setup.add_argument(
        "--user-data-dir",
        metavar="PATH",
        help="Chromium-family profile root; defaults to the product's only existing one",
    )
    hosts = commands.add_parser(
        "hosts",
        help="List live CU Browser helpers and their explicit tab grants",
        description="List live CU Browser helpers and their explicit tab grants",
    )
    hosts.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    grant = commands.add_parser(
        "grant",
        help="Bind a browser tab to this CU session; no browser click is involved",
        description="Bind a browser tab to this CU session; no browser click is involved",
    )
    grant.add_argument("--helper", help="Socket path from hosts; required if several exist")
    grant.add_argument("--tab", type=int, help="Browser tab ID; defaults to the active tab")
    grant.add_argument("--read-only", action="store_true", help="Bind for reads and waits only")
    grant.add_argument(
        "--cursor",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Show the amber browser cursor; defaults to the extension's setting",
    )
    grant.add_argument("--timeout", type=float, default=transport.DEFAULT_TIMEOUT)
    grant.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    stop = commands.add_parser(
        "stop",
        help="Release the bound browser tab, cancelling its pending work",
        description="Release the bound browser tab, cancelling its pending work",
    )
    stop.add_argument("--helper", help="Socket path from hosts; required if several exist")
    stop.add_argument("--tab", type=int, help="Bound tab ID; defaults to the current binding")
    stop.add_argument("--timeout", type=float, default=transport.DEFAULT_TIMEOUT)
    stop.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    read = commands.add_parser(
        "read",
        help="Read bounded page evidence from an explicitly granted tab",
        description="Read bounded page evidence from an explicitly granted tab",
    )
    read.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    _common(read)
    read.add_argument("--query", help="Case-insensitive text/name search")
    read.add_argument("--role", help="Filter approximate semantic role")
    read.add_argument("--selector", help="Bounded CSS selector scope")
    read.add_argument(
        "--details", action="store_true", help="Include bounded links and candidate time labels"
    )
    read.add_argument("--page", type=int, default=0)
    read.add_argument("--limit", type=int, default=20)
    read.add_argument("--values", action="store_true", help="Include nonsecret field values")

    action = commands.add_parser(
        "action",
        help="Interact with an explicitly granted active browser tab",
        description="Interact with an explicitly granted active browser tab",
    )
    action.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    _common(action)
    action.set_defaults(injects=True)
    action.add_argument("action", choices=sorted(transport.ACTION_OPERATIONS - {"capture"}))
    action.add_argument("--ref", help="Stable ref from a prior observation")
    action.add_argument(
        "--value", help="Replacement value for fill or exact option value for select"
    )
    action.add_argument("--label", help="Visible option label for select")
    action.add_argument("--target", choices=("document", "element"), default="element")
    action.add_argument("--dx", type=int, default=0)
    action.add_argument("--dy", type=int, default=0)

    wait = commands.add_parser(
        "wait",
        help="Wait for a fresh bounded DOM or media condition",
        description="Wait for a fresh bounded DOM or media condition",
    )
    wait.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    _common(wait)
    wait.set_defaults(timeout=12.0)
    targets = wait.add_mutually_exclusive_group(required=True)
    targets.add_argument("--ref", help="Stable ref from a prior observation")
    targets.add_argument("--selector", help="Bounded CSS selector to re-evaluate")
    wait.add_argument(
        "--state",
        choices=("present", "visible", "enabled", "text", "value", "playing", "paused", "ended"),
        required=True,
    )
    wait.add_argument("--text")
    wait.add_argument("--value")
    wait.add_argument("--timeout-ms", type=int, default=transport.MAX_WAIT_MS)
    wait.add_argument("--poll-ms", type=int, default=50)

    capture = commands.add_parser(
        "capture",
        help="Capture the explicitly granted active tab viewport as PNG",
        description="Capture the explicitly granted active tab viewport as PNG",
    )
    capture.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    _common(capture)
    capture.add_argument(
        "--region",
        help="CSS viewport rectangle as 'x,y wxh' or 'x,y,w,h'; never a desktop coordinate",
    )
    for child in commands.choices.values():
        _context(child)


def _chromium_profile_root(product: str, user_data_dir: str | None) -> Path:
    if user_data_dir is None:
        existing = [
            Path(profile).expanduser()
            for profile in browser.CHROMIUM_PRODUCTS[product]["profiles"]
            if Path(profile).expanduser().is_dir()
        ]
        if len(existing) != 1:
            raise Refused(
                exits.BROWSER_READ_AMBIGUOUS if existing else exits.BROWSER_READ_REFUSED,
                "select the browser profile root with --user-data-dir",
            )
        return existing[0]
    root = Path(user_data_dir).expanduser()
    if not root.is_absolute():
        raise ValueError("--user-data-dir must be an absolute path")
    if not root.is_dir():
        raise Refused(
            exits.BROWSER_READ_REFUSED,
            f"no browser profile root at {root}; setup never creates one",
        )
    return root


def install_host(browser_name: str = "firefox", user_data_dir: str | None = None) -> dict[str, Any]:
    data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    launcher = data / "cu" / "browser-native-host"
    if not launcher.is_absolute():
        raise ValueError("XDG_DATA_HOME must be absolute")
    script = (
        "#!/bin/sh\n# CU Firefox read host\n"
        f"exec {shlex.quote(sys.executable)} -m cu.browser_read host\n"
    )
    if browser_name == "firefox":
        if user_data_dir is not None:
            raise ValueError("--user-data-dir applies only to Chromium-family browsers")
        manifest = Path.home() / ".mozilla/native-messaging-hosts/org.cu.read.json"
        extension = "cu-browser-read@local"
        definition = {
            "name": "org.cu.read",
            "description": "CU Firefox browser bridge",
            "path": str(launcher),
            "type": "stdio",
            "allowed_extensions": [extension],
        }
    else:
        manifest = (
            _chromium_profile_root(browser_name, user_data_dir)
            / "NativeMessagingHosts"
            / "org.cu.read.json"
        )
        extension = CHROMIUM_EXTENSION_ID
        definition = {
            "name": "org.cu.read",
            "description": "CU Chromium browser bridge",
            "path": str(launcher),
            "type": "stdio",
            "allowed_origins": [f"chrome-extension://{extension}/"],
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
        "browser": browser_name,
        "manifest": str(manifest),
        "launcher": str(launcher),
        "extension_id": extension,
        "extension_installed": False,
        "tab_granted": False,
    }


def _paths(args: argparse.Namespace) -> list[Path]:
    paths = [Path(args.helper)] if args.helper else transport.discover_socket_paths()
    if not paths:
        raise transport.StaleHelper("no CU Browser helper is running; load the extension")
    if len(paths) != 1:
        raise Refused(exits.BROWSER_READ_AMBIGUOUS, "use web hosts and select --helper")
    return paths


def _grant(args: argparse.Namespace, client: transport.BrowserReadClient) -> dict[str, Any]:
    grants = [
        grant
        for grant in client.metadata["grants"]
        if args.tab is None or grant["tab_id"] == args.tab
    ]
    if not grants:
        raise Refused(
            exits.BROWSER_READ_REFUSED, "the requested tab has no browser grant; use cu web grant"
        )
    if len(grants) != 1:
        raise Refused(exits.BROWSER_READ_AMBIGUOUS, "select one granted --tab")
    grant = grants[0]
    if args.document is not None and args.document != grant["document_id"]:
        raise Refused(exits.BROWSER_DOCUMENT_STALE, "the granted document changed")
    return grant


def _identity(grant: dict[str, Any]) -> dict[str, Any]:
    return {key: grant[key] for key in ("session", "tab_id", "document_id")}


def _check_response(result: dict[str, Any]) -> dict[str, Any]:
    if result["ok"]:
        return result
    reason = result["error"]["code"]
    code = (
        exits.BROWSER_DOCUMENT_STALE
        if reason in {"stale-document", "stale-generation", "stale-element"}
        else exits.BROWSER_ACTION_UNCERTAIN
        if reason == "action-uncertain"
        else exits.BROWSER_READ_BUDGET
        if reason in {"capture-budget", "observation-too-large"}
        else exits.BROWSER_READ_PROTOCOL
        if reason in {"capture-format", "capture-scale-mismatch", "capture-identity-mismatch"}
        else exits.BROWSER_READ_TIMEOUT
        if reason in {"wait-timeout", "cancelled"}
        else exits.BROWSER_READ_REFUSED
    )
    detail = f"browser request refused: {reason}"
    if result["error"].get("state") == "grant-revoked":
        detail += "; the grant is revoked; bind the tab again with cu web grant"
    raise Refused(code, detail)


def _parse_region(text: str) -> dict[str, float | str]:
    cleaned = text.replace("x", " ").replace(",", " ")
    parts = cleaned.split()
    if len(parts) != 4:
        raise ValueError(f"browser region must be 'x,y wxh' or 'x,y,w,h', got {text!r}")
    x, y, width, height = (float(part) for part in parts)
    if not all(math.isfinite(value) for value in (x, y, width, height)):
        raise ValueError("browser region coordinates and dimensions must be finite")
    if width <= 0 or height <= 0:
        raise ValueError("browser region width and height must be positive")
    return {"x": x, "y": y, "width": width, "height": height, "space": "css-viewport"}


def _read(args: argparse.Namespace) -> dict[str, Any]:
    payload = transport.validate_payload(
        "query",
        {
            "search": args.query or "",
            "role": args.role,
            "page": args.page,
            "page_size": args.limit,
            "include_values": args.values,
            "selector": args.selector,
            "include_details": args.details,
        },
    )
    with transport.BrowserReadClient.connect(_paths(args)[0], args.timeout) as client:
        grant = _grant(args, client)
        result = client.request(
            "query", **_identity(grant), generation=args.generation, payload=payload
        )
    _check_response(result)
    return {"evidence_only": True, "untrusted_page_content": True, **result}


def _action(args: argparse.Namespace) -> dict[str, Any]:
    if args.document is None or args.generation is None:
        raise Refused(
            exits.BROWSER_READ_REFUSED,
            "browser actions require --document and --generation from a prior observation",
        )
    safety.check_abort()
    safety.check_session()
    safety.check_denylist(config.load(), observe.focused())
    payload: dict[str, Any]
    if args.action in {"click", "focus", "hover"}:
        if not args.ref:
            raise ValueError(f"{args.action} requires --ref")
        payload = {"ref": args.ref}
    elif args.action == "select":
        if not args.ref:
            raise ValueError("select requires --ref")
        if (args.value is None) == (args.label is None):
            raise ValueError("select requires exactly one of --value or --label")
        payload = {"ref": args.ref}
        payload["value" if args.value is not None else "label"] = (
            args.value if args.value is not None else args.label
        )
    elif args.action == "fill":
        if args.ref is None or args.value is None:
            raise ValueError("fill requires --ref and --value")
        payload = {"ref": args.ref, "value": args.value}
    elif args.action == "reload":
        if args.ref or args.value is not None or args.label is not None or args.dx or args.dy:
            raise ValueError("reload names no element or value; it reloads the granted tab")
        payload = {}
    else:
        if args.target == "element" and not args.ref:
            raise ValueError("element scroll requires --ref")
        if args.target == "document" and args.ref:
            raise ValueError("document scroll does not accept --ref")
        payload = {
            "target": args.target,
            "dx": args.dx,
            "dy": args.dy,
            **({"ref": args.ref} if args.ref else {}),
        }
    with transport.BrowserReadClient.connect(_paths(args)[0], args.timeout) as client:
        grant = _grant(args, client)
        if "interact" not in grant.get("capabilities", ["read"]):
            raise Refused(
                exits.BROWSER_READ_REFUSED,
                "the current browser grant is read-only; bind the tab again with cu web grant",
            )
        result = client.action(
            args.action,
            **_identity(grant),
            generation=args.generation,
            payload=payload,
            timeout=args.timeout,
        )
    _check_response(result)
    receipt = result["result"]
    safety.record(
        config.load(),
        {
            "action": f"browser-{args.action}",
            "backend": receipt.get("backend"),
            "verification": receipt.get("verification"),
            "tab_id": grant["tab_id"],
            "document_id": grant["document_id"],
        },
    )
    return {"browser_action": True, "dispatch_completed": True, **result}


def _bind(args: argparse.Namespace) -> dict[str, Any]:
    safety.check_abort()
    safety.check_session()
    capabilities = ["read"] if args.read_only else ["read", "interact"]
    with transport.BrowserReadClient.connect(_paths(args)[0], args.timeout) as client:
        result = client.control(
            "grant",
            tab_id=args.tab,
            capabilities=capabilities,
            cursor=args.cursor,
            timeout=args.timeout,
        )
    _check_response(result)
    grant = result["result"]["grant"]
    safety.record(
        config.load(),
        {
            "action": "browser-grant",
            "tab_id": grant["tab_id"],
            "document_id": grant["document_id"],
            "capabilities": grant["capabilities"],
        },
    )
    return {"browser_grant": True, **result}


def _release(args: argparse.Namespace) -> dict[str, Any]:
    with transport.BrowserReadClient.connect(_paths(args)[0], args.timeout) as client:
        result = client.control("release", tab_id=args.tab, timeout=args.timeout)
    _check_response(result)
    revoked = result["result"]["revoked_grant"]
    safety.record(
        config.load(),
        {
            "action": "browser-stop",
            "tab_id": revoked["tab_id"],
            "document_id": revoked["document_id"],
        },
    )
    return {"browser_grant_released": True, **result}


def _wait(args: argparse.Namespace) -> dict[str, Any]:
    if args.document is None:
        raise Refused(
            exits.BROWSER_READ_REFUSED, "browser waits require --document from a prior observation"
        )
    payload = {
        "ref": args.ref,
        "selector": args.selector,
        "state": args.state,
        "text": args.text,
        "value": args.value,
        "timeout_ms": args.timeout_ms,
        "poll_ms": args.poll_ms,
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    transport.validate_payload("wait", payload)
    with transport.BrowserReadClient.connect(_paths(args)[0], args.timeout) as client:
        grant = _grant(args, client)
        result = client.wait(
            **_identity(grant), generation=args.generation, payload=payload, timeout=args.timeout
        )
    _check_response(result)
    return {"evidence_only": True, "untrusted_page_content": True, **result}


def _save_capture(data: bytes) -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime or not Path(runtime).is_absolute():
        raise Refused(exits.PRECONDITION_FAILED, "XDG_RUNTIME_DIR is required for browser captures")
    directory = Path(runtime) / "cu" / "browser" / "captures"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Refused(
            exits.PRECONDITION_FAILED, "browser captures require a private owned directory"
        )
    lock_fd = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "wb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        captures = sorted(
            directory.glob("capture-" + "[0-9a-f]" * 24 + ".png"),
            key=lambda path: path.lstat().st_mtime_ns,
        )
        for old in captures[:-59]:
            old.unlink()
        path = directory / f"capture-{secrets.token_hex(12)}.png"
        handle = path.open("xb")
        try:
            with handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(data)
        except OSError:
            path.unlink(missing_ok=True)
            raise
    return path


def _capture(args: argparse.Namespace) -> dict[str, Any]:
    if args.document is None or args.generation is None:
        raise Refused(
            exits.BROWSER_READ_REFUSED,
            "browser captures require --document and --generation from a prior observation",
        )
    safety.check_abort()
    region = _parse_region(args.region) if args.region else None
    with transport.BrowserReadClient.connect(_paths(args)[0], args.timeout) as client:
        grant = _grant(args, client)
        if "interact" not in grant.get("capabilities", ["read"]):
            raise Refused(
                exits.BROWSER_READ_REFUSED,
                "the current browser grant is read-only; bind the tab again with cu web grant",
            )
        if args.generation != grant["generation"]:
            raise Refused(exits.BROWSER_DOCUMENT_STALE, "the granted generation changed")
        result = client.capture(
            **_identity(grant),
            generation=args.generation,
            region=region,
            timeout=args.timeout,
        )
    _check_response(result)
    request = transport.make_request(
        "capture",
        session=grant["session"],
        tab_id=grant["tab_id"],
        document_id=grant["document_id"],
        generation=args.generation,
        payload={} if region is None else {"region": region},
    )
    try:
        result = transport.normalize_capture_response(result, request)
    except transport.CaptureTransformError as exc:
        code = (
            exits.BROWSER_DOCUMENT_STALE
            if exc.code in {"stale-document", "stale-generation"}
            else exits.BROWSER_READ_BUDGET
            if exc.code == "capture-budget"
            else exits.BROWSER_READ_REFUSED
            if exc.code == "unsupported-geometry"
            else exits.BROWSER_READ_PROTOCOL
        )
        raise Refused(code, f"browser request refused: {exc.code}") from exc
    body = dict(result["result"])
    try:
        data = base64.b64decode(body.pop("png_base64"), validate=True)
    except (KeyError, binascii.Error, ValueError) as exc:
        raise Refused(
            exits.BROWSER_READ_PROTOCOL, "browser capture did not contain valid PNG bytes"
        ) from exc
    if len(data) > transport.MAX_CAPTURE_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise Refused(
            exits.BROWSER_READ_BUDGET, "browser capture is outside the bounded PNG format"
        )
    if len(data) < 24:
        raise Refused(exits.BROWSER_READ_PROTOCOL, "truncated browser PNG")
    width, height = struct.unpack(">II", data[16:24])
    if width * height > transport.MAX_CAPTURE_PIXELS:
        raise Refused(exits.BROWSER_READ_BUDGET, "browser capture exceeds its pixel budget")
    try:
        width, height, _ = png.decode(data)
    except ValueError as exc:
        raise Refused(exits.BROWSER_READ_PROTOCOL, "invalid browser PNG") from exc
    if body.get("pixels") != {"width": width, "height": height, "space": "image-pixels"}:
        raise Refused(exits.BROWSER_READ_PROTOCOL, "browser PNG dimensions do not match metadata")
    if body.get("actionable", False) is not False or body.get("input_authority", "none") != "none":
        raise Refused(exits.BROWSER_READ_PROTOCOL, "browser capture carries native-input authority")
    path = _save_capture(data)
    return {
        "browser_capture": True,
        "path": str(path),
        "bytes": len(data),
        **{key: value for key, value in body.items() if key != "png_base64"},
    }


def run(args: argparse.Namespace) -> int:
    try:
        if args.web_command == "setup":
            result = install_host(args.browser, args.user_data_dir)
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
        elif args.web_command == "grant":
            result = _bind(args)
        elif args.web_command == "stop":
            result = _release(args)
        elif args.web_command == "read":
            result = _read(args)
        elif args.web_command == "action":
            result = _action(args)
        elif args.web_command == "wait":
            result = _wait(args)
        else:
            result = _capture(args)
        print(json.dumps(result, indent=None if args.json else 2))
        return exits.OK
    except transport.BrowserActionUncertain as exc:
        raise Refused(exits.BROWSER_ACTION_UNCERTAIN, str(exc)) from exc
    except transport.BrowserReadTimeout as exc:
        raise Refused(exits.BROWSER_READ_TIMEOUT, str(exc)) from exc
    except (transport.StaleHelper, transport.HelperUnavailable) as exc:
        raise Refused(exits.BROWSER_READ_UNAVAILABLE, str(exc)) from exc
    except transport.OversizeMessage as exc:
        raise Refused(exits.BROWSER_READ_BUDGET, str(exc)) from exc
    except transport.ProtocolError as exc:
        raise Refused(exits.BROWSER_READ_PROTOCOL, str(exc)) from exc
