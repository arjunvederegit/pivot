"""Pivot Estimated Impact (TEI).

**The acronym is historical and kept on purpose.** It was coined when this product was
called TradeLab, and it names the `player_impact_estimates.tei` column, the `tei` field on
every API response, and every `ModelVersion` registered against it. Renaming it would be a
migration and a breaking API change in exchange for nothing a reader gains, so the
expansion is corrected everywhere it is written out — TradeLab, then RosterLab, now Pivot
Estimated Impact — and the three letters stay.

TEI is this project's own portfolio-model estimate of player impact, on a unitless index
scale (a weighted z-score composite × 2.5) — not points per 100 possessions; a team's
minutes-weighted TEI converts to net rating only through the fitted coefficient in
`projection.py`. It is NOT RAPTOR, EPM, LEBRON, BPM, or any proprietary metric, and it is documented as
an estimate with uncertainty (see docs/model-card-player-impact.md).

**The transparent weighted z-score index is the production metric (R3-1).** A ridge
challenger was trained and served until R3-1 on the strength of a held-out MAE of 0.637
against 0.645 for the index — a comparison on a *player-level next-season proxy*, which
is not the question the product asks of it. Measured at the level it is actually used,
team impact, the ridge has no validity at all:

    net_rating ~ team TEI (90 team-seasons)   index R2 = 0.7505   ridge R2 = 0.0039
    change-on-change (60 transitions)         index R2 = 0.6236   ridge R2 = 0.0030

The ridge is also a volume metric rather than an impact one, and — decisively for R3-2 —
it is not computable per season from stored artifacts, so a conversion coefficient could
only ever be fitted on n = 30 of a metric carrying no signal. It is retired, not
demoted: nothing in the serving path can select it.

Validation is still time-aware (no row-level random splits across seasons; transitions
only ever predict forward) and still reports the index against a persistence baseline,
because "better than assuming last season repeats" remains a claim worth checking."""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

from .features import season_moments, zscore_against, zscore_by_season

# Transparent baseline index weights (documented in docs/methodology.md).
INDEX_WEIGHTS: dict[str, float] = {
    "z_pts_per75": 0.22,
    "z_TS_PCT": 0.18,
    "z_AST_PCT": 0.14,
    "z_TM_TOV_PCT": -0.08,
    "z_USG_PCT": 0.06,
    "z_OREB_PCT": 0.04,
    "z_DREB_PCT": 0.08,
    "z_stl_per_min": 0.12,
    "z_blk_per_min": 0.10,
    "z_PIE": 0.14,
}

OFFENSE_KEYS = ["z_pts_per75", "z_TS_PCT", "z_AST_PCT", "z_TM_TOV_PCT", "z_USG_PCT", "z_OREB_PCT"]
DEFENSE_KEYS = ["z_DREB_PCT", "z_stl_per_min", "z_blk_per_min"]

Z_SOURCE_COLS = [
    "pts_per75",
    "TS_PCT",
    "USG_PCT",
    "AST_PCT",
    "TM_TOV_PCT",
    "OREB_PCT",
    "DREB_PCT",
    "stl_per_min",
    "blk_per_min",
    "fg3a_rate",
    "fta_rate",
    "MIN",
    "PIE",
    "NET_RATING",
]

TEI_SCALE = 2.5  # index points per z-unit; elite seasons land around +5

# R3-4: sigma^2 = SIGMA_INTERCEPT + SIGMA_PER_MINUTE / total_minutes, estimated from 921
# same-player consecutive-season pairs on the ingested history. See `sigma_for_minutes`.
SIGMA_INTERCEPT = 0.0326
SIGMA_PER_MINUTE = 240.9


def add_zscores(df: pd.DataFrame) -> pd.DataFrame:
    for col in Z_SOURCE_COLS:
        if col in df.columns:
            df = zscore_by_season(df, col)
    return df


def baseline_index(df: pd.DataFrame) -> pd.Series:
    """Weighted z-score index on the TEI scale."""
    total = pd.Series(np.zeros(len(df)), index=df.index)
    for key, weight in INDEX_WEIGHTS.items():
        if key in df.columns:
            total = total + weight * pd.to_numeric(df[key], errors="coerce").fillna(0.0)
    return total * TEI_SCALE / sum(abs(w) for w in INDEX_WEIGHTS.values())


