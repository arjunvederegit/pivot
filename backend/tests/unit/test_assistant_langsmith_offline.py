"""The LangSmith evaluation framework itself cannot give full marks to a demonstrably wrong
answer — run end to end through `langsmith.evaluate()`, offline.

The scorer tests prove each deterministic evaluator rejects the V1 failures. This file proves
the *LangSmith path* does too: the same evaluator wrappers the hosted runner uploads
(`langsmith_runner.make_evaluators`), executed by LangSmith's own `evaluate()` over in-memory
`Example`s, with `upload_results=False` and a client pointed at a closed loopback port, so
nothing leaves the machine (no key, no dataset, no traces).

Mandatory regressions (from the V2 brief):
  * "Niko Reed is a bad defender [S03]."             — S03 grades his defense A.
  * "Cruz shoots 89% free throws [S01]. NOT PROVIDED elsewhere." — no FT% exists.
  * "Theo Vance shoots 10.4% ... [S05]"               — gold 40.4%, citation valid.
Each must score below perfect on the evaluator that owns the error, while citation
*validity* stays perfect — the V1 evaluator's exact blind spot.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytest.importorskip("langsmith")

from langsmith import Client, evaluate  # noqa: E402
from langsmith.schemas import Example  # noqa: E402

from app.assistant_rag.evaluation.dataset import load_benchmark  # noqa: E402
from app.assistant_rag.evaluation.fixtures_adversarial import make_output  # noqa: E402
from app.assistant_rag.evaluation.langsmith_runner import (  # noqa: E402
    example_inputs,
    example_outputs,
    make_evaluators,
)
from app.assistant_rag.manifest import load_manifest  # noqa: E402

MANIFEST = load_manifest()
BENCHMARK = load_benchmark()

V1_ANSWER = "Niko Reed is a bad defender [S03]. Cruz shoots 89% free throws [S01]. NOT PROVIDED elsewhere."
#: case id -> the wrong answer the fake target returns for it
WRONG_ANSWERS = {
    "polarity_reed_defense": V1_ANSWER,
    "abstain_mateo_cruz_ft": V1_ANSWER,
    "fact_vance_three_point": "Theo Vance shoots 10.4% from three on 6.1 attempts per game [S05].",
}
CORRECT_ANSWER = "Theo Vance shoots 40.4% from three on 6.1 attempts per game [S04]."


def _offline_client() -> Client:
    # A closed loopback port: the client's background calls fail fast and locally.
    # auto_batch_tracing=False: no background upload thread outliving the test session.
    return Client(api_key="offline-test-key", api_url="http://127.0.0.1:9", auto_batch_tracing=False)


def _examples(case_ids: list[str]) -> list[Example]:
    dataset_id = uuid.uuid4()
    return [
        Example(
            id=uuid.uuid4(),
            dataset_id=dataset_id,
            inputs=example_inputs(BENCHMARK.case(cid)),
            outputs=example_outputs(BENCHMARK.case(cid), BENCHMARK),
        )
        for cid in case_ids
    ]


def _run(answers: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Run langsmith.evaluate over the given cases; return {case_id: {key: (score, comment)}}."""
    by_question = {BENCHMARK.case(cid).question: answer for cid, answer in answers.items()}

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        return make_output(by_question[inputs["question"]], manifest=MANIFEST)

    results = evaluate(
        target,
        data=_examples(list(answers)),
        evaluators=make_evaluators(BENCHMARK, MANIFEST),
        client=_offline_client(),
        upload_results=False,
        max_concurrency=0,
    )
    out: dict[str, dict[str, Any]] = {}
    for row in results:
        case_id = row["example"].outputs["case_id"]
        out[case_id] = {
            r.key: (r.score, r.comment) for r in row["evaluation_results"]["results"]
        }
    return out


@pytest.fixture(scope="module")
def wrong() -> dict[str, dict[str, Any]]:
    return _run(WRONG_ANSWERS)


def test_langsmith_evaluate_runs_offline_and_scores_every_case(wrong: dict[str, dict[str, Any]]) -> None:
    assert set(wrong) == set(WRONG_ANSWERS)
    for case_id, scores in wrong.items():
        assert scores, f"no evaluator ran for {case_id}"


def test_v1_bad_defender_fails_polarity_and_support_inside_langsmith(wrong: dict[str, dict[str, Any]]) -> None:
    scores = wrong["polarity_reed_defense"]
    assert scores["claim_polarity"][0] == 0.0, scores["claim_polarity"]
    assert scores["citation_support"][0] is not None and scores["citation_support"][0] < 1.0
    assert scores["citation_validity"][0] == 1.0  # the pointer is valid; the claim is false


def test_v1_free_throw_fabrication_fails_abstention_inside_langsmith(wrong: dict[str, dict[str, Any]]) -> None:
    scores = wrong["abstain_mateo_cruz_ft"]
    assert scores["abstention_correctness"][0] == 0.0, scores["abstention_correctness"]
    assert scores["citation_validity"][0] == 1.0


def test_wrong_percentage_fails_fact_correctness_inside_langsmith(wrong: dict[str, dict[str, Any]]) -> None:
    scores = wrong["fact_vance_three_point"]
    score, comment = scores["fact_correctness"]
    # 3PA (6.1) is right, 3P% (10.4 vs gold 40.4) is wrong: partial credit, and a FAIL verdict.
    assert score is not None and score < 1.0 and comment.startswith("FAIL"), scores["fact_correctness"]
    assert "10.4%" in comment and "40.4" in comment
    assert scores["citation_validity"][0] == 1.0


def test_no_wrong_answer_gets_a_perfect_score_on_every_evaluator(wrong: dict[str, dict[str, Any]]) -> None:
    # The V1 failure mode, stated as a property: averaging everything would hide the error,
    # so at least one applicable evaluator must score each wrong answer below 1.0.
    for case_id, scores in wrong.items():
        applicable = [s for s, _ in scores.values() if s is not None and 0.0 <= s <= 1.0]
        assert applicable and min(applicable) < 1.0, (case_id, scores)


def test_a_correct_answer_is_not_penalised_by_the_same_path() -> None:
    scores = _run({"fact_vance_three_point": CORRECT_ANSWER})["fact_vance_three_point"]
    assert scores["fact_correctness"][0] == 1.0, scores["fact_correctness"]
    assert scores["citation_validity"][0] == 1.0
