from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import ScenarioIn, ScenarioPatch
from app.core.errors import NotFoundError
from app.db.base import get_db
from app.db.models import Scenario, ScenarioWeight, Team

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


def _scenario_out(s: Scenario) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "focal_team_id": s.focal_team_id,
        "focal_team": {
            "abbreviation": s.focal_team.abbreviation,
            "full_name": s.focal_team.full_name,
        },
        "strategy": s.strategy,
        "horizon_years": s.horizon_years,
        "risk_tolerance": s.risk_tolerance,
        "max_added_payroll": s.max_added_payroll,
        "willing_to_cross_tax": s.willing_to_cross_tax,
        "willing_to_cross_first_apron": s.willing_to_cross_first_apron,
        "willing_to_cross_second_apron": s.willing_to_cross_second_apron,
        "untouchable_player_ids": s.untouchable_player_ids,
        "preferred_outgoing_player_ids": s.preferred_outgoing_player_ids,
        "positional_needs": s.positional_needs,
        "weights": {w.component: w.weight for w in s.weights},
        "created_at": s.created_at.isoformat(),
    }


@router.post("", status_code=201)
def create_scenario(payload: ScenarioIn, db: Session = Depends(get_db)) -> dict:
    if db.get(Team, payload.focal_team_id) is None:
        raise NotFoundError(f"team {payload.focal_team_id} not found")
    scenario = Scenario(
        name=payload.name,
        focal_team_id=payload.focal_team_id,
        strategy=payload.strategy,
        horizon_years=payload.horizon_years,
        risk_tolerance=payload.risk_tolerance,
        max_added_payroll=payload.max_added_payroll,
        willing_to_cross_tax=payload.willing_to_cross_tax,
        willing_to_cross_first_apron=payload.willing_to_cross_first_apron,
        willing_to_cross_second_apron=payload.willing_to_cross_second_apron,
        untouchable_player_ids=payload.untouchable_player_ids,
        preferred_outgoing_player_ids=payload.preferred_outgoing_player_ids,
        positional_needs=payload.positional_needs,
    )
    db.add(scenario)
    db.flush()
    weights = payload.weights.model_dump()
    total = sum(weights.values()) or 1.0
    for component, weight in weights.items():
        db.add(ScenarioWeight(scenario_id=scenario.id, component=component, weight=weight / total))
    db.commit()
    db.refresh(scenario)
    return _scenario_out(scenario)


@router.get("")
def list_scenarios(db: Session = Depends(get_db)) -> list[dict]:
    from sqlalchemy import select

    return [
        _scenario_out(s)
        for s in db.scalars(select(Scenario).order_by(Scenario.created_at.desc())).all()
    ]


@router.get("/{scenario_id}")
def get_scenario(scenario_id: str, db: Session = Depends(get_db)) -> dict:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise NotFoundError(f"scenario {scenario_id} not found")
    return _scenario_out(scenario)


@router.patch("/{scenario_id}")
def patch_scenario(scenario_id: str, payload: ScenarioPatch, db: Session = Depends(get_db)) -> dict:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise NotFoundError(f"scenario {scenario_id} not found")
    data = payload.model_dump(exclude_unset=True, exclude={"weights"})
    for key, value in data.items():
        setattr(scenario, key, value)
    if payload.weights is not None:
        for weight_row in list(scenario.weights):
            db.delete(weight_row)
        db.flush()
        weights = payload.weights.model_dump()
        total = sum(weights.values()) or 1.0
        for component, weight in weights.items():
            db.add(
                ScenarioWeight(scenario_id=scenario.id, component=component, weight=weight / total)
            )
    db.commit()
    db.refresh(scenario)
    return _scenario_out(scenario)
