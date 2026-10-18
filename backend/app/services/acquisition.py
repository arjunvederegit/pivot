"""Start from a need, not from a trade.

The trade evaluator answers "is this deal good?". A front office asks the question the
other way round — "we cannot shoot; who can we get, and what would it cost?" — and until
R6 this product had no path from a diagnosed weakness to a specific name.

The chain is: **diagnosis → need → candidates → fit → acquisition cost → a trade you can
evaluate.** Every link reuses a system that is already validated; nothing new is fitted.

## The ranking rule, stated

Candidates are **filtered** by the need and **ranked** by projected wins.

- *Filter*: the player's percentile in the skill that addresses the chosen need must
  exceed the acquiring roster's own strength in that skill. A player who does not improve
  the thing the team is short of is not a target for it, however good he is.
- *Rank*: the projected win change from adding him, from `EvaluationService._performance`
  — R3's calibrated conversion through R5.5's rotation allocator.

**Ranking on `fit` alone was rejected, and the reason is written into `_fit` already:**
`fit_score` normalises minutes within each side, so a package's size cancels and
"acquiring an 8-minute player who answers a need scores like acquiring a 32-minute one".
As a component of a trade score that is correct — `performance` carries magnitude — but as
an ordering of acquisition targets it would put a specialist who plays six minutes level
with a starter.

**A single combined score was also rejected.** Blending need improvement with projected
wins needs a weight, and nothing in this repository can fit one: there is no labelled
outcome for "was this a good target". Both numbers are returned per candidate, the sort
key is named in the response, and `sort=need` reorders by need improvement instead.

## Cost is reported, never folded into the ranking

Three costs, each from an existing module and each able to say "unavailable":

- **On-court value** the counterparty must get back, in the units `generate_candidates`
  already balances packages in — `Σ (minutes/240)·(TEI − replacement)` — converted to
  projected wins through the same fitted constants.
- **Salary matching**: the outgoing salary the CBA's expanded-TPE bands would require.
  `unavailable` wherever the target has no contract, which under the available data is
  most of the league.
- **How hard he is to pry loose**: his share of his own team's modelled minutes and his
  rank there. Reported, never scored — it is a fact about the counterparty, not a price.

And then a **suggested outgoing package** from the acquiring roster that balances the deal
inside `MAX_VALUE_GAP_WINS`, returned as a ready-to-evaluate trade payload so the chain
ends where the trade evaluator begins.

## Feasibility closes the loop, and it changes the answer

Ranking by projected wins alone produces the same list for everybody. Measured across the
30 ingested rosters, the unfiltered top five of all 30 teams contained **26 distinct
players**: the need decides *which* elite player, and then every team is told to trade for
him. That is not a target list, it is a leaderboard.

So each candidate is put through the trade evaluator with its suggested package, and kept
only if it clears the conditions `generate_candidates` already commits to — both sides
above the composite's own neutral point, neither projecting worse than
`MAX_PROJECTED_WIN_LOSS`, and not verified illegal. The constants are imported from that
module rather than restated, so the two features cannot disagree about what a front office
would accept. The budget, the rejection tally and the reason for each rejection are
reported, exactly as the generator reports its own.

`feasible_only=false` returns the unfiltered ranking, so the difference the filter makes is
inspectable rather than asserted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.archetypes import SKILL_KEYS, UNADDRESSABLE_NEEDS
from app.analytics.needs import NEED_TO_SKILL
from app.analytics.projection import REPLACEMENT_TEI, TEAM_MINUTES, TEI_TO_NET_RATING
from app.canonical.rosters import resolve_roster_season
from app.cba.builder import build_trade_context, load_cap_params, player_salaries
from app.cba.engine import TradeLegalityEngine
from app.cba.rules.roster import MAX_WITH_TWO_WAYS
from app.cba.rules.salary import max_incoming_below_first_apron
from app.config import get_settings
from app.core.errors import DomainError, NotFoundError
from app.core.logging import get_logger
from app.db.models import Scenario, Team, TeamNeed
from app.services.candidates import (
    MAX_PROJECTED_WIN_LOSS,
    MAX_VALUE_GAP_WINS,
    MIN_UTILITY,
)
from app.services.evaluation import EvaluationService, PlayerCard

#: Targets returned by default. Bounded so the response stays readable; the count of
#: candidates that survived each filter is reported either way.
DEFAULT_LIMIT = 10

logger = get_logger(__name__)

#: Context failures quoted verbatim in the response. Enough to name the cause, bounded
#: so a systemic failure does not return one line per candidate.
_ERROR_SAMPLE = 5

MAX_LIMIT = 50
#: Outgoing packages considered when suggesting how to balance the deal.
MAX_OUTGOING_PACKAGE = 3
#: Trades actually evaluated while looking for feasible targets. Bounded for the same
#: reason `EVALUATIONS_PER_COUNTERPARTY` is, and reported for the same reason: a truncated
#: search that does not say so reads as an exhaustive one.
FEASIBILITY_BUDGET = 60

SORT_KEYS = ("impact", "need")


@dataclass
class Candidate:
    card: PlayerCard
    team: Team
    skill_percentile: float
    need_improvement: float
    delta_wins: float | None
    fit_score: float | None
    fit_detail: dict
    redundancy: float
    salary: int | None
    minutes_share_of_own_team: float | None
    rank_on_own_team_by_minutes: int | None


def _skill_for_need(need_key: str) -> str | None:
    return NEED_TO_SKILL.get(need_key)


def _team_needs(db: Session, team_id: str, season: str) -> list[TeamNeed]:
    return list(
        db.scalars(
            select(TeamNeed)
            .where(TeamNeed.team_id == team_id, TeamNeed.season == season)
            .order_by(TeamNeed.severity.desc(), TeamNeed.need_key)
        ).all()
    )


def _diagnosis(needs: list[TeamNeed]) -> list[dict]:
    return [
        {
            "need_key": need.need_key,
            "severity": round(need.severity, 3),
            "percentile": need.percentile,
            "explanation": need.explanation,
            "skill": _skill_for_need(need.need_key),
            "addressable": _skill_for_need(need.need_key) is not None,
            "not_addressable_reason": UNADDRESSABLE_NEEDS.get(need.need_key),
        }
        for need in needs
    ]


def _roster_strength(roster: list[PlayerCard], skill: str) -> float | None:
    """The acquiring roster's current level in one skill: its third-best rotation value.

    The same definition `_fit` uses for "already strong here", and for the same reason —
    a single good game-to-game specialist does not make a roster strong at something.
    """
    values = sorted(
        card.skills[skill]
        for card in roster
        if card.skills and skill in card.skills and card.minutes is not None
    )
    return values[-3] if len(values) >= 3 else None


def _package_value(cards: list[PlayerCard]) -> float:
    total = 0.0
    for card in cards:
        if card.tei is None or card.minutes is None:
            continue
        total += (card.minutes / TEAM_MINUTES) * (card.tei - REPLACEMENT_TEI)
    return total


def _value_in_wins(value: float, wins_slope: float) -> float:
    return value * TEI_TO_NET_RATING * wins_slope


def acquisition_targets(
    db: Session,
    team_id: str,
    need_key: str | None = None,
    limit: int = DEFAULT_LIMIT,
    sort: str = "impact",
    feasible_only: bool = True,
    scenario_id: str | None = None,
) -> dict:
    """Diagnosis, targets, cost, and a trade to evaluate.

    `scenario_id` applies the scenario's untouchable players: they are excluded from every
    suggested package, so a team that will not move its best player is not shown packages
    built around him. Nothing else about the scenario is read here — the weights belong to
    the trade evaluator, and reading them would make the target list depend on a weighting
    the ranking rule does not use.
    """
    if sort not in SORT_KEYS:
        raise DomainError(f"sort must be one of {', '.join(SORT_KEYS)}")
    settings = get_settings()
    team = db.get(Team, team_id)
    if team is None:
        raise NotFoundError(f"team {team_id} not found")
    untouchables: set[str] = set()
    if scenario_id:
        scenario = db.get(Scenario, scenario_id)
        if scenario is None:
            raise NotFoundError(f"scenario {scenario_id} not found")
        untouchables = set(scenario.untouchable_player_ids or [])

    roster_season = resolve_roster_season(db)
    needs = _team_needs(db, team_id, settings.current_season)
    diagnosis = _diagnosis(needs)
    if not needs:
        return {
            "team": {"id": team.id, "abbreviation": team.abbreviation, "name": team.full_name},
            "available": False,
            "unavailable_reason": (
                "team needs have not been computed for "
                f"{settings.current_season}; run `make score`"
            ),
            "diagnosis": [],
            "targets": [],
        }

    addressable = [d for d in diagnosis if d["addressable"] and d["severity"] > 0]
    if need_key is None:
        if not addressable:
            withheld = [d for d in diagnosis if d["severity"] > 0 and not d["addressable"]]
            named = ", ".join(d["need_key"].replace("_", " ") for d in withheld)
            return {
                "team": {"id": team.id, "abbreviation": team.abbreviation, "name": team.full_name},
                "available": False,
                "unavailable_reason": (
                    f"this roster's only measured weakness is {named}, and no player skill "
                    "claims to address it — "
                    + (withheld[0]["not_addressable_reason"] or "")
                    if withheld
                    else "no measured need on this roster is severe enough to search for"
                ),
                "diagnosis": diagnosis,
                "targets": [],
            }
        need_key = addressable[0]["need_key"]
    chosen = next((d for d in diagnosis if d["need_key"] == need_key), None)
    if chosen is None:
        raise DomainError(
            f"{need_key} is not a measured need for this team; "
            f"measured: {', '.join(d['need_key'] for d in diagnosis)}"
        )
    skill = chosen["skill"]
    if skill is None:
        return {
            "team": {"id": team.id, "abbreviation": team.abbreviation, "name": team.full_name},
            "available": False,
            "unavailable_reason": chosen["not_addressable_reason"]
            or f"no player skill addresses {need_key}",
            "diagnosis": diagnosis,
            "target_need": chosen,
            "targets": [],
        }

    service = EvaluationService(db)
    roster = service._roster_cards(team_id)
    strength = _roster_strength(roster, skill)
    wins_slope = float(service.wins_mapping().get("slope", 2.7))

    teams = {t.id: t for t in db.scalars(select(Team)).all()}
    minutes_by_team: dict[str, float] = {}
    league: list[tuple[PlayerCard, Team]] = []
    for other_id, other in teams.items():
        if other_id == team_id:
            continue
        for card in service._roster_cards(other_id):
            league.append((card, other))
            minutes_by_team[other_id] = minutes_by_team.get(other_id, 0.0) + (card.minutes or 0.0)

    counted = {
        "players_considered": len(league),
        "no_skill_measured": 0,
        "no_impact_estimate": 0,
        "does_not_improve_the_need": 0,
        "no_roster_strength_to_compare": 0,
    }
    if strength is None:
        counted["no_roster_strength_to_compare"] = len(league)
        return {
            "team": {"id": team.id, "abbreviation": team.abbreviation, "name": team.full_name},
            "available": False,
            "unavailable_reason": (
                f"fewer than three players on this roster have {skill} measured, so the "
                "roster's own level in it is unknown and a target cannot be said to "
                "improve on it"
            ),
            "diagnosis": diagnosis,
            "target_need": chosen,
            "targets": [],
            "search": counted,
        }

    salaries = player_salaries(
        db, [card.player_id for card, _ in league], settings.cap_league_year
    )
    salary_by_player = {pid: value[0] for pid, value in salaries.items()}
    ranks: dict[str, int] = {}
    for other_id in teams:
        if other_id == team_id:
            continue
        ordered = sorted(
            service._roster_cards(other_id),
            key=lambda c: (-(c.minutes or 0.0), c.player_id),
        )
        for index, card in enumerate(ordered, start=1):
            ranks[card.player_id] = index

    candidates: list[Candidate] = []
    for card, other in league:
        if not card.skills or skill not in card.skills:
            counted["no_skill_measured"] += 1
            continue
        if card.tei is None or card.minutes is None:
            counted["no_impact_estimate"] += 1
            continue
        percentile = card.skills[skill]
        improvement = percentile - strength
        if improvement <= 0:
            counted["does_not_improve_the_need"] += 1
            continue
        # The pure add: what he does for this roster before anything leaves. `_fit`'s
        # one-way baseline (R5.5-2) is exactly the case this needs.
        _, perf_detail = service._performance(roster, [card], set())
        fit_value, fit_detail = service._fit(team_id, roster, [card], [])
        total_minutes = minutes_by_team.get(other.id) or 0.0
        candidates.append(
            Candidate(
                card=card,
                team=other,
                skill_percentile=percentile,
                need_improvement=improvement,
                delta_wins=perf_detail.get("delta_wins"),
                fit_score=fit_value,
                fit_detail=fit_detail,
                redundancy=sum((fit_detail.get("redundancies") or {}).values()),
                salary=salary_by_player.get(card.player_id),
                minutes_share_of_own_team=(
                    round((card.minutes or 0.0) / total_minutes, 4) if total_minutes > 0 else None
                ),
                rank_on_own_team_by_minutes=ranks.get(card.player_id),
            )
        )

    if sort == "impact":
        candidates.sort(
            key=lambda c: (-(c.delta_wins if c.delta_wins is not None else -99), c.card.player_id)
        )
    else:
        candidates.sort(key=lambda c: (-c.need_improvement, c.card.player_id))

    cap_params = load_cap_params(db, settings.cap_league_year)
    focal_salaries = player_salaries(
        db, [c.player_id for c in roster], settings.cap_league_year
    )
    focal_salary_map = {pid: value[0] for pid, value in focal_salaries.items()}

    wanted = min(max(limit, 1), MAX_LIMIT)
    payload: list[dict] = []
    rejected: dict[str, int] = {
        "no_balancing_package": 0,
        "verified_illegal": 0,
        "focal_utility": 0,
        "counterparty_utility": 0,
        "projected_win_loss": 0,
        "context_error": 0,
    }
    evaluated = 0
    truncated = False
    context_error_types: dict[str, int] = {}
    context_error_samples: list[str] = []
    feasibility: dict[str, Any] = {
        "applied": feasible_only,
        "budget": FEASIBILITY_BUDGET if feasible_only else 0,
        "trades_evaluated": 0,
        "rejected": rejected,
        "truncated_by_budget": False,
        "conditions": {
            "both_sides_above": MIN_UTILITY,
            "max_projected_win_loss": MAX_PROJECTED_WIN_LOSS,
            "source": "app.services.candidates — the same policy the generator applies",
        },
    }
    for candidate in candidates:
        if len(payload) >= wanted:
            break
        entry = _target_payload(
            candidate,
            team,
            roster,
            focal_salary_map,
            cap_params,
            wins_slope,
            skill,
            chosen,
            untouchables,
        )
        if not feasible_only:
            payload.append(entry)
            continue
        if evaluated >= FEASIBILITY_BUDGET:
            truncated = True
            break
        verdict = _evaluate_feasibility(db, service, team_id, candidate, entry)
        evaluated += 1
        reason = verdict["reason"]
        if reason is not None:
            rejected[reason] += 1
            if reason == "context_error":
                error_type = verdict.get("error_type") or "Exception"
                context_error_types[error_type] = context_error_types.get(error_type, 0) + 1
                if len(context_error_samples) < _ERROR_SAMPLE:
                    context_error_samples.append(str(verdict.get("error")))
            continue
        entry["trade_evaluation"] = verdict["evaluation"]
        payload.append(entry)
    feasibility["trades_evaluated"] = evaluated
    feasibility["truncated_by_budget"] = truncated
    # A count with no cause cannot be acted on. Non-zero here with a single repeated type
    # is schema drift, not a league with no feasible trades in it.
    feasibility["context_errors"] = {
        "types": dict(sorted(context_error_types.items())),
        "sample": context_error_samples,
    }
    return {
        "team": {"id": team.id, "abbreviation": team.abbreviation, "name": team.full_name},
        "available": True,
        "season": settings.current_season,
        # R8.2: the diagnosis below was measured on the `season` team; the targets are scored
        # against the roster of `roster_season`. When they differ the need may not describe it.
        "roster_season": roster_season,
        "needs_match_roster": roster_season == settings.current_season,
        "diagnosis": diagnosis,
        "target_need": {**chosen, "roster_strength_percentile": round(strength, 4)},
        "sort": sort,
        "untouchable_player_ids": sorted(untouchables),
        "sort_rule": (
            "projected win change from adding the player, before anything leaves"
            if sort == "impact"
            else "how far the player's percentile in the need's skill exceeds this "
            "roster's own level in it"
        ),
        "filter_rule": (
            f"only players whose {skill.replace('_', ' ')} percentile exceeds this "
            f"roster's own "
            f"({strength:.0%}); a player who does not improve the thing the team is "
            "short of is not a target for it"
        ),
        "search": {**counted, "candidates": len(candidates)},
        "feasibility": feasibility,
        "targets": payload,
        "notes": [
            "A target is returned only after the trade that would acquire him has been "
            "evaluated for both teams under the generator's own conditions. "
            "`feasible_only=false` returns the unfiltered ranking.",
            "Ranking and filtering are two separate rules, both stated above. No combined "
            "score is produced, because nothing in this repository labels a target as good "
            "or bad, so a weight between need and impact could not be fitted.",
            "Acquisition cost is reported, never scored. A cheap target and an expensive "
            "one are ordered the same way here.",
            "The suggested package balances modelled on-court value; it is a starting "
            "point for the trade evaluator, not a proposal the counterparty has accepted.",
        ],
    }


def _evaluate_feasibility(
    db: Session,
    service: EvaluationService,
    team_id: str,
    candidate: Candidate,
    entry: dict,
) -> dict:
    """Would both front offices do this? The generator's conditions, on one trade."""
    package = entry.get("suggested_package") or []
    if not package:
        return {"reason": "no_balancing_package", "evaluation": None}
    other_id = candidate.team.id
    moves = [
        {
            "player_id": candidate.card.player_id,
            "from_team_id": other_id,
            "to_team_id": team_id,
        }
    ] + [
        {"player_id": player["player_id"], "from_team_id": team_id, "to_team_id": other_id}
        for player in package
    ]
    team_ids = [team_id, other_id]
    try:
        context = build_trade_context(db, team_ids, moves)
        legality = TradeLegalityEngine().evaluate(context)
    except Exception as exc:  # noqa: BLE001 — counted, typed and logged; see below
        # Surfaced as a count rather than swallowed: R5.5 recorded that a bare `except`
        # around context building turned schema drift into an empty result set that read
        # as a modelling outcome. R7 adds the exception's **type** to the count, because a
        # bare tally still cannot tell a genuinely unbuildable trade apart from a database
        # one migration behind — which is the case that produced the R5.5 incident.
        logger.warning(
            "acquisition feasibility could not build a trade context: %s: %s",
            type(exc).__name__,
            exc,
            extra={"team_id": team_id, "counterparty": candidate.team.abbreviation},
        )
        return {
            "reason": "context_error",
            "evaluation": None,
            "error_type": type(exc).__name__,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if legality["overall_status"] == "verified_illegal":
        return {"reason": "verified_illegal", "evaluation": None}
    focal = service.evaluate_for_team(
        team_id, team_ids, moves, [], "custom", None, legality, simulate=False
    )
    if focal["composite_utility"] is None or focal["composite_utility"] <= MIN_UTILITY:
        return {"reason": "focal_utility", "evaluation": None}
    other = service.evaluate_for_team(
        other_id, team_ids, moves, [], "custom", None, legality, simulate=False
    )
    if other["composite_utility"] is None or other["composite_utility"] <= MIN_UTILITY:
        return {"reason": "counterparty_utility", "evaluation": None}
    win_deltas = {
        "focal": (focal["detail"].get("performance") or {}).get("delta_wins"),
        "counterparty": (other["detail"].get("performance") or {}).get("delta_wins"),
    }
    if any(d is not None and d < -MAX_PROJECTED_WIN_LOSS for d in win_deltas.values()):
        return {"reason": "projected_win_loss", "evaluation": None}
    return {
        "reason": None,
        "evaluation": {
            "team_ids": team_ids,
            "player_moves": moves,
            "legality_status": legality["overall_status"],
            "focal_utility": focal["composite_utility"],
            "counterparty_utility": other["composite_utility"],
            "focal_components": focal["components"],
            "projected_delta_wins": win_deltas,
        },
    }


def _target_payload(
    candidate: Candidate,
    team: Team,
    roster: list[PlayerCard],
    focal_salary_map: dict[str, int | None],
    cap_params: Any,
    wins_slope: float,
    skill: str,
    need: dict,
    untouchables: set[str],
) -> dict:
    value = _package_value([candidate.card])
    package, package_note = _suggest_package(
        candidate, roster, focal_salary_map, cap_params, wins_slope, value, untouchables
    )
    salary_required = None
    if candidate.salary is not None:
        # Invert the permissive band: the least outgoing salary that admits this player.
        salary_required = _minimum_outgoing_for(candidate.salary, cap_params)
    return {
        "player_id": candidate.card.player_id,
        "name": candidate.card.name,
        "team": {
            "id": candidate.team.id,
            "abbreviation": candidate.team.abbreviation,
            "name": candidate.team.full_name,
        },
        "tei": round(candidate.card.tei, 2) if candidate.card.tei is not None else None,
        "minutes": round(candidate.card.minutes, 1) if candidate.card.minutes is not None else None,
        "age": round(candidate.card.age, 1) if candidate.card.age is not None else None,
        "need_skill": skill,
        "skill_percentile": round(candidate.skill_percentile, 4),
        "need_improvement": round(candidate.need_improvement, 4),
        "projected_delta_wins": (
            round(candidate.delta_wins, 2) if candidate.delta_wins is not None else None
        ),
        "fit_score": candidate.fit_score,
        "redundancy": round(candidate.redundancy, 4),
        "skills": {k: round(v, 4) for k, v in candidate.card.skills.items() if k in SKILL_KEYS},
        "acquisition_cost": {
            "package_value": round(value, 4),
            "package_value_projected_wins": round(_value_in_wins(value, wins_slope), 2),
            "salary": candidate.salary,
            "minimum_outgoing_salary": salary_required,
            "salary_note": (
                None
                if candidate.salary is not None
                else "no contract for this player from the configured provider, so salary "
                "matching cannot be checked"
            ),
            "minutes_share_of_own_team": candidate.minutes_share_of_own_team,
            "rank_on_own_team_by_minutes": candidate.rank_on_own_team_by_minutes,
            "reported_not_scored": (
                "how central a player is to his own team is a fact about the counterparty, "
                "not a price this product can compute"
            ),
        },
        "suggested_package": package,
        "suggested_package_note": package_note,
        "why": _why(candidate, need, skill),
    }


def _minimum_outgoing_for(incoming_salary: int, cap_params: Any) -> int | None:
    """Smallest outgoing salary whose expanded-TPE band admits `incoming_salary`.

    Solved on the band the response already commits to using — the permissive
    below-first-apron one — because a verified apron position needs every rostered player
    priced, which no team has under the available contract data (the same reason
    `generate_candidates` gives).
    """
    low, high = 0.0, float(max(incoming_salary * 2, 1))
    for _ in range(60):
        mid = (low + high) / 2
        if max_incoming_below_first_apron(mid, cap_params) >= incoming_salary:
            high = mid
        else:
            low = mid
    # Ceil, not round: the returned figure has to *satisfy* the band. Rounding a
    # bisection's upper bound down lands a dollar short, and a salary-matching figure
    # that is a dollar short is wrong in the direction that matters.
    return math.ceil(high)


def _suggest_package(
    candidate: Candidate,
    roster: list[PlayerCard],
    focal_salary_map: dict[str, int | None],
    cap_params: Any,
    wins_slope: float,
    target_value: float,
    untouchables: set[str],
) -> tuple[list[dict], str]:
    """The smallest package from this roster that balances the target's modelled value.

    "Balances" is `generate_candidates`'s own condition — the two packages may not differ
    by more than `MAX_VALUE_GAP_WINS` of modelled value — so the suggestion and the
    generator cannot disagree about what a fair package is.
    """
    tradeable = sorted(
        (
            c
            for c in roster
            if c.tei is not None and c.minutes is not None and c.player_id not in untouchables
        ),
        key=lambda c: (-(c.minutes or 0.0), c.player_id),
    )
    best: list[PlayerCard] | None = None
    for size in (1, 2, MAX_OUTGOING_PACKAGE):
        pool = tradeable[: 10 if size == 1 else 8 if size == 2 else 6]
        for group in combinations(pool, size):
            gap = _value_in_wins(target_value - _package_value(list(group)), wins_slope)
            if (
                abs(gap) <= MAX_VALUE_GAP_WINS
                and len(roster) - len(group) + 1 <= MAX_WITH_TWO_WAYS
            ):
                best = list(group)
                break
        if best:
            break
    if not best:
        return [], (
            "no package of up to three modelled players on this roster balances the "
            f"target's value inside {MAX_VALUE_GAP_WINS:.0f} projected wins"
        )
    salaries = [focal_salary_map.get(c.player_id) for c in best]
    priced = all(s is not None for s in salaries)
    note = (
        "balances modelled on-court value inside "
        f"{MAX_VALUE_GAP_WINS:.0f} projected wins"
        + (
            "; salary matching is verifiable for this package"
            if priced and candidate.salary is not None
            else "; salary matching cannot be checked because at least one player in it "
            "has no contract from the configured provider"
        )
    )
    return (
        [
            {
                "player_id": c.player_id,
                "name": c.name,
                "tei": round(c.tei, 2) if c.tei is not None else None,
                "minutes": round(c.minutes, 1) if c.minutes is not None else None,
                "salary": focal_salary_map.get(c.player_id),
            }
            for c in best
        ],
        note,
    )


def _why(candidate: Candidate, need: dict, skill: str) -> list[str]:
    reasons = [
        f"{candidate.card.name} sits at the {candidate.skill_percentile:.0%} percentile in "
        f"{skill.replace('_', ' ')}, {candidate.need_improvement:.0%} above this roster's "
        "own level in it.",
        f"The need it addresses — {need['need_key'].replace('_', ' ')} — is measured at "
        f"severity {need['severity']:.2f}: {need['explanation']}",
    ]
    if candidate.delta_wins is not None:
        reasons.append(
            f"Adding him alone projects {candidate.delta_wins:+.1f} wins, before anything "
            "leaves."
        )
    if candidate.redundancy > 0:
        reasons.append(
            f"He also adds to skills this roster is already strong in "
            f"(redundancy {candidate.redundancy:.2f}), which the fit score charges for."
        )
    if candidate.rank_on_own_team_by_minutes is not None:
        reasons.append(
            f"He is {candidate.team.abbreviation}'s number "
            f"{candidate.rank_on_own_team_by_minutes} by minutes; how willingly they part "
            "with him is not something this product can model."
        )
    return reasons
