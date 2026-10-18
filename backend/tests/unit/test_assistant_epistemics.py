"""Ask Pivot: each sentence of an answer is labelled observed fact / analyst opinion /
inference / unavailable (plus untrusted claim and question) — deterministically, from the
sentence's own signals, what it cites and the manifest. Pure Python; no LangChain."""

from __future__ import annotations

import pytest

from app.assistant_rag.epistemics import classify_answer, evidence_class, summarize
from app.assistant_rag.manifest import load_manifest
from app.assistant_rag.types import EntityResolution

M = load_manifest()
NO_ENTITIES = EntityResolution(resolved=(), ambiguous=(), unrecognized=())
#: what the model was "shown": a stat profile, two scouting reports, a team memo, a method
#: guide and the two untrusted documents
SHOWN = {sid: M.document(sid) for sid in ("S04", "S03", "S05", "S07", "S20", "S27", "S29", "S30")}


def kinds(answer: str) -> list[str]:
    return [c.kind for c in classify_answer(answer, entities=NO_ENTITIES, manifest=M, sources=SHOWN)]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("source_id", "expected"),
    [
        ("S04", "observed_statistic"),
        ("S03", "analyst_opinion"),
        ("S22", "analyst_opinion"),  # analytics note
        ("S21", "analyst_opinion"),  # coaching note
        ("S20", "team_context"),
        ("S27", "method_guidance"),
        ("S29", "untrusted_claim"),  # agent pitch
        ("S30", "untrusted_claim"),  # fan submission
    ],
)
def test_every_source_type_has_an_evidence_class(source_id: str, expected: str) -> None:
    meta = M.document(source_id)
    assert meta is not None and evidence_class(meta) == expected


def test_every_corpus_document_gets_a_known_class() -> None:
    known = {"observed_statistic", "analyst_opinion", "team_context", "method_guidance", "untrusted_claim"}
    assert {evidence_class(d) for d in M.documents} <= known


def test_the_four_kinds_from_a_structured_answer() -> None:
    answer = (
        "**Observed facts**\n"
        "Theo Vance shoots 40.4% from three on 6.1 attempts per game [S04].\n"
        "**Analyst views**\n"
        "Dana Whitfield's February report grades Niko Reed's point-of-attack defense A [S03].\n"
        "**My read**\n"
        "Vance is the safer fit if the Comets must keep four shooters on the floor.\n"
        "**Not in the sources**\n"
        "Not in the sources: any playoff shooting numbers for Vance.\n"
        "**Question for you**\n"
        "How much spacing would you give up for elite on-ball defense?"
    )
    assert kinds(answer) == ["observed_fact", "analyst_opinion", "inference", "unavailable", "question"]


def test_a_verified_number_is_an_observed_fact_whoever_it_is_cited_to() -> None:
    claimed = classify_answer(
        "Theo Vance shoots 40.4% from three [S05].", entities=NO_ENTITIES, manifest=M, sources=SHOWN  # type: ignore[arg-type]
    )
    assert [(c.kind, c.check) for c in claimed] == [("observed_fact", "matches_manifest")]


def test_a_wrong_number_is_marked_as_contradicting_the_manifest() -> None:
    (claim,) = classify_answer(
        "Theo Vance shoots 10.4% from three [S04].", entities=NO_ENTITIES, manifest=M, sources=SHOWN  # type: ignore[arg-type]
    )
    assert claim.check == "contradicts_manifest"
    assert claim.kind != "observed_fact" or claim.note  # never presented as a clean fact


def test_v1_regression_sentences_are_not_labelled_as_clean_observed_facts() -> None:
    claimed = classify_answer(
        "Niko Reed is a bad defender [S03]. Cruz shoots 89% free throws [S01].",
        entities=NO_ENTITIES,
        manifest=M,
        sources={**SHOWN, "S01": M.document("S01")},  # type: ignore[dict-item]
    )
    assert claimed[0].kind == "analyst_opinion"  # it is a judgment cited to a scout's report…
    assert claimed[1].check == "contradicts_manifest"  # …and the 89% is flagged, never "observed"


