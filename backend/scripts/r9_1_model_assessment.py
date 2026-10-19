"""R9.1 — quantitative facts for the formal assessment of the incumbent TEI.

Read-only on a database COPY. Prints aggregates (no per-player values):

1. reproduces the validation stored on the active model (index MAE vs persistence MAE);
2. measures the scale mismatch inside that validation (the index and the target are on different scales);
3. decomposes how much of the index is the target's own ingredient (PIE) and how correlated the two are;
4. year-over-year stability of every candidate quantity, for rotation players.

    python scripts/r9_1_model_assessment.py /path/to/scratch-copy.db
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
from scipy import stats
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.analytics.features import build_player_season_features, recency_weighted_features
from app.analytics.impact import (
    INDEX_WEIGHTS,
    TEI_SCALE,
    add_zscores,
    baseline_index,
    build_target,
    score_players,
    train_impact_models,
)
from app.modeling.panel import PlayerSeasonPanel, season_key
from app.modeling.targets import TARGETS


def weighted_corr(x: np.ndarray, y: np.ndarray, w: np.ndarray) -> float:
    mx, my = np.average(x, weights=w), np.average(y, weights=w)
    cov = np.average((x - mx) * (y - my), weights=w)
    return float(cov / np.sqrt(np.average((x - mx) ** 2, weights=w) * np.average((y - my) ** 2, weights=w)))


def main(path: str) -> dict:
    with Session(create_engine(f"sqlite:///{path}")) as db:
        raw = build_player_season_features(db)
    panel = PlayerSeasonPanel.from_frame(raw)
    seasons = ["2023-24", "2024-25", "2025-26"]
    out: dict = {}

    # 1. The stored validation, reproduced: transitions inside the 3-season training window.
    res = train_impact_models(raw, seasons)
    out["stored_validation"] = {k: res.validation[k] for k in ("validation_transition", "n_valid", "index_mae", "persistence_mae")}

    # 2. Scale. Rebuild the validation pairs exactly as train_impact_models does.
    df = add_zscores(raw.copy())
    df["target"] = build_target(df)
    df["baseline_tei"] = baseline_index(df)
    cur = df[df["season"] == "2024-25"].set_index("player_id")
    nxt = df[df["season"] == "2025-26"].set_index("player_id")
    pairs = cur.join(nxt[["target"]].rename(columns={"target": "target_next"}), how="inner").dropna(subset=["target_next"])
    index_pred = pairs["baseline_tei"] / TEI_SCALE
    y = pairs["target_next"]
    slope, intercept = np.polyfit(index_pred, y, 1)
    out["scale"] = {
        "n": int(len(pairs)),
        "sd_of_index_prediction": float(index_pred.std()),
        "sd_of_next_season_target": float(y.std()),
        "calibration_slope_of_target_on_index": float(slope),
        "calibration_intercept": float(intercept),
        "note": "a slope far from 1 means the index is not on the target's scale; compare the two MAEs below to see whether that matters for absolute error",
        "mae_index_as_shipped": float(np.abs(y - index_pred).mean()),
        "mae_index_linearly_recalibrated": float(np.abs(y - (intercept + slope * index_pred)).mean()),
        "mae_persistence": float(np.abs(y - pairs["target"]).mean()),
        "share_with_under_500_next_season_minutes": float((nxt.loc[pairs.index, "total_minutes"] < 500).mean()),
    }

    # 3. Overlap between the index and its own target.
    weights = INDEX_WEIGHTS
    out["index_composition"] = {
        "weight_on_PIE": weights["z_PIE"],
        "share_of_total_abs_weight_on_PIE": round(weights["z_PIE"] / sum(abs(w) for w in weights.values()), 4),
        "target_weight_on_PIE": 0.6,
        "target_weight_on_NET_RATING": 0.4,
        "NET_RATING_in_index": "z_NET_RATING is computed but has weight 0 in INDEX_WEIGHTS",
    }
    cur_rot = cur[cur["total_minutes"] >= 500]
    out["same_season_overlap_rotation"] = {
        "n": int(len(cur_rot)),
        "corr_index_vs_z_PIE": float(np.corrcoef(cur_rot["baseline_tei"], cur_rot["z_PIE"])[0, 1]),
        "corr_index_vs_z_NET_RATING": float(np.corrcoef(cur_rot["baseline_tei"], cur_rot["z_NET_RATING"])[0, 1]),
        "corr_index_vs_season_target": float(np.corrcoef(cur_rot["baseline_tei"], cur_rot["target"])[0, 1]),
    }

    # 4. Year-over-year stability of each quantity (rotation players in both seasons).
    stability: dict = {}
    for name, spec in TARGETS.items():
        vals = {}
        for s in panel.seasons:
            rows = panel.season_rows(s)
            vals[s] = pd.Series(spec.measure(rows).to_numpy(), index=rows["player_id"].to_numpy())
        mins = {s: panel.season_rows(s).set_index("player_id")["total_minutes"] for s in panel.seasons}
        r_all, rho_all, n_all = [], [], 0
        for a, b in zip(panel.seasons[:-1], panel.seasons[1:], strict=True):
            ok = (mins[a] >= 500) & (mins[b].reindex(mins[a].index) >= 500)
            ids = mins[a].index[ok]
            xa, xb = vals[a].reindex(ids), vals[b].reindex(ids)
            keep = xa.notna() & xb.notna()
            if keep.sum() > 30:
                r_all.append(np.corrcoef(xa[keep], xb[keep])[0, 1])
                rho_all.append(stats.spearmanr(xa[keep], xb[keep]).statistic)
                n_all += int(keep.sum())
        stability[name] = {
            "mean_yoy_pearson": round(float(np.mean(r_all)), 3),
            "min_max_yoy_pearson": [round(float(np.min(r_all)), 3), round(float(np.max(r_all)), 3)],
            "mean_yoy_spearman": round(float(np.mean(rho_all)), 3),
            "player_season_pairs": n_all,
        }
    # The season-level TEI (index of single-season z-scores), for comparison with its own target.
    tei_by_season = {}
    for s in panel.seasons:
        rows = panel.season_rows(s)
        tei_by_season[s] = pd.Series(baseline_index(add_zscores(rows.copy())).to_numpy(), index=rows["player_id"].to_numpy())
    rs = []
    for a, b in zip(panel.seasons[:-1], panel.seasons[1:], strict=True):
        ma, mb = panel.season_rows(a).set_index("player_id")["total_minutes"], panel.season_rows(b).set_index("player_id")["total_minutes"]
        ids = ma.index[(ma >= 500) & (mb.reindex(ma.index) >= 500)]
        rs.append(np.corrcoef(tei_by_season[a].reindex(ids), tei_by_season[b].reindex(ids))[0, 1])
    stability["tei_single_season_index"] = {"mean_yoy_pearson": round(float(np.mean(rs)), 3), "min_max_yoy_pearson": [round(float(min(rs)), 3), round(float(max(rs)), 3)]}
    out["year_over_year_stability_rotation_players"] = stability
    out["seasons"] = panel.seasons
    out["covid_note"] = "2019-20 (bubble, ~64-73 games) and 2020-21 (72 games) are included; per-minute and rate quantities are unaffected by schedule length, season totals are not"
    return out


if __name__ == "__main__":
    json.dump(main(sys.argv[1]), sys.stdout, indent=2, sort_keys=True)
    print()
