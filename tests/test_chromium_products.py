"""Explicit Chrome/Chromium product reporting without guessed installation."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

from cu import browser, browser_read_cli, cli


def test_product_candidates_require_binary_and_profile(tmp_path, monkeypatch):
    profile = tmp_path / "chromium"
    profile.mkdir()
    monkeypatch.setattr(
        browser,
        "CHROMIUM_PRODUCTS",
        {
            "chromium": {
                "name": "Chromium",
                "binaries": ("chromium",),
                "profiles": (str(profile),),
            },
            "google-chrome": {
                "name": "Google Chrome",
                "binaries": ("google-chrome",),
                "profiles": (str(tmp_path / "chrome"),),
            },
        },
    )
    monkeypatch.setattr(
        browser.shutil,
        "which",
        lambda name: "/usr/bin/chromium" if name == "chromium" else None,
    )
    products = browser.product_candidates()
    chromium = next(product for product in products if product["id"] == "chromium")
    chrome = next(product for product in products if product["id"] == "google-chrome")
    assert chromium["available"] is True
    assert chrome["available"] is False


def test_products_command_reports_candidates_without_grant(monkeypatch, capsys):
    monkeypatch.setattr(cli.browser, "discover", lambda: [])
    monkeypatch.setattr(cli.browser, "product_candidates", lambda: [
        {"name": "Chromium", "available": False, "resolved": None, "profiles": []}
    ])
    args = argparse.Namespace(
        action="products", browser=None, backend=None, dry_run=False, json=True
    )
    assert cli.cmd_browser(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["products"][0]["available"] is False


def test_setup_state_is_native_host_only_where_it_was_proven(tmp_path, monkeypatch):
    profile = tmp_path / "thorium"
    profile.mkdir()
    monkeypatch.setattr(
        browser,
        "CHROMIUM_PRODUCTS",
        {
            "thorium": {
                "name": "Thorium",
                "binaries": ("thorium-browser",),
                "profiles": (str(profile),),
                "native_host": True,
            },
            "chromium": {
                "name": "Chromium",
                "binaries": ("chromium",),
                "profiles": (str(tmp_path / "missing"),),
            },
        },
    )
    monkeypatch.setattr(browser.shutil, "which", lambda name: f"/usr/bin/{name}")
    products = {product["id"]: product for product in browser.product_candidates()}
    assert products["thorium"]["setup"] == "native-host"
    assert products["thorium"]["available"] is True
    assert products["chromium"]["setup"] == "unprobed"
    assert products["chromium"]["available"] is False


def test_pinned_extension_id_follows_the_committed_public_key():
    manifest_path = Path(__file__).parents[1] / "browser-extension/chromium/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    digest = hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
    derived = "".join(chr(ord("a") + int(char, 16)) for char in digest)
    assert derived == browser_read_cli.CHROMIUM_EXTENSION_ID
