"""Rolling-origin backtesting.

For each fold with information cutoff ``t`` (the end of season ``t``):

1. Training pairs are every ``(s -> s+1)`` with ``s+1 <= t``. Their features come from
   ``panel.as_of(s)``; their outcomes are season ``s+1``'s, all of which are already in the past at ``t``.
2. Every transformation and hyper-parameter is fitted on those pairs alone.
3. The scoring population at ``t`` — everyone with at least ``min_origin_minutes`` over the window —
   receives a forecast for season ``t+1``.
4. Only then is season ``t+1``'s outcome attached, and only for players who played at least
   ``min_target_minutes``; that is the evaluation population, and it is the *same* population for
   every model.

Nothing about a fold depends on any season after ``t``; ``leakage_audit`` proves it by scrambling
every later season and checking that the fold's forecasts do not move.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from .intervals import fit_interval, loto_residuals
from .metrics import BOOTSTRAP_DRAWS, paired_cluster_bootstrap, summarise
from .pairs import FeatureConfig, PairCache
from .panel import PlayerSeasonPanel, replace_future, season_key, season_label
from .predictors import FitContext, predictors_for
from .targets import TargetSpec, get_target
from .team_gate import team_gate

SEED = 20260719
REFERENCE_MODELS = ("regressed_persistence_age", "incumbent_tei_recalibrated")


@dataclass(frozen=True)
class Fold:
    origin: str
    target_season: str
    train_origins: tuple[str, ...]


def rolling_origin_folds(seasons: list[str], min_train_transitions: int = 2) -> list[Fold]:
    ordered = sorted(seasons, key=season_key)
    keys = [season_key(s) for s in ordered]
    if keys != list(range(keys[0], keys[0] + len(keys))):
        raise ValueError(f"seasons are not consecutive: {ordered}")
    folds = []
    for i, origin in enumerate(ordered[:-1]):
        train_origins = tuple(ordered[:i])  # origin s trains iff s+1 <= t, i.e. s < t
        if len(train_origins) >= min_train_transitions:
            folds.append(Fold(origin, ordered[i + 1], train_origins))
    return folds


@dataclass(frozen=True)
class BacktestConfig:
    target: str = "impact_proxy"
    models: tuple[str, ...] | None = None
    #: Test origins to run (``None`` = every fold the data supports). The experiment runner uses
    #: this to separate development folds from the locked holdout.
    test_origins: tuple[str, ...] | None = None
    min_train_transitions: int = 2
    interval_level: float = 0.8
    bootstrap_draws: int = BOOTSTRAP_DRAWS
    seed: int = SEED
    reference_models: tuple[str, ...] = REFERENCE_MODELS
    features: FeatureConfig = field(default_factory=FeatureConfig)

    def as_dict(self) -> dict:
        return {
            "target": self.target,
            "models": list(self.models) if self.models else None,
            "test_origins": list(self.test_origins) if self.test_origins else None,
            "min_train_transitions": self.min_train_transitions,
            "interval_level": self.interval_level,
            "bootstrap_draws": self.bootstrap_draws,
            "seed": self.seed,
            "reference_models": list(self.reference_models),
            "features": self.features.as_dict(),
        }


@dataclass
class BacktestResult:
    config: BacktestConfig
    target: TargetSpec
    folds: list[Fold]
    predictions: pd.DataFrame  # every scored player in every fold, every model
    fitted_params: dict  # {fold origin: {model: params}}
    attrition: pd.DataFrame
    summary: dict
    runtime_seconds: float

    def eval_rows(self) -> pd.DataFrame:
        return self.predictions[self.predictions["target_eligible"]]


def _fit_and_predict(
    predictors, train: pd.DataFrame, test: pd.DataFrame, ctx: FitContext, level: float
) -> tuple[list[pd.DataFrame], dict]:
    frames, params = [], {}
    for predictor in predictors:
        fitted = predictor.fit(train, ctx)
        pred = fitted.predict(test)
        interval = fit_interval(loto_residuals(predictor, train, ctx), level)
        lo, hi = interval.apply(pred, test["window_exposure"].to_numpy(dtype=float))
        native = fitted.native_interval(test)
        frame = pd.DataFrame(
            {
                "model": predictor.name,
                "player_id": test["player_id"].to_numpy(),
                "pred": pred,
                "lo": lo,
                "hi": hi,
                "native_lo": native[0] if native else np.nan,
                "native_hi": native[1] if native else np.nan,
            }
        )
        frames.append(frame)
        params[predictor.name] = {"fitted": fitted.params, "interval": interval.params()}
    return frames, params


def run_backtest(
    panel: PlayerSeasonPanel,
    config: BacktestConfig,
    standings: pd.DataFrame | None = None,
    cache: PairCache | None = None,
) -> BacktestResult:
    started = time.perf_counter()
    target = get_target(config.target)
    cache = cache or PairCache(panel, config.features)
    predictors = predictors_for(target, list(config.models) if config.models else None)
    ctx = FitContext(target=target, seed=config.seed)

    all_folds = rolling_origin_folds(panel.seasons, config.min_train_transitions)
    folds = [f for f in all_folds if config.test_origins is None or f.origin in config.test_origins]
    if config.test_origins:
        missing = set(config.test_origins) - {f.origin for f in folds}
        if missing:
            raise ValueError(f"requested test origins the data cannot support: {sorted(missing)}")

    prediction_parts, attrition, fitted_params = [], [], {}
    for fold in folds:
        train_all = pd.concat([cache.pairs(s, target) for s in fold.train_origins], ignore_index=True)
        # A training pair's outcome must be observable at the cutoff.
        assert (train_all["target_season"].map(season_key) <= season_key(fold.origin)).all()
        train = train_all[train_all["target_eligible"]].reset_index(drop=True)
        test = cache.pairs(fold.origin, target).reset_index(drop=True)
        frames, params = _fit_and_predict(predictors, train, test, ctx, config.interval_level)
        fitted_params[fold.origin] = params
        meta_cols = [
            "player_id", "origin", "target_season", "age", "origin_minutes", "window_minutes",
            "window_exposure", "team_id", "prior_seasons", "new_to_corpus", "has_origin_row", "z_REB_PCT",
            "y", "y_exposure", "y_minutes", "y_team", "target_observed", "target_eligible",
        ]
        meta = test[meta_cols]
        for frame in frames:
            prediction_parts.append(frame.merge(meta, on="player_id", how="left", validate="m:1"))
        attrition.append(
            {
                "origin": fold.origin,
                "target_season": fold.target_season,
                "train_origins": len(fold.train_origins),
                "train_pairs": int(len(train)),
                "scored": int(len(test)),
                "target_observed": int(test["target_observed"].sum()),
                "evaluated": int(test["target_eligible"].sum()),
            }
        )

    predictions = pd.concat(prediction_parts, ignore_index=True) if prediction_parts else pd.DataFrame()
    attrition_df = pd.DataFrame(attrition)
    if not attrition_df.empty:
        attrition_df["share_evaluated_of_scored"] = attrition_df["evaluated"] / attrition_df["scored"]
    summary = _summarise(predictions, config, standings) if not predictions.empty else {}
    return BacktestResult(
        config=config,
        target=target,
        folds=folds,
        predictions=predictions,
        fitted_params=fitted_params,
        attrition=attrition_df,
        summary=summary,
        runtime_seconds=round(time.perf_counter() - started, 2),
    )


# ------------------------------------------------------------------------------ summaries


def paired_comparison(
    rows: pd.DataFrame, model: str, reference: str, draws: int = BOOTSTRAP_DRAWS, seed: int = SEED
) -> dict | None:
    """MAE(model) - MAE(reference) on identical rows, with a player-cluster bootstrap interval.

    Negative = ``model`` is better. ``rows`` must be evaluation rows (``target_eligible``)."""
    key = ["origin", "player_id"]
    ref_rows = rows[rows["model"] == reference].set_index(key)
    other = rows[rows["model"] == model].set_index(key)
    idx = ref_rows.index.intersection(other.index)
    if len(idx) == 0:
        return None
    a, b = other.loc[idx], ref_rows.loc[idx]
    loss_a = np.abs(a["y"].to_numpy() - a["pred"].to_numpy())
    loss_b = np.abs(b["y"].to_numpy() - b["pred"].to_numpy())
    players = idx.get_level_values("player_id").to_numpy()
    result = paired_cluster_bootstrap(loss_a, loss_b, players, draws, seed)
    origins = idx.get_level_values("origin")
    per_fold = {o: float(np.mean((loss_a - loss_b)[origins == o])) for o in sorted(set(origins))}
    result["per_fold_mean_diff"] = per_fold
    result["folds_improved"] = int(sum(v < 0 for v in per_fold.values()))
    result["folds"] = len(per_fold)
    result["model"], result["reference"] = model, reference
    result["metric"] = "MAE difference (model - reference); negative = model better"
    return result


def subgroup_masks(rows: pd.DataFrame) -> dict[str, pd.Series]:
    """Evaluation subgroups, defined from information at the cutoff — except ``changed_team``,
    which uses the outcome season's team and is therefore a *reporting* stratifier only."""
    return {
        "all": pd.Series(True, index=rows.index),
        "age_le_22": rows["age"] <= 22,
        "age_ge_31": rows["age"] >= 31,
        "new_to_corpus": rows["new_to_corpus"].astype(bool),
        "low_minutes_window_lt_1000": rows["window_minutes"] < 1000,
        "high_minutes_window_ge_3000": rows["window_minutes"] >= 3000,
        "changed_team": rows["team_id"].astype(str) != rows["y_team"].astype(str),
        "big_man_proxy_top_quartile_reb_pct": rows["z_REB_PCT"] >= rows["z_REB_PCT"].quantile(0.75),
    }


