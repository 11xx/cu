"""Facts about the machine, kept apart from facts about an application."""

from __future__ import annotations

import pytest

from cu import cli, knowledge


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))


def test_an_unknown_host_reads_as_empty_rather_than_missing():
    assert knowledge.load_host() == {"notes": []}


def test_a_host_note_survives_and_keeps_its_order():
    knowledge.add_host_note("keystrokes pass through a remapping layer")
    knowledge.add_host_note("DP-2 reports a different scale")
    texts = [n["text"] for n in knowledge.load_host()["notes"]]
    assert texts == [
        "keystrokes pass through a remapping layer",
        "DP-2 reports a different scale",
    ]


def test_an_application_named_host_keeps_its_own_record():
    """The separation has to be structural, not a reserved word.

    KEY_PATTERN requires an application key to start alphanumeric, so no
    application can resolve to the host file however it is named.
    """
    knowledge.add_host_note("about the machine")
    knowledge.add_note("host", "about an application called host")

    assert [n["text"] for n in knowledge.load_host()["notes"]] == ["about the machine"]
    assert [n["text"] for n in knowledge.load("host")["notes"]] == [
        "about an application called host"
    ]
    assert knowledge.path_for("host") != knowledge.host_path()


def test_the_host_is_not_listed_as_an_application():
    knowledge.add_host_note("about the machine")
    assert knowledge.listing() == []


def test_a_note_is_dropped_by_the_position_the_listing_prints():
    for text in ("first", "second", "third"):
        knowledge.add_host_note(text)
    knowledge.forget_host(2)
    assert [n["text"] for n in knowledge.load_host()["notes"]] == ["first", "third"]


def test_forgetting_a_position_that_is_not_there_refuses():
    knowledge.add_host_note("only one")
    with pytest.raises(knowledge.KnowledgeError):
        knowledge.forget_host(2)
    assert len(knowledge.load_host()["notes"]) == 1


def test_forgetting_everything_leaves_nothing_behind():
    knowledge.add_host_note("about the machine")
    knowledge.forget_host()
    assert knowledge.load_host() == {"notes": []}
    assert not knowledge.host_path().exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["know", "host", "a fact", "--forget"],
        ["know", "host", "a fact", "--forget", "1"],
        ["know", "host", "--forget", "1", "a fact"],
    ],
)
def test_host_text_and_forget_are_mutually_exclusive(argv):
    """Adding and dropping in one command: one of the two would be discarded."""
    knowledge.add_host_note("already here")
    with pytest.raises(SystemExit) as exit_code:
        cli.main(argv)
    assert exit_code.value.code == 2
    assert [n["text"] for n in knowledge.load_host()["notes"]] == ["already here"]


def test_each_host_form_survives_on_its_own():
    """The exclusion must not cost the three forms the command actually has."""
    assert cli.main(["know", "host", "a fact"]) == 0
    assert cli.main(["know", "host"]) == 0
    assert cli.main(["know", "host", "--forget"]) == 0
    assert knowledge.load_host() == {"notes": []}


def test_a_corrupt_host_record_reads_as_an_error_rather_than_as_empty():
    knowledge.add_host_note("about the machine")
    knowledge.host_path().write_text("{not json")
    with pytest.raises(knowledge.KnowledgeError):
        knowledge.load_host()
