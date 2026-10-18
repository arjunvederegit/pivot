"""Source transparency endpoints: dataset freshness/completeness and the provider registry."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.canonical.calendar import season_context, today_in_league
from app.config import get_settings
from app.db.base import get_db
from app.services.data_provenance import dataset_freshness, provider_registry, rollover_readiness

router = APIRouter(prefix="/data", tags=["data"])


@router.get("/freshness")
def get_freshness(db: Session = Depends(get_db)) -> dict:
    """Per dataset: freshness state, why, completeness, providers and the latest refresh attempt.

    Judged against the NBA calendar (R8.1): a completed season reads `finalized`, a season that
    has not started reads `not_yet_available`, and only data that *should* be moving can be
    `stale`. The *oldest* row decides, and a dataset with no rows is `missing`, not an empty
    success. `season` says which season is being read and whether it is a baseline.
    """
    settings = get_settings()
    ctx = season_context(today_in_league(), grace_days=settings.season_rollover_grace_days)
    datasets = dataset_freshness(db)
    body: dict = {
        "season": ctx.describe(),
        "datasets": {name: meta.model_dump(mode="json") for name, meta in datasets.items()},
        "attention": sorted(name for name, meta in datasets.items() if meta.needs_attention),
    }
    if ctx.analysis_is_baseline:
        # The league has moved on; say what exists for the season it is actually in.
        upcoming = dataset_freshness(db, ctx.league_season)
        body["league_season_datasets"] = {
            name: meta.model_dump(mode="json")
            for name, meta in upcoming.items()
            if name in ("rosters", "standings", "player_stats", "team_stats", "games")
        }
    return body


@router.get("/rollover")
def get_rollover_readiness(db: Session = Depends(get_db)) -> dict:
    """Whether the data needed to move the tools to the league's season is in place."""
    return rollover_readiness(db)


@router.get("/providers")
def get_providers(db: Session = Depends(get_db)) -> dict:
    """Which providers Pivot reads from and whether each is configured. Never a credential."""
    return {"providers": provider_registry(db)}
