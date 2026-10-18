"""Feature engineering from ingested provider-backed season stats.

One row per (player, season) combining base, advanced, and (when available) estimated
metrics. All features derive from real NBA.com data — nothing is imputed beyond
documented, conservative minute-weighted league means for optional columns."""

from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Player, PlayerSeasonStats

RANDOM_SEED = 20260720

# Advanced-table columns kept as features.
ADVANCED_COLS = [
    "OFF_RATING",
    "DEF_RATING",
    "NET_RATING",
    "AST_PCT",
    "AST_TO",
    "OREB_PCT",
    "DREB_PCT",
    "REB_PCT",
    "TM_TOV_PCT",
    "EFG_PCT",
    "TS_PCT",
    "USG_PCT",
    "PACE",
    "PIE",
    "POSS",
]
BASE_COLS = [
    "PTS",
    "REB",
    "AST",
    "STL",
    "BLK",
    "TOV",
    "PF",
    "FGA",
    "FG3A",
    "FG3M",
    "FTA",
    "PLUS_MINUS",
    "AGE",
]
ESTIMATED_COLS = ["E_OFF_RATING", "E_DEF_RATING", "E_NET_RATING", "E_USG_PCT"]


def _teammate_def_rating_excluding_self(df: pd.DataFrame) -> pd.Series:
    """Minutes-weighted mean on-court `DEF_RATING` of a player's TEAMMATES.

    The baseline a defensive differential is measured against. Using the team's own
    published `DEF_RATING` instead is very nearly the same number per player —
    r = 0.998 — but the two behave completely differently once aggregated back to a
    team, because the team stat puts the player on both sides of the subtraction. The
    minutes-weighted team aggregate of `(team_DEF - player_DEF)` correlates **+0.342**
    with team defensive rating, and the leave-self-out version **-0.292**: the same
    player-level quantity, opposite signs, purely from the self term. Excluding the
    player is the construction that means what it says.
    """
    dr = pd.to_numeric(df["DEF_RATING"], errors="coerce")
    minutes = pd.to_numeric(df["total_minutes"], errors="coerce")
    ok = dr.notna() & minutes.notna() & (minutes > 0)
    # Zero out unusable rows so they contribute nothing to either running total.
    weighted = (dr * minutes).where(ok, 0.0)
    weights = minutes.where(ok, 0.0)

    roster = [df["team_id"], df["season"]]
    others_weighted = weighted.groupby(roster).transform("sum") - weighted
    others_minutes = weights.groupby(roster).transform("sum") - weights
    # A player who is his own team's only measured row has no teammates to compare
    # against; that is missing, not zero.
    return (others_weighted / others_minutes.replace(0, np.nan)).where(ok)


