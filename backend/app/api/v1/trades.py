from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    ComparablesRequest,
    EvaluateRequest,
    GenerateRequest,
    MemoRequest,
    ReportFormat,
    TradeIn,
    ValidateRequest,
)
from app.canonical.tool_seasons import tool_seasons
from app.cba.builder import build_trade_context
from app.cba.engine import TradeLegalityEngine
from app.core.errors import DomainError, NotFoundError
from app.core.provenance import describe_providers
from app.db.base import get_db
from app.db.models import (
    DataSyncRun,
    GeneratedReport,
    RosterEntry,
    Scenario,
    Team,
    TradeAsset,
    TradeEvaluation,
    TradeProposal,
    TradeRuleResult,
    TradeTeam,
)
from app.services.candidates import generate_candidates
from app.services.comparables import ComparableTradeService
from app.services.evaluation import EvaluationService
from app.services.reports import build_report_markdown, memo_payload, report_to_html
from app.services.trade_financials import trade_financial_analysis

router = APIRouter(prefix="/trades", tags=["trades"])

#: Comparable trades shown in a memo. Three, not twenty-five: the memo is a document a
#: person reads, and the fourth-closest precedent has never changed anyone's mind.
MEMO_COMPARABLES = 3


def _moves_from_payload(payload: ValidateRequest) -> tuple[list[dict], list[dict]]:
    player_moves = [m.model_dump() for m in payload.player_moves]
    pick_moves = [m.model_dump() for m in payload.pick_moves]
    return player_moves, pick_moves


def _scenario_weights(db: Session, scenario_id: str | None) -> tuple[str, dict | None, str | None]:
    if not scenario_id:
        return "custom", None, None
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise NotFoundError(f"scenario {scenario_id} not found")
    return (
        scenario.strategy,
        {w.component: w.weight for w in scenario.weights},
        scenario.focal_team_id,
    )


def _financial_analysis(db: Session, context, legality: dict) -> dict:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import OperationalError, ProgrammingError

    try:
        seasons = tool_seasons(db)
    except (OperationalError, ProgrammingError):  # an older schema: the season basis is unavailable, the rest is not
        db.rollback()
        seasons = {}
    return trade_financial_analysis(
        context,
        legality,
        roster_basis=seasons.get("roster"),
        stats_label=(seasons.get("performance") or {}).get("label"),
    )


@router.post("/validate")
def validate_trade(payload: ValidateRequest, db: Session = Depends(get_db)) -> dict:
    """Backend-authoritative legality check (the frontend never decides legality)."""
    player_moves, pick_moves = _moves_from_payload(payload)
    context = build_trade_context(db, payload.team_ids, player_moves, pick_moves)
    legality = TradeLegalityEngine().evaluate(context)
    # R9.3A: additive. The engine result is returned exactly as before, plus the financial picture
    # and the inspectable checked / failed / unverified account the live rules panel renders.
    return {**legality, "financial_analysis": _financial_analysis(db, context, legality)}


@router.post("/evaluate")
def evaluate_trade(payload: EvaluateRequest, db: Session = Depends(get_db)) -> dict:
    player_moves, pick_moves = _moves_from_payload(payload)
    strategy, weights, _ = _scenario_weights(db, payload.scenario_id)
    if payload.weights is not None:
        weights = payload.weights.model_dump()
    if payload.strategy != "custom":
        strategy = payload.strategy

    context = build_trade_context(db, payload.team_ids, player_moves, pick_moves)
    legality = TradeLegalityEngine().evaluate(context)
    service = EvaluationService(db)
    evaluations = {
        team_id: service.evaluate_for_team(
            team_id, payload.team_ids, player_moves, pick_moves, strategy, weights, legality
        )
        for team_id in payload.team_ids
    }
    return {
        "legality": legality,
        "evaluations": evaluations,
        # R8.2: which season each input came from (roster / statistics / needs / contracts).
        "season_basis": service.season_basis(),
        # R9.3A: verified contract facts, salary comparison, and the inspectable legality assessment.
        "financial_analysis": _financial_analysis(db, context, legality),
    }