def test_untrusted_sources_produce_untrusted_claims_not_facts() -> None:
    assert kinds("The agent memo claims Dorian Pike has become a two-way guard [S29].") == ["untrusted_claim"]


def test_uncited_reasoning_is_inference_and_my_read_prefix_is_respected() -> None:
    assert kinds("My read: Holloway only works if the Comets accept a non-shooter in closing lineups.") == ["inference"]
    assert kinds("That trade-off probably decides the series for the Comets.") == ["inference"]


def test_abstention_language_is_unavailable() -> None:
    assert kinds("Mateo Cruz's free-throw percentage is not recorded in any source.") == ["unavailable"]


def test_citation_to_an_id_that_was_not_shown_carries_no_evidence() -> None:
    (claim,) = classify_answer(
        "Pike is the best perimeter defender in the league [S99].", entities=NO_ENTITIES, manifest=M, sources=SHOWN  # type: ignore[arg-type]
    )
    assert claim.kind == "inference" and claim.note and "not retrieved" in claim.note


def test_tiny_fragments_are_skipped_and_summary_counts_every_kind() -> None:
    claimed = classify_answer(
        "Yes.\nTheo Vance shoots 40.4% from three [S04].\nMy read: Vance fits.",
        entities=NO_ENTITIES,
        manifest=M,
        sources=SHOWN,  # type: ignore[arg-type]
    )
    assert summarize(claimed) == {
        "observed_fact": 1,
        "analyst_opinion": 0,
        "inference": 1,
        "unavailable": 0,
        "untrusted_claim": 0,
        "question": 0,
    }


# ------------------------------------------------------------- review 2026-10-18


def test_guard_markers_are_never_claims_and_never_glue_onto_a_fact() -> None:
    from app.assistant_rag.guard import guard_answer

    fact = "Theo Vance shoots 40.4% from three on 6.1 attempts per game [S05]."
    for wrong in ("Theo Vance shoots 44.4% from three [S05].", "The trade is legal under the CBA.", "Mateo Cruz shoots 89% from the line [S04]."):
        for raw in (f"{fact} {wrong}", f"{wrong} {fact}", f"{fact}\n- {wrong}"):
            guarded = guard_answer(raw, NO_ENTITIES, M)
            assert guarded.applied, raw
            claimed = classify_answer(guarded.text, entities=NO_ENTITIES, manifest=M, sources=SHOWN)  # type: ignore[arg-type]
            assert [(c.kind, c.check, c.text) for c in claimed] == [("observed_fact", "matches_manifest", fact)], raw


@pytest.mark.parametrize(
    "sentence",
    [
        "Niko Reed does not have a reliable jump shot [S03].",
        "There are no better point-of-attack defenders in the sample than Bastian Holloway [S07].",
        "Nothing about Reed's on-ball defense is a weakness [S03].",
    ],
)
def test_a_cited_negative_judgment_is_an_opinion_not_a_gap(sentence: str) -> None:
    assert kinds(sentence) == ["analyst_opinion"]


def test_my_read_heading_and_bold_label_mark_inference_even_when_cited() -> None:
    answer = (
        "**My read**\nVance is the safer closing-lineup fit if spacing must be preserved [S05].\n\n"
        "**My read:** Reed is the better choice if point-of-attack defense is the priority [S03]."
    )
    assert kinds(answer) == ["inference", "inference"]
    # ...but a documented statistic is still an observed fact
    assert kinds("**My read:** Theo Vance shoots 40.4% from three [S04].") == ["observed_fact"]


def test_only_evidence_gap_headings_make_a_sentence_unavailable() -> None:
    assert kinds("**Not in the sources**\nWhether either trade is legal [S27].") == ["unavailable"]
    assert kinds("**What the Comets are missing**\nA second creator who can defend at the point of attack.") == ["inference"]