def build_player_season_features(db: Session) -> pd.DataFrame:
    """Wide feature frame: one row per player-season with GP/MIN and stat columns."""
    rows = db.execute(
        select(
            PlayerSeasonStats.player_id,
            PlayerSeasonStats.season,
            PlayerSeasonStats.stat_type,
            PlayerSeasonStats.games_played,
            PlayerSeasonStats.minutes,
            PlayerSeasonStats.stats,
            PlayerSeasonStats.team_id,
            Player.full_name,
            Player.nba_player_id,
            Player.birth_date,
            Player.height_inches,
            Player.position,
            Player.is_active,
        )
        .join(Player, Player.id == PlayerSeasonStats.player_id)
        # Explicit, not implied: a playoff row (same player, season and stat_type) would
        # otherwise be folded into the regular-season feature row by the `by_key` merge below.
        .where(PlayerSeasonStats.season_type == "regular")
    ).all()
    if not rows:
        return pd.DataFrame()

    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        key = (r.player_id, r.season)
        entry = by_key.setdefault(
            key,
            {
                "player_id": r.player_id,
                "season": r.season,
                "full_name": r.full_name,
                "nba_player_id": r.nba_player_id,
                "height_inches": r.height_inches,
                "position": r.position,
                "is_active": r.is_active,
                "team_id": None,
            },
        )
        stats = r.stats or {}
        # Only some stat types carry a team: every `estimated` row has team_id NULL, so
        # an unconditional assignment would erase the team the `base` row supplied.
        if r.team_id is not None:
            entry["team_id"] = r.team_id
        if r.stat_type == "base":
            entry["GP"] = r.games_played
            entry["MIN"] = r.minutes
            for col in BASE_COLS:
                entry[col] = stats.get(col)
        elif r.stat_type == "advanced":
            for col in ADVANCED_COLS:
                entry[col] = stats.get(col)
        elif r.stat_type == "estimated":
            for col in ESTIMATED_COLS:
                entry[col] = stats.get(col)

    df = pd.DataFrame(list(by_key.values()))
    df = df.dropna(subset=["GP", "MIN"])
    df["total_minutes"] = df["GP"].astype(float) * df["MIN"].astype(float)

    # Derived rates (per-minute to sidestep pace-of-play distortion in per-game stats)
    for col, name in (
        ("PTS", "pts_per_min"),
        ("STL", "stl_per_min"),
        ("BLK", "blk_per_min"),
        ("REB", "reb_per_min"),
        ("AST", "ast_per_min"),
        ("TOV", "tov_per_min"),
        ("PF", "pf_per_min"),
    ):
        df[name] = pd.to_numeric(df[col], errors="coerce") / df["MIN"].astype(float)
    fga = pd.to_numeric(df["FGA"], errors="coerce")
    df["fg3a_rate"] = pd.to_numeric(df["FG3A"], errors="coerce") / fga.replace(0, np.nan)
    df["fta_rate"] = pd.to_numeric(df["FTA"], errors="coerce") / fga.replace(0, np.nan)
    # Three-point ATTEMPTS per minute, which is what "three-point volume" means. The
    # existing `fg3a_rate` is 3PA/FGA — shot selection, not volume — and it tracks the
    # need it is supposed to address (team 3PA per game) at rho +0.754 where this tracks
    # it at +0.845, a bootstrap difference of +0.093 [+0.016, +0.178].
    df["fg3a_per_min"] = pd.to_numeric(df["FG3A"], errors="coerce") / df["MIN"].astype(float)
    # Points per 75 possessions using individual possessions when available
    poss = pd.to_numeric(df.get("POSS"), errors="coerce")
    pts_total = pd.to_numeric(df["PTS"], errors="coerce") * df["GP"].astype(float)
    df["pts_per75"] = np.where(poss > 0, pts_total / poss * 75, np.nan)

    # Team-relative defensive differential, positive = the team defends better with this
    # player on the floor. Derived HERE, in the season frame, because it needs team
    # context and 188 of 632 window players changed team inside the window — collapsing
    # first would attribute a player's differential to the wrong roster.
    if "DEF_RATING" in df.columns:
        df["def_diff_raw"] = _teammate_def_rating_excluding_self(df) - pd.to_numeric(
            df["DEF_RATING"], errors="coerce"
        )
    else:
        df["def_diff_raw"] = np.nan
    return df


# The ONLY numeric columns that survive `recency_weighted_features`. A column absent
# from this list is silently dropped by the collapse — measured on the real database, the
# season frame carries 49 columns and the window frame 25 — and a skill defined on a
# dropped column resolves for nobody. That is the C7 trap, and it is why every R4 skill
# input appears here.
MODEL_FEATURES = [
    "pts_per75",
    "TS_PCT",
    "USG_PCT",
    "AST_PCT",
    "TM_TOV_PCT",
    "OREB_PCT",
    "DREB_PCT",
    "stl_per_min",
    "blk_per_min",
    "pf_per_min",
    "fg3a_rate",
    "fg3a_per_min",
    "fta_rate",
    "def_diff_raw",
    "MIN",
    "GP",
    "AGE",
    "PIE",
    "NET_RATING",
]

# R4-1d. Empirical-Bayes shrinkage for three-point accuracy, in ATTEMPTS. Three
# independent estimators on the 1714 ingested player-seasons agree: beta-binomial
# method-of-moments 272.5, maximum likelihood 297.2, and the out-of-sample binomial
# log-loss optimum 326.
#
# Shrinkage is not optional here. 635 of 1714 player-seasons (37.0 %) have fewer than 50
# total attempts and 219 sit at exactly 0.000 or at least 1.000, so an unshrunk
# percentage ranks small-sample non-shooters at both extremes.
FG3_SHRINKAGE_ATTEMPTS = 300.0
FG3_LEAGUE_MEAN = 0.3618

