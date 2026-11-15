"""Constrained trade-candidate generation.

## What was wrong, measured

Run against the 30 ingested rosters at the end of R4, generating for five focal teams:

| | |
| --- | --- |
| Counterparties reached | **4 of 29 (13.8 %)** — the budget was spent on the first four alphabetically |
| Candidates surfaced | 40 |
| ...whose **counterparty** utility was above neutral | **2** |
| Salary matching applied | none |

So 38 of 40 "recommendations" were deals the product's own model said would *hurt* the
other team. `COUNTERPARTY_MIN_UTILITY` was **42.0** — below the 50 that means "this deal
changes nothing" — so a package only had to be not-quite-catastrophic for the counterparty
to qualify as mutually attractive. That is how "Sam Hauser for LaMelo Ball" arrived at
focal 69.9 / counterparty 42.3 and was presented as a candidate.

## What replaces it

**Both sides must clear neutral.** A trade is a voluntary act by two front offices, so a
deal neither would refuse must be better than nothing for both. The threshold is 50 — the
scale's own definition of "changes nothing" — not a tuned number. That is a genuinely
restrictive condition and not an artifact of an inflationary composite: over 241 random
two-team trades on the same rosters, **both sides clear 50 only 9.5 % of the time**, one
side does 76.8 %, and the two utilities sum to 99.80 ± 7.32 against the 100 a strictly
zero-sum scale would give.

**And a basketball check the composite does not perform.** Requiring both sides above
neutral still surfaced "Coby White for Cooper Flagg, Dallas at 55.0", because a `fit` gain
can outweigh a `performance` loss under the default weights — `fit` carries 0.340 of
composite variance against `performance`'s 0.130. Two stated policies, both denominated in
projected wins so they read as one sentence: **neither side may project worse than −2
wins**, and **the packages may not differ by more than 2 wins of modelled value**.

**Every counterparty is searched.** The evaluation budget is divided across the 29 teams
instead of consumed by the first few, so coverage is 29 of 29 by construction and the
response reports what was enumerated versus evaluated *per counterparty*.

**Cheap constraints run before the expensive evaluation.** Salary matching (the real CBA
bands, from `cba.rules.salary`), roster-spot feasibility, and package-value balance are
checked on the pair before any legality context is built, so the evaluation budget is
spent on deals that can actually happen.

**The search is deterministic end to end.** Every ordering has a total tiebreaker on
player id, so the same database returns the same candidates in the same order.
"""

from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.needs import NEED_TO_SKILL
from app.analytics.projection import REPLACEMENT_TEI, TEAM_MINUTES, TEI_TO_NET_RATING
from app.cba.builder import build_trade_context, load_cap_params, player_salaries
from app.cba.engine import TradeLegalityEngine
from app.cba.rules.roster import MAX_WITH_TWO_WAYS
from app.cba.rules.salary import (
    max_incoming_at_or_above_first_apron,
    max_incoming_below_first_apron,
)
from app.config import get_settings
from app.core.logging import get_logger
from app.db.models import Team
from app.services.evaluation import DEFAULT_WEIGHTS, EvaluationService, PlayerCard

logger = get_logger(__name__)

MAX_PLAYERS_PER_SIDE = 3
#: Construction failures quoted verbatim in the response. Enough to name the cause,
#: bounded so a systemic failure does not return one line per pair.
_ERROR_SAMPLE = 5
#: Packages enumerated per side before ranking. Bounded so the pair space stays finite;
#: the response reports how many were enumerated and how many survived.
MAX_OUTGOING_PACKAGES = 24
MAX_INCOMING_PACKAGES = 24
#: Full evaluations per counterparty. 29 x 14 = 406 total, essentially the old global
#: budget of 400 — but spread across the whole league instead of the first four teams.
EVALUATIONS_PER_COUNTERPARTY = 14
TOP_K = 8

#: A deal must be better than doing nothing **for both teams**. 50 is the composite
#: scale's own definition of "changes nothing", not a tuned threshold. The previous 42.0
#: admitted deals the model said would hurt the counterparty: 38 of 40 candidates.
MIN_UTILITY = 50.0

#: A **stated policy**, not a fitted parameter: no candidate may project more than a
#: two-win on-court downgrade for either team.
#:
#: The composite alone does not enforce this, and the first rebuild showed why. Requiring
#: both sides above neutral removed every deal the model called harmful, but still
#: surfaced "Coby White for Cooper Flagg, Dallas at 55.0" — because a `fit` gain can
#: outweigh a `performance` loss under the default weights, and `fit` carries 0.340 of
#: composite variance against `performance`'s 0.130 (measured, 168 evaluations). A
#: composite that permits a package is not evidence that a front office would.
#:
#: Two wins because `performance` is 5 points per projected win, so this is the point
#: where the on-court term alone would read as a clear negative. It is a policy about what
#: this feature will propose, disclosed in the response, and it is deliberately not tuned
#: against the candidate list it produces.
MAX_PROJECTED_WIN_LOSS = 2.0

