"""Application notes require evidence from distinct, uncontested runs."""

from __future__ import annotations

import json

import pytest

from cu import cli, context, exits, knowledge, safety


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def note_in(run):
    with context.active(run):
        return knowledge.add_note("browser", "tools opens developer tools")


def score_in(run, note_id, hit=True):
    with context.active(run):
        return knowledge.score("browser", note_id, hit)


def test_note_provenance_carries_only_supplied_identity(monkeypatch):
    monkeypatch.setenv("CU_HARNESS", "codex")
    monkeypatch.setenv("CU_HARNESS_SESSION", "source-session")
    note = note_in("first-run")
    provenance = note["provenance"]
    assert provenance["cu_session"] == "first-run"
    assert provenance["harness"] == "codex"
    assert provenance["harness_session"] == "source-session"
    assert provenance["invocation_id"]
    assert provenance["human_interference"] == "not-detected"
    assert note["status"] == "observed-once"
    assert knowledge.load("browser")["notes"][0] == note


def test_only_another_run_can_confirm_guidance():
    note = note_in("first-run")
    before = knowledge.path_for("browser").read_bytes()
    with pytest.raises(knowledge.KnowledgeError, match="another CU session"):
        score_in("first-run", note["id"])
    assert knowledge.path_for("browser").read_bytes() == before
    assert score_in("second-run", note["id"])["status"] == "confirmed"
    with pytest.raises(knowledge.KnowledgeError, match="another CU session"):
        score_in("second-run", note["id"])


def test_missing_run_identity_cannot_confirm_guidance():
    note = knowledge.add_note("browser", "a menu fact")
    assert note["provenance"] == {"human_interference": "untracked"}
    with pytest.raises(knowledge.KnowledgeError, match="named CU session"):
        knowledge.score("browser", note["id"], True)
    assert score_in("first-run", note["id"])["status"] == "observed-once"
    assert score_in("second-run", note["id"])["status"] == "confirmed"


def test_interference_is_kept_between_invocations_and_scoped_to_one_run():
    with context.active("first-run"):
        safety.Refused(exits.HUMAN_ACTIVE, "human movement")
    note = note_in("first-run")
    assert note["provenance"]["human_interference"] == "detected"
    assert score_in("second-run", note["id"])["status"] == "observed-once"
    assert score_in("third-run", note["id"])["status"] == "confirmed"


def test_interference_after_learning_withdraws_that_runs_evidence():
    note = note_in("first-run")
    assert score_in("second-run", note["id"])["status"] == "confirmed"
    with context.active("first-run"):
        safety.Refused(exits.HUMAN_ACTIVE, "human movement")
    loaded = knowledge.load("browser")["notes"][0]
    assert loaded["status"] == "observed-once"
    assert loaded["provenance"]["human_interference"] == "detected"
    assert score_in("third-run", note["id"])["status"] == "confirmed"


def test_interference_evidence_survives_runtime_expiry(tmp_path, monkeypatch):
    note = note_in("first-run")
    score_in("second-run", note["id"])
    with context.active("first-run"):
        safety.Refused(exits.HUMAN_ACTIVE, "human movement")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "next-runtime"))
    loaded = knowledge.load("browser")["notes"][0]
    assert loaded["status"] == "observed-once"
    assert loaded["provenance"]["human_interference"] == "detected"


def test_other_refusals_do_not_claim_interference():
    with context.active("first-run"):
        safety.Refused(exits.STALE_FRAME, "stale")
    assert note_in("first-run")["provenance"]["human_interference"] == "not-detected"


def test_contrary_evidence_requires_two_fresh_runs():
    note = note_in("first-run")
    score_in("second-run", note["id"])
    missed = score_in("third-run", note["id"], False)
    assert missed["status"] == "needs-confirmation"
    assert missed["missed"] == 1
    assert score_in("fourth-run", note["id"])["status"] == "needs-confirmation"
    assert score_in("fifth-run", note["id"])["status"] == "confirmed"


def test_forget_preserves_other_notes_and_landmarks():
    first = note_in("first-run")
    second = note_in("second-run")
    knowledge.set_landmark("browser", "menu", {"x": 0, "y": 0, "w": .1, "h": .1})
    knowledge.forget("browser", first["id"])
    record = knowledge.load("browser")
    assert [note["id"] for note in record["notes"]] == [second["id"]]
    assert list(record["landmarks"]) == ["menu"]
    with pytest.raises(knowledge.KnowledgeError, match="no note"):
        knowledge.forget("browser", first["id"])


def test_legacy_note_ids_survive_deleting_a_preceding_note():
    record = {"app": "browser", "landmarks": {}, "notes": [
        {"text": "first", "at": 1}, {"text": "second", "at": 2},
    ]}
    knowledge.save(record)
    raw = knowledge.path_for("browser").read_bytes()
    first, second = knowledge.load("browser")["notes"]
    assert knowledge.path_for("browser").read_bytes() == raw
    assert second["status"] == "observed-once"
    assert second["provenance"] == {"human_interference": "untracked"}
    knowledge.forget("browser", first["id"])
    assert knowledge.load("browser")["notes"][0]["id"] == second["id"]
    assert score_in("first-run", second["id"])["status"] == "observed-once"
    assert score_in("second-run", second["id"])["status"] == "confirmed"


def test_cli_shows_scores_and_forgets_a_note_by_id(monkeypatch, capsys):
    monkeypatch.setenv("CU_SESSION", "first-run")
    assert cli.main(["know", "note", "browser", "menu fact", "--json"]) == 0
    note = json.loads(capsys.readouterr().out)
    assert cli.main(["know", "confirm", "browser", note["id"], "--json"]) == (
        exits.NOTE_CONFIRMATION_REFUSED
    )
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["name"] == "NOTE_CONFIRMATION_REFUSED"
    monkeypatch.setenv("CU_SESSION", "second-run")
    assert cli.main(["know", "confirm", "browser", note["id"], "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "confirmed"
    assert cli.main(["know", "show", "browser"]) == 0
    text = capsys.readouterr().out
    assert note["id"] in text and "[confirmed]" in text and "first-run" in text
    assert cli.main(["know", "forget", "browser", note["id"]]) == 0
    assert knowledge.load("browser")["notes"] == []
