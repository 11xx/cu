"""Capture profiles: inherited from one base, and costed in the consumer's own unit."""

from __future__ import annotations

import pytest

from cu import capture, cli, config, exits


@pytest.fixture
def cfg():
    return config.load(config.config_path().parent / "does-not-exist.toml")


def test_a_profile_states_only_its_deltas(cfg):
    claude = capture.resolve("claude", cfg)
    base = capture.resolve("generic", cfg)
    # Declared by claude itself.
    assert claude["max_long_edge"] == 1920
    # Never mentioned by claude, so it comes from the base.
    assert claude["token_multiplier"] == base["token_multiplier"]


def test_every_field_resolves_for_every_configured_profile(cfg):
    for name in capture.names(cfg):
        assert set(capture.resolve(name, cfg)) == set(capture.PROFILE_FIELDS)


def test_an_unknown_profile_names_the_ones_that_exist(cfg):
    with pytest.raises(ValueError) as err:
        capture.resolve("gemini", cfg)
    assert "claude" in str(err.value)


def test_a_profile_cannot_introduce_a_field_the_base_lacks(cfg):
    cfg["capture"]["profiles"]["typo"] = {"max_long_edg": 800}
    with pytest.raises(config.ConfigError) as err:
        capture.resolve("typo", cfg)
    assert "max_long_edg" in str(err.value)


def test_a_new_consumer_is_a_stanza_rather_than_a_code_change(cfg):
    cfg["capture"]["profiles"]["frugal"] = {"max_long_edge": 640}
    frugal = capture.resolve("frugal", cfg)
    assert frugal["max_long_edge"] == 640
    assert frugal["patch_px"] == capture.resolve("generic", cfg)["patch_px"]
    assert "frugal" in capture.names(cfg)


def test_the_two_vendors_do_not_share_a_patch_size(cfg):
    """One long edge cannot express both, which is why the field exists."""
    assert capture.resolve("claude", cfg)["patch_px"] == 28
    assert capture.resolve("openai", cfg)["patch_px"] == 32


def test_an_image_is_billed_in_whole_patches(cfg):
    claude = capture.resolve("claude", cfg)
    # 1920/28 -> 69 (68.57 rounds up), 1080/28 -> 39 (38.57 rounds up).
    assert capture.visual_tokens(1920, 1080, claude) == 69 * 39
    openai = capture.resolve("openai", cfg)
    # 1920/32 -> 60 exactly, 1080/32 -> 34 (33.75 rounds up).
    assert capture.visual_tokens(1920, 1080, openai) == 60 * 34


def test_a_budget_caps_the_estimate_and_zero_means_none(cfg):
    claude = capture.resolve("claude", cfg)
    assert capture.visual_tokens(8000, 8000, claude) == claude["token_budget"]
    openai = capture.resolve("openai", cfg)
    assert openai["token_budget"] == 0
    assert capture.visual_tokens(8000, 8000, openai) > claude["token_budget"]


def test_the_base_prices_an_unmeasured_consumer_no_lower_than_a_known_one(cfg):
    """A conservative base over-prices rather than under-prices a guess.

    Asserted over what cu ships: a user is free to configure something smaller,
    and the point is that an *unconfigured* field never resolves optimistically.
    """
    base = capture.resolve(capture.BASE_PROFILE, cfg)
    for name in ("claude", "openai"):
        other = capture.resolve(name, cfg)
        assert base["patch_px"] <= other["patch_px"]
        assert base["max_long_edge"] <= other["max_long_edge"]
        assert base["max_bytes"] <= other["max_bytes"]


def test_profile_rejects_non_positive_patch_size(cfg):
    """Zero patches per side divides by zero inside `visual_tokens`.

    An arithmetic exception carries no exit code, so the caller learns that
    something went wrong rather than which configured line to fix.
    """
    cfg["capture"]["profiles"]["broken"] = {"patch_px": 0}
    with pytest.raises(config.ConfigError) as err:
        capture.resolve("broken", cfg)
    assert "patch_px" in str(err.value)


@pytest.mark.parametrize("value", [True, "1920", None])
def test_a_field_that_is_not_a_number_is_refused_rather_than_carried(cfg, value):
    """`True` is an `int` in Python, so it has to be excluded by name."""
    cfg["capture"]["profiles"]["broken"] = {"max_long_edge": value}
    with pytest.raises(config.ConfigError):
        capture.resolve("broken", cfg)


def test_zero_stays_a_budget_of_none_rather_than_becoming_a_refusal(cfg):
    cfg["capture"]["profiles"]["frugal"] = {"token_budget": 0}
    assert capture.resolve("frugal", cfg)["token_budget"] == 0


def test_a_profile_cannot_downscale_past_its_own_ceiling(cfg):
    """The two would contradict each other, and the larger one would be used."""
    cfg["capture"]["profiles"]["backwards"] = {"max_long_edge": 4000, "ceiling": 2000}
    with pytest.raises(config.ConfigError):
        capture.resolve("backwards", cfg)


def test_an_unknown_default_is_configuration_while_a_typed_one_is_usage(cfg):
    """The two ask different things: one is a file to fix, one is a retype."""
    cfg["capture"]["profile"] = "gemini"
    with pytest.raises(config.ConfigError):
        capture.default_name(cfg)
    with pytest.raises(ValueError):
        capture.resolve("gemini", cfg)


def test_profiles_command_rejects_unknown_configured_default(tmp_path, monkeypatch, capsys):
    """A table with no marker on it reads as 'no default', not as broken."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "cu").mkdir()
    (tmp_path / "cu" / "config.toml").write_text('[capture]\nprofile = "gemini"\n')

    assert cli.main(["profiles"]) == exits.PRECONDITION_FAILED
    assert "gemini" in capsys.readouterr().err


def test_a_loaded_config_does_not_alias_the_defaults():
    """A caller that edits what it was given must not edit everyone's defaults."""
    missing = config.config_path().parent / "does-not-exist.toml"
    first = config.load(missing)
    first["capture"]["profiles"]["scratch"] = {"max_long_edge": 1}
    assert "scratch" not in config.load(missing)["capture"]["profiles"]
