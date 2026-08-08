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


# --- a resize is not a scale ----------------------------------------------
#
# Under a tiling compositor a window's size is not its own to keep, and the
# controls inside it do not all follow the change the same way. A toolbar keeps
# its pixel height; the field inside it stretches. So a landmark resolves to
# what covers every possibility, and says how much of it to believe.


def urlbar(learned=(1000, 1000), **kw):
    """A toolbar control: near the top, a fixed 40px tall at the learned size."""
    return knowledge.set_landmark(
        "zen", "urlbar", {"x": 0.1, "y": 0.02, "w": 0.6, "h": 0.04},
        learned_rect={"x": 0, "y": 0, "w": learned[0], "h": learned[1]}, **kw
    )


def covers(region, top, bottom):
    return region["y"] <= top and region["y"] + region["h"] >= bottom


def test_the_learned_geometry_is_recorded_with_the_fraction():
    """Without it, the only available projection is the one that stretches."""
    assert urlbar()["learned_rect"]["h"] == 1000


def test_an_unchanged_window_is_resolved_exactly():
    """Every projection coincides here, so hedging would only spend pixels."""
    aim = knowledge.resolve(urlbar(), window(x=0, y=0, w=1000, h=1000))
    assert aim["confidence"] == "exact"
    assert aim["rect"] == {"x": 100.0, "y": 20.0, "w": 600.0, "h": 40.0}


def test_a_halved_window_still_covers_a_fixed_height_toolbar():
    """The failure this exists to prevent: the toolbar is still 20px down and
    40px tall, while the stored fraction now claims 10px down and 20px tall."""
    aim = knowledge.resolve(urlbar(), window(x=0, y=0, w=1000, h=500))
    assert aim["confidence"] == "widened"
    assert covers(aim["rect"], 20.0, 60.0)


def test_hedging_widens_only_the_axis_that_changed():
    tall = knowledge.resolve(urlbar(), window(x=0, y=0, w=1000, h=500))["rect"]
    assert tall["w"] < 700.0


def test_a_named_anchor_narrows_what_must_be_covered():
    """A run that knows how a control behaves can say so; it buys a smaller
    capture, and being wrong costs a miss rather than a wrong coordinate."""
    auto = knowledge.resolve(urlbar(), window(x=0, y=0, w=1000, h=500))["rect"]
    pinned = knowledge.resolve(
        urlbar(anchor_y="top"), window(x=0, y=0, w=1000, h=500)
    )["rect"]
    assert pinned["h"] < auto["h"]
    assert covers(pinned, 20.0, 60.0)


def test_an_edge_anchored_control_follows_its_edge():
    """A control 50px from the right edge is 50px from the right edge at any
    width — the fraction that describes it changes with every resize."""
    mark = knowledge.set_landmark(
        "zen", "menu", {"x": 0.9, "y": 0.0, "w": 0.05, "h": 0.04},
        learned_rect={"x": 0, "y": 0, "w": 1000, "h": 1000}, anchor_x="right",
    )
    aim = knowledge.resolve(mark, window(x=0, y=0, w=2000, h=1000))
    assert aim["rect"]["x"] <= 1900.0 <= aim["rect"]["x"] + aim["rect"]["w"]


def test_a_projection_that_lands_outside_the_window_is_dropped():
    """A control 940px above the bottom edge of a 500px window is not there,
    and a hypothesis that says so would widen the region to no purpose."""
    aim = knowledge.resolve(urlbar(), window(x=0, y=0, w=1000, h=500))
    assert aim["rect"]["h"] < 200.0


def test_a_window_too_far_from_the_learned_one_reports_itself_stale():
    """Grown fourfold on both axes, the surviving projections are far enough
    apart that covering them takes most of the window — which is not a hint.
    It says so and hands back the window, which is what the caller would have
    captured with no landmark at all."""
    frame = window(x=0, y=0, w=4000, h=4000)
    aim = knowledge.resolve(urlbar(), frame)
    assert aim["confidence"] == "stale"
    assert aim["rect"] == frame["logical_rect"]


def test_a_widened_band_across_a_much_wider_window_is_still_a_hint():
    """Width alone diverging leaves a thin full-width strip: weak on one axis,
    and still a large majority of the window's pixels never captured."""
    aim = knowledge.resolve(urlbar(), window(x=0, y=0, w=4000, h=1000))
    assert aim["confidence"] == "widened"
    assert aim["rect"]["h"] < 200.0


def test_a_landmark_with_no_learned_geometry_is_never_exact():
    """Older records, and any learned while the window could not be found."""
    mark = knowledge.set_landmark("zen", "urlbar", {"x": 0.1, "y": 0.02, "w": 0.6, "h": 0.04})
    aim = knowledge.resolve(mark, window())
    assert aim["confidence"] == "widened"


def test_a_hedged_region_stays_inside_its_own_window():
    """Beyond the window is another application's pixels — always, under a
    tiling compositor — and reading those answers a different question."""
    frame = window(x=100, y=50, w=1000, h=400)
    rect = knowledge.resolve(urlbar(), frame)["rect"]
    bounds = frame["logical_rect"]
    assert rect["x"] >= bounds["x"] and rect["y"] >= bounds["y"]
    assert rect["x"] + rect["w"] <= bounds["x"] + bounds["w"]
    assert rect["y"] + rect["h"] <= bounds["y"] + bounds["h"]


def test_an_unusable_anchor_is_refused():
    with pytest.raises(knowledge.KnowledgeError, match="anchor"):
        knowledge.set_landmark("zen", "urlbar", {"x": 0, "y": 0, "w": 0.1, "h": 0.1},
                               anchor_y="left")


def test_relearning_keeps_the_geometry_when_none_is_supplied():
    urlbar()
    knowledge.set_landmark("zen", "urlbar", {"x": 0.2, "y": 0.02, "w": 0.6, "h": 0.04})
    assert knowledge.get_landmark("zen", "urlbar")["learned_rect"]["w"] == 1000


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
