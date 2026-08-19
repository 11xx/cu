"""Help renders like the sibling tools, and says the same thing they do.

The shape is asserted rather than eyeballed because it is easy to reintroduce
argparse's default layout with one parser added the ordinary way.
"""

from __future__ import annotations

import argparse

import pytest

from cu import cli


def _parsers():
    """Every parser in the tree, keyed by the command path that reaches it."""
    root = cli.build_parser()
    pending = [("cu", root)]
    while pending:
        path, parser = pending.pop()
        yield path, parser
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    pending.append((f"{path} {name}", child))


ALL = list(_parsers())


@pytest.mark.parametrize("path,parser", ALL, ids=[p for p, _ in ALL])
def test_help_leads_with_a_sentence_then_usage(path, parser):
    lines = parser.format_help().splitlines()
    assert lines[0] and not lines[0].startswith("Usage:"), path
    assert any(line.startswith("Usage: ") for line in lines), path


@pytest.mark.parametrize("path,parser", ALL, ids=[p for p, _ in ALL])
def test_sections_are_named_for_what_they_hold(path, parser):
    text = parser.format_help()
    assert "positional arguments:" not in text, path
    assert "options:\n" not in text, path
    assert "Options:" in text, path


@pytest.mark.parametrize("path,parser", ALL, ids=[p for p, _ in ALL])
def test_descriptions_get_a_column_wide_enough_for_a_sentence(path, parser):
    formatter = parser._get_formatter()
    assert formatter._width - formatter._max_help_position >= 60, path