#: How far apart two packages' on-court value may be, **in the same projected wins the
#: policy above is denominated in**. One number, two applications: neither side may lose
#: more than two projected wins, and the packages may not differ by more than two
#: projected wins of modelled value.
#:
#: A relative band was tried first — 60 % of the larger side — and it is the wrong unit.
#: It passed "Jayson Tatum and Nikola Vučević for Cade Cunningham" at a 39 % gap, which in
#: win terms is a **9.9-win** transfer: the exact signature of an obviously one-sided
#: package. A share of a large number is a large number.
#:
#: Package value is `Σ (minutes/240)·(TEI − replacement)`, the quantity the projection
#: consumes, so it converts to wins through the same fitted constants the rest of the
#: engine uses — no new calibration and nothing tuned against the candidate list.
MAX_VALUE_GAP_WINS = 2.0


@dataclass
class PackageValue:
    """A package's on-court value in the units the projection actually consumes.

    `team_tei_per_minute` weights each player by his share of the 240 team minutes and
    measures him against replacement level, so a 32-minute starter and a 6-minute reserve
    are not interchangeable — which summing raw TEI made them.
    """

    value: float
    salary: int | None
    players: list[PlayerCard] = field(default_factory=list)

    @property
    def salary_known(self) -> bool:
        return self.salary is not None


def _value_gap_wins(value_delta: float, wins_slope: float) -> float:
    """Convert a package-value difference into projected wins.

    Through the same fitted constants the projection uses — R3's TEI→net-rating
    coefficient and the calibrated wins-per-net-rating slope — so the constraint is
    denominated in a quantity a reader already understands and nothing new is calibrated.
    """
    return value_delta * TEI_TO_NET_RATING * wins_slope


def _package_value(cards: list[PlayerCard], salaries: dict[str, int | None]) -> PackageValue:
    total = 0.0
    for card in cards:
        if card.tei is None or card.minutes is None:
            continue
        total += (card.minutes / TEAM_MINUTES) * (card.tei - REPLACEMENT_TEI)
    known = [salaries.get(c.player_id) for c in cards]
    salary = sum(s or 0 for s in known) if all(s is not None for s in known) else None
    return PackageValue(value=total, salary=salary, players=list(cards))


def _salary_match_possible(
    out_pkg: PackageValue, in_pkg: PackageValue, cap_params: Any, apron_unknown: bool = True
) -> tuple[bool, str | None]:
    """Can this pair satisfy salary matching for either team?

    Uses the real expanded-TPE bands. When a team's apron position is unknown — which it
    is for every team under the available contract data, because a verified payroll needs
    every rostered player priced — the **more permissive** band is applied and the
    response says so. Filtering on the stricter band would silently discard legal deals on
    the strength of a payroll figure nobody has.
    """
    if not (out_pkg.salary_known and in_pkg.salary_known):
        return True, "salaries unknown for at least one side; salary matching not applied"
    limit = (
        max_incoming_below_first_apron
        if apron_unknown
        else max_incoming_at_or_above_first_apron
    )
    out_salary = float(out_pkg.salary or 0)
    in_salary = float(in_pkg.salary or 0)
    if in_salary > limit(out_salary, cap_params):
        return False, None
    if out_salary > limit(in_salary, cap_params):
        return False, None
    return True, None


def _roster_feasible(out_count: int, in_count: int, focal_size: int, other_size: int) -> bool:
    """Neither post-trade roster may exceed the 18-spot ceiling the roster rule fails on."""
    return (
        focal_size - out_count + in_count <= MAX_WITH_TWO_WAYS
        and other_size - in_count + out_count <= MAX_WITH_TWO_WAYS
    )


def _need_gain(cards: list[PlayerCard], target_skills: set[str]) -> float:
    """Minutes-weighted skill percentile a package brings in the skills a team needs."""
    total_minutes = sum(c.minutes or 0.0 for c in cards)
    if total_minutes <= 0 or not target_skills:
        return 0.0
    return sum(
        sum(c.skills.get(skill, 0.0) for skill in target_skills)
        * ((c.minutes or 0.0) / total_minutes)
        for c in cards
    )


