"""BM25 keyword index and tokenizer for Ask Pivot (pure Python, no LangChain)."""

from __future__ import annotations

import math

import pytest

from app.assistant_rag.lexical import STOPWORDS, BM25Index, normalise, tokenize

# ------------------------------------------------------------------------------ tokenize


def test_tokenize_splits_hyphenated_compounds_and_keeps_the_joined_form() -> None:
    assert tokenize("point-of-attack") == ["point", "attack", "point-of-attack"]


def test_tokenize_keeps_decimal_numbers_whole() -> None:
    tokens = tokenize("He shoots 40.4% from three on 6.1 attempts.")
    assert "40.4" in tokens
    assert "6.1" in tokens


def test_tokenize_drops_stopwords_and_single_letters_but_keeps_single_digits() -> None:
    tokens = tokenize("What is the 3-point rate, e.g. for a wing?")
    assert not {"what", "is", "the", "for", "a", "e", "g"} & set(tokens)
    assert "3" in tokens
    assert "3-point" in tokens


def test_tokenize_folds_case_accents_possessives_and_apostrophes() -> None:
    assert tokenize("Reed's") == ["reed"]
    assert tokenize("RÉSUMÉ") == tokenize("resume")
    assert tokenize("Vance’s") == tokenize("Vance")
    assert "dont" not in tokenize("don't")  # contraction, then stopword


def test_tokenize_preserves_order_and_duplicates() -> None:
    assert tokenize("Reed guards; Reed rebounds") == ["reed", "guard", "reed", "rebound"]


def test_tokenize_empty_text() -> None:
    assert tokenize("") == []
    assert tokenize("the of and") == []


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("rebounds", "rebound"),
        ("abilities", "ability"),
        ("pass", "pass"),  # -ss is not a plural
        ("bonus", "bonus"),  # -us is not a plural
        ("analysis", "analysis"),  # -is is not a plural
        ("shooting", "shoot"),
        ("switched", "switch"),
        ("reed", "reed"),  # too short for -ed
        ("king", "king"),  # too short for -ing
        ("40.4", "40.4"),  # numbers are never changed
        ("s03", "s03"),
    ],
)
def test_normalise_rules(word: str, expected: str) -> None:
    assert normalise(word) == expected


@pytest.mark.parametrize(("a", "b"), [("space", "spacing"), ("grade", "graded"), ("defender", "defenders")])
def test_inflections_meet_at_one_token(a: str, b: str) -> None:
    assert tokenize(a) == tokenize(b)


def test_stopwords_do_not_contain_domain_words() -> None:
    assert not {"defense", "guard", "team", "spacing", "three"} & STOPWORDS


# ---------------------------------------------------------------------------------- BM25

DOCS = [
    ("d1", "Niko Reed is an elite point-of-attack defender for the Mesa Foxes."),
    ("d2", "Theo Vance shoots 40.4% from three on 6.1 attempts per game."),
    ("d3", "Lucas Achterberg anchors a drop coverage scheme near the rim."),
    ("d4", "The defender rotated late; the defense still held."),
]


def test_exact_name_query_ranks_the_right_document_first() -> None:
    index = BM25Index(DOCS)
    assert index.search("Achterberg", k=3)[0][0] == "d3"
    assert index.search("What does the sample say about Theo Vance?", k=3)[0][0] == "d2"


def test_exact_number_query_matches_only_the_document_with_that_number() -> None:
    hits = BM25Index(DOCS).search("40.4", k=3)
    assert [doc_id for doc_id, _ in hits] == ["d2"]


def test_documents_without_a_query_term_are_excluded() -> None:
    hits = BM25Index(DOCS).search("drop coverage", k=10)
    assert [doc_id for doc_id, _ in hits] == ["d3"]
    assert all(score > 0 for _, score in hits)
    assert BM25Index(DOCS).search("photosynthesis", k=10) == []


def test_idf_is_positive_even_for_a_term_in_every_document() -> None:
    docs = [("a", "defense wins"), ("b", "defense travels"), ("c", "defense matters")]
    index = BM25Index(docs)
    n = 3
    assert index.idf("defens") == pytest.approx(math.log(1 + (n - n + 0.5) / (n + 0.5)))
    assert index.idf("defens") > 0
    # a rare term outweighs a ubiquitous one
    assert index.idf("win") > index.idf("defens")
    assert index.idf("unknown-term") == 0.0
    assert len(index.search("defense", k=5)) == 3


def test_term_frequency_saturates_and_length_normalises() -> None:
    index = BM25Index(
        [
            ("once", "Reed defends."),
            ("thrice", "Reed Reed Reed defends."),
            ("long", "Reed defends " + "filler words about other things " * 10),
            ("other", "Vance shoots."),
        ]
    )
    scores = index.scores("Reed")
    assert scores["thrice"] > scores["once"] > scores["long"]
    assert scores["thrice"] < 3 * scores["once"]  # k1 saturation: not linear in tf


def test_repeated_query_terms_count_once() -> None:
    index = BM25Index(DOCS)
    assert index.scores("Reed Reed Reed") == index.scores("Reed")


def test_search_respects_k_and_breaks_ties_by_id() -> None:
    index = BM25Index([("b", "wing defense"), ("a", "wing defense"), ("c", "wing defense")])
    assert [doc_id for doc_id, _ in index.search("wing", k=2)] == ["a", "b"]
    assert index.search("wing", k=0) == []


def test_index_rejects_duplicate_ids_and_handles_empty_input() -> None:
    with pytest.raises(ValueError):
        BM25Index([("x", "one"), ("x", "two")])
    empty = BM25Index([])
    assert len(empty) == 0
    assert empty.search("anything", k=5) == []
    blank = BM25Index([("x", "the and of")])
    assert blank.vocabulary_size == 0
    assert blank.search("the", k=5) == []
