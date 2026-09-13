"""The public launcher serves only its packaged fixture on loopback."""

import json
import signal
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from cu import cli


def test_playground_launches_outside_the_checkout_and_stops(tmp_path):
    process = subprocess.Popen(
        [sys.executable, "-m", "cu.cli", "playground", "--json"],
        cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        announcement = json.loads(process.stdout.readline())
        assert announcement["extension_required"] is False
        assert announcement["url"].startswith("http://127.0.0.1:")
        with urlopen(announcement["url"], timeout=3) as response:
            page = response.read().decode()
            assert response.headers["Cache-Control"] == "no-store"
        assert "Native basics" in page and "Schedule readiness" in page
        for path in ("AGENTS.md", "../pyproject.toml", "media.webm"):
            with pytest.raises(HTTPError) as caught:
                urlopen(announcement["url"] + path, timeout=3)
            assert caught.value.code == 404
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=3) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


@pytest.mark.parametrize("port", ["-1", "65536"])
def test_invalid_port_refuses_without_starting(port, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/not-a-desktop-session")
    assert cli.main(["playground", "--port", port]) == 2
