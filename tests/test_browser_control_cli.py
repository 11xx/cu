import base64
import fcntl
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cu import browser_read as wire
from cu import browser_read_cli as web
from cu import cli, config, desktop, exits, png, safety, status


@pytest.fixture
def bridge(monkeypatch):
    grant = {
        "session": "fixture-session",
        "tab_id": 1,
        "window_id": 1,
        "document_id": "fixture-document",
        "generation": 0,
        "active": True,
        "hidden": False,
        "capabilities": ["read", "interact"],
    }
    state = SimpleNamespace(calls=0, grant=grant, failure=None, capture=None)

    class Client:
        def __init__(self):
            self.metadata = {"grants": [grant]}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def action(self, operation, **kwargs):
            with desktop.lock_path().open("a") as lock, pytest.raises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state.calls += 1
            if state.failure:
                raise state.failure
            return {"ok": True, "result": {"kind": "action", "verification": "unverified"}}

        def capture(self, **kwargs):
            return {"ok": True, "result": dict(state.capture)}

    monkeypatch.setattr(wire.BrowserReadClient, "connect", lambda *args: Client())
    monkeypatch.setattr(web, "_paths", lambda args: [Path("unused")])
    monkeypatch.setattr(safety, "check_session", lambda: None)
    monkeypatch.setattr(web.observe, "focused", lambda: {"class": "firefox", "title": "Fixture"})
    monkeypatch.setattr(safety, "record", lambda *args: None)
    return state


def action_args():
    return [
        "web",
        "action",
        "click",
        "--document",
        "fixture-document",
        "--generation",
        "0",
        "--ref",
        "e1",
        "--json",
    ]


def test_action_holds_desktop_and_does_not_invent_verified_effect(bridge, capsys):
    assert cli.main(action_args()) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["dispatch_completed"] is True
    assert receipt["result"]["verification"] == "unverified"
    assert "uncertainty" not in receipt
    assert bridge.calls == 1


def test_uncertain_action_has_a_distinct_exit_and_is_not_replayed(bridge):
    bridge.failure = wire.BrowserActionUncertain("response lost after dispatch")
    assert cli.main(action_args()) == exits.BROWSER_ACTION_UNCERTAIN
    assert bridge.calls == 1


def test_read_grant_cannot_dispatch_action(bridge):
    bridge.grant["capabilities"] = ["read"]
    assert cli.main(action_args()) == exits.BROWSER_READ_REFUSED
    assert bridge.calls == 0


def test_abort_and_reservation_refuse_before_browser_dispatch(bridge):
    flag = config.abort_path()
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    assert cli.main(action_args()) == exits.ABORTED_BY_USER
    assert bridge.calls == 0
    # Only remove the test's isolated flag, never the user's runtime state.
    flag.unlink()
    holder = status.begin("another driver", exclusive=True)
    try:
        assert cli.main(action_args()) == exits.DESKTOP_RESERVED
        assert bridge.calls == 0
    finally:
        status.end(holder["id"])


def test_browser_capture_validates_pixels_and_writes_a_private_nonframe(bridge, capsys):
    image = png.encode(2, 1, bytes([255, 0, 0, 255]) * 2)
    bridge.capture = {
        "kind": "capture",
        "png_base64": base64.b64encode(image).decode(),
        "pixels": {"width": 2, "height": 1, "space": "image-pixels"},
        "document_id": "fixture-document",
        "generation": 0,
        "provenance": "browser-rendered, not a CU compositor frame",
    }
    args = ["web", "capture", "--document", "fixture-document", "--generation", "0", "--json"]
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    path = Path(result["path"])
    assert path.read_bytes() == image
    assert path.stat().st_mode & 0o777 == 0o600
    assert "frame" not in result
    bridge.capture["pixels"]["width"] = 3
    assert cli.main(args) == exits.BROWSER_READ_PROTOCOL
    assert list(path.parent.glob("*.png")) == [path]