def _target_skills(needs: dict[str, float], top_n: int = 3) -> tuple[set[str], list[tuple[str, float]]]:
    top = sorted(needs.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    return {NEED_TO_SKILL[k] for k, _ in top if k in NEED_TO_SKILL}, top


def _packages(
    cards: list[PlayerCard], limit: int, max_size: int = MAX_PLAYERS_PER_SIDE
) -> list[list[PlayerCard]]:
    """Singles, then pairs, then triples — deterministic, and bounded by `limit`.

    The pool is pre-sorted by the caller; combinations of a sorted pool are themselves
    deterministic, so the same database always produces the same package list.
    """
    packages: list[list[PlayerCard]] = [[c] for c in cards]
    if max_size >= 2:
        packages += [list(pair) for pair in combinations(cards[:8], 2)]
    if max_size >= 3:
        packages += [list(triple) for triple in combinations(cards[:6], 3)]
    return packages[:limit]


def generate_candidates(
    db: Session,
    focal_team_id: str,
    strategy: str = "custom",
    weights: dict[str, float] | None = None,
    untouchable_player_ids: list[str] | None = None,
    preferred_outgoing_ids: list[str] | None = None,
    max_candidates: int = TOP_K,
) -> dict:
    service = EvaluationService(db)
    settings = get_settings()
    untouchables = set(untouchable_player_ids or [])
    preferred = set(preferred_outgoing_ids or [])
    weights = weights or DEFAULT_WEIGHTS.get(strategy, DEFAULT_WEIGHTS["custom"])
    cap_params = load_cap_params(db, settings.cap_league_year)
    wins_slope = float(service.wins_mapping().get("slope", 2.7))

    focal_roster = service._roster_cards(focal_team_id)
    focal_needs = service._team_needs(focal_team_id)
    if not focal_needs:
        return {"error": "team needs have not been computed", "candidates": []}

    # The generator can only reason about players the impact model has scored; an
    # unmodelled player has no TEI to balance a package against (R1-4).
    tradeable = [
        c
        for c in focal_roster
        if c.player_id not in untouchables and c.tei is not None and c.minutes is not None
    ]
    # Total order: preferred first, then by minutes, then by id. The id tiebreaker is what
    # makes the whole search reproducible.
    tradeable.sort(key=lambda c: (c.player_id not in preferred, -(c.minutes or 0.0), c.player_id))

    focal_skills, focal_top_needs = _target_skills(focal_needs)
    teams = db.scalars(
        select(Team).where(Team.id != focal_team_id).order_by(Team.abbreviation)
    ).all()
    engine = TradeLegalityEngine()

    outgoing_packages = _packages(tradeable, MAX_OUTGOING_PACKAGES)
    focal_salaries = player_salaries(
        db, [c.player_id for c in tradeable], settings.cap_league_year
    )
    focal_salary_map = {pid: value[0] for pid, value in focal_salaries.items()}

    candidates: list[dict] = []
    per_counterparty: list[dict] = []
    evaluated_total = 0
    filtered = {"salary": 0, "roster": 0, "value_balance": 0, "illegal": 0,
                "focal_utility": 0, "counterparty_utility": 0, "projected_win_loss": 0}
    salary_matching_applied = 0
    salary_matching_skipped = 0
    construction_errors = 0
    error_types: dict[str, int] = {}
    error_samples: list[str] = []

    for other in teams:
        other_roster = service._roster_cards(other.id)
        other_needs = service._team_needs(other.id)
        other_skills, _ = _target_skills(other_needs)

        incoming_pool = sorted(
            (c for c in other_roster if c.skills and c.tei is not None and c.minutes is not None),
            key=lambda c: (-_need_gain([c], focal_skills), c.player_id),
        )
        incoming_packages = _packages(incoming_pool, MAX_INCOMING_PACKAGES)
        other_salaries = player_salaries(
            db, [c.player_id for c in incoming_pool], settings.cap_league_year
        )
        other_salary_map = {pid: value[0] for pid, value in other_salaries.items()}

        # ---- cheap constraints, then rank what survives -------------------------
        ranked: list[tuple[float, int, int, list[PlayerCard], list[PlayerCard]]] = []
        enumerated = 0
        for out_index, outgoing in enumerate(outgoing_packages):
            out_pkg = _package_value(outgoing, focal_salary_map)
            for in_index, incoming in enumerate(incoming_packages):
                enumerated += 1
                if not _roster_feasible(
                    len(outgoing), len(incoming), len(focal_roster), len(other_roster)
                ):
                    filtered["roster"] += 1
                    continue
                in_pkg = _package_value(incoming, other_salary_map)
                if abs(_value_gap_wins(in_pkg.value - out_pkg.value, wins_slope)) > (
                    MAX_VALUE_GAP_WINS
                ):
                    filtered["value_balance"] += 1
                    continue
                ok, note = _salary_match_possible(out_pkg, in_pkg, cap_params)
                if note is None:
                    salary_matching_applied += 1
                else:
                    salary_matching_skipped += 1
                if not ok:
                    filtered["salary"] += 1
                    continue
                # Rank by how well each side addresses the OTHER team's needs. The old
                # generator ranked on the focal team's needs only, which is why the
                # counterparty's side of the deal was an afterthought.
                score = _need_gain(incoming, focal_skills) + _need_gain(outgoing, other_skills)
                ranked.append((-score, out_index, in_index, outgoing, incoming))

        ranked.sort()
        budget = EVALUATIONS_PER_COUNTERPARTY
        evaluated_here = 0
        surviving_here = 0
        for _, _, _, outgoing, incoming in ranked[:budget]:
            moves = [
                {"player_id": c.player_id, "from_team_id": focal_team_id, "to_team_id": other.id}
                for c in outgoing
            ] + [
                {"player_id": c.player_id, "from_team_id": other.id, "to_team_id": focal_team_id}
                for c in incoming
            ]
            team_ids = [focal_team_id, other.id]
            evaluated_here += 1
            evaluated_total += 1
            try:
                context = build_trade_context(db, team_ids, moves)
                legality = engine.evaluate(context)
            except Exception as exc:  # noqa: BLE001 — counted and reported, see below
                # R7. This used to be a bare `continue`, and the failure mode is the whole
                # reason it is not one now: at R5.5 the dev database sat one migration
                # behind, `build_trade_context` raised on the missing column for every
                # pair, and the generator returned **0 candidates on all 30 teams** with
                # 406 pairs silently discarded. That reads as a modelling outcome — "no
                # trade improves this roster" — when it is schema drift.
                #
                # It is still caught, because one unbuildable pair must not abort a
                # league-wide search. What changed is that it is counted, its type is
                # named, and `coverage.construction_errors` puts it in the response, so a
                # generator returning nothing can be told apart from one that broke.
                construction_errors += 1
                error_types[type(exc).__name__] = error_types.get(type(exc).__name__, 0) + 1
                if construction_errors <= _ERROR_SAMPLE:
                    error_samples.append(
                        f"{focal_team_id[:8]}/{other.abbreviation}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                logger.warning(
                    "candidate pair could not be built: %s: %s",
                    type(exc).__name__,
                    exc,
                    extra={"focal_team_id": focal_team_id, "counterparty": other.abbreviation},
                )
                continue
            if legality["overall_status"] == "verified_illegal":
                filtered["illegal"] += 1
                continue
            focal_eval = service.evaluate_for_team(
                focal_team_id, team_ids, moves, [], strategy, weights, legality,
                simulate=False,
            )
            focal_utility = focal_eval["composite_utility"]
            if focal_utility is None or focal_utility <= MIN_UTILITY:
                filtered["focal_utility"] += 1
                continue
            other_eval = service.evaluate_for_team(
                other.id, team_ids, moves, [], "custom", None, legality, simulate=False,
            )
            other_utility = other_eval["composite_utility"]
            if other_utility is None or other_utility <= MIN_UTILITY:
                filtered["counterparty_utility"] += 1
                continue
            # The basketball sanity check the composite does not perform. A `fit` gain can
            # outweigh a `performance` loss under the default weights, so "both above
            # neutral" still admitted deals like Coby White for Cooper Flagg.
            win_deltas = {
                team: (evaluation["detail"].get("performance") or {}).get("delta_wins")
                for team, evaluation in (
                    (focal_team_id, focal_eval), (other.id, other_eval)
                )
            }
            if any(
                delta is not None and delta < -MAX_PROJECTED_WIN_LOSS
                for delta in win_deltas.values()
            ):
                filtered["projected_win_loss"] += 1
                continue
            surviving_here += 1
            candidates.append(
                {
                    "counterparty": {
                        "team_id": other.id,
                        "abbreviation": other.abbreviation,
                        "name": other.full_name,
                    },
                    "outgoing": [_asset(c) for c in outgoing],
                    "incoming": [_asset(c) for c in incoming],
                    "legality_status": legality["overall_status"],
                    "focal_utility": focal_utility,
                    "counterparty_utility": other_utility,
                    "focal_components": focal_eval["components"],
                    "counterparty_components": other_eval["components"],
                    "package_value": {
                        "outgoing": round(_package_value(outgoing, focal_salary_map).value, 4),
                        "incoming": round(_package_value(incoming, other_salary_map).value, 4),
                        "gap_projected_wins": round(
                            _value_gap_wins(
                                _package_value(incoming, other_salary_map).value
                                - _package_value(outgoing, focal_salary_map).value,
                                wins_slope,
                            ),
                            2,
                        ),
                    },
                    "projected_delta_wins": {
                        "focal": win_deltas[focal_team_id],
                        "counterparty": win_deltas[other.id],
                    },
                    "rationale": _rationale(focal_top_needs, incoming, other.abbreviation, outgoing),
                }
            )
        per_counterparty.append(
            {
                "abbreviation": other.abbreviation,
                "pairs_enumerated": enumerated,
                "pairs_after_constraints": len(ranked),
                "pairs_evaluated": evaluated_here,
                "candidates_surviving": surviving_here,
                "truncated": len(ranked) > budget,
            }
        )

    # Total order on the output too: utility, then counterparty, then the players moved.
    candidates.sort(
        key=lambda c: (
            -(c["focal_utility"] or 0.0),
            c["counterparty"]["abbreviation"],
            tuple(p["player_id"] for p in c["outgoing"]),
            tuple(p["player_id"] for p in c["incoming"]),
        )
    )
    truncated = [row["abbreviation"] for row in per_counterparty if row["truncated"]]
    return {
        "focal_team_id": focal_team_id,
        "strategy": strategy,
        "target_needs": [k for k, _ in focal_top_needs],
        "evaluations_run": evaluated_total,
        "coverage": {
            "counterparties_searched": len(teams),
            "counterparties_total": len(teams),
            "share_searched": 1.0 if teams else None,
            "searched": [t.abbreviation for t in teams],
            "not_searched": [],
            # The whole league is always searched now; truncation happens *inside* a
            # counterparty, and this says exactly where.
            "truncated_by_budget": bool(truncated),
            "counterparties_truncated": truncated,
            "evaluations_per_counterparty": EVALUATIONS_PER_COUNTERPARTY,
            "per_counterparty": per_counterparty,
            "pairs_rejected_by_constraint": filtered,
            #: Pairs the legality context could not be built for at all. Distinct from
            #: every entry in `pairs_rejected_by_constraint`, which are decisions; this is
            #: the search failing to run. Non-zero here means an empty candidate list is
            #: not a modelling result — check `alembic current` before the ranking.
            "construction_errors": {
                "pairs": construction_errors,
                "share_of_evaluated": (
                    round(construction_errors / evaluated_total, 4) if evaluated_total else None
                ),
                "types": dict(sorted(error_types.items())),
                "sample": error_samples,
            },
            "salary_matching": {
                "pairs_checked": salary_matching_applied,
                "pairs_skipped_unknown_salaries": salary_matching_skipped,
                "note": (
                    "Expanded-TPE bands from the CBA rules module. Applied only where both "
                    "packages are fully priced; the permissive below-first-apron band is "
                    "used because a verified apron position needs every rostered player "
                    "priced, which no team has under the available contract data."
                ),
            },
            "both_sides_above_neutral": MIN_UTILITY,
            "max_value_gap_wins": MAX_VALUE_GAP_WINS,
            "max_projected_win_loss": MAX_PROJECTED_WIN_LOSS,
        },
        "note": (
            "EXPERIMENTAL. Candidates are model-generated explorations under documented "
            "constraints; a utility score is not evidence that a real front office would "
            f"accept. Both sides must score above {MIN_UTILITY:.0f} — the composite's own "
            "definition of 'changes nothing' — and neither side may project worse than "
            f"−{MAX_PROJECTED_WIN_LOSS:.0f} wins on the floor, because a fit gain "
            "outweighing an on-court loss is something the composite permits and a front "
            f"office does not. All {len(teams)} counterparties were searched."
        ),
        "candidates": candidates[:max_candidates],
    }


def _asset(card: PlayerCard) -> dict:
    return {
        "player_id": card.player_id,
        "name": card.name,
        "tei": round(card.tei, 2) if card.tei is not None else None,
        "minutes": round(card.minutes, 1) if card.minutes is not None else None,
    }


def _rationale(
    top_needs: list[tuple[str, float]],
    incoming: list[PlayerCard],
    counterparty: str,
    outgoing: list[PlayerCard],
) -> str:
    needs_text = ", ".join(k.replace("_", " ") for k, _ in top_needs)
    arriving = ", ".join(c.name for c in incoming)
    leaving = ", ".join(c.name for c in outgoing)
    return (
        f"Targets top roster needs ({needs_text}) with {arriving}. "
        f"{counterparty} takes {leaving} and also scores above neutral, which is the "
        "condition for surfacing this at all."
    )