@router.post(
    "/generate",
    summary="[experimental] Generate candidate trades",
    description=(
        "**Experimental — no UI entry point (R1-8).** Rebuilt in R5-3. The search covers "
        "**every** counterparty: the evaluation budget is divided across the league "
        "rather than consumed by the first few teams alphabetically, which is how it "
        "previously reached 13.8 % of counterparties. Salary matching uses the CBA's "
        "expanded-TPE bands where both packages are priced. A candidate is surfaced only "
        "if **both** teams score above 50 — the composite's own definition of 'changes "
        "nothing' — and neither projects worse than −2 wins, and the packages differ by "
        "no more than 2 wins of modelled value. Responses carry a `coverage` block "
        "stating what was enumerated, evaluated and rejected, per counterparty."
    ),
)
def generate_trades(payload: GenerateRequest, db: Session = Depends(get_db)) -> dict:
    strategy, weights, scenario_focal = _scenario_weights(db, payload.scenario_id)
    focal_team_id = payload.focal_team_id or scenario_focal
    if not focal_team_id:
        raise DomainError("focal_team_id or scenario_id is required")
    untouchables: list[str] = []
    preferred: list[str] = []
    if payload.scenario_id:
        scenario = db.get(Scenario, payload.scenario_id)
        if scenario:
            untouchables = scenario.untouchable_player_ids or []
            preferred = scenario.preferred_outgoing_player_ids or []
    if payload.strategy != "custom":
        strategy = payload.strategy
    return generate_candidates(
        db,
        focal_team_id,
        strategy=strategy,
        weights=weights,
        untouchable_player_ids=untouchables,
        preferred_outgoing_ids=preferred,
        max_candidates=payload.max_candidates,
    )


@router.post(
    "/comparables",
    summary="Completed trades most like this one, from one team's side",
    description=(
        "Retrieval over ten seasons of Basketball-Reference transaction pages, normalized "
        "locally. The unit is a **side**: what one team sent, received and was at the "
        "time, because 'we trade a star for picks' and 'we trade picks for a star' are "
        "the same transaction and different decisions.\n\n"
        "Similarity is an interpretable grouped distance over six dimensions, each "
        "returned with its own similarity and the feature values that produced it. "
        "Salary is not among them — no source here carries a historical contract — and "
        "**nothing reads what happened next**. A comparable is evidence about precedent, "
        "not about consequence."
    ),
)
def comparable_trades(payload: ComparablesRequest, db: Session = Depends(get_db)) -> dict:
    if payload.focal_team_id not in payload.team_ids:
        raise DomainError("focal_team_id must be one of team_ids")
    player_moves, pick_moves = _moves_from_payload(payload)
    return ComparableTradeService(db).find(
        payload.focal_team_id, payload.team_ids, player_moves, pick_moves, k=payload.k
    )


@router.get("/comparables/coverage")
def comparable_coverage(db: Session = Depends(get_db)) -> dict:
    """What the historical corpus contains, and where it stops."""
    return ComparableTradeService(db).coverage()


@router.post("", status_code=201)
def create_trade(payload: TradeIn, db: Session = Depends(get_db)) -> dict:
    trade = TradeProposal(name=payload.name, scenario_id=payload.scenario_id, notes=payload.notes)
    db.add(trade)
    db.flush()
    for team_id in payload.team_ids:
        if db.get(Team, team_id) is None:
            raise NotFoundError(f"team {team_id} not found")
        db.add(TradeTeam(trade_id=trade.id, team_id=team_id))
    for move in payload.player_moves:
        db.add(
            TradeAsset(
                trade_id=trade.id,
                asset_type="player",
                from_team_id=move.from_team_id,
                to_team_id=move.to_team_id,
                player_id=move.player_id,
            )
        )
    for pick in payload.pick_moves:
        db.add(
            TradeAsset(
                trade_id=trade.id,
                asset_type="pick",
                from_team_id=pick.from_team_id,
                to_team_id=pick.to_team_id,
                draft_year=pick.draft_year,
                round_number=pick.round_number,
                protections=pick.protections,
                is_hypothetical=True,
            )
        )
    db.commit()
    return _trade_detail(db, trade.id, persist_results=True)


def _trade_moves(trade: TradeProposal) -> tuple[list[str], list[dict], list[dict]]:
    team_ids = [t.team_id for t in trade.teams]
    player_moves = [
        {"player_id": a.player_id, "from_team_id": a.from_team_id, "to_team_id": a.to_team_id}
        for a in trade.assets
        if a.asset_type == "player" and a.player_id
    ]
    pick_moves = [
        {
            "from_team_id": a.from_team_id,
            "to_team_id": a.to_team_id,
            "draft_year": a.draft_year,
            "round_number": a.round_number,
            "protections": a.protections,
            "is_hypothetical": a.is_hypothetical,
        }
        for a in trade.assets
        if a.asset_type == "pick"
    ]
    return team_ids, player_moves, pick_moves


