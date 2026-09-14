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
    state = SimpleNamespace(
        calls=0, grant=grant, failure=None, capture=None, payloads=[], controls=[], requests=[]
    )

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
            state.payloads.append((operation, kwargs["payload"]))
            if state.failure:
                raise state.failure
            return {"ok": True, "result": {"kind": "action", "verification": "unverified"}}

        def capture(self, **kwargs):
            state.requests.append(("capture", kwargs))
            return {"ok": True, "result": dict(state.capture)}

        def wait(self, **kwargs):
            state.requests.append(("wait", kwargs["payload"]))
            return state.wait_result

        def control(self, operation, **kwargs):
            state.controls.append((operation, kwargs))
            if operation.startswith("debug-"):
                attached = "attached" if operation == "debug-attach" else "detached"
                return {"ok": True, "result": {"grant": grant, "debugger": attached}}
            key = "grant" if operation == "grant" else "revoked_grant"
            return {"ok": True, "result": {key: grant}}

        def request(self, operation, **kwargs):
            state.requests.append((operation, kwargs["payload"]))
            return {"ok": True, "result": {"kind": "debugger-evidence", "evidence": operation}}

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


def test_web_subcommands_accept_session_and_intent_flags(bridge):
    assert cli.main([*action_args(), "--session", "fixture-driver", "--why", "check delivery"]) == 0
    assert bridge.calls == 1


def test_uncertain_action_has_a_distinct_exit_and_is_not_replayed(bridge):
    bridge.failure = wire.BrowserActionUncertain("response lost after dispatch")
    assert cli.main(action_args()) == exits.BROWSER_ACTION_UNCERTAIN
    assert bridge.calls == 1


def test_read_grant_cannot_dispatch_action(bridge):
    bridge.grant["capabilities"] = ["read"]
    assert cli.main(action_args()) == exits.BROWSER_READ_REFUSED
    assert bridge.calls == 0


def test_select_action_requires_one_option_identity_and_forwards_value(bridge, capsys):
    args = [
        "web",
        "action",
        "select",
        "--document",
        "fixture-document",
        "--generation",
        "0",
        "--ref",
        "e1",
        "--value",
        "two",
        "--json",
    ]
    assert cli.main(args) == 0
    json.loads(capsys.readouterr().out)
    assert bridge.payloads[-1] == ("select", {"ref": "e1", "value": "two"})

    with pytest.raises(ValueError, match="exactly one"):
        web._action(
            SimpleNamespace(
                action="select",
                input="dom",
                key=None,
                document="fixture-document",
                generation=0,
                ref="e1",
                value=None,
                label=None,
                target="element",
                dx=0,
                dy=0,
                helper=None,
                tab=None,
                timeout=2.0,
            )
        )


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


