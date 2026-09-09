"""Recipes: what survives being stored, and what may not run.

A recipe is generated code aimed at a live, possibly authenticated browser, so
the interesting properties are the ones that stop it running rather than the
ones that make it work.
"""

from __future__ import annotations

import pytest

from cu import capture, cli, config, exits, frames, observe, recipes, wait


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))


def log(**extra):
    return {
        "action": "key", "chord": "ctrl+t", "semantic": None,
        "window": {"class": "zen"}, **extra,
    }


def build(entries=None, name="demo"):
    return recipes.from_log(entries or [log()], name, "example.com", "a task", "claude")


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
        "action": "click",
        "window": {
            "class": "zen",
            "logical_rect": {"x": 0, "y": 0, "w": 800, "h": 600},
        },
        "relative": {"x": 0.5, "y": 0.25}, "button": "left", "count": 2,
    }
    step = recipes.step_from_log(entry)
    assert step["relative"] == {"x": 0.5, "y": 0.25}
    assert step["window_class"] == "zen"
    assert step["window_size"] == {"w": 800, "h": 600}
    assert step["count"] == 2


def test_an_aimed_scroll_keeps_its_target():
    entry = {
        "action": "scroll",
        "window": {
            "class": "zen",
            "logical_rect": {"x": 0, "y": 0, "w": 800, "h": 600},
        },
        "relative": {"x": 0.5, "y": 0.25},
        "dy": -3,
    }
    step = recipes.step_from_log(entry)
    assert step["relative"] == entry["relative"]
    assert step["window_class"] == "zen"
    assert step["window_size"] == {"w": 800, "h": 600}


def test_pointer_steps_without_window_geometry_are_not_promoted():
    entry = {
        "action": "click",
        "window": {"class": "zen"},
        "relative": {"x": 0.5, "y": 0.25},
    }
    assert recipes.step_from_log(entry) is None


def test_a_pointer_fraction_maps_through_the_visible_crop():
    window = {
        "class": "zen",
        "logical_rect": {"x": -100, "y": 0, "w": 1000, "h": 600},
    }
    frame = {
        "logical_rect": {"x": 0, "y": 0, "w": 900, "h": 600},
        "image_size": {"w": 900, "h": 600},
    }
    step = {
        "relative": {"x": 0.5, "y": 0.25},
        "window_size": {"w": 1000, "h": 600},
    }
    assert recipes._point_in_frame(step, window, frame) == (400, 150)


def test_a_resized_window_refuses_a_pointer_step():
    window = {
        "class": "zen",
        "logical_rect": {"x": 0, "y": 0, "w": 500, "h": 600},
    }
    frame = {
        "logical_rect": window["logical_rect"],
        "image_size": {"w": 500, "h": 600},
    }
    step = {
        "relative": {"x": 0.5, "y": 0.25},
        "window_size": {"w": 1000, "h": 600},
    }
    with pytest.raises(recipes.RecipeError, match="recorded at"):
        recipes._point_in_frame(step, window, frame)


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
        recipes.from_log([log(dry_run=True)], "demo", "", "", "claude")


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


@pytest.mark.parametrize(
    ("verified", "expected"),
    [(False, exits.VERIFY_FAILED), (True, exits.OK), (None, exits.OK)],
)
def test_replay_exit_reflects_stated_verification(verified, expected, monkeypatch):
    recipes.save(build())
    recipes.approve("demo")
    result = {
        "recipe": "demo",
        "revision": 1,
        "steps": 1,
        "verified": verified,
        "dry_run": False,
    }
    monkeypatch.setattr(recipes, "replay", lambda *_args, **_kwargs: result)

    assert cli.main(["recipe", "replay", "demo"]) == expected


def test_a_missing_recipe_is_named_in_the_error():
    with pytest.raises(recipes.RecipeError, match="absent"):
        recipes.load("absent")


def test_a_missing_recipe_maps_to_target_not_found(capsys):
    assert cli.main(["recipe", "replay", "absent"]) == exits.TARGET_NOT_FOUND
    assert "no recipe named 'absent'" in capsys.readouterr().err


def test_a_replay_wait_timeout_maps_to_timed_out(monkeypatch, capsys):
    recipe = build(
        [
            {
                "action": "wait",
                "condition": "window-open",
                "class": "zen",
                "window": {},
            }
        ]
    )
    recipes.save(recipe)
    recipes.approve("demo")
    monkeypatch.setattr(
        wait,
        "until",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(wait.WaitTimeout("late")),
    )

    assert cli.main(["recipe", "replay", "demo"]) == exits.TIMED_OUT
    assert "step 1: late" in capsys.readouterr().err


def test_a_genuine_recipe_policy_refusal_stays_denied(capsys):
    recipes.save(build())
    assert cli.main(["recipe", "replay", "demo"]) == exits.DENIED_BY_POLICY
    assert "never been reviewed" in capsys.readouterr().err


@pytest.mark.parametrize("moved", [False, True])
def test_recipe_capture_rechecks_window_geometry(
    monkeypatch, moved
):
    monitor = {
        "name": "DP-1",
        "scale": 1.0,
        "logical_rect": {"x": 0, "y": 0, "w": 800, "h": 600},
    }
    window = {
        "address": "0x1",
        "class": "zen",
        "title": "page",
        "logical_rect": monitor["logical_rect"],
    }
    live = window
    if moved:
        live = {**window, "logical_rect": {**window["logical_rect"], "x": 20}}
    queries = []

    monkeypatch.setattr(
        capture,
        "grab_region",
        lambda _rect, scratch, _scale: scratch.write_bytes(b"captured"),
    )
    monkeypatch.setattr(capture, "size_of", lambda _scratch: {"w": 800, "h": 600})
    monkeypatch.setattr(
        observe,
        "windows",
        lambda monitors: queries.append(monitors) or [live],
    )
    monkeypatch.setattr(
        observe,
        "snapshot",
        lambda: {"monitors": [monitor], "windows": [live], "layer_surfaces": []},
    )

    if moved:
        with pytest.raises(frames.FrameError, match="during capture"):
            recipes._capture_window(window, [monitor], config.DEFAULTS, "claude")
    else:
        captured = recipes._capture_window(
            window, [monitor], config.DEFAULTS, "claude"
        )
        assert captured["window"]["logical_rect"] == window["logical_rect"]
    assert queries == [[monitor]]


# --- the profile a replay will use ----------------------------------------


def test_save_rejects_an_unknown_capture_profile(capsys):
    """The name is stored verbatim and resolved again at replay.

    Writing one that does not resolve defers the refusal to the run the recipe
    was saved for, which is the run least able to do anything about it.
    """
    assert cli.main(["recipe", "save", "demo", "--for", "gemini"]) == exits.USAGE
    assert "gemini" in capsys.readouterr().err
    assert recipes.listing() == []


def test_save_separates_a_typed_profile_from_a_configured_one(tmp_path, monkeypatch):
    """An unknown default is a file to fix, not a command to retype."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    (tmp_path / "config" / "cu").mkdir(parents=True)
    (tmp_path / "config" / "cu" / "config.toml").write_text(
        '[capture]\nprofile = "gemini"\n'
    )

    assert cli.main(["recipe", "save", "demo"]) == exits.PRECONDITION_FAILED
    assert recipes.listing() == []
