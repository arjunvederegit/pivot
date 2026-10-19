"""What a model is asked to predict.

A target is one observable quantity in the **target season**, with the sample size behind it. It is
defined here once so the baseline, the incumbent and every candidate are scored on exactly the same
thing, in the same units.

Every target registered here is a *projected* quantity (``output_kind = "projected"``): an observed
statistic in a later season. None of them is a claim about underlying ability — an observed rate in
a later season is a noisy measurement of ability, and a model that predicts it well has shown
forecasting skill, which is evidence about ability but not a measurement of it (see
``docs/r9-1/current-model-assessment.md`` §6).

``impact_proxy`` is the target the incumbent TEI has always documented for itself
(``0.6*z(PIE) + 0.4*z(NET_RATING)``). It is kept because it is the incumbent's own stated objective,
**not** because it is a good measure of impact: PIE is a box-score share and NET_RATING is an
on-court team result confounded by teammates. The rate targets are the ones with an unambiguous
meaning.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analytics.impact import add_zscores, build_target


@dataclass(frozen=True)
class TargetSpec:
    name: str
    description: str
    unit: str
    #: One season's rows -> the measured value (NaN where it cannot be measured).
    measure: Callable[[pd.DataFrame], pd.Series]
    #: One season's rows -> the sample size behind the measure (minutes, or attempts).
    exposure: Callable[[pd.DataFrame], pd.Series]
    exposure_unit: str
    source_columns: tuple[str, ...]
    interpretation: str
    output_kind: str = "projected"

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "unit": self.unit,
            "exposure_unit": self.exposure_unit,
            "source_columns": list(self.source_columns),
            "output_kind": self.output_kind,
            "interpretation": self.interpretation,
        }


def _minutes(rows: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(rows["total_minutes"], errors="coerce").astype(float)


def _column(name: str) -> Callable[[pd.DataFrame], pd.Series]:
    def measure(rows: pd.DataFrame) -> pd.Series:
        return pd.to_numeric(rows[name], errors="coerce").astype(float)

    measure.__name__ = f"measure_{name}"
    return measure


def _impact_proxy(rows: pd.DataFrame) -> pd.Series:
    """The incumbent's target, built by the incumbent's own code on a **single season**.

    ``zscore_by_season`` standardises within the season it is given, so passing one season's rows
    uses only that season's league distribution — which is how the production target is built."""
    scored = add_zscores(rows.copy())
    return build_target(scored).astype(float)


def _true_shooting_attempts(rows: pd.DataFrame) -> pd.Series:
    """TSA = FGA + 0.44*FTA, season total (per-game columns times games played)."""
    per_game = pd.to_numeric(rows["FGA"], errors="coerce") + 0.44 * pd.to_numeric(
        rows["FTA"], errors="coerce"
    )
    return (per_game * pd.to_numeric(rows["GP"], errors="coerce")).astype(float)


TARGETS: dict[str, TargetSpec] = {
    "impact_proxy": TargetSpec(
        name="impact_proxy",
        description="0.6*z(PIE) + 0.4*z(on-court NET_RATING), minutes-weighted z within the season",
        unit="z (season league SDs)",
        measure=_impact_proxy,
        exposure=_minutes,
        exposure_unit="minutes",
        source_columns=("PIE", "NET_RATING", "total_minutes"),
        interpretation=(
            "the incumbent TEI's own documented objective: a box-score share blended with an on-court "
            "team result. A proxy for contribution, confounded by teammates; not a measure of impact"
        ),
    ),
    "pie": TargetSpec(
        name="pie",
        description="Player Impact Estimate (NBA.com): the player's share of the game's box-score events",
        unit="fraction of 1 (share of all box-score events while on the floor)",
        measure=_column("PIE"),
        exposure=_minutes,
        exposure_unit="minutes",
        source_columns=("PIE", "total_minutes"),
        interpretation=(
            "observed box-score contribution share. Unlike impact_proxy it contains no on-court team "
            "result, so a team-level check against wins is not circular for it"
        ),
    ),
    "ts_pct": TargetSpec(
        name="ts_pct",
        description="True shooting percentage, NBA.com advanced table",
        unit="fraction of 1 (points per 2 true shooting attempts)",
        measure=_column("TS_PCT"),
        exposure=_true_shooting_attempts,
        exposure_unit="true shooting attempts",
        source_columns=("TS_PCT", "FGA", "FTA", "GP"),
        interpretation="observed scoring efficiency in the target season; a noisy measurement of shooting skill",
    ),
    "ast_pct": TargetSpec(
        name="ast_pct",
        description="Assist percentage (share of teammate field goals assisted while on the floor)",
        unit="fraction of 1",
        measure=_column("AST_PCT"),
        exposure=_minutes,
        exposure_unit="minutes",
        source_columns=("AST_PCT", "total_minutes"),
        interpretation="observed playmaking volume; depends on role and teammates as well as skill",
    ),
    "reb_pct": TargetSpec(
        name="reb_pct",
        description="Total rebound percentage",
        unit="fraction of 1",
        measure=_column("REB_PCT"),
        exposure=_minutes,
        exposure_unit="minutes",
        source_columns=("REB_PCT", "total_minutes"),
        interpretation="observed rebounding share; strongly position-dependent",
    ),
    "usg_pct": TargetSpec(
        name="usg_pct",
        description="Usage percentage (share of team possessions ended by the player while on the floor)",
        unit="fraction of 1",
        measure=_column("USG_PCT"),
        exposure=_minutes,
        exposure_unit="minutes",
        source_columns=("USG_PCT", "total_minutes"),
        interpretation="observed offensive role; a choice as much as an ability",
    ),
}


def get_target(name: str) -> TargetSpec:
    try:
        return TARGETS[name]
    except KeyError as exc:
        raise KeyError(f"unknown target {name!r}; registered: {sorted(TARGETS)}") from exc


def measure_by_player(spec: TargetSpec, rows: pd.DataFrame) -> pd.DataFrame:
    """``player_id, value, exposure`` for one season, one row per player."""
    out = pd.DataFrame(
        {
            "player_id": rows["player_id"].to_numpy(),
            "value": spec.measure(rows).to_numpy(),
            "exposure": spec.exposure(rows).to_numpy(),
        }
    )
    out["value"] = out["value"].replace([np.inf, -np.inf], np.nan)
    return out
