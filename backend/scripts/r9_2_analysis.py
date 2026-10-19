"""R9.2 supplementary analyses over the DEVELOPMENT folds (never the holdout).

    python -m scripts.r9_2_analysis --db sqlite:///copy.db --out docs/r9-2/results/shooting_analysis.json

Reads the database read-only, re-fits each model exactly as the backtest does (same pairs, same
training window per fold) and reports what the headline MAE cannot:

* **posterior predictive check** — the standardised error of next season's observed rate under the
  model's predictive distribution *given the attempts actually taken*, overall and by attempts bin;
* **reliability by attempts** — how much of the spread in true ability one season of evidence resolves,
  from the fitted variance components at the last development cutoff;
* **stability** — year-over-year correlation of the measured rates, and how well the ability estimate
  correlates with next season's observed rate compared with the raw rate;
* **participation** — of the players scored at a cutoff, how many meet the evaluation condition next
  season, by how much evidence they have (the selection effect behind every conditional forecast).

The holdout folds are not touched here: a holdout is read once, by ``backtest holdout``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modeling.experiment import R92_PROTOCOL, split_origins
from app.modeling.pairs import FeatureConfig, PairCache
from app.modeling.panel import season_key
from app.modeling.predictors import PREDICTORS, FitContext
from app.modeling.targets import get_target
from app.player_intelligence.panel import build_skill_panel

QUANTITIES = ("ts_pct", "fg3_pct", "ft_pct")
ATTEMPT_BINS = [0, 100, 200, 400, 800, 10_000]
LEVELS = (0.5, 0.8, 0.9)


def _bin(values: pd.Series, edges: list[int]) -> pd.Series:
    return pd.cut(values, edges, right=False).astype(str)


def predictive_check(panel, quantity: str, method: str, dev_origins: list[str]) -> dict:
    target = get_target(quantity)
    cache = PairCache(panel, FeatureConfig())
    rows = []
    for origin in dev_origins:
        train_origins = [s for s in panel.seasons if season_key(s) < season_key(origin)]
        train = pd.concat([cache.pairs(s, target) for s in train_origins], ignore_index=True)
        train = train[train["target_eligible"]].reset_index(drop=True)
        fitted = PREDICTORS[method].fit(train, FitContext(target=target))
        test = cache.pairs(origin, target).reset_index(drop=True)
        test = test[test["target_eligible"]].reset_index(drop=True)
        post = fitted.posterior(test)
        p = fitted.p
        sd = np.sqrt(post.var_next + p.phi * test["y_sv"].to_numpy(dtype=float))
        z = (test["y"].to_numpy(dtype=float) - post.level_next) / sd
        rows.append(pd.DataFrame({
            "origin": origin, "z": z, "attempts": test["y_exposure"].to_numpy(dtype=float),
            "window_exposure": test["window_exposure"].to_numpy(dtype=float),
            "ability_sd": np.sqrt(post.var_next), "sampling_sd": np.sqrt(p.phi * test["y_sv"].to_numpy(dtype=float)),
        }))
    d = pd.concat(rows, ignore_index=True)
    out: dict = {"n": int(len(d)), "mean_z": float(d["z"].mean()), "sd_z": float(d["z"].std(ddof=1))}
    out["coverage"] = {str(level): float((np.abs(d["z"]) < stats.norm.ppf(0.5 + level / 2)).mean()) for level in LEVELS}
    d["bin"] = _bin(d["attempts"], ATTEMPT_BINS)
    out["by_target_attempts"] = {
        b: {"n": int(len(g)), "coverage_80": float((np.abs(g["z"]) < stats.norm.ppf(0.9)).mean()), "sd_z": float(g["z"].std(ddof=1))}
        for b, g in d.groupby("bin") if len(g) >= 20
    }
    d["pbin"] = _bin(d["window_exposure"], ATTEMPT_BINS)
    out["by_prior_attempts"] = {
        b: {"n": int(len(g)), "coverage_80": float((np.abs(g["z"]) < stats.norm.ppf(0.9)).mean()), "sd_z": float(g["z"].std(ddof=1))}
        for b, g in d.groupby("pbin") if len(g) >= 20
    }
    out["by_fold_coverage_80"] = {str(o): float((np.abs(g["z"]) < stats.norm.ppf(0.9)).mean()) for o, g in d.groupby("origin")}
    share_ability = float((d["ability_sd"] ** 2 / (d["ability_sd"] ** 2 + d["sampling_sd"] ** 2)).mean())
    out["mean_share_of_forecast_variance_from_ability_uncertainty"] = share_ability
    return out


def reliability_curve(panel, quantity: str, method: str, last_dev_origin: str) -> dict:
    target = get_target(quantity)
    cache = PairCache(panel, FeatureConfig())
    train_origins = [s for s in panel.seasons if season_key(s) < season_key(last_dev_origin)]
    train = pd.concat([cache.pairs(s, target) for s in train_origins], ignore_index=True)
    train = train[train["target_eligible"]].reset_index(drop=True)
    fitted = PREDICTORS[method].fit(train, FitContext(target=target))
    p = fitted.p
    unit = p.unit_variance
    out = {"params": p.as_dict(), "cutoff": last_dev_origin, "one_season_reliability_by_attempts": {}}
    for n in (25, 50, 100, 200, 400, 800, 1600):
        post_var = 1.0 / (1.0 / p.tau2 + n / (p.phi * unit))
        out["one_season_reliability_by_attempts"][str(n)] = {
            "reliability": float(1 - post_var / p.tau2), "posterior_sd": float(np.sqrt(post_var)),
            "prior_equivalent_attempts": float(p.phi * unit / p.tau2),
        }
    return out


def stability(panel, quantity: str, min_attempts: float = 100.0) -> dict:
    target = get_target(quantity)
    frame = panel.frame
    parts = []
    for season in panel.seasons:
        rows = frame[frame["season"] == season]
        v = target.measure(rows).to_numpy(dtype=float)
        e = target.exposure(rows).to_numpy(dtype=float)
        parts.append(pd.DataFrame({"player_id": rows["player_id"].to_numpy(), "season": season, "x": v, "n": e}))
    d = pd.concat(parts, ignore_index=True)
    d["key"] = d["season"].map(season_key)
    a = d.rename(columns={"x": "x0", "n": "n0"}).assign(key=lambda t: t["key"] + 1)[["player_id", "key", "x0", "n0"]]
    b = d[["player_id", "key", "x", "n"]]
    m = a.merge(b, on=["player_id", "key"])
    m = m[(m["n0"] >= min_attempts) & (m["n"] >= min_attempts)].dropna(subset=["x0", "x"])
    return {
        "min_attempts_both_seasons": min_attempts, "n_pairs": int(len(m)),
        "pearson_year_over_year": float(np.corrcoef(m["x0"], m["x"])[0, 1]),
        "spearman_year_over_year": float(stats.spearmanr(m["x0"], m["x"]).statistic),
    }


def ability_vs_raw(panel, quantity: str, method: str, dev_origins: list[str]) -> dict:
    """Does the ability estimate rank next season's outcomes better than last season's raw rate?"""
    target = get_target(quantity)
    cache = PairCache(panel, FeatureConfig())
    res = {"folds": {}}
    for origin in dev_origins:
        train_origins = [s for s in panel.seasons if season_key(s) < season_key(origin)]
        train = pd.concat([cache.pairs(s, target) for s in train_origins], ignore_index=True)
        train = train[train["target_eligible"]].reset_index(drop=True)
        fitted = PREDICTORS[method].fit(train, FitContext(target=target))
        test = cache.pairs(origin, target).reset_index(drop=True)
        test = test[test["target_eligible"] & test["x_t"].notna()].reset_index(drop=True)
        post = fitted.posterior(test)
        y = test["y"].to_numpy(dtype=float)
        res["folds"][origin] = {
            "n": int(len(test)),
            "spearman_ability_vs_next": float(stats.spearmanr(post.level_now, y).statistic),
            "spearman_raw_last_vs_next": float(stats.spearmanr(test["x_t"], y).statistic),
        }
    f = res["folds"].values()
    res["mean_spearman_ability_vs_next"] = float(np.mean([v["spearman_ability_vs_next"] for v in f]))
    res["mean_spearman_raw_last_vs_next"] = float(np.mean([v["spearman_raw_last_vs_next"] for v in f]))
    return res


def participation(panel, quantity: str, dev_origins: list[str]) -> dict:
    """Of the players scored at a cutoff, who meets the evaluation condition the following season?"""
    target = get_target(quantity)
    cache = PairCache(panel, FeatureConfig())
    parts = [cache.pairs(o, target) for o in dev_origins]
    d = pd.concat(parts, ignore_index=True)
    d["bin"] = _bin(d["window_exposure"], ATTEMPT_BINS)
    out = {
        "scored": int(len(d)), "appears_next_season": float(d["target_observed"].mean()),
        "meets_evaluation_condition": float(d["target_eligible"].mean()),
        "by_prior_attempts": {
            b: {"n": int(len(g)), "appears": float(g["target_observed"].mean()), "meets_condition": float(g["target_eligible"].mean())}
            for b, g in d.groupby("bin")
        },
    }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    engine = create_engine(args.db)
    with Session(engine) as db:
        panel = build_skill_panel(db)
    dev, _ = split_origins(panel, R92_PROTOCOL)
    method = {"ts_pct": "state_space", "fg3_pct": "state_space", "ft_pct": "state_space"}
    report = {"protocol": R92_PROTOCOL.version, "development_origins": dev, "note": "development folds only; the holdout is not read here", "quantities": {}}
    for q in QUANTITIES:
        report["quantities"][q] = {
            "method": method[q],
            "predictive_check": predictive_check(panel, q, method[q], dev),
            "reliability": reliability_curve(panel, q, method[q], dev[-1]),
            "stability_of_measured_rate": stability(panel, q, 100.0 if q != "ts_pct" else 300.0),
            "ability_vs_raw": ability_vs_raw(panel, q, method[q], dev),
            "participation": participation(panel, q, dev),
        }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