# R4-2. Shrinkage for the defensive differential, in MINUTES.
#
# Documented as **arbitrary within a wide band**, deliberately: every value from 1,000 to
# 20,000 gives the same answer to three decimal places (year-over-year reliability of the
# shipped composite moves 0.860 -> 0.859 across that whole range). Only K = 0 behaves
# differently. There is nothing here to tune, and a future recalibration that "optimises"
# K would be fitting noise.
DEF_SHRINKAGE_MINUTES = 5300.0

# R4-2. The defensive composites. **Committed before any named-player check was run**,
# which is the anti-overfitting requirement (A'''): no player's value selected these
# weights, and the face-validity review that follows is reported, never gated.
#
# The plan prescribed the team-demeaned on-court differential as the PRIMARY term at
# >= 50 % weight, with event rates minor. The data contradicts that, and the criteria the
# plan proposed to justify it turn out not to test the release:
#
#   - A placebo that ignores the player entirely — score everyone by their own team's
#     defensive rating — scores rho -0.988 on the plan's A', a decile gap of 11.01 on A''
#     (threshold 3.0) and R2 0.904 on the supporting change-on-change design. A criterion
#     a zero-information metric wins by 2.6x cannot discriminate.
#   - A' is additionally degenerate by construction for a demeaned quantity: each
#     possession is charged to five on-court players, so the possession-weighted mean of
#     on-court DEF_RATING over a full roster IS the team rating, and aggregation destroys
#     94.7 % of the dispersion (player sd 8.66 -> team aggregate 0.455).
#   - On the non-circular test — does the metric add anything beyond the persistence of
#     the differential itself — the differential adds nothing (partial r -0.056) while the
#     event rates add the most (+0.133). Out-of-sample gain against a team's own prior
#     defensive rating falls monotonically as the impact weight rises: +0.0253 at weight
#     0.00, +0.0093 at 0.30, -0.0089 at 0.60.
#   - Reliability decides the rest. Year-over-year, the shipped composite is 0.859
#     (0.869 for players who changed team); the plan's 0.60-impact blend is 0.44 and 0.37.
#     A trade tool projects a player onto a new roster, so a metric that does not survive
#     the move is not fit for the one thing it is for.
#
# The differential is kept as a 0.10 minority term rather than dropped, because it is the
# only term carrying any signal on the question the product actually asks: for the 100
# players who changed team, prior-season defensive value against the change in the
# RECEIVING team's defensive rating gives -0.151 for the differential and +0.001 for
# steals. That correlation is not distinguishable from zero at this sample size, and is
# reported as such — it is a reason to keep a minority term, not a claim of validation.
#
# WHAT SELECTED THESE TERMS, and what did not.
#
# **The weights are chosen by construct, not by the numbers above.** Adversarial checking
# established that the criteria cannot tell a defensive statistic from an offensive one:
# holding this vector fixed and swapping only the 0.22 term for `TS_PCT` — true-shooting
# percentage, which has no defensive content whatsoever — BEATS `DREB_PCT` on every
# non-circular criterion:
#
#     criterion                     DREB_PCT      TS_PCT
#     A' (team aggregate)             -0.499      -0.542
#     A'' (decile gap)                  4.50        5.23
#     lagged team fit, rho            -0.309      -0.377
#     ...partial t                     -1.84       -2.58
#     ...leave-one-team-out gain      +0.013      +0.054
#
# A criterion that prefers an offensive statistic in a defensive slot is measuring "good
# player on a good team", not defence. So the numbers reported above are evidence that
# this composite is not WORSE than the steals proxy on the things anyone has been able to
# measure; they are not evidence that the weights are right.
#
# Every term here is included because it is a defensive act: a block, a steal, ending the
# opponent's possession with a rebound, the cost of fouling, and the points the opponent
# scored while the player was on the floor. `TS_PCT` is excluded despite scoring better,
# which is what construct validity is for.
#
# What is NOT claimed: none of this validates the composite as a measure of defensive
# ability. Every target available in this repository is derived from on-court DEF_RATING,
# so every test is circular to some degree. Honest validation needs the matchup and
# tracking data that R6 defers, and `docs/limitations.md` says so.
#
# Known and disclosed rather than fixed: blocks and defensive rebounding carry 0.58 of the
# weight, so the composite is big-biased; and its `DREB_PCT` term correlates 0.907 with
# the input of the separate `rebounding` skill, giving a 0.579 overlap between the two
# skills. Dropping `DREB_PCT` would cut that overlap to 0.280 — but the only evidence that
# dropping it costs anything comes from the criteria just shown to be unable to
# discriminate, so there is no measured basis for either choice. It stays because ending a
# defensive possession is a defensive act, and the overlap is stated instead.
TEAM_DEFENSE_WEIGHTS = {
    "def_impact": 0.10,
    "blk_per_min": 0.36,
    "stl_per_min": 0.27,
    "DREB_PCT": 0.22,
    "pf_per_min": -0.05,
}

