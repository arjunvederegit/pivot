"""The validation battery itself has to work, and its nulls have to be nulls.

The numbers in `ROSTERLAB_R6_IMPLEMENTATION_REPORT.md` come from running this over the
real corpus. What is asserted here is the machinery: that a null really destroys the
signal, that a threshold failure is reported rather than swallowed, and that the battery
runs end to end on a corpus small enough for a test.
"""

import statistics
from datetime import date

import pytest

from app.analytics.comparables import PickLeg, PlayerLeg, TradeSide, robust_scales
from app.analytics.comparables_validation import (
    THRESHOLDS,
    _is_asymmetric,
    _jaccard,
    _MirroredSide,
    _top_keys,
    archetype_of,
    archetype_precision,
    era_structure,
    random_ranker,
    run_battery,
    sd_scales,
    season_concentration,
    shuffled_corpus,
)


def side(key: str, **overrides) -> TradeSide:
    base = {
        "key": key,
        "team_abbreviation": "AAA",
        "season": "2025-26",
        "feature_season": "2025-26",
        "transaction_date": date(2026, 2, 5),
        "is_in_season": True,
        "n_teams": 2,
        "win_pct": 0.5,
        "counterparty_win_pct": 0.5,
    }
    base.update(overrides)
    return TradeSide(**base)


def leg(name: str, tei: float, minutes: float = 30.0, age: float = 27.0) -> PlayerLeg:
    return PlayerLeg(name=name, player_id=name, tei=tei, minutes=minutes, age=age)


@pytest.fixture()
def corpus() -> list[TradeSide]:
    """A small corpus containing both directional archetypes and some filler."""
    sides: list[TradeSide] = []
    for i in range(6):
        sides.append(
            side(
                f"sell{i}",
                outgoing=(leg(f"star{i}", 2.5 + i * 0.2),),
                picks_in=tuple(PickLeg(2029 + j, 1) for j in range(2)),
                win_pct=0.3 + i * 0.02,
            )
        )
        sides.append(
            side(
                f"buy{i}",
                incoming=(leg(f"star{i}", 2.5 + i * 0.2),),
                picks_out=tuple(PickLeg(2029 + j, 1) for j in range(2)),
                win_pct=0.6 + i * 0.02,
            )
        )
        sides.append(
            side(
                f"swap{i}",
                incoming=(leg(f"a{i}", 0.3 + i * 0.05),),
                outgoing=(leg(f"b{i}", 0.28 + i * 0.05),),
                win_pct=0.5,
            )
        )
    return sides


def test_the_battery_runs_and_reports_every_check(corpus):
    report = run_battery(corpus, k=3)
    names = [c["name"] for c in report["checks"]]
    assert "perturbation_stability" in names
    assert "archetype_recovery" in names
    assert "direction_confusion" in names
    assert "era_structure" in names
    assert report["corpus_sides"] == len(corpus)
    assert report["weights"]
    # Every gated check reports a threshold and a verdict; ungated ones report neither.
    for check in report["checks"]:
        assert (check["threshold"] is None) == (check["passed"] is None)


def test_the_random_null_lands_on_the_base_rate(corpus):
    scales = robust_scales(corpus)
    real = archetype_precision(corpus, scales, k=3)
    null = archetype_precision(corpus, scales, k=3, ranker=random_ranker(3))
    assert real["precision_at_k"] > null["precision_at_k"]
    assert null["precision_at_k"] == pytest.approx(null["base_rate"], abs=0.25)


def test_the_shuffled_null_keeps_the_marginals_and_destroys_the_correspondence(corpus):
    swapped = shuffled_corpus(corpus)
    assert {s.key for s in swapped} == {s.key for s in corpus}
    def vector(entry) -> str:
        return repr(sorted(entry.features().items()))

    originals = sorted(vector(s) for s in corpus)
    shuffled = sorted(vector(s) for s in swapped)
    assert originals == shuffled  # same multiset of vectors...
    assert any(
        s.features() != next(o for o in corpus if o.key == s.key).features() for s in swapped
    )  # ...attached to different sides


