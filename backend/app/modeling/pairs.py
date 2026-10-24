"""Origin features and (origin, target-season) pairs.

For an information cutoff ``t`` this builds, for every player Pivot would score at ``t``, the
features known at the end of season ``t`` and — separately, and only when asked — the outcome in
season ``t+1``. The two never share a code path: ``origin_features`` takes an ``AsOfPanel`` and has
no access to later seasons; ``attach_outcomes`` takes the *target season's rows* and returns only
the outcome columns.

The incumbent TEI at the cutoff is computed by **the production functions, unchanged**
(``recency_weighted_features`` → ``score_players``), fed an as-of frame. That is deliberate: the
backtest's "existing Pivot estimate" is then not a re-implementation of the incumbent but the
incumbent itself, which `tests/unit/test_modeling_incumbent.py` pins against stored active estimates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from app.analytics.features import recency_weighted_features, zscore_by_season
from app.analytics.impact import add_zscores, score_players, train_impact_models

from .panel import AsOfPanel, PlayerSeasonPanel, season_key, season_label
from .targets import TargetSpec, measure_by_player

#: The incumbent's index features plus the other columns the production z-scoring covers.
Z_FEATURES = (
    "pts_per75", "TS_PCT", "AST_PCT", "TM_TOV_PCT", "USG_PCT", "OREB_PCT", "DREB_PCT",
    "stl_per_min", "blk_per_min", "PIE", "NET_RATING", "fg3a_rate", "fta_rate", "MIN",
)


DEFAULT_HISTORY = 10


@dataclass(frozen=True)
class FeatureConfig:
    window: int = 3
    decay: float = 0.7
    min_origin_minutes: float = 200.0
    min_target_minutes: float = 500.0
    #: R9.2. Seasons of per-player history (counting the cutoff season) exposed as ``x_lag*`` /
    #: ``exposure_lag*`` / ``sv_lag*`` columns for models that read more than the population window.
    #: It does NOT change who is scored or how ``x_bar`` is built, so R9.1 models are unaffected.
    history: int = DEFAULT_HISTORY

    def as_dict(self) -> dict:
        out = asdict(self)
        # ``history`` only adds columns that R9.1 models never read; leaving the default out keeps every
        # R9.1 configuration hash (and so every stored candidate version id) exactly as it was.
        if out["history"] == DEFAULT_HISTORY:
            del out["history"]
        return out


def _legacy_view(frame: pd.DataFrame) -> pd.DataFrame:
    """Give the production feature collapse the identity columns it reads but never uses.

    ``recency_weighted_features`` carries ``is_active`` / ``position`` / ``height_inches`` through
    as identity metadata. They are stripped from every as-of view (they describe the present), so
    they are re-added here as empty columns, only for the incumbent adapter, and are not read."""
    out = frame.copy()
    for col in ("is_active", "position", "height_inches"):
        if col not in out.columns:
            out[col] = None
    return out


def incumbent_scores(asof: AsOfPanel, cfg: FeatureConfig) -> pd.DataFrame:
    """Production TEI at the cutoff: ``player_id, tei_window, tei_low, tei_high``."""
    seasons = asof.window(cfg.window)
    window_frame = _legacy_view(asof.frame[asof.frame["season"].isin(seasons)])
    result = train_impact_models(window_frame, seasons)
    weighted = recency_weighted_features(
        window_frame, seasons, decay=cfg.decay, min_total_minutes=cfg.min_origin_minutes
    )
    if weighted.empty:
        return pd.DataFrame(columns=["player_id", "tei_window", "tei_low", "tei_high"])
    scored = score_players(weighted, result, window_frame)
    return scored[["player_id", "tei", "tei_low", "tei_high"]].rename(columns={"tei": "tei_window"})


def origin_features(
    asof: AsOfPanel, target: TargetSpec, cfg: FeatureConfig, target_season: str | None = None
) -> pd.DataFrame:
    """One row per player Pivot would score at the cutoff, with features known at the cutoff."""
    frame = asof.frame
    origin = asof.origin
    seasons = asof.window(cfg.window)
    o_key = season_key(origin)

    per_season: dict[int, pd.DataFrame] = {}
    for season in seasons:
        rows = frame[frame["season"] == season]
        per_season[o_key - season_key(season)] = measure_by_player(target, rows).set_index("player_id")

    # The scoring population is everyone with evidence in the window — including a player who missed
    # the whole origin season (injured, say) but has earlier seasons in it. Production scores such a
    # player, and a forecaster that cannot is blind to the returners it will be asked about.
    in_window = frame[frame["season"].isin(seasons)].copy()
    in_window["_key"] = in_window["season"].map(season_key)
    latest = in_window.sort_values("_key").groupby("player_id").tail(1).set_index("player_id")
    rows_o = frame[frame["season"] == origin].copy()
    has_origin = set(rows_o["player_id"])
    feats = pd.DataFrame(
        {
            "origin": origin,
            "target_season": target_season or season_label(o_key + 1),
            # Age at the cutoff: the last observed age plus the seasons since.
            "age": pd.to_numeric(latest["AGE"], errors="coerce") + (o_key - latest["_key"]),
            "team_id": latest["team_id"],
            "has_origin_row": latest.index.isin(has_origin),
        },
        index=latest.index,
    )
    feats["origin_minutes"] = rows_o.set_index("player_id")["total_minutes"].reindex(feats.index).fillna(0.0)

    current = per_season[0]
    feats["x_t"] = current["value"].reindex(feats.index)
    feats["exposure_t"] = current["exposure"].reindex(feats.index)
    for lag in (1, 2):
        series = per_season.get(lag)
        feats[f"x_lag{lag}"] = series["value"].reindex(feats.index) if series is not None else np.nan

    # R9.2: a longer per-player history for models that opt in (lags 0..history-1). Who is scored,
    # and x_bar / window_exposure below, still come from the ``window`` seasons only.
    hist_seasons = asof.window(max(cfg.window, cfg.history))
    for season in hist_seasons:
        lag = o_key - season_key(season)
        if lag >= cfg.history:
            continue
        rows_s = frame[frame["season"] == season]
        series = per_season[lag] if lag in per_season else measure_by_player(target, rows_s).set_index("player_id")
        if lag >= 3:
            feats[f"x_lag{lag}"] = series["value"].reindex(feats.index)
        if lag >= 1:
            feats[f"exposure_lag{lag}"] = series["exposure"].reindex(feats.index)
        if target.sampling_variance is not None and _has_columns(rows_s, target):
            sv = pd.Series(target.sampling_variance(rows_s).to_numpy(dtype=float), index=rows_s["player_id"].to_numpy())
            feats["sv_t" if lag == 0 else f"sv_lag{lag}"] = sv.reindex(feats.index)
    for lag in range(1, cfg.history):
        for col in (f"x_lag{lag}", f"exposure_lag{lag}", f"sv_lag{lag}"):
            if col not in feats:
                feats[col] = np.nan
    if "sv_t" not in feats:
        feats["sv_t"] = np.nan

    # Recency- and exposure-weighted window mean, and the total evidence behind it.
    num = pd.Series(0.0, index=feats.index)
    den = pd.Series(0.0, index=feats.index)
    window_exposure = pd.Series(0.0, index=feats.index)
    window_minutes = pd.Series(0.0, index=feats.index)
    minutes_by_k = {
        o_key - season_key(s): frame[frame["season"] == s].set_index("player_id")["total_minutes"]
        for s in seasons
    }
    for k, series in per_season.items():
        v = series["value"].reindex(feats.index)
        e = series["exposure"].reindex(feats.index)
        ok = v.notna() & e.notna() & (e > 0)
        w = (cfg.decay**k) * e.where(ok, 0.0)
        num += (w * v.where(ok, 0.0))
        den += w
        window_exposure += e.where(e.notna(), 0.0)
        window_minutes += minutes_by_k[k].reindex(feats.index).fillna(0.0)
    feats["x_bar"] = num / den.replace(0.0, np.nan)
    feats["window_exposure"] = window_exposure
    feats["window_minutes"] = window_minutes

    prior = frame[frame["season"].map(season_key) < o_key].groupby("player_id")["season"].nunique()
    feats["prior_seasons"] = prior.reindex(feats.index).fillna(0).astype(int)
    # "New to the corpus": no earlier season in the data. Only meaningful after the first season.
    feats["new_to_corpus"] = (feats["prior_seasons"] == 0) & (o_key > min(season_key(s) for s in asof.seasons))

    z = add_zscores(rows_o.copy()).set_index("player_id")
    for col in Z_FEATURES:
        # NaN (not 0) for a player with no origin-season row: 0 would assert "league average".
        feats[f"z_{col}"] = pd.to_numeric(z[f"z_{col}"], errors="coerce").reindex(feats.index) if f"z_{col}" in z else np.nan

    # Context for a stratified report (frontcourt proxy), not a model input: derived from the origin
    # season's own rebounding share, so it is known at the cutoff.
    feats["z_REB_PCT"] = (
        zscore_by_season(rows_o.copy(), "REB_PCT").set_index("player_id")["z_REB_PCT"].reindex(feats.index)
    )

    inc = incumbent_scores(asof, cfg).set_index("player_id")
    for col in ("tei_window", "tei_low", "tei_high"):
        feats[col] = inc[col].reindex(feats.index)

    out = feats.reset_index().rename(columns={"index": "player_id"})
    # The scoring population: the production eligibility rule (>= 200 window minutes), applied
    # to information available at the cutoff.
    return out[out["window_minutes"] >= cfg.min_origin_minutes].reset_index(drop=True)


def _has_columns(rows: pd.DataFrame, target: TargetSpec) -> bool:
    """A sampling-variance model needs the shot-count columns; a panel without them (R9.1's) has none."""
    needed = {"ts_pct": ("fg2m", "fg2a", "fg3m", "fg3a", "ftm", "fta", "tsa"), "fg3_pct": ("fg3m", "fg3a"), "ft_pct": ("ftm", "fta")}
    return all(c in rows.columns for c in needed.get(target.name, ()))


def attach_outcomes(
    features: pd.DataFrame, target_rows: pd.DataFrame, target: TargetSpec, cfg: FeatureConfig
) -> pd.DataFrame:
    """Join the target season's outcome onto an origin's features. Evaluation / training only."""
    measured = measure_by_player(target, target_rows).set_index("player_id")
    minutes = target_rows.set_index("player_id")["total_minutes"]
    team = target_rows.set_index("player_id")["team_id"]
    out = features.copy()
    pid = out["player_id"]
    out["y"] = measured["value"].reindex(pid).to_numpy()
    out["y_exposure"] = measured["exposure"].reindex(pid).to_numpy()
    # R9.2: the sampling variance of the outcome itself (outcome side only; used to fit and score
    # models that separate sampling noise from change in ability, never as a feature).
    if target.sampling_variance is not None and _has_columns(target_rows, target):
        sv = pd.Series(target.sampling_variance(target_rows).to_numpy(dtype=float), index=target_rows["player_id"].to_numpy())
        out["y_sv"] = sv.reindex(pid).to_numpy()
    else:
        out["y_sv"] = np.nan
    out["y_minutes"] = minutes.reindex(pid).to_numpy()
    out["y_team"] = team.reindex(pid).to_numpy()
    out["target_observed"] = out["y_minutes"].notna()
    out["target_eligible"] = (
        out["target_observed"]
        & out["y"].notna()
        & (out["y_minutes"] >= cfg.min_target_minutes)
        & (out["y_exposure"].fillna(0.0) >= target.min_exposure)
    )
    return out


#: Columns that describe the OUTCOME season. A predictor is fitted on rows that carry them (the training pairs) but is
#: handed a frame WITHOUT them to forecast, so a careless predictor cannot peek at the answer — the leakage audit
#: should not be the only thing standing between a model and the outcome it is scored against.
OUTCOME_COLUMNS = ("y", "y_exposure", "y_minutes", "y_team", "y_sv", "target_observed", "target_eligible")


def features_only(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.drop(columns=[c for c in OUTCOME_COLUMNS if c in frame.columns])



class PairCache:
    """Per-experiment cache of origin features and outcomes, keyed by (origin, target).

    Features for an origin are computed once from ``panel.as_of(origin)`` and reused by every fold
    and every model, so all models see byte-identical inputs (the 'same folds' requirement)."""

    def __init__(self, panel: PlayerSeasonPanel, cfg: FeatureConfig) -> None:
        self.panel = panel
        self.cfg = cfg
        self._features: dict[tuple[str, str], pd.DataFrame] = {}
        self._pairs: dict[tuple[str, str], pd.DataFrame] = {}

    def features(self, origin: str, target: TargetSpec) -> pd.DataFrame:
        key = (origin, target.name)
        if key not in self._features:
            self._features[key] = origin_features(self.panel.as_of(origin), target, self.cfg)
        return self._features[key]

    def pairs(self, origin: str, target: TargetSpec) -> pd.DataFrame:
        """Features at ``origin`` with the next season's outcome attached (if that season exists)."""
        key = (origin, target.name)
        if key not in self._pairs:
            nxt = season_label(season_key(origin) + 1)
            feats = self.features(origin, target)
            if nxt in self.panel.seasons:
                self._pairs[key] = attach_outcomes(feats, self.panel.season_rows(nxt), target, self.cfg)
            else:
                self._pairs[key] = feats.assign(
                    y=np.nan, y_exposure=np.nan, y_minutes=np.nan, y_team=None, y_sv=np.nan,
                    target_observed=False, target_eligible=False,
                )
        return self._pairs[key]
