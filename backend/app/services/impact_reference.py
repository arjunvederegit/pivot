"""The league reference distribution for Pivot Estimated Impact (TEI).

A served TEI is an index — a weighted z-score composite multiplied by 2.5 — so "+1.8" is
only meaningful against the other players it was scored alongside. This module returns
that population, exactly as every other endpoint serves it, so a client can place one
player inside it as a percentile and a rank without inventing a scale.

**The population is the whole scored league, never a roster.** It is every
`player_impact_estimates` row for the current season under the *active* `player_impact`
model version: the same rows `/players/{id}`, `/teams/{id}/roster` and the trade
evaluator read. Players under the 200-window-minute threshold have no row and are not in
it; they are counted separately as unscored, never placed at zero or at the mean.

**Values are rounded to two decimals**, the precision every endpoint serves TEI at, so a
player's served value is always an exact member of this list and ties are resolved the
same way on every page.

The percentile and rank conventions are stated in the response rather than implied:

- percentile — mid-rank: the share of the population with a lower index, counting players
  with an equal index as half. 0 < p < 100, and tied players share a percentile.
- rank — competition ranking: 1 + the number of players with a higher index, so tied
  players share a rank ("1, 2, 2, 4").

Nothing here is a calibrated 0–100 performance score: a percentile says where a player's
index sits among measured players, not how good he is on an absolute scale.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.rosters import current_roster_clause
from app.config import get_settings
from app.db.models import ModelVersion, PlayerImpactEstimate, RosterEntry

#: The window-minutes gate the impact model applies (`features.recency_weighted_features`).
#: Restated here only so the qualification rule can be shown to a reader.
MIN_WINDOW_MINUTES = 200

PERCENTILE_METHOD = (
    "mid-rank: the share of scored players with a lower index, counting players with an "
    "equal index as half"
)
RANK_METHOD = "competition: 1 + the number of scored players with a higher index; ties share a rank"


def percentile_of(value: float, values: list[float]) -> float:
    """Mid-rank percentile of `value` within ascending `values` (0 < p < 100)."""
    if not values:
        raise ValueError("empty reference population")
    below = bisect_left(values, value)
    equal = bisect_right(values, value) - below
    return 100.0 * (below + 0.5 * equal) / len(values)


def rank_of(value: float, values: list[float]) -> int:
    """Competition rank of `value` within ascending `values`: 1 = highest index."""
    return len(values) - bisect_right(values, value) + 1


def impact_reference(db: Session) -> dict:
    settings = get_settings()
    model = db.scalar(
        select(ModelVersion).where(
            ModelVersion.model_name == "player_impact", ModelVersion.is_active
        )
    )
    if model is None:
        return {
            "available": False,
            "reason": "No impact model is active — run `make train`. Pivot does not rank "
            "players it has not scored.",
        }

    rows = db.execute(
        select(PlayerImpactEstimate.player_id, PlayerImpactEstimate.tei).where(
            PlayerImpactEstimate.model_version_id == model.id,
            PlayerImpactEstimate.season == settings.current_season,
        )
    ).all()
    if not rows:
        return {
            "available": False,
            "reason": f"The active impact model has no estimates for {settings.current_season}.",
        }

    scored_ids = {player_id for player_id, _ in rows}
    rostered_ids = set(
        db.scalars(
            select(RosterEntry.player_id).where(current_roster_clause(settings.current_season))
        ).all()
    )
    values = sorted(round(tei, 2) for _, tei in rows)

    return {
        "available": True,
        "metric": "Pivot Estimated Impact (TEI)",
        "scale": (
            "Unitless index: a fixed-weight, minutes-weighted z-score composite of box-score "
            "rates, times 2.5. Not points per 100 possessions."
        ),
        "season": settings.current_season,
        "model": {
            "version": model.version,
            "algorithm": model.algorithm,
            "training_period": model.training_period,
            "trained_at": model.trained_at.isoformat() if model.trained_at else None,
        },
        "population": {
            "size": len(values),
            "definition": (
                f"Every player with an impact estimate for {settings.current_season} under the "
                f"active model: at least {MIN_WINDOW_MINUTES} minutes across the "
                f"{model.training_period or 'training'} window, and on a current roster or "
                "listed as active."
            ),
            "min_window_minutes": MIN_WINDOW_MINUTES,
            "rostered_scored": len(scored_ids & rostered_ids),
            "rostered_unscored": len(rostered_ids - scored_ids),
            "unrostered_scored": len(scored_ids - rostered_ids),
            # At served precision: how many players genuinely share a value.
            "distinct_values": len(set(values)),
        },
        "values": values,
        "precision": 2,
        "percentile_method": PERCENTILE_METHOD,
        "rank_method": RANK_METHOD,
        "limitations": [
            "Box-score inputs only: defense, screening and off-ball value are under-measured.",
            "Each estimate carries a 10–90% band; neighbouring ranks usually overlap inside it.",
            "Players below the minutes threshold are unscored — not ranked last.",
            "A percentile places an index among measured players. It is not a calibrated "
            "0–100 rating.",
        ],
    }
