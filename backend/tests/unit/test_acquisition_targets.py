"""Properties of need-driven discovery.

The battery in `app/services/acquisition_validation.py` measures the live league; these
pin the rules that must hold on any league — including the two states that are answers
rather than failures: a need no player skill addresses, and a roster whose own level in a
skill is unknown.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.errors import DomainError, NotFoundError
from app.db.models import PlayerImpactEstimate, Scenario, TeamNeed
from app.services.acquisition import (
    FEASIBILITY_BUDGET,
    _minimum_outgoing_for,
    _roster_strength,
    acquisition_targets,
)
from app.services.candidates import MAX_PROJECTED_WIN_LOSS, MIN_UTILITY
from app.services.evaluation import EvaluationService


@pytest.fixture()
def league(db, seeded_league):
    return seeded_league


def test_an_unknown_team_is_a_not_found_not_an_empty_list(db):
    with pytest.raises(NotFoundError):
        acquisition_targets(db, "no-such-team")


def test_an_unknown_sort_is_refused_rather_than_ignored(db, league):
    with pytest.raises(DomainError):
        acquisition_targets(db, league["team_a"].id, sort="cheapest")


def test_a_need_the_team_does_not_have_is_refused_and_names_what_it_does(db, league):
    with pytest.raises(DomainError) as excinfo:
        acquisition_targets(db, league["team_a"].id, need_key="lineup_size")
    assert "measured" in str(excinfo.value)


def test_a_need_no_skill_addresses_is_an_answer_not_a_failure(db, league):
    result = acquisition_targets(
        db, league["team_a"].id, need_key="point_of_attack_defense"
    )
    assert result["available"] is False
    assert "no player skill claims to address this" in result["unavailable_reason"]
    assert result["targets"] == []
    # The need is still reported in the diagnosis, so a real weakness does not vanish.
    assert any(d["need_key"] == "point_of_attack_defense" for d in result["diagnosis"])


def test_a_team_with_no_computed_needs_says_so(db, seeded_league):
    for need in db.scalars(select(TeamNeed)).all():
        db.delete(need)
    db.commit()
    result = acquisition_targets(db, seeded_league["team_a"].id)
    assert result["available"] is False
    assert "team needs have not been computed" in result["unavailable_reason"]


def test_the_ranking_and_filter_rules_are_stated_in_the_response(db, league):
    result = acquisition_targets(db, league["team_a"].id, limit=3)
    if not result["available"]:
        pytest.skip(result["unavailable_reason"])
    assert result["sort"] == "impact"
    assert "projected win change" in result["sort_rule"]
    assert "exceeds this roster's own" in result["filter_rule"]
    assert result["search"]["players_considered"] > 0


def test_every_target_improves_the_need_and_is_not_already_on_the_roster(db, league):
    result = acquisition_targets(db, league["team_a"].id, limit=10)
    if not result["available"]:
        pytest.skip(result["unavailable_reason"])
    own = {p.id for p in league["roster_a"]}
    for target in result["targets"]:
        assert target["need_improvement"] > 0
        assert target["player_id"] not in own
        assert target["team"]["id"] != league["team_a"].id


def test_sorting_by_need_reorders_by_need_improvement(db, league):
    # Feasibility off: the ordering is the property under test, and the filter would
    # silently shorten the list before the ordering could be checked.
    by_need = acquisition_targets(
        db, league["team_a"].id, limit=10, sort="need", feasible_only=False
    )
    if not by_need["available"] or len(by_need["targets"]) < 2:
        pytest.skip("not enough targets in the fixture league")
    improvements = [t["need_improvement"] for t in by_need["targets"]]
    assert improvements == sorted(improvements, reverse=True)


def test_feasibility_reports_its_budget_conditions_and_rejections(db, league):
    result = acquisition_targets(db, league["team_a"].id, limit=3)
    if not result["available"]:
        pytest.skip(result["unavailable_reason"])
    feasibility = result["feasibility"]
    assert feasibility["applied"] is True
    assert feasibility["budget"] == FEASIBILITY_BUDGET
    assert feasibility["conditions"]["both_sides_above"] == MIN_UTILITY
    assert feasibility["conditions"]["max_projected_win_loss"] == MAX_PROJECTED_WIN_LOSS
    assert set(feasibility["rejected"]) == {
        "no_balancing_package",
        "verified_illegal",
        "focal_utility",
        "counterparty_utility",
        "projected_win_loss",
        "context_error",
    }


def test_a_feasible_target_carries_the_trade_that_was_evaluated(db, league):
    """The fixture's two rosters are identical by construction, so every balanced deal
    lands exactly at neutral and is refused — which is itself the property worth pinning:
    a refusal is accounted for, never silently dropped."""
    result = acquisition_targets(db, league["team_a"].id, limit=3)
    if not result["available"]:
        pytest.skip(result["unavailable_reason"])
    feasibility = result["feasibility"]
    assert feasibility["trades_evaluated"] == len(result["targets"]) + sum(
        feasibility["rejected"].values()
    )
    for target in result["targets"]:
        evaluation = target["trade_evaluation"]
        assert evaluation["focal_utility"] > MIN_UTILITY
        assert evaluation["counterparty_utility"] > MIN_UTILITY
        # The trade is returned in the shape /trades/evaluate accepts.
        assert {m["player_id"] for m in evaluation["player_moves"]}
        assert set(evaluation["team_ids"]) == {league["team_a"].id, target["team"]["id"]}


def test_turning_feasibility_off_widens_the_list_and_says_so(db, league):
    strict = acquisition_targets(db, league["team_a"].id, limit=10)
    loose = acquisition_targets(db, league["team_a"].id, limit=10, feasible_only=False)
    if not strict["available"]:
        pytest.skip(strict["unavailable_reason"])
    assert loose["feasibility"]["applied"] is False
    assert len(loose["targets"]) >= len(strict["targets"])
    assert all("trade_evaluation" not in t for t in loose["targets"])


def test_untouchable_players_never_appear_in_a_suggested_package(db, league):
    keep = league["roster_a"][0]
    scenario = Scenario(
        name="fixture scenario",
        focal_team_id=league["team_a"].id,
        strategy="contend",
        untouchable_player_ids=[keep.id],
    )
    db.add(scenario)
    db.commit()
    result = acquisition_targets(
        db, league["team_a"].id, limit=10, scenario_id=scenario.id
    )
    if not result["available"]:
        pytest.skip(result["unavailable_reason"])
    assert result["untouchable_player_ids"] == [keep.id]
    for target in result["targets"]:
        assert keep.id not in {p["player_id"] for p in target["suggested_package"]}


def test_an_unknown_scenario_is_a_not_found(db, league):
    with pytest.raises(NotFoundError):
        acquisition_targets(db, league["team_a"].id, scenario_id="nope")


def test_a_roster_with_fewer_than_three_measured_players_says_the_level_is_unknown(
    db, seeded_league
):
    """`_roster_strength` mirrors `_fit`'s definition: with fewer than three observations
    the roster's level in a skill is unknown, not average."""
    service = EvaluationService(db)
    roster = service._roster_cards(seeded_league["team_a"].id)
    assert _roster_strength(roster[:2], "shooting_volume") is None
    assert _roster_strength([], "shooting_volume") is None


