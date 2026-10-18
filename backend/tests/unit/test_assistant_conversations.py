"""Ask Pivot: the multi-turn conversation harness — scripted GM conversations whose history
is the assistant's own earlier answers, scored turn by turn with the benchmark scorers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.assistant_rag.evaluation.conversations import (
    CONVERSATIONS_PATH,
    _with_history,
    load_conversations,
)
from app.assistant_rag.evaluation.runner import OK, CaseRecord, Variant
from app.assistant_rag.manifest import load_manifest


def test_every_scripted_turn_is_a_valid_benchmark_case() -> None:
    conversations = load_conversations()
    assert len(conversations) >= 4
    turns = [t for c in conversations for t in c.turns]
    assert len(turns) >= 12
    assert len({t.id for t in turns}) == len(turns)
    # the illustrative V2 flow is present: strategy -> revision -> regret -> legality -> follow-up
    flow = next(c for c in conversations if c.id == "comets_strategy_revision")
    assert [t.category for t in flow.turns] == ["team_fit", "goal_revision", "counterevidence", "legality", "comparison"]
    assert ("comets_t1_strategy", "comets_t2_revision") in flow.pairs


def test_history_is_the_assistants_own_previous_answers() -> None:
    flow = next(c for c in load_conversations() if c.id == "comets_strategy_revision")
    first, second = flow.turns[0], flow.turns[1]
    record = CaseRecord(first, Variant("grounded", "hybrid"), {}, OK, 1.0, response={"answer": "Vance first [S05]."})
    case = _with_history(second, [record])
    assert [(m.role, m.content) for m in case.history] == [("user", first.question), ("assistant", "Vance first [S05].")]


def test_a_pair_must_name_turns_of_its_own_conversation(tmp_path: Path) -> None:
    raw = json.loads(CONVERSATIONS_PATH.read_text(encoding="utf-8"))
    raw["conversations"][0]["pairs"] = [["comets_t1_strategy", "no_such_turn"]]
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="names a turn"):
        load_conversations(path, load_manifest())


def test_fake_run_writes_a_transcript_and_scores_every_turn(tmp_path: Path) -> None:
    pytest.importorskip("langchain_core")
    from app.assistant_rag.evaluation.conversations import run_conversations

    result = run_conversations(mode="fake", conversation_ids=["comets_strategy_revision"], output_dir=tmp_path)
    assert result.exit_code == 0  # fake mode never fails on scorer verdicts
    (record,) = result.results
    assert len(record.turns) == 5 and all(t.scores for t in record.turns if t.response is not None)
    # turn 2 was asked with turn 1's question and answer as history
    assert len(record.turns[1].request["history"]) == 2
    assert record.pair_scores and record.pair_scores[0].key == "objective_sensitivity"
    transcript = (result.directory / "transcript.md").read_text(encoding="utf-8")
    assert "FICTIONAL DATA" in transcript and "NOT a quality signal" in transcript and "Turn 5" in transcript