def test_a_directional_side_is_asymmetric_and_a_swap_is_not(corpus):
    assert _is_asymmetric(next(s for s in corpus if s.key == "sell0"))
    assert not _is_asymmetric(next(s for s in corpus if s.key == "swap0"))


def test_mirroring_reverses_every_directional_feature(corpus):
    original = next(s for s in corpus if s.key == "sell0")
    mirrored = _MirroredSide(original)
    before, after = original.features(), mirrored.features()
    assert after["firsts_net_unconditional"] == -before["firsts_net_unconditional"]
    assert after["firsts_net_conditional"] == -before["firsts_net_conditional"]
    assert after["value_in"] == before["value_out"]
    assert after["picks_in"] == before["picks_out"]
    assert after["players_out"] == before["players_in"]
    # ...and leaves the direction-invariant ones alone, which is why the mirror is
    # measured by rank rather than by similarity level.
    assert after["n_teams"] == before["n_teams"]
    assert after["is_in_season"] == before["is_in_season"]


def test_direction_confusion_is_measured_between_opposites_only(corpus):
    report = run_battery(corpus, k=3)
    check = next(c for c in report["checks"] if c["name"] == "direction_confusion")
    assert check["threshold"] == THRESHOLDS["direction_confusion_max"]
    assert check["detail"]["directional_neighbours"] > 0


def test_sd_scales_cover_the_features_that_vary(corpus):
    scales = sd_scales(corpus)
    assert scales["value_out"] > 0
    assert "is_in_season" not in scales  # constant in this corpus, so it has no sd


def test_season_concentration_reports_its_own_base_rate(corpus):
    result = season_concentration(corpus, robust_scales(corpus), k=3)
    assert result["base_rate"] == pytest.approx(1.0)  # one season only
    assert result["same_feature_season_share"] == pytest.approx(1.0)


def test_era_structure_needs_no_player_model(corpus):
    older = [
        side(f"old{i}", season="2021-22", picks_in=(PickLeg(2024, 2),)) for i in range(3)
    ]
    summary = era_structure([*corpus, *older])
    assert summary["2017_cba"]["sides"] == 3
    assert summary["2023_cba"]["sides"] == len(corpus)
    assert summary["2023_cba"]["mean_picks_per_side"] > 0


def test_a_failing_threshold_is_listed_rather_than_swallowed(corpus, monkeypatch):
    monkeypatch.setitem(THRESHOLDS, "perturbation_rank_max", 0.0)
    report = run_battery(corpus, k=3)
    assert "perturbation_rank_displacement" in report["failed"]


def test_perturbation_stability_is_reported_and_no_longer_gated(corpus):
    """R7 withdrew it as a gate: the random-hash null scores a perfect 1.0 on it, because
    a ranking keyed on identity alone cannot move when the query's features do. A check
    with no threshold cannot appear in `failed`, and the nulls are published beside it so
    the withdrawal is checkable from the output."""
    report = run_battery(corpus, k=3)
    check = next(c for c in report["checks"] if c["name"] == "perturbation_stability")
    assert check["threshold"] is None
    assert check["passed"] is None
    assert "perturbation_stability" not in report["failed"]
    assert check["detail"]["null_random_hash"] == 1.0
    assert check["measured"] <= check["detail"]["null_random_hash"]


def test_the_gated_perturbation_criterion_is_a_rank_not_a_level(corpus):
    """The replacement asks where a query's neighbours went, not whether they are the
    same five. It is bounded by the corpus size rather than by 1.0, which is what lets one
    threshold hold as the corpus grows."""
    report = run_battery(corpus, k=3)
    check = next(
        c for c in report["checks"] if c["name"] == "perturbation_rank_displacement"
    )
    assert check["threshold"] == THRESHOLDS["perturbation_rank_max"]
    assert check["passed"] is True
    assert 1.0 <= check["measured"] <= len(corpus)
    assert "never validity" in check["detail"]["role"]


