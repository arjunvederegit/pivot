"""R9.2 — a scoped study of availability: does a model forecast *appearing* and *games played* better than persistence?

    python -m scripts.r9_2_availability_study --db sqlite:///copy.db --out docs/r9-2/results/availability_study.json

Availability is the largest unmodelled uncertainty in R9.1's findings: about 46% of the players scored at a
cutoff do not meet the next-season rotation condition, and a third do not appear at all. A conditional
shooting forecast says nothing about that. This study asks three separate questions, each scored on the
**same rolling-origin development folds** as the shooting models (origins 2018-19..2022-23; the holdout is not read):

1. **Does the player appear next season?** (>= 1 game.) Logistic regression on his own history vs the base rate
   and a "played last season" rule. Scored by Brier score, log loss and calibration by decile.
2. **Given he appears, what share of his team's games does he play?** OLS on history vs the league mean,
   persistence, and Pivot's existing decayed-GP availability (``analytics.availability``).
3. **Total future availability** (zero if absent) = P(appear) x E[share | appear], vs persistence.

Season length varies (shortened 2019-20 / 2020-21; teams played different numbers of games), so the denominator
is the player's team's own games (wins + losses in the standings, every season; checked against ``team_season_stats.GP``
where that exists). Injury records are *current only* and are
never used: using them would reconstruct knowledge that did not exist at the cutoff.

This is an **analysis, not a served model**: nothing here is stored or exposed by the service.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize, special, stats
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.analytics.availability import availability_from_history
from app.db.models import Standing, TeamSeasonStats
from app.modeling.experiment import R92_PROTOCOL, split_origins
from app.modeling.pairs import FeatureConfig, PairCache
from app.modeling.panel import season_key
from app.modeling.targets import get_target
from app.player_intelligence.panel import build_skill_panel


def add_share(frame: pd.DataFrame, db: Session) -> tuple[pd.DataFrame, dict]:
    """Games played as a share of the player's team's games (wins + losses from the standings, every season).

    A traded player's row carries his *last* team and his total games, so his share is clipped at 1."""
    standings = pd.DataFrame(
        [(s.team_id, s.season, s.wins + s.losses) for s in db.scalars(select(Standing)).all()], columns=["team_id", "season", "team_games"]
    )
    f = frame.merge(standings, on=["team_id", "season"], how="left")
    f["team_games_proxy"] = f.groupby(["season", "team_id"])["GP"].transform("max")  # the earlier proxy, kept for the check
    check_rows = db.execute(
        select(TeamSeasonStats.team_id, TeamSeasonStats.season, TeamSeasonStats.stats).where(TeamSeasonStats.stat_type == "base", TeamSeasonStats.season_type == "regular")
    ).all()
    actual = pd.DataFrame([{"team_id": r.team_id, "season": r.season, "gp": (r.stats or {}).get("GP")} for r in check_rows])
    m = standings.merge(actual, on=["team_id", "season"]).dropna()
    missing = int(f["team_games"].isna().sum())
    f["team_games"] = f["team_games"].fillna(f["team_games_proxy"])
    f["gp_share"] = (f["GP"] / f["team_games"]).clip(upper=1.0)
    return f, {
        "denominator": "team wins + losses from the standings",
        "rows_without_standings_fell_back_to_max_teammate_gp": missing,
        "standings_vs_team_season_stats_GP": {"team_seasons": int(len(m)), "share_equal": float((m["team_games"] == m["gp"]).mean())} if len(m) else None,
    }