# There is deliberately no point-of-attack composite here. One was built, scored WORSE
# than the steals proxy it replaced on its pre-registered class check, and was withdrawn
# rather than reweighted — see `archetypes.UNADDRESSABLE_NEEDS`. A computed-but-unread
# defensive score is an invitation to wire it up later without re-running that check.


def minutes_weighted_league_mean(df: pd.DataFrame, col: str) -> float:
    values = pd.to_numeric(df[col], errors="coerce")
    weights = df["total_minutes"].astype(float)
    mask = values.notna() & weights.notna()
    if not mask.any():
        return 0.0
    return float(np.average(values[mask], weights=weights[mask]))


def zscore_by_season(df: pd.DataFrame, col: str, out_col: str | None = None) -> pd.DataFrame:
    """Minutes-weighted z-score within each season (cross-season comparability)."""
    out_col = out_col or f"z_{col}"

    result = pd.Series(np.zeros(len(df)), index=df.index, dtype=float)
    for _, group in df.groupby("season"):
        values = pd.to_numeric(group[col], errors="coerce")
        weights = group["total_minutes"].astype(float)
        mask = values.notna() & (weights > 0)
        if mask.sum() < 10:
            continue
        mean = np.average(values[mask], weights=weights[mask])
        var = np.average((values[mask] - mean) ** 2, weights=weights[mask])
        std = np.sqrt(var) if var > 0 else 1.0
        result.loc[group.index] = ((values - mean) / std).fillna(0.0)
    df[out_col] = result
    return df


def season_moments(df: pd.DataFrame, season: str, cols: list[str]) -> dict[str, tuple[float, float]]:
    """Minutes-weighted (mean, std) per column for one season — the reference a served
    row must be z-scored against.

    `zscore_by_season` computes these per season at training time. At serve time the
    recency-weighted window frame is a *different population*, so z-scoring it against
    itself produces numbers on a different scale from the ones the model was fitted on
    (C5). Measured before the fix: team-level production TEI correlated only r = 0.387
    with the same teams' season-z TEI, and the two candidate rescalings implied by that
    disagreed by 2.6x — which is the proof that no single transfer factor exists and the
    reference has to be shared instead.
    """
    rows = df[df["season"] == season]
    moments: dict[str, tuple[float, float]] = {}
    for col in cols:
        if col not in rows.columns:
            continue
        values = pd.to_numeric(rows[col], errors="coerce")
        weights = rows["total_minutes"].astype(float)
        mask = values.notna() & (weights > 0)
        if mask.sum() < 10:
            continue
        mean = float(np.average(values[mask], weights=weights[mask]))
        var = float(np.average((values[mask] - mean) ** 2, weights=weights[mask]))
        moments[col] = (mean, float(np.sqrt(var)) if var > 0 else 1.0)
    return moments


def zscore_against(
    df: pd.DataFrame, moments: dict[str, tuple[float, float]]
) -> pd.DataFrame:
    """Apply a reference season's moments to another frame, so served rows land on the
    same scale the model was fitted on. Columns with no reference are left at 0.0 rather
    than z-scored against the wrong population."""
    for col, (mean, std) in moments.items():
        if col in df.columns:
            values = pd.to_numeric(df[col], errors="coerce")
            df[f"z_{col}"] = ((values - mean) / (std or 1.0)).fillna(0.0)
    return df


