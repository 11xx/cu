"""Learned knowledge, and the boundary that keeps it safe.

Knowledge is written automatically by whatever ran last, with no human review,
so the property under test is that it can only ever aim a *capture*. A stored
coordinate that acts directly is the confidently-wrong click this project
exists to prevent, and it fails silently because the click lands on something.
"""

from __future__ import annotations

import pytest

from cu import knowledge, sandbox


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))


def window(x=100, y=50, w=1000, h=500):
    return {"logical_rect": {"x": x, "y": y, "w": w, "h": h}}


# --- landmarks are fractions ---------------------------------------------


def test_a_landmark_is_stored_as_a_fraction():
    mark = knowledge.set_landmark("zen", "urlbar", {"x": 0.1, "y": 0.02, "w": 0.6, "h": 0.04})
    assert mark["rect"]["x"] == 0.1


def test_an_absolute_coordinate_is_refused():
    """Pixels describe one arrangement of one desktop at one moment."""
    with pytest.raises(knowledge.KnowledgeError):
        knowledge.set_landmark("zen", "urlbar", {"x": 412.0, "y": 300.0, "w": 100.0, "h": 20.0})


def test_a_landmark_resolves_against_current_geometry_not_remembered_geometry():
    """A moved window must yield a correctly-aimed region rather than a
    confidently wrong one."""
    mark = knowledge.set_landmark("zen", "urlbar", {"x": 0.5, "y": 0.0, "w": 0.25, "h": 0.1})
    here = knowledge.to_logical(mark, window(x=0, y=0, w=1000, h=500))
    moved = knowledge.to_logical(mark, window(x=2048, y=100, w=800, h=400))
    assert here == {"x": 500.0, "y": 0.0, "w": 250.0, "h": 50.0}
    assert moved == {"x": 2448.0, "y": 100.0, "w": 200.0, "h": 40.0}


def test_a_degenerate_landmark_still_yields_a_capturable_area():
    mark = knowledge.set_landmark("zen", "dot", {"x": 0.5, "y": 0.5, "w": 0.0, "h": 0.0})
    resolved = knowledge.to_logical(mark, window())
    assert resolved["w"] >= 1.0 and resolved["h"] >= 1.0


# --- scoping --------------------------------------------------------------


def test_a_context_scopes_a_landmark_to_one_site():
    """A browser's search box belongs to a page, not to the browser."""
    knowledge.set_landmark("zen", "search", {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.05},
                           context="example.com")
    with pytest.raises(knowledge.KnowledgeError):
        knowledge.get_landmark("zen", "search")
    assert knowledge.get_landmark("zen", "search", "example.com")["rect"]["x"] == 0.1


def test_an_unscoped_landmark_answers_a_scoped_lookup():
    """The URL bar belongs to the browser whichever page is open."""
    knowledge.set_landmark("zen", "urlbar", {"x": 0.1, "y": 0.0, "w": 0.6, "h": 0.04})
    assert knowledge.get_landmark("zen", "urlbar", "example.com")["rect"]["w"] == 0.6


def test_an_unknown_landmark_lists_what_is_known():
    knowledge.set_landmark("zen", "urlbar", {"x": 0.1, "y": 0.0, "w": 0.6, "h": 0.04})
    with pytest.raises(knowledge.KnowledgeError, match="urlbar"):
        knowledge.get_landmark("zen", "nosuch")


# --- evidence -------------------------------------------------------------


def test_hits_and_misses_accumulate():
    """A landmark that keeps missing belongs to an application that changed;
    the counters are how that becomes visible rather than quietly costing an
    extra capture every run."""
    knowledge.set_landmark("zen", "urlbar", {"x": 0.1, "y": 0.0, "w": 0.6, "h": 0.04})
    knowledge.score("zen", "urlbar", True)
    knowledge.score("zen", "urlbar", False)
    mark = knowledge.get_landmark("zen", "urlbar")
    assert (mark["confirmed"], mark["missed"]) == (1, 1)


def test_relearning_keeps_the_evidence_and_the_original_date():
    knowledge.set_landmark("zen", "urlbar", {"x": 0.1, "y": 0.0, "w": 0.6, "h": 0.04})
    knowledge.score("zen", "urlbar", True)
    first = knowledge.get_landmark("zen", "urlbar")["learned_at"]
    knowledge.set_landmark("zen", "urlbar", {"x": 0.2, "y": 0.0, "w": 0.6, "h": 0.04})
    mark = knowledge.get_landmark("zen", "urlbar")
    assert mark["confirmed"] == 1 and mark["learned_at"] == first


def test_notes_hold_facts_with_no_coordinate():
    knowledge.add_note("Alacritty", "paste is ctrl+shift+v here")
    assert knowledge.load("Alacritty")["notes"][0]["text"] == "paste is ctrl+shift+v here"


def test_forgetting_one_landmark_leaves_the_rest():
    knowledge.set_landmark("zen", "a", {"x": 0, "y": 0, "w": 0.1, "h": 0.1})
    knowledge.set_landmark("zen", "b", {"x": 0, "y": 0, "w": 0.1, "h": 0.1})
    knowledge.forget("zen", "a")
    assert list(knowledge.load("zen")["landmarks"]) == ["b"]


def test_an_unknown_application_is_empty_rather_than_an_error():
    assert knowledge.load("never-seen")["landmarks"] == {}


# --- the sandbox guard ----------------------------------------------------


@pytest.mark.parametrize("name", ["DP-1", "eDP-1", "HDMI-A-1", ""])
def test_only_headless_outputs_may_be_removed(name):
    """`hyprctl output remove` does not distinguish, and removing a physical
    monitor blanks the desk."""
    assert not sandbox.is_headless(name)
    with pytest.raises(sandbox.SandboxError, match="not a headless output"):
        sandbox.remove(name)


def test_headless_names_are_recognised_whatever_their_index():
    assert sandbox.is_headless("HEADLESS-1")
    assert sandbox.is_headless("HEADLESS-27")
