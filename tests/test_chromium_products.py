"""Explicit Chrome/Chromium product reporting without guessed installation."""

from __future__ import annotations

import argparse
import json

from cu import browser, cli


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