def recency_weighted_features(
    df: pd.DataFrame, seasons: list[str], decay: float = 0.7, min_total_minutes: float = 200.0
) -> pd.DataFrame:
    """Collapse multi-season rows to one row per player using minutes x recency weights:

        X_weighted = sum(decay^(s-1) * minutes_s * X_s) / sum(decay^(s-1) * minutes_s)

    where s=1 is the most recent season. Players below min_total_minutes across the
    window are excluded from modeling (insufficient evidence, not imputed).

    **Two quantities are derived AFTER the collapse rather than before it**, because
    shrinking per season and then averaging is not the same operation as shrinking the
    window (R4-1d, R4-2):

    - `def_impact` shrinks the defensive differential on *window* minutes. Shrinking each
      season separately and then averaging loses 42 % of the spread — sd 0.363 against
      0.625 — and moves 298 of 632 players by more than five percentile points, because
      every season is shrunk as though it were the only evidence there is.
    - `fg3_pct_shrunk` shrinks three-point accuracy on *window* attempts. Attempts are
      accumulated as a recency-weighted SUM, not a mean: the shrinkage constant is
      denominated in attempts, and a mean of per-season attempts is a rate, not evidence.
      Measured, a collapsed weighted mean is a median 0.475x the true window total.
    """
    ordered = list(reversed(seasons))  # most recent first
    weight_by_season = {season: decay**i for i, season in enumerate(ordered)}
    df = df[df["season"].isin(seasons)].copy()
    df["season_w"] = df["season"].map(weight_by_season).astype(float)
    df["recency_w"] = df["season_w"] * df["total_minutes"].astype(float)

    # R5-4. Grouped arithmetic rather than a Python loop over players.
    #
    # The loop ran `pd.to_numeric` and `np.average` once per (player, column) — 632 x 19
    # calls, each rebuilding a Series and a boolean mask — and it was the measured
    # cold-cache hotspot of the whole request path at **1.045 s**. Every operation below
    # is the same arithmetic done column-wise: `sum(w*x over the rows where x is present)
    # / sum(w over those same rows)` is exactly what `np.average(x[mask], weights=w[mask])`
    # computes, so this is a rewrite, not an approximation, and
    # `test_feature_collapse_equivalence.py` asserts equality on the real frame.
    keep = df.groupby("player_id").agg(
        total_minutes_window=("total_minutes", "sum"),
        weight_total=("recency_w", "sum"),
        seasons_observed=("season", "size"),
    )
    keep = keep[(keep["weight_total"] > 0) & (keep["total_minutes_window"] >= min_total_minutes)]
    if keep.empty:
        return pd.DataFrame()

    df = df[df["player_id"].isin(keep.index)]
    grouper = df["player_id"]
    weights = df["recency_w"].astype(float)

    # The most recent season's row supplies the identity columns.
    latest = (
        df.sort_values(["player_id", "season"])
        .groupby("player_id")
        .last()[["full_name", "nba_player_id", "height_inches", "position", "is_active"]]
    )
    latest_season = df.groupby("player_id")["season"].max().rename("latest_season")

    window = pd.concat(
        [
            keep[["total_minutes_window", "seasons_observed"]],
            latest,
            latest_season,
            _recency_sums(df, grouper),
        ],
        axis=1,
    )
    for col in (c for c in MODEL_FEATURES if c in df.columns):
        values = pd.to_numeric(df[col], errors="coerce")
        present = values.notna()
        weighted = (values * weights).where(present, 0.0).groupby(grouper).sum()
        denominator = weights.where(present, 0.0).groupby(grouper).sum()
        window[col] = (weighted / denominator.replace(0.0, np.nan)).reindex(window.index)

    window = window.reset_index().rename(columns={"index": "player_id"})
    window["total_minutes_window"] = window["total_minutes_window"].astype(float)
    window["seasons_observed"] = window["seasons_observed"].astype(int)
    return _derive_post_collapse(window)