def _trade_detail(db: Session, trade_id: str, persist_results: bool = False) -> dict:
    trade = db.get(TradeProposal, trade_id)
    if trade is None:
        raise NotFoundError(f"trade {trade_id} not found")
    team_ids, player_moves, pick_moves = _trade_moves(trade)
    strategy, weights, _ = _scenario_weights(db, trade.scenario_id)

    context = build_trade_context(db, team_ids, player_moves, pick_moves)
    legality = TradeLegalityEngine().evaluate(context)
    service = EvaluationService(db)
    evaluations = {
        team_id: service.evaluate_for_team(
            team_id, team_ids, player_moves, pick_moves, strategy, weights, legality
        )
        for team_id in team_ids
    }

    if persist_results:
        for rule in legality["rule_results"]:
            db.add(
                TradeRuleResult(
                    trade_id=trade.id,
                    rule_code=rule["rule_code"],
                    team_id=rule["team_id"],
                    status=rule["status"],
                    message=rule["message"],
                    calculation=rule.get("calculation", {}),
                    source_reference=rule.get("source_reference"),
                    confidence=rule.get("confidence", "high"),
                )
            )
        for team_id, evaluation in evaluations.items():
            db.add(
                TradeEvaluation(
                    trade_id=trade.id,
                    scenario_id=trade.scenario_id,
                    team_id=team_id,
                    legality_status=evaluation["legality"].get(
                        "status", legality["overall_status"]
                    ),
                    composite_utility=evaluation["composite_utility"],
                    components=evaluation["components"],
                    uncertainty=evaluation["uncertainty"],
                    sensitivity={"tornado": evaluation["sensitivity_tornado"]},
                    explanation={"drivers": evaluation["detail"]},
                )
            )
        db.commit()

    assets = [
        {
            "asset_type": a.asset_type,
            "from_team_id": a.from_team_id,
            "to_team_id": a.to_team_id,
            "player_id": a.player_id,
            "player_name": a.player.full_name if a.player else None,
            "draft_year": a.draft_year,
            "round_number": a.round_number,
            "protections": a.protections,
            "is_hypothetical": a.is_hypothetical,
        }
        for a in trade.assets
    ]
    return {
        "id": trade.id,
        "name": trade.name,
        "scenario_id": trade.scenario_id,
        "notes": trade.notes,
        "created_at": trade.created_at.isoformat(),
        "teams": [
            {"team_id": t.team_id, "abbreviation": t.team.abbreviation, "name": t.team.full_name}
            for t in trade.teams
        ],
        "assets": assets,
        "legality": legality,
        "evaluations": evaluations,
        "financial_analysis": _financial_analysis(db, context, legality),
    }


@router.get("")
def list_trades(scenario_id: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(TradeProposal).order_by(TradeProposal.created_at.desc())
    if scenario_id:
        stmt = stmt.where(TradeProposal.scenario_id == scenario_id)
    trades = db.scalars(stmt.limit(100)).all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "scenario_id": t.scenario_id,
            "created_at": t.created_at.isoformat(),
            "teams": [tt.team.abbreviation for tt in t.teams],
            "n_players": len([a for a in t.assets if a.asset_type == "player"]),
            "n_picks": len([a for a in t.assets if a.asset_type == "pick"]),
        }
        for t in trades
    ]


@router.get("/{trade_id}")
def get_trade(trade_id: str, db: Session = Depends(get_db)) -> dict:
    return _trade_detail(db, trade_id)


@router.get("/{trade_id}/comparables")
def saved_trade_comparables(
    trade_id: str, focal_team_id: str | None = None, k: int = 5, db: Session = Depends(get_db)
) -> dict:
    trade = db.get(TradeProposal, trade_id)
    if trade is None:
        raise NotFoundError(f"trade {trade_id} not found")
    team_ids, player_moves, pick_moves = _trade_moves(trade)
    _, _, scenario_focal = _scenario_weights(db, trade.scenario_id)
    focal = focal_team_id or scenario_focal or (team_ids[0] if team_ids else None)
    if focal is None or focal not in team_ids:
        raise DomainError("focal_team_id must be one of the trade's teams")
    return ComparableTradeService(db).find(focal, team_ids, player_moves, pick_moves, k=k)