def _summarise(
    predictions: pd.DataFrame, config: BacktestConfig, standings: pd.DataFrame | None
) -> dict:
    rows = predictions[predictions["target_eligible"]].copy()
    models = list(dict.fromkeys(rows["model"]))
    summary: dict = {"models": models, "overall": {}, "by_fold": {}, "by_subgroup": {}, "comparisons": {}}

    for model in models:
        m = rows[rows["model"] == model]
        summary["overall"][model] = summarise(m)
        if m["native_lo"].notna().all():
            native = m.assign(lo=m["native_lo"], hi=m["native_hi"])
            summary["overall"][model]["native_interval"] = {
                k: v for k, v in summarise(native).items() if k in ("coverage", "mean_width")
            }
        summary["by_fold"][model] = {
            str(o): summarise(g) for o, g in m.groupby("origin")
        }
        masks = subgroup_masks(m)
        summary["by_subgroup"][model] = {
            name: summarise(m[mask]) for name, mask in masks.items() if int(mask.sum()) >= 15
        }

    # Paired comparisons on identical rows: every model against each reference model.
    for ref in config.reference_models:
        if ref not in models:
            continue
        for model in models:
            if model == ref:
                continue
            result = paired_comparison(rows, model, ref, config.bootstrap_draws, config.seed)
            if result is not None:
                summary["comparisons"][f"{model} vs {ref}"] = result

    if standings is not None:
        summary["team_gate"] = {m: team_gate(predictions, standings, m) for m in models}
    return summary


