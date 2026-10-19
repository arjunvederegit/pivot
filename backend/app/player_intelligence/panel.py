"""The R9.2 player-season panel: R9.1's panel plus integer shot counts.

Nothing in R9.1's panel is changed. This module merges, by ``(player_id, season)``, the shot counts
recovered in :mod:`app.player_intelligence.components` onto the frame
``app.analytics.features.build_player_season_features`` already produces, and hands the result to
``PlayerSeasonPanel.from_frame`` — so the same unit validation, one-row-per-player-season check and
as-of discipline apply.

Only **regular-season** rows are read, as in R9.1. Playoff rows would otherwise be folded into the
season total (the unique key is ``(player, season, season_type, stat_type)``).
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.features import build_player_season_features
from app.db.models import PlayerSeasonStats
from app.modeling.panel import PanelValidationError, PlayerSeasonPanel

from .components import COMPONENT_COLUMNS, components_frame


def load_stat_dicts(db: Session) -> pd.DataFrame:
    """``player_id, season, GP, base_json, advanced_json`` — the stored provider JSON, regular season only."""
    rows = db.execute(
        select(
            PlayerSeasonStats.player_id,
            PlayerSeasonStats.season,
            PlayerSeasonStats.stat_type,
            PlayerSeasonStats.games_played,
            PlayerSeasonStats.stats,
        ).where(PlayerSeasonStats.season_type == "regular", PlayerSeasonStats.stat_type.in_(("base", "advanced")))
    ).all()
    base: dict[tuple[str, str], dict] = {}
    adv: dict[tuple[str, str], dict] = {}
    gp: dict[tuple[str, str], int | None] = {}
    for r in rows:
        key = (r.player_id, r.season)
        if r.stat_type == "base":
            base[key] = r.stats or {}
            gp[key] = r.games_played
        else:
            adv[key] = r.stats or {}
    return pd.DataFrame(
        [
            {"player_id": k[0], "season": k[1], "GP": gp.get(k), "base_json": base[k], "advanced_json": adv.get(k, {})}
            for k in base
        ]
    )


def attach_components(frame: pd.DataFrame, stat_dicts: pd.DataFrame) -> pd.DataFrame:
    comps = components_frame(stat_dicts)
    merged = frame.merge(comps, on=["player_id", "season"], how="left", validate="1:1")
    merged["shooting_counts_quality"] = merged["shooting_counts_quality"].fillna("missing")
    return merged


def build_skill_panel(db: Session) -> PlayerSeasonPanel:
    """The panel every R9.2 model and the intelligence service read."""
    base = build_player_season_features(db)
    if base.empty:
        raise PanelValidationError("no ingested player statistics")
    stat_dicts = load_stat_dicts(db)
    merged = attach_components(base, stat_dicts)
    missing = [c for c in COMPONENT_COLUMNS if c not in merged.columns]
    if missing:  # pragma: no cover - defensive
        raise PanelValidationError(f"shot-count columns missing after merge: {missing}")
    return PlayerSeasonPanel.from_frame(merged)