def component_index(df: pd.DataFrame, keys: list[str]) -> pd.Series:
    total = pd.Series(np.zeros(len(df)), index=df.index)
    weight_sum = 0.0
    for key in keys:
        weight = INDEX_WEIGHTS.get(key, 0.0)
        if key in df.columns and weight:
            total = total + weight * pd.to_numeric(df[key], errors="coerce").fillna(0.0)
            weight_sum += abs(weight)
    return total * TEI_SCALE / weight_sum if weight_sum else total


def build_target(df: pd.DataFrame) -> pd.Series:
    """Next-season impact proxy: minutes-weighted blend of z(PIE) and z(NET_RATING)."""
    z_pie = pd.to_numeric(df.get("z_PIE"), errors="coerce").fillna(0.0)
    z_net = pd.to_numeric(df.get("z_NET_RATING"), errors="coerce").fillna(0.0)
    return 0.6 * z_pie + 0.4 * z_net


@dataclass
class ImpactTrainingResult:
    chosen_model: str
    algorithm: str
    validation: dict
    coefficients: dict
    feature_names: list[str]
    #: Reference-season moments the served window must be z-scored against (C5).
    reference_season: str = ""
    reference_moments: dict[str, tuple[float, float]] = field(default_factory=dict)


def _make_transitions(df: pd.DataFrame, seasons: list[str]) -> pd.DataFrame:
    """Pairs (features in season s, target in season s+1) for the same player."""
    frames = []
    for current, following in zip(seasons[:-1], seasons[1:], strict=False):
        cur = df[df["season"] == current].set_index("player_id")
        nxt = df[df["season"] == following].set_index("player_id")
        joined = cur.join(nxt[["target"]].rename(columns={"target": "target_next"}), how="inner")
        joined = joined.dropna(subset=["target_next"])
        joined["transition"] = f"{current}->{following}"
        frames.append(joined.reset_index())
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


#: R9.2. What is known about the coverage of the ``tei_low`` / ``tei_high`` band, stated wherever the band
#: is served. The band is *nominally* a 10th-90th percentile (80%) range, but R9.1's out-of-time backtest
#: (rolling origin, the production code run as of each cutoff, scored on the documented next-season
#: target) found it contained the outcome 60.8% of the time in development (n=1,591) and 57.2% on the
#: locked holdout (n=654). It is therefore *uncalibrated*: it ranks players' relative uncertainty (wider
#: for less playing time) but must not be read as an 80% probability. The TEI formula is unchanged.
TEI_BAND_CALIBRATION = {
    "nominal_level": 0.8,
    "status": "uncalibrated",
    "measured_coverage": {"development": 0.608, "holdout": 0.572},
    "measured_on": {"development": 1591, "holdout": 654, "unit": "player-seasons"},
    "target": "next-season 0.6*z(PIE) + 0.4*z(NET_RATING), the documented TEI objective",
    "reference": "docs/r9-1/current-model-assessment.md",
    "reading": (
        "A model-spread band, not an 80% interval: in Pivot's own out-of-time test it contained the next "
        "season's outcome about 57-61% of the time. Use it to compare how uncertain two estimates are, not "
        "as odds."
    ),
}


def sigma_for_minutes(total_minutes: float) -> float:
    """Per-player TEI uncertainty as a function of playing time (R3-4).

    Estimated from 921 same-player consecutive-season pairs on the ingested history:
    observed per-season variance, binned by the smaller of the two seasons' minutes and
    regressed on 1/minutes, gives

        sigma^2 = 0.0326 + 240.9 / minutes

    which is 0.72 at 500 minutes and 0.36 at 2500. It replaces a **constant 2.462**
    derived from the retired ridge's residual spread — a band that was both wrong in
    level and identical for a 2,800-minute starter and a 300-minute rookie.

    A framing hazard worth stating where the numbers are shown: the new bands are
    *narrower*, which reads as overconfidence when it is the opposite. And this quantity
    is season-to-season variability, which is the right input for a forward-looking
    interval and the wrong thing to call "measurement error".
    """
    minutes = max(float(total_minutes or 0.0), 1.0)
    return float(np.sqrt(max(SIGMA_INTERCEPT + SIGMA_PER_MINUTE / minutes, 1e-9)))


