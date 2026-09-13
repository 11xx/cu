"""The public launcher serves only its declared packaged native assets."""

import json
from unittest.mock import patch

import pytest

from cu import cli
from cu.native_playground import server


def test_packaged_assets_are_available_outside_the_checkout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for path in ("/", "/app.js?cache=ignored", "/state.js", "/style.css"):
        served = server.asset(path)
        assert served is not None
        content_type, body = served
        assert content_type
        assert body
    assert server.asset("/index.html") is None
    assert server.asset("/../pyproject.toml") is None
    assert b"hover-action" not in server.asset("/")[1]


def test_launcher_emits_explicit_foreground_contract(capsys):
    class FakeServer:
        server_port = 43127

        def __init__(self, address, handler):
            self.address = address
            self.handler = handler
            self.timeout = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def handle_request(self):
            raise KeyboardInterrupt

    with patch.object(server, "HTTPServer", FakeServer):
        server.serve(0, as_json=True)

    announcement = json.loads(capsys.readouterr().out)
    assert announcement == {
        "url": "http://127.0.0.1:43127/",
        "suite": "native",
        "fixture": "cu-native-playground/1",
        "assets": ["/", "/app.js", "/state.js", "/style.css"],
        "extension_required": False,
        "lifecycle": "foreground-until-interrupted",
    }


@pytest.mark.parametrize("port", ["-1", "65536"])
def test_invalid_port_refuses_without_starting(port, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/not-a-desktop-session")
    assert cli.main(["playground", "--port", port]) == 2
