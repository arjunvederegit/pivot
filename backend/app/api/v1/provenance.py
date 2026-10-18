"""Source transparency endpoints: dataset freshness/completeness and the provider registry."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.services.data_provenance import dataset_freshness, provider_registry

router = APIRouter(prefix="/data", tags=["data"])


@router.get("/freshness")
def get_freshness(db: Session = Depends(get_db)) -> dict:
    """Per dataset: oldest/newest retrieval, freshness state, completeness, providers.

    The *oldest* row decides freshness (a table that is mostly current but partly stale is
    stale), and a dataset with no rows is `missing`, not an empty success.
    """
    datasets = dataset_freshness(db)
    return {
        "datasets": {name: meta.model_dump(mode="json") for name, meta in datasets.items()},
        "attention": sorted(
            name for name, meta in datasets.items() if meta.freshness.value != "fresh"
        ),
    }


@router.get("/providers")
def get_providers(db: Session = Depends(get_db)) -> dict:
    """Which providers Pivot reads from and whether each is configured. Never a credential."""
    return {"providers": provider_registry(db)}
