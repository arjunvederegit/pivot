from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.analytics.sensitivity import normalize_weights, rank_stability
from app.api.schemas import ComparisonIn
from app.core.errors import NotFoundError
from app.db.base import get_db
from app.db.models import ComparisonSet, Scenario, TradeProposal
from app.services.evaluation import COMPONENT_KEYS, DEFAULT_WEIGHTS

router = APIRouter(prefix="/comparisons", tags=["comparisons"])


#: Domination is judged on **every** component, not a hardcoded subset.
#:
#: The list used to be `["performance", "fit", "timeline", "assets", "risk"]` — omitting
#: `contract` — while the docstring above it said "(performance, risk)". Neither described
#: the other, and a deal that was better on price alone could be marked dominated.
PARETO_AXES = COMPONENT_KEYS

#: A comparison needs enough shared axes to mean anything. Two was the old floor, and with
#: `assets` now withheld on every player-only trade and `contract` unavailable without a
#: provider, two axes is a coin flip dressed as a Pareto frontier.
MIN_SHARED_AXES = 3


def _pareto_flags(rows: list[dict]) -> None:
    """Mark alternatives another alternative dominates on every axis both of them have.

    An alternative is dominated when another is >= on all shared axes and > on at least
    one. The number of axes the judgement rested on is published beside it, because
    "dominated on three axes of six" and "dominated on all six" are different claims and
    the response used to make them look identical.
    """
    for row in rows:
        row["dominated_by"] = None
        row["domination"] = None
    for row in rows:
        for other in rows:
            if other is row:
                continue
            pairs = [
                (other["components"][axis], row["components"][axis])
                for axis in PARETO_AXES
                if other["components"].get(axis) is not None
                and row["components"].get(axis) is not None
            ]
            if len(pairs) < MIN_SHARED_AXES:
                continue
            if all(o >= r for o, r in pairs) and any(o > r for o, r in pairs):
                row["dominated_by"] = other["trade_id"]
                row["domination"] = {
                    "axes_compared": len(pairs),
                    "axes_total": len(PARETO_AXES),
                    "axes": [
                        axis
                        for axis in PARETO_AXES
                        if other["components"].get(axis) is not None
                        and row["components"].get(axis) is not None
                    ],
                }
                break


@router.post("", status_code=201)
def create_comparison(payload: ComparisonIn, db: Session = Depends(get_db)) -> dict:
    for trade_id in payload.trade_ids:
        if db.get(TradeProposal, trade_id) is None:
            raise NotFoundError(f"trade {trade_id} not found")
    comparison = ComparisonSet(
        name=payload.name, scenario_id=payload.scenario_id, trade_ids=payload.trade_ids
    )
    db.add(comparison)
    db.commit()
    return get_comparison(comparison.id, db)


@router.get("/{comparison_id}")
def get_comparison(comparison_id: str, db: Session = Depends(get_db)) -> dict:
    from app.api.v1.trades import _trade_detail

    comparison = db.get(ComparisonSet, comparison_id)
    if comparison is None:
        raise NotFoundError(f"comparison {comparison_id} not found")

    scenario = db.get(Scenario, comparison.scenario_id) if comparison.scenario_id else None
    focal_team_id = scenario.focal_team_id if scenario else None
    strategy = scenario.strategy if scenario else "custom"
    weights = (
        {w.component: w.weight for w in scenario.weights}
        if scenario and scenario.weights
        else DEFAULT_WEIGHTS.get(strategy, DEFAULT_WEIGHTS["custom"])
    )
    weights = normalize_weights(weights)

    rows = []
    for trade_id in comparison.trade_ids:
        detail = _trade_detail(db, trade_id)
        team_id = focal_team_id or detail["teams"][0]["team_id"]
        evaluation = detail["evaluations"].get(team_id)
        if evaluation is None:
            continue
        team_legality = evaluation.get("legality", {})
        decision_status = evaluation.get("decision_status", "scored")
        rows.append(
            {
                "trade_id": trade_id,
                "name": detail["name"],
                "legality_status": detail["legality"]["overall_status"],
                "decision_status": decision_status,
                "suppression": evaluation.get("suppression"),
                # Carried so the comparison board renders the same confidence the
                # Trade Evaluator does, instead of synthesizing its own (C13).
                "confidence": evaluation.get("confidence"),
                "has_unmodeled_players": evaluation.get("has_unmodeled_players", False),
                "unmodeled_players": evaluation.get("unmodeled_players", []),
                # R8.2: players in the deal who are on no current roster; scored, but unverified.
                "membership_warnings": evaluation.get("membership_warnings", []),
                "composite_utility": evaluation["composite_utility"],
                "components": evaluation["components"],
                # A suppressed evaluation carries no detail at all — the deal cannot be
                # executed, so there is no projected win change to report.
                "delta_wins": (evaluation.get("detail") or {})
                .get("performance", {})
                .get("delta_wins"),
                "uncertainty": evaluation["uncertainty"],
                "payroll_after": team_legality.get("payroll_after"),
                "apron_status_after": team_legality.get("apron_status_after"),
                "incoming": evaluation["incoming"],
                "outgoing": evaluation["outgoing"],
            }
        )

    # Only deals that can actually be executed compete. Ranking an illegal deal against
    # legal ones invites picking the one that cannot happen.
    rankable = [r for r in rows if r["decision_status"] == "scored"]
    _pareto_flags(rankable)
    for row in rows:
        row.setdefault("dominated_by", None)
        row.setdefault("domination", None)
    stability = rank_stability({r["trade_id"]: r["components"] for r in rankable}, weights)
    rows.sort(
        key=lambda r: (r["decision_status"] == "scored", r["composite_utility"] or 0),
        reverse=True,
    )
    excluded = [r["trade_id"] for r in rows if r["decision_status"] != "scored"]
    return {
        "id": comparison.id,
        "name": comparison.name,
        "scenario_id": comparison.scenario_id,
        "focal_team_id": focal_team_id,
        "weights": weights,
        "alternatives": rows,
        "sensitivity": stability,
        "excluded_from_ranking": excluded,
        "note": (
            "Alternatives marked `dominated_by` are Pareto-dominated across **every** "
            "component both deals could be scored on, and `domination.axes_compared` says "
            f"how many that was (of {len(COMPONENT_KEYS)}). Fewer than "
            f"{MIN_SHARED_AXES} shared axes is not enough to call domination, so no flag "
            "is set. Deals that fail a verified rule, or that no component could score, "
            "are listed but never ranked."
        ),
    }
