import json
import os
import selectors
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from cu import browser_read as wire
from cu import browser_read_cli, cli, exits


def test_cli_absence_is_optional(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert cli.main(["web", "hosts", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["helpers"] == []
    assert cli.main(["web", "read", "--json"]) == exits.BROWSER_READ_UNAVAILABLE


def test_setup_is_explicit_and_does_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data with spaces"))
    result = browser_read_cli.install_host()
    assert not result["extension_installed"] and not result["tab_granted"]
    manifest = json.loads(Path(result["manifest"]).read_text())
    assert manifest["allowed_extensions"] == ["cu-browser-read@local"]
    assert browser_read_cli.install_host() == result
    with open(result["launcher"], "w") as handle:
        handle.write("operator content")
    with pytest.raises(cli.Refused, match="overwrite"):
        browser_read_cli.install_host()


def test_thorium_setup_registers_the_pinned_origin_in_one_profile_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    root = tmp_path / "thorium-profile"
    root.mkdir()
    result = browser_read_cli.install_host("thorium", str(root))
    manifest_path = Path(result["manifest"])
    assert manifest_path == root / "NativeMessagingHosts" / "org.cu.read.json"
    manifest = json.loads(manifest_path.read_text())
    origin = f"chrome-extension://{browser_read_cli.CHROMIUM_EXTENSION_ID}/"
    assert manifest["allowed_origins"] == [origin]
    assert "allowed_extensions" not in manifest
    assert not (tmp_path / ".mozilla").exists()
    assert not result["extension_installed"] and not result["tab_granted"]
    assert browser_read_cli.install_host("thorium", str(root)) == result

    monkeypatch.setitem(
        browser_read_cli.browser.CHROMIUM_PRODUCTS,
        "thorium",
        {**browser_read_cli.browser.CHROMIUM_PRODUCTS["thorium"], "profiles": (str(root),)},
    )
    assert browser_read_cli.install_host("thorium") == result

    manifest_path.write_text("{}")
    with pytest.raises(cli.Refused, match="overwrite"):
        browser_read_cli.install_host("thorium", str(root))
    with pytest.raises(cli.Refused, match="never creates one"):
        browser_read_cli.install_host("thorium", str(tmp_path / "absent"))
    with pytest.raises(ValueError, match="absolute"):
        browser_read_cli.install_host("thorium", "relative-profile")
    with pytest.raises(ValueError, match="Chromium-family"):
        browser_read_cli.install_host("firefox", str(root))


def test_setup_command_selects_the_browser(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    root = tmp_path / "profile"
    root.mkdir()
    args = ["web", "setup", "--browser", "thorium", "--user-data-dir", str(root), "--json"]
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["extension_id"] == browser_read_cli.CHROMIUM_EXTENSION_ID
    assert cli.main(["web", "setup", "--user-data-dir", str(root), "--json"]) == exits.USAGE


def test_native_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    process = subprocess.Popen(
        [sys.executable, "-m", "cu.browser_read", "host"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(2), "native host did not send ready"
        ready = wire.read_frame(process.stdout)
        grant = {
            "session": "fixture-session",
            "tab_id": 1,
            "window_id": 1,
            "document_id": "fixture-document",
            "generation": 0,
            "active": True,
            "hidden": False,
        }
        wire.write_frame(
            process.stdin,
            {
                "protocol": wire.PROTOCOL,
                "version": wire.VERSION,
                "type": "hello",
                "grants": [grant],
            },
        )
        errors = []

        def browser():
            try:
                request = wire.read_frame(process.stdout)
                wire.write_frame(
                    process.stdin,
                    {
                        "protocol": wire.PROTOCOL,
                        "version": wire.VERSION,
                        "type": "response",
                        **{k: request[k] for k in ("id", "session", "tab_id", "document_id")},
                        "ok": True,
                        "result": {"fixture": "matched"},
                    },
                )
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=browser, daemon=True)
        thread.start()
        with wire.BrowserReadClient.connect(ready["socket"]) as client:
            result = client.observe(
                session=grant["session"], tab_id=1, document_id=grant["document_id"]
            )
            assert result["result"] == {"fixture": "matched"}
        thread.join(2)
        assert not thread.is_alive() and not errors
        process.send_signal(signal.SIGTERM)
        assert process.wait(2) == 0
        assert not os.path.exists(ready["socket"])
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=2)


def test_native_host_forwards_a_session_control_before_any_grant(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    process = subprocess.Popen(
        [sys.executable, "-m", "cu.browser_read", "host"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(2), "native host did not send ready"
        ready = wire.read_frame(process.stdout)
        envelope = {"protocol": wire.PROTOCOL, "version": wire.VERSION}
        wire.write_frame(process.stdin, {**envelope, "type": "hello", "grants": []})
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
        errors = []

        def browser():
            try:
                control = wire.read_frame(process.stdout)
                assert (control["type"], control["operation"]) == ("control", "grant")
                wire.write_frame(process.stdin, {**envelope, "type": "grant", "grant": grant})
                wire.write_frame(
                    process.stdin,
                    {
                        **envelope,
                        "type": "response",
                        "id": control["id"],
                        **{key: grant[key] for key in ("session", "tab_id", "document_id")},
                        "ok": True,
                        "result": {"grant": grant},
                    },
                )
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=browser, daemon=True)
        thread.start()
        with wire.BrowserReadClient.connect(ready["socket"]) as client:
            assert client.metadata["grants"] == []
            result = client.control("grant", capabilities=["read", "interact"])
            assert result["result"]["grant"] == grant
            assert client.metadata["grants"] == [grant]
        thread.join(2)
        assert not thread.is_alive() and not errors
        process.send_signal(signal.SIGTERM)
        assert process.wait(2) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=2)


def test_native_host_cancels_a_disconnected_waiter(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    process = subprocess.Popen(
        [sys.executable, "-m", "cu.browser_read", "host"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(2)
        ready = wire.read_frame(process.stdout)
        grant = {
            "session": "s",
            "tab_id": 1,
            "window_id": 1,
            "document_id": "d",
            "generation": 0,
            "active": True,
            "hidden": False,
            "capabilities": ["read"],
        }
        wire.write_frame(
            process.stdin,
            {
                "protocol": wire.PROTOCOL,
                "version": wire.VERSION,
                "type": "hello",
                "grants": [grant],
            },
        )
        with (
            wire.BrowserReadClient.connect(ready["socket"]) as client,
            pytest.raises(wire.BrowserReadTimeout),
        ):
            client.wait(
                session="s",
                tab_id=1,
                document_id="d",
                timeout=0.05,
                payload={"selector": "video", "state": "present", "timeout_ms": 1000},
            )
        request = wire.read_frame(process.stdout)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(2), "client disconnect did not cancel its native request"
        cancelled = wire.read_frame(process.stdout)
        assert cancelled == {
            "protocol": wire.PROTOCOL,
            "version": wire.VERSION,
            "type": "cancel",
            "id": request["id"],
            "session": "s",
        }
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=2)


def test_native_host_delivers_a_reload_receipt_before_the_browser_disconnects(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    process = subprocess.Popen(
        [sys.executable, "-m", "cu.browser_read", "host"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(2), "native host did not send ready"
        ready = wire.read_frame(process.stdout)
        grant = {
            "session": "fixture-session",
            "tab_id": 1,
            "window_id": 1,
            "document_id": "fixture-document",
            "generation": 3,
            "active": True,
            "hidden": False,
            "capabilities": ["read", "interact"],
        }
        envelope = {"protocol": wire.PROTOCOL, "version": wire.VERSION}
        wire.write_frame(process.stdin, {**envelope, "type": "hello", "grants": [grant]})
        errors = []

        def browser():
            try:
                request = wire.read_frame(process.stdout)
                identity = {k: request[k] for k in ("session", "tab_id", "document_id")}
                wire.write_frame(process.stdin, {**envelope, "type": "revoke", **identity})
                wire.write_frame(
                    process.stdin,
                    {
                        **envelope,
                        "type": "response",
                        "id": request["id"],
                        **identity,
                        "ok": True,
                        "result": {
                            "kind": "action",
                            "action": "reload",
                            "backend": "tabs",
                            "document_id": grant["document_id"],
                            "generation": 3,
                            "verification": "unverified",
                            "revoked_grant": {**grant, "reason": "reload"},
                            "new_document_grant": "none",
                        },
                    },
                )
                process.stdin.close()
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=browser, daemon=True)
        thread.start()
        with wire.BrowserReadClient.connect(ready["socket"]) as client:
            result = client.action(
                "reload",
                session=grant["session"],
                tab_id=1,
                document_id=grant["document_id"],
                generation=3,
                payload={},
            )
            assert result["result"]["new_document_grant"] == "none"
            assert client.metadata["grants"] == []
        thread.join(2)
        assert not thread.is_alive() and not errors
        assert process.wait(2) == 0
        assert not os.path.exists(ready["socket"])
    finally:
        if process.poll() is None:
            process.kill()
        # The fake browser closed stdin itself, so communicate() cannot flush it.
        process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()