def test_archetype_rules_assign_at_most_one_class(corpus):
    for entry in corpus:
        label = archetype_of(entry)
        assert label is None or isinstance(label, str)


def test_the_distance_form_seam_is_reachable_from_rank(corpus):
    """`_clipped_top_keys` measures the distance form by rebinding `feature_distance`.

    R7 added a distance-only fast path inside `rank`, and inlining the arithmetic there
    made the rebinding unreachable — the check would have compared the shipped ranking
    against itself and reported a perfect 1.0 while measuring nothing. This asserts the
    seam is live: replacing the function changes the distance `rank` computes.
    """
    import app.analytics.comparables as module
    from app.analytics.comparables import robust_scales

    scales = robust_scales(corpus)
    query = corpus[0]
    before = _top_keys(query, corpus, scales, k=3)

    original = module.feature_distance
    try:
        # Reverse the ordering entirely. If the seam is live, the list must move.
        module.feature_distance = lambda a, b, scale: 1.0 - (abs(a - b) / (abs(a - b) + scale))
        after = _top_keys(query, corpus, scales, k=3)
    finally:
        module.feature_distance = original

    assert before != after, "rank no longer routes through feature_distance"
    assert _top_keys(query, corpus, scales, k=3) == before, "the rebinding leaked"


def test_the_fast_distance_equals_the_full_one(corpus):
    """The fast path must be the same arithmetic, not an approximation of it.

    "Same" is to floating-point rounding, not to the last bit: `compare` totals its weighted
    terms with the builtin `sum()`, which Python 3.12 made compensated (Neumaier) and 3.11 did
    not, while the fast path accumulates with `+=`. The two therefore agree bit-for-bit on 3.11
    and can differ by one unit in the last place on 3.12 (CI), e.g. 0.41885331753121824 vs
    0.4188533175312183. A relative bound of 1e-12 is thousands of times that difference and
    still far tighter than any ranking or validation threshold; a genuine change of arithmetic
    (a dropped term, a different weighting) moves the distance by orders of magnitude more.
    """
    from app.analytics.comparables import compare, distance_between, robust_scales

    scales = robust_scales(corpus)
    checked = 0
    for query in corpus:
        for other in corpus:
            if other.key == query.key:
                continue
            assert distance_between(
                query.features(), other.features(), scales
            ) == pytest.approx(compare(query, other, scales).distance, rel=1e-12, abs=0)
            checked += 1
    assert checked > 0


def test_the_shared_weighting_pass_reproduces_the_per_weighting_one(corpus):
    """R7 collapsed thirteen weighting passes into one shared pass over the corpus.

    Seven weightings plus six leave-one-dimension-out all share the per-dimension
    distances and differ only in how they combine them, so they are computed once and
    re-weighted. This asserts the collapse is a refactor: every published overlap is the
    number the separate passes produced, to the digit.
    """
    from app.analytics.comparables import (
        DIMENSION_WEIGHTS,
        FEATURE_DIMENSIONS,
        robust_scales,
    )

    scales = robust_scales(corpus)
    report = run_battery(corpus, k=3)
    baseline = {s.key: _top_keys(s, corpus, scales, k=3) for s in corpus}

    def overlap(weights):
        return round(
            statistics.fmean(
                _jaccard(baseline[s.key], _top_keys(s, corpus, scales, weights, k=3))
                for s in corpus
            ),
            4,
        )

    published = next(c for c in report["checks"] if c["name"] == "best_single_dimension_null")
    assert published["detail"]["overlaps"]["uniform"] == overlap(
        dict.fromkeys(FEATURE_DIMENSIONS, 1.0)
    )
    loo_published = next(
        c for c in report["checks"] if c["name"] == "leave_one_dimension_out"
    )["detail"]["overlap_without"]
    for dimension in FEATURE_DIMENSIONS:
        assert published["detail"]["overlaps"][f"only_{dimension}"] == overlap(
            {dimension: 1.0}
        ), dimension
        assert loo_published[dimension] == overlap(
            {d: w for d, w in DIMENSION_WEIGHTS.items() if d != dimension}
        ), dimension
