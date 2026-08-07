"""Recipes: what survives being stored, and what may not run.

A recipe is generated code aimed at a live, possibly authenticated browser, so
the interesting properties are the ones that stop it running rather than the
ones that make it work.
"""

from __future__ import annotations

import pytest

from cu import recipes


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))


def log(**extra):
    return {
        "action": "key", "chord": "ctrl+t", "semantic": None,
        "window": {"class": "zen"}, **extra,
    }


def build(entries=None, name="demo"):
    return recipes.from_log(entries or [log()], name, "example.com", "a task")


# --- names become filenames ----------------------------------------------


@pytest.mark.parametrize("bad", ["../escape", "Has Spaces", "UPPER", "", "/abs"])
def test_an_unusable_name_is_refused_rather_than_rewritten(bad):
    """Sanitising two different names into one file would let a recipe replay
    steps that belong to another."""
    with pytest.raises(recipes.RecipeError):
        recipes.validate_name(bad)


def test_ordinary_names_pass():
    assert recipes.validate_name("login.bank-2") == "login.bank-2"


# --- what becomes a step -------------------------------------------------


def test_a_dry_run_is_history_rather_than_procedure():
    assert recipes.step_from_log(log(dry_run=True)) is None


def test_a_click_without_a_relative_position_is_not_replayable():
    """An absolute coordinate describes one arrangement of one desktop."""
    entry = {"action": "click", "window": {"class": "zen"}, "relative": None}
    assert recipes.step_from_log(entry) is None


def test_a_click_keeps_its_window_relative_position():
    entry = {
        "action": "click", "window": {"class": "zen"},
        "relative": {"x": 0.5, "y": 0.25}, "button": "left", "count": 2,
    }
    step = recipes.step_from_log(entry)
    assert step["relative"] == {"x": 0.5, "y": 0.25}
    assert step["window_class"] == "zen"
    assert step["count"] == 2


def test_a_browser_step_keeps_the_action_not_the_chord():
    """A keymap changed between recording and replay should move the recipe
    with it; a frozen chord would fire a binding that now means something else."""
    entry = {"action": "browser", "semantic": "focus-urlbar",
             "chord": "ctrl+alt+shift+f4", "browser": "Zen", "window": {"class": "zen"}}
    step = recipes.step_from_log(entry)
    assert step["semantic"] == "focus-urlbar"
    assert "chord" not in step


def test_typed_text_becomes_a_parameter():
    """The log never holds what was typed, which is also what lets one recipe
    serve more than one input."""
    recipe = build([{"action": "type", "length": 8, "method": "clipboard", "window": {}}])
    assert recipe["steps"][0]["param"] == "text1"
    assert "text" not in recipe["steps"][0]


def test_a_log_slice_with_nothing_replayable_is_an_error():
    with pytest.raises(recipes.RecipeError):
        recipes.from_log([log(dry_run=True)], "demo", "", "")


# --- the review gate ------------------------------------------------------


def test_a_new_recipe_is_unreviewed():
    recipes.save(build())
    assert recipes.unreviewed_reason(recipes.load("demo")) == "it has never been reviewed"


def test_approval_makes_it_replayable():
    recipes.save(build())
    recipes.approve("demo")
    assert recipes.unreviewed_reason(recipes.load("demo")) is None


def test_editing_the_steps_revokes_the_approval():
    """This is the whole point: a stored recipe must never change silently."""
    recipes.save(build())
    recipes.approve("demo")
    edited = recipes.load("demo")
    edited["steps"].append({"action": "key", "chord": "ctrl+w", "semantic": None})
    recipes.save(edited)
    assert "changed since the review" in recipes.unreviewed_reason(recipes.load("demo"))


def test_recording_an_outcome_does_not_revoke_the_approval():
    """A failed replay is not an edit; invalidating the review would make every
    recorded failure require a fresh reading of steps nobody touched."""
    recipes.save(build())
    recipes.approve("demo")
    recipes.record_outcome("demo", "failed", "site changed")
    assert recipes.unreviewed_reason(recipes.load("demo")) is None
    assert recipes.load("demo")["history"][0]["outcome"] == "failed"


def test_saving_again_increments_the_revision():
    recipes.save(build())
    recipes.save(build())
    assert recipes.load("demo")["revision"] == 2


# --- verification ---------------------------------------------------------


def test_no_verification_is_not_success():
    """A replay that clicked into empty space looks identical to one that
    worked, unless something says otherwise."""
    assert recipes.check(None) is None


def test_an_unknown_verification_kind_is_an_error():
    with pytest.raises(recipes.RecipeError):
        recipes.check({"kind": "vibes"})


def test_a_missing_recipe_is_named_in_the_error():
    with pytest.raises(recipes.RecipeError, match="absent"):
        recipes.load("absent")