def test_browser_full_page_capture_asks_for_the_debugger_render(bridge, capsys):
    image = png.encode(2, 3, bytes([0, 0, 255, 255]) * 6)
    bridge.capture = {
        "kind": "capture",
        "backend": "debugger",
        "png_base64": base64.b64encode(image).decode(),
        "pixels": {"width": 2, "height": 3, "space": "image-pixels"},
        "document_id": "fixture-document",
        "tab_id": 1,
        "generation": 0,
        "document_size": {"width": 2, "height": 3, "space": "css-document"},
        "actionable": False,
    }
    args = ["web", "capture", "--document", "fixture-document", "--generation", "0"]
    assert cli.main([*args, "--full-page", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert Path(result["path"]).read_bytes() == image
    assert bridge.requests[-1][1]["full_page"] is True
    assert bridge.requests[-1][1]["region"] is None
    assert cli.main([*args, "--full-page", "--region", "0,0 1x1", "--json"]) == exits.USAGE
    assert len(bridge.requests) == 1


def test_browser_region_capture_crops_a_known_pattern_and_preserves_coverage(bridge, capsys):
    pixels = bytes(
        [
            255,
            0,
            0,
            255,
            0,
            255,
            0,
            255,
            0,
            0,
            255,
            255,
            255,
            255,
            0,
            255,
        ]
    )
    image = png.encode(2, 2, pixels)
    bridge.capture = {
        "kind": "capture",
        "png_base64": base64.b64encode(image).decode(),
        "pixels": {"width": 2, "height": 2, "space": "image-pixels"},
        "document_id": "fixture-document",
        "tab_id": 1,
        "generation": 0,
        "viewport": {
            "width": 2,
            "height": 2,
            "device_pixel_ratio": 1,
            "space": "css-viewport",
        },
        "provenance": "browser-rendered, not a CU compositor frame",
        "actionable": False,
    }
    args = [
        "web",
        "capture",
        "--document",
        "fixture-document",
        "--generation",
        "0",
        "--region",
        "1,0 1x1",
        "--json",
    ]
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    width, height, cropped = png.decode(Path(result["path"]).read_bytes())
    assert (width, height, cropped) == (1, 1, bytes([0, 255, 0, 255]))
    assert result["coverage"]["requested"] == {
        "x": 1.0,
        "y": 0.0,
        "width": 1.0,
        "height": 1.0,
        "space": "css-viewport",
    }
    assert result["coverage"]["returned"] == result["coverage"]["requested"]
    assert result["coverage"]["image_pixels"] == {
        "x": 1,
        "y": 0,
        "width": 1,
        "height": 1,
        "space": "image-pixels",
    }
    assert result["source_pixels"] == {"width": 2, "height": 2, "space": "image-pixels"}
    assert result["actionable"] is False
    assert "frame" not in result


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        (
            "viewport",
            {"width": 1, "height": 2, "space": "css-viewport"},
            exits.BROWSER_READ_PROTOCOL,
        ),
        ("document_id", "other-document", exits.BROWSER_DOCUMENT_STALE),
        ("generation", 1, exits.BROWSER_DOCUMENT_STALE),
    ],
)
def test_browser_region_capture_refuses_scale_and_identity_drift(bridge, field, value, code):
    image = png.encode(2, 2, bytes([255, 0, 0, 255]) * 4)
    bridge.capture = {
        "kind": "capture",
        "png_base64": base64.b64encode(image).decode(),
        "pixels": {"width": 2, "height": 2, "space": "image-pixels"},
        "document_id": "fixture-document",
        "tab_id": 1,
        "generation": 0,
        "viewport": {"width": 2, "height": 2, "space": "css-viewport"},
    }
    bridge.capture[field] = value
    args = [
        "web",
        "capture",
        "--document",
        "fixture-document",
        "--generation",
        "0",
        "--region",
        "0,0 1x1",
        "--json",
    ]
    assert cli.main(args) == code


def test_browser_region_capture_refuses_unsupported_geometry_and_budget(bridge):
    image = png.encode(2, 2, bytes([255, 0, 0, 255]) * 4)
    bridge.capture = {
        "kind": "capture",
        "png_base64": base64.b64encode(image).decode(),
        "pixels": {"width": 2, "height": 2, "space": "image-pixels"},
        "document_id": "fixture-document",
        "tab_id": 1,
        "generation": 0,
        "viewport": {"width": 2, "height": 2, "space": "css-viewport"},
    }
    outside = [
        "web",
        "capture",
        "--document",
        "fixture-document",
        "--generation",
        "0",
        "--region",
        "1,1 2x2",
        "--json",
    ]
    assert cli.main(outside) == exits.BROWSER_READ_REFUSED
    bridge.capture = {
        **bridge.capture,
        "viewport": {"width": 0, "height": 2, "space": "css-viewport"},
    }
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(wire, "MAX_CAPTURE_BYTES", 1)
        assert cli.main([*outside[:-2], "0,0 1x1", "--json"]) == exits.BROWSER_READ_BUDGET


def reload_args(*extra):
    return [
        "web",
        "action",
        "reload",
        "--document",
        "fixture-document",
        "--generation",
        "0",
        *extra,
        "--json",
    ]


def test_reload_forwards_an_empty_payload_through_the_interlocks(bridge, capsys):
    assert cli.main(reload_args("--tab", "1")) == 0
    assert json.loads(capsys.readouterr().out)["dispatch_completed"] is True
    assert bridge.payloads == [("reload", {})]
    for extra in (["--ref", "e1"], ["--value", "x"], ["--label", "X"], ["--dy", "5"]):
        assert cli.main(reload_args(*extra)) == exits.USAGE
    assert bridge.calls == 1


def test_reload_on_a_read_grant_refuses_before_dispatch(bridge):
    bridge.grant["capabilities"] = ["read"]
    assert cli.main(reload_args()) == exits.BROWSER_READ_REFUSED
    assert bridge.calls == 0


def test_revoked_refusal_says_only_a_new_grant_restores_access():
    with pytest.raises(cli.Refused, match="bind the tab again with cu web grant") as refused:
        web._check_response(
            {
                "ok": False,
                "error": {"code": "reload-rejected", "state": "grant-revoked", "message": "x"},
            }
        )
    assert refused.value.code == exits.BROWSER_READ_REFUSED


