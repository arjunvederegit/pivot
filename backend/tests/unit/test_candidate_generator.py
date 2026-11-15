"""R5-3. The candidate generator: coverage, determinism, and the deals it refuses.

Measured on the 30 ingested rosters at the end of R4, generating for five focal teams:
**4 of 29 counterparties reached (13.8 %)**, 40 candidates surfaced, and **2 of those 40**
had a counterparty utility above neutral. `COUNTERPARTY_MIN_UTILITY` was 42.0 — below the
50 that means "this deal changes nothing" — so 38 of 40 "recommendations" were deals the
product's own model said would hurt the other team.

The tests that matter here are the refusals.
"""

import pytest
from sqlalchemy.orm import Session

from app.analytics.projection import TEI_TO_NET_RATING
from app.services import candidates as gen
from app.services.candidates import (
    MAX_PROJECTED_WIN_LOSS,
    MAX_VALUE_GAP_WINS,
    MIN_UTILITY,
    PackageValue,
    _need_gain,
    _package_value,
    _roster_feasible,
    _salary_match_possible,
    _value_gap_wins,
    generate_candidates,
)
from app.services.evaluation import PlayerCard


def _card(pid: str, tei: float, minutes: float, skills: dict | None = None) -> PlayerCard:
    return PlayerCard(
        player_id=pid,
        name=pid.upper(),
        tei=tei,
        tei_sigma=0.5,
        availability=0.9,
        minutes=minutes,
        age=27.0,
        skills=skills or {},
    )


class TestThresholds:
    def test_both_sides_must_clear_the_scales_own_neutral(self):
        """Not a tuned number: 50 is what the composite calls 'changes nothing'. The old
        42.0 admitted deals the model said would hurt the counterparty."""
        assert MIN_UTILITY == 50.0

    def test_the_two_policies_share_one_unit(self):
        """Both are in projected wins, so the response can state them in a sentence a
        reader already understands."""
        assert MAX_PROJECTED_WIN_LOSS == MAX_VALUE_GAP_WINS == 2.0

    def test_the_value_gap_converts_through_the_fitted_constants(self):
        """No new calibration: package value goes to wins through R3's coefficient and the
        calibrated wins slope."""
        assert _value_gap_wins(0.1, 2.2) == pytest.approx(0.1 * TEI_TO_NET_RATING * 2.2)

    def test_a_relative_band_would_have_admitted_a_ten_win_transfer(self):
        """Why the unit changed. 'Tatum and Vučević for Cunningham' was a 39 % gap on the
        old relative band — and a 9.9-win transfer, the exact signature of an obviously
        one-sided package."""
        out_value, in_value = 0.7651, 0.4663
        relative_gap = abs(in_value - out_value) / max(out_value, in_value)
        assert relative_gap < 0.60, "the old band let it through"
        assert abs(_value_gap_wins(in_value - out_value, 2.2)) > 9.0
        assert abs(_value_gap_wins(in_value - out_value, 2.2)) > MAX_VALUE_GAP_WINS


class TestPackageValue:
    def test_it_weights_by_minutes_not_by_headcount(self):
        """Summing raw TEI made a 32-minute starter and a 6-minute reserve
        interchangeable, which is how a lopsided package passed a TEI-gap filter."""
        starter = _package_value([_card("a", 3.0, 32.0)], {})
        reserves = _package_value([_card("b", 3.0, 6.0), _card("c", 3.0, 6.0)], {})
        assert starter.value > reserves.value

    def test_it_measures_against_replacement_not_zero(self):
        replacement_level = _package_value([_card("a", -1.214, 30.0)], {})
        assert replacement_level.value == pytest.approx(0.0, abs=1e-6)

    def test_an_unmodelled_player_contributes_nothing_rather_than_a_default(self):
        card = PlayerCard("x", "X", None, None, 0.9, 20.0, 27.0, {})
        assert _package_value([card], {}).value == 0.0

    def test_salary_is_known_only_when_every_player_is_priced(self):
        cards = [_card("a", 1.0, 20.0), _card("b", 1.0, 20.0)]
        assert _package_value(cards, {"a": 1_000_000, "b": 2_000_000}).salary == 3_000_000
        assert _package_value(cards, {"a": 1_000_000}).salary is None