def train_impact_models(df: pd.DataFrame, seasons: list[str]) -> ImpactTrainingResult:
    """Time-aware validation of the transparent index against a persistence baseline.

    No model is selected here — the index is the metric (R3-1). What is still worth
    checking, and is still checked, is whether it beats "assume last season repeats".
    """
    df = add_zscores(df.copy())
    df["target"] = build_target(df)
    df["baseline_tei"] = baseline_index(df)

    transitions = _make_transitions(df, seasons)
    features = sorted(INDEX_WEIGHTS)

    validation: dict = {"note": "insufficient transitions for supervised validation"}

    if not transitions.empty and transitions["transition"].nunique() >= 2:
        transition_names = sorted(transitions["transition"].unique())
        valid_mask = transitions["transition"] == transition_names[-1]
        y_valid = transitions.loc[valid_mask, "target_next"].to_numpy()
        persistence_pred = transitions.loc[valid_mask, "target"].fillna(0.0).to_numpy()
        index_pred = (transitions.loc[valid_mask, "baseline_tei"] / TEI_SCALE).to_numpy()

        validation = {
            "train_transition": transition_names[:-1],
            "validation_transition": transition_names[-1],
            "n_valid": int(valid_mask.sum()),
            "index_mae": float(mean_absolute_error(y_valid, index_pred)),
            "persistence_mae": float(mean_absolute_error(y_valid, persistence_pred)),
            "index_beats_persistence": bool(
                mean_absolute_error(y_valid, index_pred)
                <= mean_absolute_error(y_valid, persistence_pred)
            ),
            "target": "next-season 0.6*z(PIE) + 0.4*z(NET_RATING), minutes-weighted z within season",
            "band_model": f"sigma^2 = {SIGMA_INTERCEPT} + {SIGMA_PER_MINUTE}/minutes (R3-4)",
            "retired_ridge_note": (
                "A ridge challenger was served until R3-1 on a player-level next-season MAE "
                "of 0.637 vs 0.645. At team level, which is where it was used, it explained "
                "R2 = 0.0039 of net rating against the index's 0.7505. Retired."
            ),
        }

    reference = seasons[-1] if seasons else ""
    return ImpactTrainingResult(
        chosen_model="baseline_index",
        algorithm="weighted z-score index (transparent, fixed weights)",
        validation=validation,
        coefficients=dict(INDEX_WEIGHTS),
        feature_names=features,
        reference_season=reference,
        reference_moments=season_moments(df, reference, Z_SOURCE_COLS) if reference else {},
    )


def score_players(
    weighted: pd.DataFrame, result: ImpactTrainingResult, season_df: pd.DataFrame
) -> pd.DataFrame:
    """Score the recency-weighted window on the SAME scale the index is defined on.

    The window frame used to be z-scored against itself (`season = "window"`), which put
    served TEI on a different scale from every fitted quantity — team-level correlation
    between the two constructions was r = 0.387, and the two rescalings that implied
    disagreed by 2.6x. Serving against the reference season's moments removes the
    mismatch instead of trying to correct for it (C5).
    """
    weighted = weighted.copy()
    weighted["total_minutes"] = weighted["total_minutes_window"]
    if result.reference_moments:
        weighted = zscore_against(weighted, result.reference_moments)
    else:
        # No reference (single-season database): fall back to the window's own
        # distribution and say so, rather than silently serving an unanchored scale.
        weighted["season"] = "window"
        weighted = add_zscores(weighted)

    weighted["tei"] = baseline_index(weighted).to_numpy()
    weighted["tei_offense"] = component_index(weighted, OFFENSE_KEYS)
    weighted["tei_defense"] = component_index(weighted, DEFENSE_KEYS)

    # R3-4: one band per player, from that player's own playing time.
    sigma = weighted["total_minutes_window"].apply(sigma_for_minutes) * TEI_SCALE
    band = 1.2816 * sigma  # 10th/90th percentile under normal residuals
    weighted["tei_sigma"] = sigma
    weighted["tei_low"] = weighted["tei"] - band
    weighted["tei_high"] = weighted["tei"] + band
    return weighted