def features_at(frame: pd.DataFrame, pop: pd.DataFrame, origin: str, nxt: str) -> pd.DataFrame:
    """Availability features at ``origin`` for the scored population ``pop``, with next-season outcomes."""
    o = season_key(origin)
    by = {k: frame[frame["season"] == s].set_index("player_id") for k, s in ((0, origin), (1, f"{o - 1}-{(o) % 100:02d}"))}
    x = pop[["player_id", "age", "window_minutes", "prior_seasons", "has_origin_row"]].copy().set_index("player_id")
    x["share_t"] = by[0]["gp_share"].reindex(x.index)
    x["share_lag1"] = by[1]["gp_share"].reindex(x.index)
    by2 = frame[frame["season"] == f"{o - 2}-{(o - 1) % 100:02d}"].set_index("player_id")
    x["share_lag2"] = by2["gp_share"].reindex(x.index)
    x["min_pg_t"] = by[0]["MIN"].reindex(x.index)
    nrows = frame[frame["season"] == nxt].set_index("player_id")
    x["appears"] = x.index.isin(nrows.index[nrows["GP"] >= 1]).astype(float)
    x["share_next"] = nrows["gp_share"].reindex(x.index)
    x["origin"] = origin
    return x.reset_index()


def design(df: pd.DataFrame) -> np.ndarray:
    age = df["age"].fillna(27.0).to_numpy() - 27.0
    s0 = df["share_t"].fillna(0.0).to_numpy()
    s1 = df["share_lag1"].fillna(0.0).to_numpy()
    s2 = df["share_lag2"].fillna(0.0).to_numpy()
    return np.column_stack([
        np.ones(len(df)), s0, s1, s2, df["share_t"].isna().to_numpy(float), np.log1p(df["window_minutes"].to_numpy()),
        age, age**2, df["prior_seasons"].to_numpy(float).clip(max=6),
    ])


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0) -> np.ndarray:
    def nll(b: np.ndarray) -> float:
        z = X @ b
        return float(np.sum(np.logaddexp(0, z) - y * z) + 0.5 * l2 * (b[1:] @ b[1:]))

    def grad(b: np.ndarray) -> np.ndarray:
        g = X.T @ (special.expit(X @ b) - y)
        g[1:] += l2 * b[1:]
        return g

    return optimize.minimize(nll, np.zeros(X.shape[1]), jac=grad, method="L-BFGS-B").x