class TestConstraints:
    def test_roster_feasibility_respects_the_ceiling_the_rule_fails_on(self):
        # 18 + 2 - 1 = 19 exceeds the 18-spot ceiling.
        assert not _roster_feasible(out_count=1, in_count=2, focal_size=18, other_size=15)
        assert _roster_feasible(out_count=2, in_count=1, focal_size=18, other_size=15)

    def test_salary_matching_is_skipped_and_disclosed_when_prices_are_missing(self, cap_params):
        from app.cba.builder import load_cap_params

        params = load_cap_params(cap_params_session(cap_params), "2026-27")
        out_pkg = PackageValue(value=1.0, salary=None)
        in_pkg = PackageValue(value=1.0, salary=10_000_000)
        ok, note = _salary_match_possible(out_pkg, in_pkg, params)
        assert ok is True
        assert note is not None and "not applied" in note

    def test_an_impossible_salary_match_is_rejected(self, cap_params):
        from app.cba.builder import load_cap_params

        params = load_cap_params(cap_params_session(cap_params), "2026-27")
        out_pkg = PackageValue(value=1.0, salary=2_000_000)
        in_pkg = PackageValue(value=1.0, salary=60_000_000)
        ok, note = _salary_match_possible(out_pkg, in_pkg, params)
        assert ok is False
        assert note is None

    def test_need_gain_is_minutes_weighted(self):
        heavy = _card("a", 1.0, 32.0, {"shooting": 0.9})
        light = _card("b", 1.0, 4.0, {"shooting": 0.1})
        assert _need_gain([heavy, light], {"shooting"}) > _need_gain([light], {"shooting"})

    def test_need_gain_of_an_empty_target_set_is_zero(self):
        assert _need_gain([_card("a", 1.0, 30.0, {"shooting": 1.0})], set()) == 0.0


def cap_params_session(cap_params):
    from sqlalchemy.orm import object_session

    return object_session(cap_params)


class TestGeneratorEndToEnd:
    def test_every_counterparty_is_searched(self, db: Session, seeded_league: dict, cap_params):
        result = generate_candidates(db, seeded_league["team_a"].id, "improve")
        coverage = result["coverage"]
        assert coverage["counterparties_searched"] == coverage["counterparties_total"]
        assert coverage["share_searched"] == 1.0
        assert coverage["not_searched"] == []
        assert len(coverage["per_counterparty"]) == coverage["counterparties_total"]

    def test_coverage_reports_what_was_enumerated_versus_evaluated(
        self, db: Session, seeded_league: dict, cap_params
    ):
        result = generate_candidates(db, seeded_league["team_a"].id, "improve")
        for row in result["coverage"]["per_counterparty"]:
            assert row["pairs_enumerated"] >= row["pairs_after_constraints"]
            assert row["pairs_after_constraints"] >= row["candidates_surviving"]
            assert row["pairs_evaluated"] <= gen.EVALUATIONS_PER_COUNTERPARTY

    def test_every_surviving_candidate_clears_both_thresholds(
        self, db: Session, seeded_league: dict, cap_params
    ):
        result = generate_candidates(db, seeded_league["team_a"].id, "improve")
        for candidate in result["candidates"]:
            assert candidate["focal_utility"] > MIN_UTILITY
            assert candidate["counterparty_utility"] > MIN_UTILITY
            for delta in candidate["projected_delta_wins"].values():
                if delta is not None:
                    assert delta >= -MAX_PROJECTED_WIN_LOSS
            gap = candidate["package_value"]["gap_projected_wins"]
            assert abs(gap) <= MAX_VALUE_GAP_WINS + 1e-6

    def test_the_search_is_deterministic(self, db: Session, seeded_league: dict, cap_params):
        first = generate_candidates(db, seeded_league["team_a"].id, "improve")
        second = generate_candidates(db, seeded_league["team_a"].id, "improve")
        assert [
            (c["counterparty"]["abbreviation"], [p["player_id"] for p in c["outgoing"]])
            for c in first["candidates"]
        ] == [
            (c["counterparty"]["abbreviation"], [p["player_id"] for p in c["outgoing"]])
            for c in second["candidates"]
        ]
        assert first["evaluations_run"] == second["evaluations_run"]

    def test_untouchable_players_never_leave(self, db: Session, seeded_league: dict, cap_params):
        untouchable = seeded_league["roster_a"][0]
        result = generate_candidates(
            db,
            seeded_league["team_a"].id,
            "improve",
            untouchable_player_ids=[untouchable.id],
        )
        for candidate in result["candidates"]:
            assert untouchable.id not in [p["player_id"] for p in candidate["outgoing"]]

    def test_the_note_states_the_policies_it_applied(
        self, db: Session, seeded_league: dict, cap_params
    ):
        result = generate_candidates(db, seeded_league["team_a"].id, "improve")
        note = result["note"]
        assert "EXPERIMENTAL" in note
        assert "above 50" in note
        assert "counterparties were searched" in note
        assert result["coverage"]["salary_matching"]["note"]

    def test_it_refuses_to_run_without_team_needs(self, db: Session, cap_params):
        from tests.conftest import make_team

        team = make_team(db, 999, "ZZZ", "Nowhere")
        result = generate_candidates(db, team.id, "improve")
        assert result["candidates"] == []
        assert "team needs have not been computed" in result["error"]