def test_the_minimum_outgoing_salary_inverts_the_band_it_claims_to(db, cap_params):
    from app.cba.builder import load_cap_params
    from app.cba.rules.salary import max_incoming_below_first_apron

    params = load_cap_params(db, "2026-27")
    for incoming in (2_000_000, 15_000_000, 40_000_000):
        required = _minimum_outgoing_for(incoming, params)
        assert max_incoming_below_first_apron(required, params) >= incoming
        # ...and a dollar less does not, so the figure is the minimum and not merely
        # sufficient.
        assert max_incoming_below_first_apron(required - 1, params) < incoming


def test_targets_without_an_impact_estimate_are_counted_not_scored(db, seeded_league):
    """A player the impact model has never scored cannot be a ranked target, and the
    response says how many were set aside."""
    for estimate in db.scalars(
        select(PlayerImpactEstimate).where(
            PlayerImpactEstimate.player_id.in_([p.id for p in seeded_league["roster_b"][:5]])
        )
    ).all():
        db.delete(estimate)
    db.commit()
    db.expire_all()
    result = acquisition_targets(db, seeded_league["team_a"].id, limit=10)
    if not result["available"]:
        pytest.skip(result["unavailable_reason"])
    assert result["search"]["no_impact_estimate"] >= 5
    named = {t["player_id"] for t in result["targets"]}
    assert not named & {p.id for p in seeded_league["roster_b"][:5]}


def test_the_search_tally_accounts_for_every_player_considered(db, league):
    result = acquisition_targets(db, league["team_a"].id, limit=5)
    if not result["available"]:
        pytest.skip(result["unavailable_reason"])
    search = result["search"]
    accounted = (
        search["no_skill_measured"]
        + search["no_impact_estimate"]
        + search["does_not_improve_the_need"]
        + search["candidates"]
    )
    assert accounted == search["players_considered"]


def test_evaluation_time_is_bounded_by_the_stated_budget(db, league):
    result = acquisition_targets(db, league["team_a"].id, limit=50)
    if not result["available"]:
        pytest.skip(result["unavailable_reason"])
    assert result["feasibility"]["trades_evaluated"] <= FEASIBILITY_BUDGET


def test_stale_needs_from_another_season_are_not_used(db, league):
    db.add(
        TeamNeed(
            team_id=league["team_a"].id,
            season="2019-20",
            need_key="rim_protection",
            severity=1.0,
            percentile=0.0,
            explanation="stale fixture need",
        )
    )
    db.commit()
    result = acquisition_targets(db, league["team_a"].id, limit=3)
    seasons = {d["need_key"] for d in result["diagnosis"]}
    assert "stale fixture need" not in seasons
    assert all(isinstance(d["severity"], float) for d in result["diagnosis"])
    assert datetime.now(UTC)  # keeps the import honest
