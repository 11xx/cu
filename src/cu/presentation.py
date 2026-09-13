"""One explicit, guarded window-presentation capability.

Presentation is deliberately smaller than a window manager.  The capability
can toggle a window's floating state and move it to one selected output; the
compositor adapter is responsible for spelling those two operations.  Every
operation is followed by a fresh observation, and restore compares that
observation with the last state the token recorded before it writes anything.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import secrets
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from . import hypr, observe, sandbox

TOKEN_SCHEMA = "cu-presentation/1"
TOKEN_PATTERN = re.compile(r"p-[0-9a-f]{32}\Z")
ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]+\Z")


class PresentationFailure(RuntimeError):
    """A bounded capability outcome that the action layer maps to an exit code."""

    def __init__(
        self,
        kind: str,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        if message is None:
            message = kind
            kind = "refused"
        super().__init__(message)
        self.kind = kind
        self.details = details or {}


class TokenStore(Protocol):
    """The private token operations needed by this capability only."""

    def create(self, payload: dict[str, Any]) -> str: ...

    def read(self, token: str) -> dict[str, Any]: ...

    def write(self, token: str, payload: dict[str, Any]) -> None: ...

    def invalidate(self, token: str) -> None: ...


@dataclass(frozen=True)
class WindowSeam:
    """The two compositor operations presentation is allowed to request."""

    toggle_floating: Callable[[str], None]
    move: Callable[[str, float, float], None]


def hyprland_seam() -> WindowSeam:
    """Build the verified Hyprland 0.56 operation seam.

    The public capability does not expose these dispatcher names.  Keeping
    their spelling here makes the seam replaceable in deterministic tests and
    keeps an un-dispatched userdata value from being mistaken for a change.
    """

    def toggle_floating(address: str) -> None:
        hypr.lua(
            f'hl.dispatch(hl.dsp.window.float({{window = "address:{_address(address)}"}}))'
        )

    def move(address: str, x: float, y: float) -> None:
        hypr.lua(
            "hl.dispatch(hl.dsp.window.move("
            f'{{window = "address:{_address(address)}", '
            f"x = {_number(x)}, y = {_number(y)}}}))"
        )

    return WindowSeam(toggle_floating=toggle_floating, move=move)


def _address(value: str) -> str:
    if not isinstance(value, str) or not ADDRESS_PATTERN.fullmatch(value):
        raise PresentationFailure(
            "stale",
            "the window address is not a supported compositor identity",
        )
    return value


def _number(value: float) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise PresentationFailure("refused", "the presentation position is not finite")
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


class RuntimeTokenStore:
    """Store opaque presentation tokens in the volatile CU runtime only."""

    def __init__(self, root: Path | None = None) -> None:
        self._configured_root = root

    def _root(self) -> Path:
        if self._configured_root is not None:
            root = self._configured_root
        else:
            runtime = os.environ.get("XDG_RUNTIME_DIR")
            if not runtime or not Path(runtime).is_absolute():
                raise PresentationFailure(
                    "token", "XDG_RUNTIME_DIR is required for presentation restore"
                )
            root = Path(runtime) / "cu" / "presentation"
        try:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(root, 0o700)
        except OSError as exc:
            raise PresentationFailure(
                "token", "private presentation token storage is unavailable"
            ) from exc
        return root

    @staticmethod
    def _check_token(token: str) -> None:
        if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
            raise PresentationFailure("token", "the presentation token is invalid")

    def _path(self, token: str) -> Path:
        self._check_token(token)
        return self._root() / token

    def create(self, payload: dict[str, Any]) -> str:
        root = self._root()
        for _ in range(8):
            token = f"p-{secrets.token_hex(16)}"
            path = root / token
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(path, flags, 0o600)
                stored = {**payload, "token": token}
                with os.fdopen(fd, "w") as handle:
                    json.dump(stored, handle, sort_keys=True, separators=(",", ":"))
                    handle.write("\n")
                return token
            except FileExistsError:
                continue
            except (OSError, TypeError, ValueError) as exc:
                with suppress(OSError):
                    path.unlink(missing_ok=True)
                raise PresentationFailure(
                    "token", "the presentation token could not be saved"
                ) from exc
        raise PresentationFailure("token", "the presentation token could not be named")

    def read(self, token: str) -> dict[str, Any]:
        path = self._path(token)
        try:
            payload = json.loads(path.read_text())
        except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
            raise PresentationFailure(
                "token", "the presentation token is missing or unreadable"
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != TOKEN_SCHEMA
            or payload.get("token") != token
        ):
            raise PresentationFailure("token", "the presentation token is invalid")
        return payload

    def write(self, token: str, payload: dict[str, Any]) -> None:
        path = self._path(token)
        root = path.parent
        temporary = root / f".{token}.{secrets.token_hex(8)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(temporary, flags, 0o600)
            with os.fdopen(fd, "w") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        except (OSError, TypeError, ValueError) as exc:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
            raise PresentationFailure(
                "token", "the presentation token could not be updated"
            ) from exc

    def invalidate(self, token: str) -> None:
        path = self._path(token)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise PresentationFailure(
                "token", "the presentation token could not be invalidated"
            ) from exc


def state(window: dict[str, Any]) -> dict[str, Any]:
    """Return only facts required to identify and restore one window."""

    address = window.get("address")
    rect = window.get("logical_rect")
    if (
        not isinstance(address, str)
        or not address
        or not isinstance(rect, dict)
        or any(name not in rect for name in ("x", "y", "w", "h"))
        or not isinstance(window.get("floating"), bool)
        or not isinstance(window.get("visible", True), bool)
        or not isinstance(window.get("hidden", False), bool)
    ):
        raise PresentationFailure("stale", "the observed window state is incomplete")
    if any(
        isinstance(rect[name], bool)
        or not isinstance(rect[name], int | float)
        or not math.isfinite(rect[name])
        for name in ("x", "y", "w", "h")
    ) or rect["w"] <= 0 or rect["h"] <= 0:
        raise PresentationFailure("stale", "the observed window geometry is invalid")
    return {
        "address": address,
        "pid": window.get("pid"),
        "class": window.get("class", ""),
        "workspace": window.get("workspace"),
        "monitor": window.get("monitor"),
        "floating": window["floating"],
        "visible": window.get("visible", True),
        "hidden": window.get("hidden", False),
        "logical_rect": {name: rect[name] for name in ("x", "y", "w", "h")},
    }


def _diagnostic(
    window: dict[str, Any] | None, monitors: list[dict[str, Any]]
) -> dict[str, Any] | None:
    if window is None:
        return None
    try:
        result = state(window)
    except PresentationFailure:
        result = {
            "address": window.get("address"),
            "class": window.get("class"),
            "title": window.get("title"),
            "logical_rect": window.get("logical_rect"),
        }
    else:
        result["title"] = window.get("title")
    result["visible"] = window.get("visible", True)
    result["hidden"] = window.get("hidden", False)
    rect = window.get("logical_rect")
    if isinstance(rect, dict) and all(name in rect for name in ("x", "y", "w", "h")):
        result["extent"] = window.get("extent") or observe.extent(rect, monitors)
        result["reachability"] = window.get("reachability") or observe.reachability(
            rect, monitors
        )
    return result


def _identity_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    if expected.get("address") != actual.get("address"):
        return False
    for name in ("pid", "class"):
        value = expected.get(name)
        if value is not None and value != actual.get(name):
            return False
    return True


def _arrangement_matches(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return all(
        expected.get(name) == actual.get(name)
        for name in (
            "workspace",
            "monitor",
            "floating",
            "visible",
            "hidden",
            "logical_rect",
        )
    )


def _window(snapshot: dict[str, Any], address: str) -> dict[str, Any] | None:
    matches = [
        item
        for item in snapshot.get("windows", [])
        if isinstance(item, dict) and item.get("address") == address
    ]
    return matches[0] if len(matches) == 1 else None


def _observed(
    snapshot_reader: Callable[[], dict[str, Any]], address: str
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    try:
        snapshot = snapshot_reader()
    except Exception as exc:
        raise PresentationFailure(
            "refused", "the compositor state could not be read back"
        ) from exc
    if not isinstance(snapshot, dict):
        raise PresentationFailure("refused", "the compositor state could not be read back")
    return _window(snapshot, address), snapshot


def _output(
    snapshot: dict[str, Any], window: dict[str, Any], name: str | None
) -> dict[str, Any]:
    monitors = [
        item
        for item in snapshot.get("monitors", [])
        if (
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and not item.get("disabled", False)
            and not sandbox.is_headless(item.get("name", ""))
        )
    ]
    selected_name = name or window.get("monitor")
    matches = [item for item in monitors if item.get("name") == selected_name]
    if len(matches) != 1 or not isinstance(matches[0].get("logical_rect"), dict):
        raise PresentationFailure(
            "refused",
            "the requested visible output is not available",
            details={"output": selected_name, "monitors": [item.get("name") for item in monitors]},
        )
    monitor = matches[0]
    rect = monitor["logical_rect"]
    if any(
        name not in rect
        or isinstance(rect[name], bool)
        or not isinstance(rect[name], int | float)
        or not math.isfinite(rect[name])
        for name in ("x", "y", "w", "h")
    ) or rect["w"] <= 0 or rect["h"] <= 0:
        raise PresentationFailure("refused", "the selected output geometry is invalid")
    return {
        "name": monitor["name"],
        "scale": monitor.get("scale"),
        "logical_rect": {name: rect[name] for name in ("x", "y", "w", "h")},
    }


def _destination(rect: dict[str, Any], output: dict[str, Any]) -> dict[str, float]:
    bounds = output["logical_rect"]
    x = bounds["x"] + max(0.0, (bounds["w"] - rect["w"]) / 2)
    y = bounds["y"] + max(0.0, (bounds["h"] - rect["h"]) / 2)
    return {"x": float(math.floor(x + 0.5)), "y": float(math.floor(y + 0.5))}


def _base_details(
    *,
    before: dict[str, Any],
    actual: dict[str, Any] | None,
    snapshot: dict[str, Any],
    output: dict[str, Any] | None,
    token: str | None,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "window": _diagnostic(_window(snapshot, before["address"]), snapshot.get("monitors", [])),
        "before": before,
        "actual": _diagnostic(actual, snapshot.get("monitors", [])),
        "output": output,
        "token": token,
        "steps": steps,
    }


def _failure(
    kind: str,
    message: str,
    *,
    before: dict[str, Any],
    actual: dict[str, Any] | None,
    snapshot: dict[str, Any],
    output: dict[str, Any] | None,
    token: str | None,
    steps: list[dict[str, Any]],
    token_status: str | None = None,
) -> PresentationFailure:
    details = _base_details(
        before=before,
        actual=actual,
        snapshot=snapshot,
        output=output,
        token=token,
        steps=steps,
    )
    if token_status is not None:
        details["token_status"] = token_status
    return PresentationFailure(kind, message, details=details)


def _read_and_check(
    *,
    address: str,
    expected: dict[str, Any],
    snapshot_reader: Callable[[], dict[str, Any]],
    guard: Callable[[dict[str, Any]], None],
    before: dict[str, Any],
    output: dict[str, Any] | None,
    token: str | None,
    steps: list[dict[str, Any]],
    require_arrangement: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    live, snapshot = _observed(snapshot_reader, address)
    if live is None:
        raise _failure(
            "stale",
            "the presented window is no longer observed",
            before=before,
            actual=None,
            snapshot=snapshot,
            output=output,
            token=token,
            steps=steps,
            token_status="invalidated" if token else None,
        )
    guard(live)
    live, snapshot = _observed(snapshot_reader, address)
    if live is None:
        raise _failure(
            "stale",
            "the presented window disappeared before the operation",
            before=before,
            actual=None,
            snapshot=snapshot,
            output=output,
            token=token,
            steps=steps,
            token_status="invalidated" if token else None,
        )
    current = state(live)
    if not _identity_matches(expected, current):
        raise _failure(
            "stale",
            "the observed address no longer identifies the same window",
            before=before,
            actual=live,
            snapshot=snapshot,
            output=output,
            token=token,
            steps=steps,
            token_status="invalidated" if token else None,
        )
    if require_arrangement and not _arrangement_matches(expected, current):
        raise _failure(
            "changed",
            "the window changed after presentation; restoration was refused",
            before=before,
            actual=live,
            snapshot=snapshot,
            output=output,
            token=token,
            steps=steps,
            token_status="invalidated" if token else None,
        )
    return live, snapshot


def _token_payload(
    token: str,
    before: dict[str, Any],
    presented: dict[str, Any],
    output: dict[str, Any],
    *,
    complete: bool,
) -> dict[str, Any]:
    return {
        "schema": TOKEN_SCHEMA,
        "token": token,
        "before": before,
        "presented": presented,
        "output": output,
        "complete": complete,
    }


def _save_presented(
    store: TokenStore,
    token: str,
    before: dict[str, Any],
    presented: dict[str, Any],
    output: dict[str, Any],
    *,
    complete: bool,
) -> None:
    store.write(token, _token_payload(token, before, presented, output, complete=complete))


def _saved_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PresentationFailure("token", "the presentation token has invalid window state")
    try:
        return state(value)
    except PresentationFailure as exc:
        raise PresentationFailure(
            "token", "the presentation token has invalid window state"
        ) from exc


def _saved_output(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("name"), str)
        or not isinstance(value.get("logical_rect"), dict)
    ):
        raise PresentationFailure("token", "the presentation token has invalid output state")
    rect = value["logical_rect"]
    if any(name not in rect for name in ("x", "y", "w", "h")):
        raise PresentationFailure("token", "the presentation token has invalid output state")
    return {
        "name": value["name"],
        "scale": value.get("scale"),
        "logical_rect": {name: rect[name] for name in ("x", "y", "w", "h")},
    }


def _failure_changed_state(failure: PresentationFailure, before: dict[str, Any]) -> bool:
    actual = failure.details.get("actual")
    if not isinstance(actual, dict):
        return False
    return not _arrangement_matches(before, actual)


def present(
    window: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    output_name: str | None,
    snapshot_reader: Callable[[], dict[str, Any]],
    seam: WindowSeam,
    store: TokenStore,
    guard: Callable[[dict[str, Any]], None],
    dry_run: bool,
) -> dict[str, Any]:
    """Float and center one visible window, recording verified before/after state."""

    before = state(window)
    before_diagnostic = _diagnostic(
        copy.deepcopy(window), snapshot.get("monitors", [])
    )
    if not window.get("visible", True) or window.get("hidden", False):
        raise PresentationFailure(
            "refused",
            "presentation requires a visible window",
            details={"before": before, "actual": _diagnostic(window, snapshot.get("monitors", []))},
        )
    output = _output(snapshot, window, output_name)
    destination = _destination(before["logical_rect"], output)
    planned = ["move"] if before["floating"] else ["toggle-floating", "move"]
    if dry_run:
        return {
            "action": "present",
            "window": _diagnostic(window, snapshot.get("monitors", [])),
            "output": output,
            "before": before_diagnostic,
            "after": _diagnostic(window, snapshot.get("monitors", [])),
            "planned": {"operations": planned, "destination": destination},
            "steps": [],
            "token": None,
            "changed": False,
            "dry_run": True,
        }

    token = store.create(_token_payload("pending", before, before, output, complete=False))
    expected = before
    steps: list[dict[str, Any]] = []
    try:
        if not before["floating"]:
            _read_and_check(
                address=before["address"],
                expected=expected,
                snapshot_reader=snapshot_reader,
                guard=guard,
                before=before,
                output=output,
                token=token,
                steps=steps,
                require_arrangement=True,
            )
            try:
                seam.toggle_floating(before["address"])
            except Exception as exc:
                live, read_snapshot = _observed(snapshot_reader, before["address"])
                raise _failure(
                    "refused" if live is None or state(live) == expected else "partial",
                    "the compositor refused the floating-state operation",
                    before=before,
                    actual=live,
                    snapshot=read_snapshot,
                    output=output,
                    token=token,
                    steps=steps,
                ) from exc
            live, read_snapshot = _observed(snapshot_reader, before["address"])
            if live is None:
                raise _failure(
                    "stale",
                    "the window disappeared after changing its floating state",
                    before=before,
                    actual=None,
                    snapshot=read_snapshot,
                    output=output,
                    token=token,
                    steps=steps,
                )
            after_float = state(live)
            if not _identity_matches(expected, after_float):
                raise _failure(
                    "stale",
                    "the window identity changed after the floating-state operation",
                    before=before,
                    actual=live,
                    snapshot=read_snapshot,
                    output=output,
                    token=token,
                    steps=steps,
                )
            if after_float["floating"] == expected["floating"]:
                raise _failure(
                    "refused",
                    "the compositor accepted no floating-state change",
                    before=before,
                    actual=live,
                    snapshot=read_snapshot,
                    output=output,
                    token=token,
                    steps=steps,
                )
            if not after_float["visible"] or after_float["hidden"]:
                raise _failure(
                    "partial",
                    "the window became unavailable after the floating-state operation",
                    before=before,
                    actual=live,
                    snapshot=read_snapshot,
                    output=output,
                    token=token,
                    steps=steps,
                )
            steps.append({"operation": "toggle-floating", "status": "verified"})
            expected = after_float
            _save_presented(store, token, before, expected, output, complete=False)

        destination = _destination(expected["logical_rect"], output)
        if (
            expected["logical_rect"]["x"] != destination["x"]
            or expected["logical_rect"]["y"] != destination["y"]
        ):
            _read_and_check(
                address=before["address"],
                expected=expected,
                snapshot_reader=snapshot_reader,
                guard=guard,
                before=before,
                output=output,
                token=token,
                steps=steps,
                require_arrangement=True,
            )
            try:
                seam.move(before["address"], destination["x"], destination["y"])
            except Exception as exc:
                live, read_snapshot = _observed(snapshot_reader, before["address"])
                actual_state = state(live) if live is not None else None
                raise _failure(
                    "refused" if actual_state == expected else "partial",
                    "the compositor refused the presentation move",
                    before=before,
                    actual=live,
                    snapshot=read_snapshot,
                    output=output,
                    token=token,
                    steps=steps,
                ) from exc
            live, read_snapshot = _observed(snapshot_reader, before["address"])
            if live is None:
                raise _failure(
                    "stale",
                    "the window disappeared after the presentation move",
                    before=before,
                    actual=None,
                    snapshot=read_snapshot,
                    output=output,
                    token=token,
                    steps=steps,
                )
            after_move = state(live)
            if not _identity_matches(expected, after_move):
                raise _failure(
                    "stale",
                    "the window identity changed after the presentation move",
                    before=before,
                    actual=live,
                    snapshot=read_snapshot,
                    output=output,
                    token=token,
                    steps=steps,
                )
            if not after_move["visible"] or after_move["hidden"]:
                raise _failure(
                    "partial",
                    "the window became unavailable after the presentation move",
                    before=before,
                    actual=live,
                    snapshot=read_snapshot,
                    output=output,
                    token=token,
                    steps=steps,
                )
            expected_rect = expected["logical_rect"]
            actual_rect = after_move["logical_rect"]
            if (
                actual_rect["x"] != destination["x"]
                or actual_rect["y"] != destination["y"]
                or actual_rect["w"] != expected_rect["w"]
                or actual_rect["h"] != expected_rect["h"]
            ):
                raise _failure(
                    "refused" if after_move == expected else "partial",
                    "the presentation move did not verify its geometry",
                    before=before,
                    actual=live,
                    snapshot=read_snapshot,
                    output=output,
                    token=token,
                    steps=steps,
                )
            steps.append({"operation": "move", "status": "verified"})
            expected = after_move
            _save_presented(store, token, before, expected, output, complete=False)

        final_window, final_snapshot = _observed(snapshot_reader, before["address"])
        if final_window is None:
            raise _failure(
                "stale",
                "the window disappeared before presentation completed",
                before=before,
                actual=None,
                snapshot=final_snapshot,
                output=output,
                token=token,
                steps=steps,
            )
        final_state = state(final_window)
        if not _identity_matches(expected, final_state) or not _arrangement_matches(
            expected, final_state
        ):
            raise _failure(
                "partial",
                "presentation read-back changed before the result was returned",
                before=before,
                actual=final_window,
                snapshot=final_snapshot,
                output=output,
                token=token,
                steps=steps,
            )
        _save_presented(store, token, before, final_state, output, complete=True)
        return {
            "action": "present",
            "window": _diagnostic(final_window, final_snapshot.get("monitors", [])),
            "output": output,
            "before": before_diagnostic,
            "after": _diagnostic(final_window, final_snapshot.get("monitors", [])),
            "steps": steps,
            "token": token,
            "changed": before != final_state,
            "dry_run": False,
        }
    except PresentationFailure as exc:
        if exc.kind in {"stale", "changed"} or (
            not steps and not _failure_changed_state(exc, before)
        ):
            with suppress(PresentationFailure):
                store.invalidate(token)
            exc.details["token_status"] = "invalidated"
        if "token" not in exc.details:
            exc.details["token"] = token
        raise


def restore(
    token: str,
    *,
    snapshot_reader: Callable[[], dict[str, Any]],
    seam: WindowSeam,
    store: TokenStore,
    guard: Callable[[dict[str, Any]], None],
    dry_run: bool,
) -> dict[str, Any]:
    """Restore a token only when its identity and presented state are unchanged."""

    payload = store.read(token)
    before = _saved_state(payload.get("before"))
    presented = _saved_state(payload.get("presented"))
    output = _saved_output(payload.get("output"))
    address = presented.get("address")
    if not isinstance(address, str):
        raise PresentationFailure("token", "the presentation token has no window identity")
    live, snapshot = _observed(snapshot_reader, address)
    if live is None:
        with suppress(PresentationFailure):
            store.invalidate(token)
        raise _failure(
            "stale",
            "the presented window is no longer observed; restoration was refused",
            before=before,
            actual=None,
            snapshot=snapshot,
            output=output,
            token=token,
            steps=[],
            token_status="invalidated",
        )
    guard(live)
    current = state(live)
    if not _identity_matches(presented, current):
        with suppress(PresentationFailure):
            store.invalidate(token)
        raise _failure(
            "stale",
            "the presentation token no longer names the same window",
            before=before,
            actual=live,
            snapshot=snapshot,
            output=output,
            token=token,
            steps=[],
            token_status="invalidated",
        )
    if not _arrangement_matches(presented, current):
        with suppress(PresentationFailure):
            store.invalidate(token)
        raise _failure(
            "changed",
            "the window changed after presentation; restoration was refused",
            before=before,
            actual=live,
            snapshot=snapshot,
            output=output,
            token=token,
            steps=[],
            token_status="invalidated",
        )

    if dry_run:
        return {
            "action": "restore",
            "window": _diagnostic(live, snapshot.get("monitors", [])),
            "before": before,
            "presented": _diagnostic(live, snapshot.get("monitors", [])),
            "after": _diagnostic(live, snapshot.get("monitors", [])),
            "restore_to": before,
            "steps": [],
            "token": token,
            "changed": False,
            "dry_run": True,
        }

    steps: list[dict[str, Any]] = []
    expected = presented
    try:
        _read_and_check(
            address=address,
            expected=expected,
            snapshot_reader=snapshot_reader,
            guard=guard,
            before=before,
            output=output,
            token=token,
            steps=steps,
            require_arrangement=True,
        )
        try:
            if before.get("floating"):
                target_rect = before.get("logical_rect")
                seam.move(address, target_rect["x"], target_rect["y"])
                operation = "move"
            else:
                seam.toggle_floating(address)
                operation = "toggle-floating"
        except Exception as exc:
            live, read_snapshot = _observed(snapshot_reader, address)
            actual_state = state(live) if live is not None else None
            raise _failure(
                "refused" if actual_state == expected else "partial",
                "the compositor refused the restoration operation",
                before=before,
                actual=live,
                snapshot=read_snapshot,
                output=output,
                token=token,
                steps=steps,
            ) from exc

        live, read_snapshot = _observed(snapshot_reader, address)
        if live is None:
            raise _failure(
                "stale",
                "the window disappeared during restoration",
                before=before,
                actual=None,
                snapshot=read_snapshot,
                output=output,
                token=token,
                steps=steps,
            )
        actual = state(live)
        if not _identity_matches(before, actual):
            raise _failure(
                "stale",
                "the window identity changed during restoration",
                before=before,
                actual=live,
                snapshot=read_snapshot,
                output=output,
                token=token,
                steps=steps,
            )
        if not _arrangement_matches(before, actual):
            raise _failure(
                "partial",
                "restoration read-back did not recover the saved state",
                before=before,
                actual=live,
                snapshot=read_snapshot,
                output=output,
                token=token,
                steps=steps,
            )
        steps.append({"operation": operation, "status": "verified"})
        store.invalidate(token)
        return {
            "action": "restore",
            "window": _diagnostic(live, read_snapshot.get("monitors", [])),
            "before": before,
            "presented": presented,
            "after": _diagnostic(live, read_snapshot.get("monitors", [])),
            "restore_to": before,
            "steps": steps,
            "token": token,
            "changed": presented != before,
            "dry_run": False,
        }
    except PresentationFailure as exc:
        if exc.kind in {"stale", "changed"}:
            with suppress(PresentationFailure):
                store.invalidate(token)
            exc.details["token_status"] = "invalidated"
        if "token" not in exc.details:
            exc.details["token"] = token
        raise