def test_a_wait_ended_by_revocation_refuses_instead_of_timing_out(bridge, capsys):
    args = [
        "web",
        "wait",
        "--document",
        "fixture-document",
        "--selector",
        "#never",
        "--state",
        "visible",
    ]
    bridge.wait_result = {
        "ok": False,
        "error": {"code": "cancelled", "state": "grant-revoked", "message": "x"},
    }
    assert cli.main(args) == exits.BROWSER_READ_REFUSED
    err = capsys.readouterr().err
    assert "bind the tab again with cu web grant" in err
    assert "timed out" not in err
    for reason in ("cancelled", "wait-timeout"):
        bridge.wait_result = {"ok": False, "error": {"code": reason, "message": "x"}}
        assert cli.main(args) == exits.BROWSER_READ_TIMEOUT
    with pytest.raises(cli.Refused) as uncertain:
        web._check_response(
            {
                "ok": False,
                "error": {"code": "action-uncertain", "state": "grant-revoked", "message": "x"},
            }
        )
    assert uncertain.value.code == exits.BROWSER_ACTION_UNCERTAIN


def test_grant_binds_through_the_interlocks_and_stop_releases(bridge, capsys):
    assert cli.main(["web", "grant", "--tab", "1", "--no-cursor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["browser_grant"] is True
    assert cli.main(["web", "grant", "--read-only", "--json"]) == 0
    assert cli.main(["web", "stop", "--json"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["browser_grant_released"] is True
    interact = ["read", "interact"]
    timeout = wire.DEFAULT_TIMEOUT
    assert bridge.controls == [
        ("grant", {"tab_id": 1, "capabilities": interact, "cursor": False, "timeout": timeout}),
        ("grant", {"tab_id": None, "capabilities": ["read"], "cursor": None, "timeout": timeout}),
        ("release", {"tab_id": None, "timeout": timeout}),
    ]


def test_abort_refuses_a_grant_but_not_a_stop(bridge):
    flag = config.abort_path()
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    try:
        assert cli.main(["web", "grant", "--json"]) == exits.ABORTED_BY_USER
        assert cli.main(["web", "stop", "--json"]) == 0
    finally:
        # Only remove the test's isolated flag, never the user's runtime state.
        flag.unlink()
    assert [operation for operation, _ in bridge.controls] == ["release"]


def test_debug_attach_passes_the_interlocks_and_detach_does_not(bridge):
    flag = config.abort_path()
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    try:
        assert cli.main(["web", "debug", "attach", "--json"]) == exits.ABORTED_BY_USER
        assert cli.main(["web", "debug", "detach", "--json"]) == 0
    finally:
        # Only remove the test's isolated flag, never the user's runtime state.
        flag.unlink()
    assert cli.main(["web", "debug", "attach", "--tab", "1", "--json"]) == 0
    assert [(operation, kwargs["tab_id"]) for operation, kwargs in bridge.controls] == [
        ("debug-detach", None),
        ("debug-attach", 1),
    ]


def test_debug_reads_forward_bounded_payloads(bridge, capsys):
    assert cli.main(["web", "debug", "console", "--limit", "5", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["untrusted_page_content"] is True
    ax = ["web", "debug", "ax", "--role", "button", "--name", "Apply once", "--json"]
    assert cli.main(ax) == 0
    assert cli.main(["web", "debug", "network", "--role", "button", "--json"]) == exits.USAGE
    ax_payload = {"role": "button", "name": "Apply once", "include_values": False, "limit": 128}
    assert bridge.requests == [("console", {"limit": 5}), ("ax-query", ax_payload)]


def debugger_action(*extra):
    return ["web", "action", *extra, "--document", "fixture-document", "--generation", "0"]


def test_debugger_input_maps_actions_and_never_falls_back(bridge):
    assert cli.main(debugger_action("click", "--input", "debugger", "--ref", "e1")) == 0
    typed = debugger_action("type", "--input", "debugger", "--ref", "e2", "--value", "hi")
    assert cli.main(typed) == 0
    assert cli.main(debugger_action("key", "--input", "debugger", "--key", "Enter")) == 0
    scroll = debugger_action("scroll", "--input", "debugger", "--target", "document", "--dy", "300")
    assert cli.main(scroll) == 0
    assert cli.main(debugger_action("type", "--ref", "e2", "--value", "hi")) == exits.USAGE
    select = debugger_action("select", "--input", "debugger", "--ref", "e3", "--value", "x")
    assert cli.main(select) == exits.USAGE
    assert bridge.payloads == [
        ("trusted-click", {"ref": "e1"}),
        ("trusted-type", {"ref": "e2", "text": "hi"}),
        ("trusted-key", {"key": "Enter"}),
        ("trusted-scroll", {"dx": 0, "dy": 300}),
    ]
