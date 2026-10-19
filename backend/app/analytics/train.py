"""Training pipeline: features → impact model comparison → archetypes → persistence.

Every trained artifact is recorded in model_versions with its algorithm, features,
target, validation metrics, and training window. Random seeds are fixed for
reproducibility."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.rosters import current_roster_clause
from app.config import BACKEND_DIR, get_settings
from app.core.logging import get_logger
from app.db.models import (
    ModelVersion,
    Player,
    PlayerArchetype,
    PlayerImpactEstimate,
    RosterEntry,
    Standing,
    TeamSeasonStats,
)

from .archetypes import fit_archetypes
from .availability import availability_from_history
from .features import build_player_season_features, recency_weighted_features
from .impact import TEI_SCALE, score_players, train_impact_models
from .picks import build_draft_outcomes, fit_pick_value_curve, fit_rank_persistence
from .projection import calibrate_tei_to_net_rating, calibrate_wins_per_net_rating

logger = get_logger(__name__)
ARTIFACT_DIR = BACKEND_DIR.parent / "models" / "artifacts"


def _code_commit() -> str | None:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                cwd=BACKEND_DIR,
                timeout=5,
            ).stdout.strip()
            or None
        )
    except Exception:
        return None


def _content_version(
    name: str, algorithm: str, training_period: str, features: list[str], metrics: dict
) -> str:
    """A version string that identifies the model, not the minute it was trained.

    `datetime.now().strftime("v%Y%m%d%H%M")` gave all three models trained in one run the
    *same* string, so `model_versions` held `v202607210204` three times over and a
    version string could not identify a model. Hashing the model's own content makes the
    string unique per model and stable across retrains that change nothing.
    """
    payload = json.dumps(
        {
            "name": name,
            "algorithm": algorithm,
            "training_period": training_period,
            "features": sorted(features),
            # Validation metrics move whenever the data moves, which is exactly when a
            # retrain deserves a new identity.
            "metrics": _stable(metrics),
        },
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"{datetime.now(UTC).strftime('%Y%m%d')}-{digest}"


def _stable(value: Any) -> Any:
    """Round floats so an identical retrain does not produce a new hash from noise."""
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


def _register_model(
    db: Session,
    name: str,
    version: str,
    algorithm: str,
    training_period: str,
    features: list[str],
    target: str | None,
    metrics: dict,
    artifact_path: str | None,
) -> ModelVersion:
    for old in db.scalars(
        select(ModelVersion).where(ModelVersion.model_name == name, ModelVersion.is_active)
    ).all():
        old.is_active = False
        old.status = "retired"
    version = _content_version(name, algorithm, training_period, features, metrics)
    # Retraining on unchanged data produces the same content hash; reactivate that row
    # rather than inserting a duplicate (the table has no unique constraint today, and
    # gains one in the same release).
    existing = db.scalar(
        select(ModelVersion).where(
            ModelVersion.model_name == name, ModelVersion.version == version
        )
    )
    if existing is not None:
        existing.is_active = True
        existing.status = "active"
        existing.trained_at = datetime.now(UTC)
        existing.validation_metrics = metrics
        existing.artifact_path = artifact_path
        db.flush()
        return existing
    model = ModelVersion(
        model_name=name,
        version=version,
        algorithm=algorithm,
        training_period=training_period,
        feature_list=features,
        target=target,
        validation_metrics=metrics,
        artifact_path=artifact_path,
        trained_at=datetime.now(UTC),
        code_commit=_code_commit(),
        is_active=True,
    )
    db.add(model)
    db.flush()
    return model


def _gc_superseded_estimates(db: Session) -> int:
    """Delete impact estimates belonging to inactive model versions.

    Nothing ever removed them, so every `make train && make score` added a full copy:
    1,536 rows for 512 players across three versions. Only the active version is ever
    read (`EvaluationService._impacts` filters on it), so the rest are dead weight that
    grows without bound.
    """
    active_ids = {
        m.id
        for m in db.scalars(
            select(ModelVersion).where(
                ModelVersion.model_name == "player_impact", ModelVersion.is_active
            )
        ).all()
    }
    stale = db.scalars(
        select(PlayerImpactEstimate).where(
            PlayerImpactEstimate.model_version_id.notin_(active_ids or {""})
        )
    ).all()
    for row in stale:
        db.delete(row)
    if stale:
        logger.info("garbage-collected %s superseded impact estimates", len(stale))
    return len(stale)


def _team_tei_transitions(
    db: Session,
    season_df: pd.DataFrame,
    seasons: list[str],
    net_by_team_season: dict[tuple[str, str], float],
) -> pd.DataFrame:
    """Team-season changes in minutes-weighted TEI, paired with changes in net rating.

    Scored per season with the same season-z construction the index is defined on, so the
    fitted coefficient is in the units production serves (C5). Team membership comes from
    `player_season_stats.team_id`, which is the only per-season roster record held.
    """
    from .impact import add_zscores, baseline_index

    scored = add_zscores(season_df.copy())
    scored["season_tei"] = baseline_index(scored)

    # Per-season membership now arrives on the feature frame itself: R4 added `team_id`
    # to `build_player_season_features` so the defensive differential could be measured
    # against a player's teammates. This used to re-query `player_season_stats` and merge
    # it back, which after that change collided on the column name and produced
    # `team_id_x`/`team_id_y` — the re-query is redundant, not merely duplicated.
    if "team_id" not in scored.columns:
        return pd.DataFrame()
    scored = scored[scored["team_id"].notna()]
    if scored.empty:
        return pd.DataFrame()

    scored["player_minutes"] = (
        pd.to_numeric(scored.get("total_minutes"), errors="coerce").fillna(0.0).clip(lower=1e-9)
    )
    grouped = (
        scored.dropna(subset=["team_id"])
        .groupby(["team_id", "season"])
        .apply(
            lambda t: np.average(t["season_tei"], weights=t["player_minutes"]),
            include_groups=False,
        )
        .rename("team_tei")
        .reset_index()
    )
    rows = []
    for team_id, grp in grouped.groupby("team_id"):
        grp = grp[grp["season"].isin(seasons)].sort_values("season")
        for (_, a), (_, b) in zip(grp.iloc[:-1].iterrows(), grp.iloc[1:].iterrows(), strict=False):
            net_a = net_by_team_season.get((team_id, a["season"]))
            net_b = net_by_team_season.get((team_id, b["season"]))
            if net_a is None or net_b is None:
                continue
            rows.append(
                {
                    "team_id": team_id,
                    "transition": f"{a['season']}->{b['season']}",
                    "d_tei": b["team_tei"] - a["team_tei"],
                    "d_net": net_b - net_a,
                }
            )
    return pd.DataFrame(rows)


def _fit_pick_curve(db: Session, season_df: pd.DataFrame, seasons: list[str]) -> dict[str, Any]:
    """Fit the pick-value curve and the rank drift its landing-slot support uses.

    Kept beside the other calibrations so a single `make train` produces every fitted
    quantity the serving path reads, and so the curve cannot drift away from the impact
    scale it is denominated in: it is built from the same season-z TEI construction.
    """
    from .impact import add_zscores, baseline_index
    from .picks import DEFAULT_ESTIMATION_CLASSES

    window = season_df[season_df["season"].isin(seasons)].copy()
    if window.empty:
        return {"calibrated": False, "reason": "no player-seasons in the window"}
    scored = add_zscores(window.copy())
    drafted = pd.DataFrame(
        [
            {"player_id": p.id, "draft_year": p.draft_year, "draft_number": p.draft_number}
            for p in db.scalars(
                select(Player).where(
                    Player.draft_year.is_not(None), Player.draft_number.is_not(None)
                )
            ).all()
        ]
    )
    if drafted.empty:
        return {"calibrated": False, "reason": "no player carries draft position"}
    outcomes = build_draft_outcomes(
        window, baseline_index(scored), drafted, DEFAULT_ESTIMATION_CLASSES
    )
    metrics = fit_pick_value_curve(outcomes)

    standings = pd.DataFrame(
        [
            {"team_id": s.team_id, "season": s.season, "win_pct": s.win_pct}
            for s in db.scalars(select(Standing)).all()
        ]
    )
    metrics["rank_persistence"] = fit_rank_persistence(standings)
    # Cells the estimation grid could not fill: a drafted player who never appeared could
    # be a zero or a selection that was never made, and the two are indistinguishable
    # here. Reported, never imputed.
    if not outcomes.empty:
        expected = len(DEFAULT_ESTIMATION_CLASSES) * 60
        metrics["grid_cells_present"] = int(len(outcomes))
        metrics["grid_cells_expected"] = expected
        first_round = outcomes[outcomes["slot"] <= 30]
        metrics["first_round_cells_present"] = int(len(first_round))
        metrics["first_round_cells_expected"] = len(DEFAULT_ESTIMATION_CLASSES) * 30
    return metrics


def train_all(db: Session, *, preserve_history: bool = False) -> dict[str, Any]:
    """Legacy training. ``preserve_history=True`` keeps superseded impact estimates (R9.1);
    the default still deletes them, exactly as before. For an experiment use
    ``app.modeling.registry.train_candidate``, which never deletes anything."""
    settings = get_settings()
    seasons = settings.history_season_list
    version = datetime.now(UTC).strftime("v%Y%m%d%H%M")
    training_period = f"{seasons[0]}..{seasons[-1]}"

    season_df = build_player_season_features(db)
    if season_df.empty:
        return {"error": "no ingested player stats; run `make sync-data` first"}

    result = train_impact_models(season_df, seasons)

    # No artifact: the index is fixed documented weights, so the model IS its
    # coefficients and they are recorded below. The retired ridge needed a pickle.
    artifact_path = None

    impact_model = _register_model(
        db,
        name="player_impact",
        version=version,
        algorithm=result.algorithm,
        training_period=training_period,
        features=result.feature_names,
        target=result.validation.get("target") if isinstance(result.validation, dict) else None,
        metrics={
            **result.validation,
            "chosen_model": result.chosen_model,
            "coefficients": {k: round(float(v), 4) for k, v in result.coefficients.items()},
        },
        artifact_path=artifact_path,
    )

    # Score current players: recency-weighted window features
    weighted = recency_weighted_features(season_df, seasons)
    scored = score_players(weighted, result, season_df)

    availability = availability_from_history(season_df, seasons).set_index("player_id")
    rostered_ids = {
        r.player_id
        for r in db.scalars(
            select(RosterEntry).where(current_roster_clause(settings.current_season))
        ).all()
    }

    written = 0
    for _, row in scored.iterrows():
        player_id = row["player_id"]
        if rostered_ids and player_id not in rostered_ids and not row.get("is_active"):
            continue
        avail = (
            float(availability.loc[player_id, "availability"])
            if player_id in availability.index
            else None
        )
        existing = db.scalar(
            select(PlayerImpactEstimate).where(
                PlayerImpactEstimate.player_id == player_id,
                PlayerImpactEstimate.season == settings.current_season,
                PlayerImpactEstimate.model_version_id == impact_model.id,
            )
        )
        values = {
            "player_id": player_id,
            "season": settings.current_season,
            "model_version_id": impact_model.id,
            "tei": float(row["tei"]),
            "tei_offense": float(row["tei_offense"]),
            "tei_defense": float(row["tei_defense"]),
            "tei_low": float(row["tei_low"]),
            "tei_high": float(row["tei_high"]),
            "availability": avail,
            "minutes_estimate": float(row["MIN"]) if pd.notna(row.get("MIN")) else None,
            "inputs": {
                "seasons_observed": int(row["seasons_observed"]),
                "total_minutes_window": float(row["total_minutes_window"]),
                "scale": TEI_SCALE,
            },
        }
        if existing is None:
            db.add(PlayerImpactEstimate(**values))
        else:
            for k, v in values.items():
                setattr(existing, k, v)
        written += 1

    orphaned = 0 if preserve_history else _gc_superseded_estimates(db)

    # Archetypes
    assignments, archetype_meta = fit_archetypes(scored)
    archetype_written = 0
    if not assignments.empty:
        _register_model(
            db,
            name="player_archetype",
            version=version,
            algorithm=archetype_meta["method"],
            training_period=training_period,
            features=archetype_meta["features"],
            target=None,
            # No silhouette: nothing is fitted, so there is no clustering quality to
            # report. What replaces it is the non-degeneracy evidence — how the labels
            # are actually distributed, and the league cut points that produced them.
            metrics={
                "chain_version": archetype_meta["chain_version"],
                "distribution": archetype_meta["distribution"],
                "herfindahl": archetype_meta["herfindahl"],
                "max_share": archetype_meta["max_share"],
                "unclassified": archetype_meta["unclassified"],
                "thresholds": archetype_meta["thresholds"],
            },
            artifact_path=None,
        )
        for _, row in assignments.iterrows():
            existing_archetype = db.scalar(
                select(PlayerArchetype).where(
                    PlayerArchetype.player_id == row["player_id"],
                    PlayerArchetype.season == settings.current_season,
                )
            )
            values = {
                "player_id": row["player_id"],
                "season": settings.current_season,
                "role_id": int(row["role_id"]),
                "label": str(row["label"]),
                "role_inputs": {},
            }
            if existing_archetype is None:
                db.add(PlayerArchetype(**values))
            else:
                for k, v in values.items():
                    setattr(existing_archetype, k, v)
            archetype_written += 1

    # Wins-per-net-rating calibration from ingested team seasons
    team_rows = []
    net_by_team_season: dict[tuple[str, str], float] = {}
    for stats in db.scalars(
        select(TeamSeasonStats).where(
            TeamSeasonStats.stat_type == "advanced", TeamSeasonStats.season_type == "regular"
        )
    ).all():
        standing = db.scalar(
            select(Standing).where(
                Standing.team_id == stats.team_id, Standing.season == stats.season
            )
        )
        net = (stats.stats or {}).get("NET_RATING")
        if net is not None:
            net_by_team_season[(stats.team_id, stats.season)] = float(net)
        if standing is not None and net is not None:
            team_rows.append({"net_rating": float(net), "wins": standing.wins})
    mapping = calibrate_wins_per_net_rating(pd.DataFrame(team_rows))

    # R3-2: the TEI -> net-rating conversion, fitted change-on-change on team
    # transitions. Registered with its own diagnostics because the coefficient is only
    # valid for the regressor construction recorded beside it.
    conversion = calibrate_tei_to_net_rating(
        _team_tei_transitions(db, season_df, seasons, net_by_team_season)
    )
    _register_model(
        db,
        name="tei_to_net_rating",
        version=version,
        algorithm="OLS on team-season changes (d_net ~ d_teamTEI)",
        training_period=training_period,
        features=["team minutes-weighted TEI"],
        target="change in team net rating",
        metrics=conversion,
        artifact_path=None,
    )
    _register_model(
        db,
        name="team_projection",
        version=version,
        algorithm="linear wins ~ net_rating",
        training_period=training_period,
        features=["NET_RATING"],
        target="team regular-season wins",
        metrics=mapping,
        artifact_path=None,
    )

    # R5-2: the empirical draft-pick value curve, and the rank drift the landing-slot
    # support is built from. Registered with the diagnostic that FAILS as well as the
    # ones that pass — the curve does not significantly beat a round-only rule, and the
    # model version says so rather than reporting only the flattering figures.
    pick_curve = _fit_pick_curve(db, season_df, seasons)
    _register_model(
        db,
        name="pick_value_curve",
        version=version,
        algorithm="exponential decay in draft slot, fitted on within-class-normalised "
        "above-replacement window value",
        training_period=training_period,
        features=["draft_number", "draft_year"],
        target="relative above-replacement window value (class mean = 1)",
        metrics=pick_curve,
        artifact_path=None,
    )

    db.commit()
    summary = {
        "version": impact_model.version,
        "superseded_estimates_removed": orphaned,
        "impact": {
            "chosen": result.chosen_model,
            "players_scored": written,
            "validation": result.validation,
        },
        "archetypes": {
            "players_labeled": archetype_written,
            "max_label_share": archetype_meta.get("max_share"),
            "unclassified": archetype_meta.get("unclassified"),
        },
        "wins_mapping": mapping,
    }
    logger.info("training complete: %s", summary["version"])
    return summary
