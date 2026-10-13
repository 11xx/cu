import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from cu import browser_read as wire
from cu import browser_text, cli, exits


@pytest.fixture
def private_runtime(monkeypatch):
    root = Path(os.environ["XDG_RUNTIME_DIR"])
    root.chmod(0o700)
    monkeypatch.setenv("CU_SESSION", "fixture-driver")
    return root


def test_text_artifact_is_exclusive_private_and_retained(private_runtime, monkeypatch):
    receipt = browser_text.save(b"Searchable\ncontent")
    path = Path(receipt["path"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert receipt["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "content" not in json.dumps(receipt)
    name = path.stem.removeprefix("text-")
    with monkeypatch.context() as patch:
        patch.setattr(browser_text.secrets, "token_hex", lambda _: name)
        with pytest.raises(FileExistsError):
            browser_text.save(b"overwritten")
    assert path.read_bytes() == b"Searchable\ncontent"
    for _ in range(20):
        browser_text.save(b"bounded")
    assert len(list(path.parent.glob("text-*.txt"))) == browser_text.MAX_ARTIFACTS


@pytest.mark.parametrize("component", ["cu", "browser", "text"])
def test_text_artifact_refuses_symlinked_storage(private_runtime, tmp_path, component):
    target = tmp_path / "external"
    target.mkdir(mode=0o700)
    parent = private_runtime
    for name in ("cu", "browser", "text"):
        if name == component:
            (parent / name).symlink_to(target, target_is_directory=True)
            break
        parent /= name
        parent.mkdir(mode=0o700)
    with pytest.raises(OSError):
        browser_text.save(b"PRIVATE_CANARY")
    assert list(target.iterdir()) == []


def test_text_artifact_refuses_nonprivate_storage(private_runtime):
    private_runtime.chmod(0o755)
    with pytest.raises(wire.HelperUnavailable, match="private owned"):
        browser_text.save(b"PRIVATE_CANARY")
    assert not (private_runtime / "cu").exists()


def chunk(**overrides):
    return {
        "schema": "cu-browser-text-chunk/1", "kind": "export-text", "snapshot": "a" * 24,
        "document_id": "document-1", "generation": 0, "scope": {"selector": None},
        "redaction_policy": browser_text.POLICY, "omissions": browser_text.OMISSIONS,
        "completeness": "complete", "total_bytes": 4, "offset": 0,
        "next_offset": None, "chunk": "text", **overrides,
    }


@pytest.mark.parametrize("changes", [
    {"generation": 1}, {"generation": False}, {"document_id": "different"},
    {"snapshot": "b" * 24}, {"next_offset": 0}, {"next_offset": True},
    {"chunk": "\ud800"}, {"chunk": "a" * 16385}, {"total_bytes": 1048577},
    {"redaction_policy": "unknown"}, {"omissions": ["PRIVATE_CANARY"]},
])
def test_chunk_identity_and_bounds_refuse_without_echoing_content(changes):
    with pytest.raises(wire.ProtocolError) as raised:
        browser_text.validate_chunk(
            chunk(**changes), snapshot="a" * 24, document="document-1", generation=0,
            selector=None, offset=0, allow_partial=False,
        )
    assert "PRIVATE_CANARY" not in str(raised.value)


@pytest.mark.parametrize("failure", [None, "stale", "stop", "foreign"])
def test_text_export_end_to_end_collector_native_host_cli_file(private_runtime, capsys, failure):
    # A DOM fixture has no browser, display, account or input device.
    script = r'''
      const {JSDOM} = require("jsdom");
      const {createCollector} = require("./browser-extension/collector.js");
      const dom = new JSDOM('<main><pre>' + 'Searchable line\n'.repeat(12000) +
        'TAIL_NEEDLE</pre><input value="FORM_SECRET_CANARY">' +
        '<textarea>FORM_SECRET_CANARY</textarea></main>', {pretendToBeVisual:true});
      const collector = createCollector(dom.window.document, {idFactory:()=>"fixture-document"});
      collector.handle({type:"start", session:"fixture-session", capabilities:["read"]});
      require("readline").createInterface({input:process.stdin}).on("line", line => {
        const request = JSON.parse(line);
        if (request.payload.offset && fault === "stale") dom.window.document.body.append("changed");
        if (request.payload.offset && fault === "stop")
          collector.handle({type:"stop", session:"fixture-session"});
        const result = collector.handle({type:"read-request", request});
        process.stdout.write(JSON.stringify(result) + "\n");
      });
    '''
    node = subprocess.Popen(
        ["node", "-e", f"const fault = {json.dumps(failure)};\n" + script],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    host = subprocess.Popen(
        [sys.executable, "-m", "cu.browser_read", "host"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    errors = []
    outgoing = []
    try:
        ready = wire.read_frame(host.stdout)
        grant = {
            "session": "fixture-session", "tab_id": 7, "window_id": 3,
            "document_id": "fixture-document", "generation": 0,
            "active": True, "hidden": False, "cu_session": "fixture-driver",
        }
        wire.write_frame(host.stdin, {
            "protocol": wire.PROTOCOL, "version": wire.VERSION, "type": "hello",
            "grants": [grant],
        })

        def browser():
            try:
                while True:
                    request = wire.read_frame(host.stdout)
                    if request is None:
                        break
                    if request["type"] != "request":
                        continue
                    node.stdin.write(json.dumps(request) + "\n")
                    node.stdin.flush()
                    result = json.loads(node.stdout.readline())
                    outgoing.append(result)
                    wire.write_frame(host.stdin, result)
                    if failure == "foreign" and len(outgoing) == 1:
                        wire.write_frame(host.stdin, {
                            "protocol": wire.PROTOCOL, "version": wire.VERSION, "type": "grant",
                            "grant": {**grant, "cu_session": "different-driver"},
                        })
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=browser, daemon=True)
        thread.start()
        code = cli.main([
            "web", "export-text", "--helper", ready["socket"], "--timeout", "30", "--json",
        ])
        captured = capsys.readouterr()
        text = ""
        if failure is None:
            assert code == 0
            receipt = json.loads(captured.out)
            text = Path(receipt["path"]).read_text()
            assert text.index("TAIL_NEEDLE") > 162000
            assert receipt["completeness"] == "complete"
            assert receipt["bytes"] == len(text.encode())
            assert receipt["sha256"] == hashlib.sha256(text.encode()).hexdigest()
            count = len(outgoing)
            for needle in ("TAIL_NEEDLE", "Searchable"):
                assert needle in Path(receipt["path"]).read_text()
            assert len(outgoing) == count > 10
        else:
            assert code == {
                "stale": exits.BROWSER_DOCUMENT_STALE, "stop": exits.BROWSER_READ_REFUSED,
                "foreign": exits.BROWSER_SESSION_FOREIGN,
            }[failure]
            assert not list(private_runtime.glob("cu/browser/text/text-*.txt"))
        assert "FORM_SECRET_CANARY" not in json.dumps(outgoing) + text + captured.out + captured.err
        assert "TAIL_NEEDLE" not in captured.out
        host.send_signal(signal.SIGTERM)
        assert host.wait(5) == 0
        thread.join(5)
        assert not thread.is_alive() and not errors
    finally:
        for process in (host, node):
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def test_text_export_missing_owner_refuses(private_runtime, monkeypatch, capsys):
    monkeypatch.delenv("CU_SESSION")
    assert cli.main(["web", "export-text", "--json"]) == exits.BROWSER_READ_UNAVAILABLE
    assert "chunk" not in capsys.readouterr().out