def calibration_bins(p: np.ndarray, y: np.ndarray, k: int = 10) -> list[dict]:
    order = np.argsort(p)
    out = []
    for chunk in np.array_split(order, k):
        out.append({"n": int(len(chunk)), "mean_predicted": float(p[chunk].mean()), "observed": float(y[chunk].mean())})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    with Session(create_engine(args.db)) as db:
        panel = build_skill_panel(db)
        frame, check = add_share(panel.frame, db)
    dev, _ = split_origins(panel, R92_PROTOCOL)
    cache = PairCache(panel, FeatureConfig())
    target = get_target("ts_pct")  # only for the scored population and age/minutes features
    seasons = panel.seasons
    all_pairs = []
    for i, origin in enumerate(seasons[:-1]):
        pop = cache.features(origin, target)
        all_pairs.append(features_at(frame, pop, origin, seasons[i + 1]))
    pairs = pd.concat(all_pairs, ignore_index=True)

    rows = []
    for origin in dev:
        train = pairs[pairs["origin"].map(season_key) < season_key(origin)]
        test = pairs[pairs["origin"] == origin]
        b = fit_logistic(design(train), train["appears"].to_numpy())
        p = special.expit(design(test) @ b)
        y = test["appears"].to_numpy()
        base = float(train["appears"].mean())
        played_last = np.where(test["share_t"].fillna(0).to_numpy() > 0, train.loc[train["share_t"].fillna(0) > 0, "appears"].mean(), train.loc[train["share_t"].fillna(0) <= 0, "appears"].mean())
        # conditional share: OLS among those who appear
        tr_a, te_a = train[train["appears"] == 1], test[test["appears"] == 1]
        Xtr, Xte = design(tr_a), design(te_a)
        beta = np.linalg.lstsq(Xtr, tr_a["share_next"].to_numpy(), rcond=None)[0]
        pred_share = np.clip(Xte @ beta, 0, 1)
        share = te_a["share_next"].to_numpy()
        persist = te_a["share_t"].fillna(te_a["share_lag1"]).fillna(tr_a["share_next"].mean()).to_numpy()
        legacy = availability_from_history(frame[frame["season"].map(season_key) <= season_key(origin)], [s for s in seasons if season_key(origin) - 2 <= season_key(s) <= season_key(origin)])
        leg = te_a[["player_id"]].merge(legacy, on="player_id", how="left")["availability"].fillna(tr_a["share_next"].mean()).to_numpy()
        # total availability, zero when absent
        total_true = np.where(y == 1, test["share_next"].fillna(0).to_numpy(), 0.0)
        e_share_all = np.clip(design(test) @ beta, 0, 1)
        total_model = p * e_share_all
        total_persist = test["share_t"].fillna(0).to_numpy()
        rows.append({
            "origin": origin, "n_scored": int(len(test)), "appears_rate": float(y.mean()), "n_appearing": int(len(te_a)),
            "appear": {
                "brier_model": float(np.mean((p - y) ** 2)), "brier_base_rate": float(np.mean((base - y) ** 2)), "brier_played_last_season_rule": float(np.mean((played_last - y) ** 2)),
                "logloss_model": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), "logloss_base_rate": float(-np.mean(y * np.log(base) + (1 - y) * np.log(1 - base))),
                "auc_model": float(stats.mannwhitneyu(p[y == 1], p[y == 0]).statistic / (np.sum(y == 1) * np.sum(y == 0))),
            },
            "share_given_appears": {
                "mae_league_mean": float(np.mean(np.abs(share - tr_a["share_next"].mean()))), "mae_persistence": float(np.mean(np.abs(share - persist))),
                "mae_legacy_decayed_availability": float(np.mean(np.abs(share - leg))), "mae_ols_history": float(np.mean(np.abs(share - pred_share))),
            },
            "total_availability_zero_if_absent": {
                "mae_persistence": float(np.mean(np.abs(total_true - total_persist))), "mae_two_part_model": float(np.mean(np.abs(total_true - total_model))),
                "mae_league_mean": float(np.mean(np.abs(total_true - train.assign(t=np.where(train["appears"] == 1, train["share_next"].fillna(0), 0.0))["t"].mean()))),
                "rmse_persistence": float(np.sqrt(np.mean((total_true - total_persist) ** 2))), "rmse_two_part_model": float(np.sqrt(np.mean((total_true - total_model) ** 2))),
                "rmse_league_mean": float(np.sqrt(np.mean((total_true - train.assign(t=np.where(train["appears"] == 1, train["share_next"].fillna(0), 0.0))["t"].mean()) ** 2))),
            },
        })
    last_train = pairs[pairs["origin"].map(season_key) < season_key(dev[-1])]
    b_last = fit_logistic(design(last_train), last_train["appears"].to_numpy())
    test_last = pairs[pairs["origin"] == dev[-1]]
    summary = {}
    for section, keys in (("appear", ("brier_model", "brier_base_rate", "brier_played_last_season_rule", "logloss_model", "logloss_base_rate", "auc_model")),
                          ("share_given_appears", ("mae_league_mean", "mae_persistence", "mae_legacy_decayed_availability", "mae_ols_history")),
                          ("total_availability_zero_if_absent", ("mae_persistence", "mae_two_part_model", "mae_league_mean", "rmse_persistence", "rmse_two_part_model", "rmse_league_mean"))):
        w = np.array([r["n_scored"] if section != "share_given_appears" else r["n_appearing"] for r in rows], dtype=float)
        summary[section] = {k: float(np.average([r[section][k] for r in rows], weights=w)) for k in keys}
    out = {
        "protocol": R92_PROTOCOL.version, "development_origins": dev, "note": "development folds only; analysis, not a served model; injuries are not used",
        "team_games_proxy_check": check, "folds": rows, "pooled": summary,
        "calibration_last_development_fold": calibration_bins(special.expit(design(test_last) @ b_last), test_last["appears"].to_numpy()),
        "logistic_coefficients_last_fold": dict(zip(["intercept", "share_t", "share_lag1", "share_lag2", "no_row_in_origin_season", "log_window_minutes", "age", "age_sq", "prior_seasons"], map(float, b_last), strict=True)),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, sort_keys=True))
    print(json.dumps({"team_games_proxy_check": check, "pooled": summary}, indent=2))


if __name__ == "__main__":
    main()
