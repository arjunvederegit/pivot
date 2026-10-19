"""R9.2 preview: versioned player intelligence from the shared ``PlayerIntelligenceService``.

    GET /api/v1/players/{player_id}/intelligence   records for one player (measured by default)
    GET /api/v1/player-intelligence/catalog        what the engine offers, and what it declines to

**A preview, off by default** (``PLAYER_INTELLIGENCE_PREVIEW=true``). With the flag off both routes are
404 and absent from the OpenAPI schema, so nothing existing changes. No current consumer (TEI, Cap
Center, Trade Evaluator, Ask Pivot) reads these routes; R9.3 will connect them.

Read-only. A request never trains, writes or publishes anything. Inferred and projected values come from
stored candidate models and are returned only on request (``kinds=inferred,projected``), and a model
that is still a candidate only with ``include_experimental=true`` — labelled as experimental on every record.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import NotFoundError
from app.db.base import get_db
from app.player_intelligence.service import PlayerIntelligenceService


def _require_preview() -> None:
    if not get_settings().player_intelligence_preview:
        raise NotFoundError("the player intelligence preview is disabled (set PLAYER_INTELLIGENCE_PREVIEW=true)")


router = APIRouter(tags=["player-intelligence (preview)"], dependencies=[Depends(_require_preview)])


def _split(value: str | None) -> list[str] | None:
    items = [part.strip() for part in (value or "").split(",") if part.strip()]
    return items or None


@router.get("/players/{player_id}/intelligence")
def get_player_intelligence(
    player_id: str,
    quantities: str | None = Query(None, description="comma-separated quantity ids (default: every measured quantity)"),
    as_of: str | None = Query(None, description="information cutoff, a season such as 2024-25 (default: the latest ingested season)"),
    kinds: str = Query("measured", description="comma-separated: measured, inferred, projected"),
    model_version: str | None = Query(None, description="an exact stored model version (default: the recommended method for the cutoff)"),
    include_experimental: bool = Query(False, description="also return outputs of candidate models, labelled experimental"),
    db: Session = Depends(get_db),
) -> dict:
    return PlayerIntelligenceService(db).get(
        player_id, _split(quantities), as_of, _split(kinds), model_version, include_experimental
    ).as_dict()


@router.get("/player-intelligence/catalog")
def get_catalog(db: Session = Depends(get_db)) -> dict:
    return PlayerIntelligenceService(db).catalog()