def _memo_markdown(
    db: Session,
    *,
    trade_name: str,
    team_ids: list[str],
    player_moves: list[dict],
    pick_moves: list[dict],
    focal_team_id: str,
    strategy: str,
    legality: dict,
    evaluations: dict,
    include_comparables: bool,
) -> str:
    """One builder for both memo routes, so a saved trade and an unsaved one cannot
    diverge into two documents that disagree."""
    teams = {t.id: t for t in db.scalars(select(Team).where(Team.id.in_(team_ids))).all()}
    focal_team = teams.get(focal_team_id)
    comparables = None
    if include_comparables:
        comparables = ComparableTradeService(db).find(
            focal_team_id, team_ids, player_moves, pick_moves, k=MEMO_COMPARABLES
        )
    last_sync = db.scalar(
        select(DataSyncRun.finished_at)
        .where(DataSyncRun.status == "succeeded")
        .order_by(DataSyncRun.finished_at.desc())
    )
    return build_report_markdown(
        trade_name=trade_name,
        focal_team_name=focal_team.full_name if focal_team else "the focal team",
        strategy=strategy,
        legality=legality,
        evaluations=evaluations,
        focal_team_id=focal_team_id,
        data_freshness={
            "last_sync": last_sync.isoformat() if last_sync else "never",
            # Read off the rows the memo was built from. The memo's provenance line used
            # to assert NBA.com unconditionally — in the section titled "provenance", in
            # the one artifact a user exports and hands to someone else.
            "source": describe_providers(
                {
                    p
                    for (p,) in db.execute(select(RosterEntry.source_provider).distinct())
                    if p
                }
            ),
        },
        comparables=comparables,
    )


@router.post(
    "/memo",
    summary="Decision memo for a trade that has not been saved",
    description=(
        "The same document `/trades/{id}/report` produces, for a deal the user is still "
        "building. Recommendation, what changes on the floor including the rotation "
        "consequences, roster fit, cost in salary and draft capital, the CBA rules that "
        "fired, comparable completed trades, risks, and one consolidated section naming "
        "everything the memo could not establish."
    ),
)
def trade_memo(payload: MemoRequest, db: Session = Depends(get_db)):
    if payload.focal_team_id not in payload.team_ids:
        raise DomainError("focal_team_id must be one of team_ids")
    player_moves, pick_moves = _moves_from_payload(payload)
    strategy, weights, _ = _scenario_weights(db, payload.scenario_id)
    if payload.strategy != "custom":
        strategy = payload.strategy
    context = build_trade_context(db, payload.team_ids, player_moves, pick_moves)
    legality = TradeLegalityEngine().evaluate(context)
    service = EvaluationService(db)
    evaluations = {
        team_id: service.evaluate_for_team(
            team_id, payload.team_ids, player_moves, pick_moves, strategy, weights, legality
        )
        for team_id in payload.team_ids
    }
    markdown_text = _memo_markdown(
        db,
        trade_name=payload.name,
        team_ids=payload.team_ids,
        player_moves=player_moves,
        pick_moves=pick_moves,
        focal_team_id=payload.focal_team_id,
        strategy=strategy,
        legality=legality,
        evaluations=evaluations,
        include_comparables=payload.include_comparables,
    )
    if payload.format == "html":
        return HTMLResponse(report_to_html(markdown_text))
    if payload.format == "markdown":
        return PlainTextResponse(markdown_text, media_type="text/markdown")
    return memo_payload(markdown_text)


@router.get("/{trade_id}/report")
def get_trade_report(
    trade_id: str,
    format: ReportFormat = "markdown",
    include_comparables: bool = True,
    db: Session = Depends(get_db),
):
    """`format` is a closed set. It used to be a bare `str`, and the `GeneratedReport`
    row was written *before* the branch, so `?format=pdf` returned markdown while
    persisting a record claiming PDF."""
    detail = _trade_detail(db, trade_id)
    trade = db.get(TradeProposal, trade_id)
    assert trade is not None
    team_ids, player_moves, pick_moves = _trade_moves(trade)
    strategy, _, scenario_focal = _scenario_weights(db, trade.scenario_id)
    focal_team_id = scenario_focal or detail["teams"][0]["team_id"]

    markdown_text = _memo_markdown(
        db,
        trade_name=detail["name"],
        team_ids=team_ids,
        player_moves=player_moves,
        pick_moves=pick_moves,
        focal_team_id=focal_team_id,
        strategy=strategy,
        legality=detail["legality"],
        evaluations=detail["evaluations"],
        include_comparables=include_comparables,
    )
    response = (
        HTMLResponse(report_to_html(markdown_text))
        if format == "html"
        else PlainTextResponse(markdown_text, media_type="text/markdown")
    )
    # Recorded only once the response it describes actually exists.
    db.add(
        GeneratedReport(
            trade_id=trade.id,
            scenario_id=trade.scenario_id,
            format=format,
            content=markdown_text,
            llm_enhanced=False,
        )
    )
    db.commit()
    return response