# ------------------------------------------------------------------------------ leakage audit


def leakage_audit(
    panel: PlayerSeasonPanel,
    config: BacktestConfig,
    origin: str,
    cache_factory=PairCache,
    seed: int = 7,
) -> dict:
    """Prove a fold reads nothing after its cutoff.

    Runs the fold on the real panel and on a copy in which every statistic of every later season
    has been scrambled. Forecasts and intervals must match to the last bit. If a feature builder or
    model reads the future, the two runs disagree and the audit fails."""
    narrow = replace(config, test_origins=(origin,))
    real = run_backtest(panel, narrow, cache=cache_factory(panel, config.features))
    scrambled_panel = replace_future(panel, origin, np.random.default_rng(seed))
    fake = run_backtest(
        scrambled_panel, narrow, cache=cache_factory(scrambled_panel, config.features)
    )
    cols = ["model", "player_id", "pred", "lo", "hi"]
    a = real.predictions[cols].sort_values(["model", "player_id"]).reset_index(drop=True)
    b = fake.predictions[cols].sort_values(["model", "player_id"]).reset_index(drop=True)
    identical_index = a[["model", "player_id"]].equals(b[["model", "player_id"]])
    max_diff = (
        float(np.nanmax(np.abs(a[["pred", "lo", "hi"]].to_numpy() - b[["pred", "lo", "hi"]].to_numpy())))
        if identical_index and len(a)
        else float("inf")
    )
    return {
        "origin": origin,
        "scrambled_seasons": [
            season_label(y)
            for y in range(season_key(origin) + 1, season_key(panel.seasons[-1]) + 1)
        ],
        "rows_compared": int(len(a)),
        "same_scored_players": bool(identical_index),
        "max_abs_difference": max_diff,
        "passed": bool(identical_index and max_diff == 0.0),
    }
