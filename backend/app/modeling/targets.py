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
    #: R9.2. One season's rows -> the sampling variance of that season's ``measure`` (the variance the
    #: statistic would have around the player's underlying rate, given the attempts actually taken).
    #: ``None`` for targets that have no such model. Never an input to a target's *value*.
    sampling_variance: Callable[[pd.DataFrame], pd.Series] | None = None
    #: R9.2. Minimum target-season exposure (e.g. attempts) for a row to be *evaluated and trained on*,
    #: in addition to the minutes rule. A rate on three attempts is noise, not an outcome to be scored.
    min_exposure: float = 0.0
    #: Where the model's evidence comes from, in plain words (provenance, not a computation input).
    evidence: str = "NBA.com season tables"

    def describe(self) -> dict:
        out = {
            "name": self.name,
            "description": self.description,
            "unit": self.unit,
            "exposure_unit": self.exposure_unit,
            "source_columns": list(self.source_columns),
            "output_kind": self.output_kind,
            "interpretation": self.interpretation,
        }
        if self.min_exposure or self.sampling_variance is not None:
            out["min_exposure"] = self.min_exposure
            out["has_sampling_variance"] = self.sampling_variance is not None
        return out


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


def _smoothed_binomial_variance(makes: pd.Series, attempts: pd.Series) -> pd.Series:
    """Variance of a made/attempted rate, with the plug-in probability smoothed by half a count.

    The raw plug-in p(1-p) is 0 for a player who made all or none of a handful of attempts, which
    would claim certainty exactly where there is least evidence; adding half a make and half a miss
    keeps every variance positive. No league prior is used, so the value depends on the season's own
    counts alone (it can be computed at any cutoff without leakage)."""
    a = pd.to_numeric(attempts, errors="coerce").astype(float)
    m = pd.to_numeric(makes, errors="coerce").astype(float)
    p = (m + 0.5) / (a + 1.0)
    return (p * (1.0 - p) / a).where(a > 0)


def _fg3_rate(rows: pd.DataFrame) -> pd.Series:
    m = pd.to_numeric(rows["fg3m"], errors="coerce").astype(float)
    a = pd.to_numeric(rows["fg3a"], errors="coerce").astype(float)
    return (m / a).where(a > 0)  # no attempts -> no rate (NaN), never 0


def _ft_rate(rows: pd.DataFrame) -> pd.Series:
    m = pd.to_numeric(rows["ftm"], errors="coerce").astype(float)
    a = pd.to_numeric(rows["fta"], errors="coerce").astype(float)
    return (m / a).where(a > 0)


def _exposure_of(column: str) -> Callable[[pd.DataFrame], pd.Series]:
    def exposure(rows: pd.DataFrame) -> pd.Series:
        return pd.to_numeric(rows[column], errors="coerce").astype(float)

    exposure.__name__ = f"exposure_{column}"
    return exposure


def _fg3_variance(rows: pd.DataFrame) -> pd.Series:
    return _smoothed_binomial_variance(rows["fg3m"], rows["fg3a"])


def _ft_variance(rows: pd.DataFrame) -> pd.Series:
    return _smoothed_binomial_variance(rows["ftm"], rows["fta"])


def _ts_sampling_variance(rows: pd.DataFrame) -> pd.Series:
    """Sampling variance of true shooting %, from the shot-type counts behind it.

    ``TS = PTS / (2 * TSA)`` with ``PTS = 2*FG2M + 3*FG3M + FTM`` and ``TSA = FGA + 0.44*FTA``. Treating
    attempts as given (a player's shot volume is a decision, not noise in his ability) and each
    attempt as an independent trial, ``Var(PTS) = 4*n2*q2(1-q2) + 9*n3*q3(1-q3) + nf*qf(1-qf)`` and
    ``Var(TS) = Var(PTS) / (2*TSA)^2``. This is the delta-method variance of a *ratio estimator*,
    not a binomial: it is the reason true shooting is not modelled as made/attempted. Probabilities
    are smoothed by half a count (see ``_smoothed_binomial_variance``). Independence is an
    approximation (free throws come in trips, shot quality varies); the fitted dispersion parameter
    in the model absorbs the average departure and is reported."""
    def comp(m: str, a: str) -> tuple[pd.Series, pd.Series]:
        mm = pd.to_numeric(rows[m], errors="coerce").astype(float)
        aa = pd.to_numeric(rows[a], errors="coerce").astype(float)
        q = (mm + 0.5) / (aa + 1.0)
        return aa, q * (1.0 - q)

    n2, v2 = comp("fg2m", "fg2a")
    n3, v3 = comp("fg3m", "fg3a")
    nf, vf = comp("ftm", "fta")
    tsa = pd.to_numeric(rows["tsa"], errors="coerce").astype(float)
    var_pts = 4.0 * n2 * v2 + 9.0 * n3 * v3 + nf * vf
    return (var_pts / (4.0 * tsa**2)).where(tsa > 0)


#: A target-season rate on fewer attempts than this is not scored (R9.2 pre-registration).
MIN_TARGET_ATTEMPTS = 50.0

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
        sampling_variance=_ts_sampling_variance,
    ),
    "fg3_pct": TargetSpec(
        name="fg3_pct",
        description="Three-point accuracy: season 3-pointers made / attempted (integer counts recovered from NBA.com)",
        unit="fraction of 1 (makes per attempt)",
        measure=_fg3_rate,
        exposure=_exposure_of("fg3a"),
        exposure_unit="three-point attempts",
        source_columns=("fg3m", "fg3a"),
        interpretation=(
            "observed three-point accuracy in the target season, scored only for players with at least "
            f"{int(MIN_TARGET_ATTEMPTS)} attempts; a noisy measurement of shooting skill (sampling error alone is "
            "about 5 points at 100 attempts)"
        ),
        sampling_variance=_fg3_variance,
        min_exposure=MIN_TARGET_ATTEMPTS,
    ),
    "ft_pct": TargetSpec(
        name="ft_pct",
        description="Free-throw accuracy: season free throws made / attempted (integer counts recovered from NBA.com)",
        unit="fraction of 1 (makes per attempt)",
        measure=_ft_rate,
        exposure=_exposure_of("fta"),
        exposure_unit="free-throw attempts",
        source_columns=("ftm", "fta"),
        interpretation=(
            "observed free-throw accuracy in the target season, scored only for players with at least "
            f"{int(MIN_TARGET_ATTEMPTS)} attempts; the most stable shooting rate"
        ),
        sampling_variance=_ft_variance,
        min_exposure=MIN_TARGET_ATTEMPTS,
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
