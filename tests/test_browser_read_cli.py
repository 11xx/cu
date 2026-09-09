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
            {"protocol": wire.PROTOCOL, "version": 1, "type": "hello", "grants": [grant]},
        )
        errors = []

        def browser():
            try:
                request = wire.read_frame(process.stdout)
                wire.write_frame(
                    process.stdin,
                    {
                        "protocol": wire.PROTOCOL,
                        "version": 1,
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