def _recency_sums(df: pd.DataFrame, grouper: pd.Series) -> pd.DataFrame:
    """Recency-weighted season totals of the three-point counting stats, per player.

    Older evidence counts less, exactly as it does for every other column here, but the
    result stays in *attempts* rather than becoming a rate — which is what a shrinkage
    constant denominated in attempts requires.
    """
    out = {}
    for per_game_col, name in (("FG3A", "fg3a_window"), ("FG3M", "fg3m_window")):
        if per_game_col not in df.columns or "GP" not in df.columns:
            out[name] = pd.Series(np.nan, index=grouper.unique())
            continue
        total = (
            pd.to_numeric(df[per_game_col], errors="coerce")
            * pd.to_numeric(df["GP"], errors="coerce")
            * df["season_w"].astype(float)
        )
        summed = total.groupby(grouper).sum(min_count=1)
        out[name] = summed
    return pd.DataFrame(out)


def _derive_post_collapse(window: pd.DataFrame) -> pd.DataFrame:
    """Quantities that must be shrunk against the WINDOW, not against each season.

    Kept here rather than at either call site so that `train.py` and the request-path
    `evaluation._skills()` cannot diverge — they both collapse through this function, and
    a skill that means one thing at training time and another at serve time is the exact
    failure R3 spent a release removing.
    """
    minutes = pd.to_numeric(window.get("total_minutes_window"), errors="coerce")

    if "def_diff_raw" in window.columns:
        diff = pd.to_numeric(window["def_diff_raw"], errors="coerce")
        window["def_impact"] = diff * (minutes / (minutes + DEF_SHRINKAGE_MINUTES))
    else:
        window["def_impact"] = np.nan

    makes = pd.to_numeric(window.get("fg3m_window"), errors="coerce")
    attempts = pd.to_numeric(window.get("fg3a_window"), errors="coerce")
    shrunk = (makes + FG3_SHRINKAGE_ATTEMPTS * FG3_LEAGUE_MEAN) / (
        attempts + FG3_SHRINKAGE_ATTEMPTS
    )
    # A player with no attempt record at all has no accuracy — the prior alone is the
    # league mean for everybody, which is a constant, and a constant skill is precisely
    # what the no-silent-defaults invariant exists to catch.
    window["fg3_pct_shrunk"] = shrunk.where(attempts.notna() & (attempts > 0))

    window["team_defense_score"] = _weighted_z_composite(
        window, TEAM_DEFENSE_WEIGHTS, minutes
    )
    return window


def _weighted_z_composite(
    window: pd.DataFrame, weights: dict[str, float], minutes: pd.Series
) -> pd.Series:
    """Minutes-weighted z-scores of each term, combined on fixed weights.

    Every term is standardised against the *window population itself*, which is the same
    632-player frame at training time and at serve time because both paths collapse
    through `recency_weighted_features`. A component that is entirely missing is dropped
    and the remaining weights renormalised, so the composite keeps its scale rather than
    quietly shrinking toward zero — the same rule `player_skill_vector.blend` follows.
    """
    total = pd.Series(np.zeros(len(window)), index=window.index, dtype=float)
    used = 0.0
    for col, weight in weights.items():
        if col not in window.columns:
            continue
        values = pd.to_numeric(window[col], errors="coerce")
        mask = values.notna() & minutes.notna() & (minutes > 0)
        if mask.sum() < 10:
            continue
        mean = float(np.average(values[mask], weights=minutes[mask]))
        var = float(np.average((values[mask] - mean) ** 2, weights=minutes[mask]))
        std = np.sqrt(var) if var > 0 else 1.0
        total = total + weight * ((values - mean) / std).fillna(0.0)
        used += abs(weight)
    if used <= 0:
        return pd.Series(np.nan, index=window.index, dtype=float)
    return total * (sum(abs(w) for w in weights.values()) / used)


def build_features(db: Session) -> dict:
    """CLI entry (`make build-features`): materialize the feature frame and report
    coverage so operators can sanity-check before training."""
    from app.config import get_settings

    settings = get_settings()
    season_df = build_player_season_features(db)
    if season_df.empty:
        return {"error": "no ingested player stats; run `make sync-data` first"}
    weighted = recency_weighted_features(season_df, settings.history_season_list)
    return {
        "player_seasons": int(len(season_df)),
        "seasons": sorted(season_df["season"].unique().tolist()),
        "players_with_window_features": int(len(weighted)),
        "feature_columns": [c for c in MODEL_FEATURES if c in season_df.columns],
    }
