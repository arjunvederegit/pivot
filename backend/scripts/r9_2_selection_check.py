"""R9.2 — the selection effect behind the inferred-ability output, measured instead of asserted (development folds only).

    python -m scripts.r9_2_selection_check --db sqlite:///copy.db --out docs/r9-2/results/selection_check.json

The ability models are fitted on players who *later qualify for evaluation* (>= 500 minutes and, for 3P/FT, >= 50
attempts). Two questions follow, both answered here:

1. **How much does the fitted league curve depend on that choice?** Refit on every observed player-season (>= 10
   attempts) and report the change in the league mean and in the between-player spread ``tau``.
2. **Is the estimate biased for players with little evidence?** For *every* scored player who took at least one attempt
   the next season — qualifying or not — compare the model's forecast with the pooled realised rate (makes / attempts),
   grouped by the reliability of the estimate at the cutoff. Players who later lose their minutes are not a random subset
   (their realised rates are lower), so the non-qualifying group is reported separately and the interpretation is stated
   with it: it shows what happens to the people the model is *least* able to see, not an unbiased test of ability.

The result sets the serving floor (``INFERRED_MIN_RELIABILITY``): inferred ability is withheld below it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modeling.experiment import R92_PROTOCOL, split_origins
from app.modeling.pairs import FeatureConfig, PairCache
from app.modeling.panel import season_key
from app.modeling.predictors import PREDICTORS, FitContext
from app.modeling.targets import get_target
from app.player_intelligence import rate_models as rm
from app.player_intelligence.panel import build_skill_panel

MODELS = {"fg3_pct": "state_space", "ft_pct": "state_space_role", "ts_pct": "state_space_role"}
RELIABILITY_BINS = [0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0001]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    with Session(create_engine(args.db)) as db:
        panel = build_skill_panel(db)
    dev, _ = split_origins(panel, R92_PROTOCOL)
    cache = PairCache(panel, FeatureConfig())
    out: dict = {"protocol": R92_PROTOCOL.version, "development_origins": dev, "note": "development folds only", "quantities": {}}
    for q, method in MODELS.items():
        target = get_target(q)
        refits, rows = [], []
        for origin in dev:
            train_origins = [s for s in panel.seasons if season_key(s) < season_key(origin)]
            all_pairs = pd.concat([cache.pairs(s, target) for s in train_origins], ignore_index=True)
            eligible = all_pairs[all_pairs["target_eligible"]].reset_index(drop=True)
            wide = all_pairs[all_pairs["target_observed"] & all_pairs["y"].notna() & (all_pairs["y_exposure"].fillna(0) >= 10)].reset_index(drop=True)
            fitted = PREDICTORS[method].fit(eligible, FitContext(target=target))
            fitted_all = PREDICTORS[method].fit(wide, FitContext(target=target))
            a, b = fitted.p, fitted_all.p
            refits.append({"origin": origin, "league_mean_shift": b.beta[0] - a.beta[0], "tau_ratio": float(np.sqrt(b.tau2 / a.tau2)), "n_eligible": a.n_pairs, "n_all_observed": b.n_pairs})
            test = cache.pairs(origin, target).reset_index(drop=True)
            seen = test[test["target_observed"] & test["y"].notna() & (test["y_exposure"].fillna(0) > 0)].reset_index(drop=True)
            post = fitted.posterior(seen)
            rows.append(pd.DataFrame({
                "pred": post.level_next, "rel": rm.reliability(post.var_now, a.tau2), "y": seen["y"].to_numpy(float),
                "n": seen["y_exposure"].to_numpy(float), "evaluated": seen["target_eligible"].to_numpy(bool),
            }))
        d = pd.concat(rows, ignore_index=True)
        d["bin"] = pd.cut(d["rel"], RELIABILITY_BINS, right=False).astype(str)

        def summarise(g: pd.DataFrame) -> dict:
            w = g["n"].to_numpy()
            realised = float(np.average(g["y"], weights=w))
            predicted = float(np.average(g["pred"], weights=w))
            return {"players": int(len(g)), "next_season_attempts": float(w.sum()), "predicted": predicted, "realised_pooled": realised, "bias_predicted_minus_realised": predicted - realised}

        out["quantities"][q] = {
            "model": method,
            "refit_on_all_observed_player_seasons": refits,
            "by_reliability_all_who_played": {k: summarise(g) for k, g in d.groupby("bin")},
            "by_reliability_qualifying_only": {k: summarise(g) for k, g in d[d["evaluated"]].groupby("bin")},
            "by_reliability_not_qualifying": {k: summarise(g) for k, g in d[~d["evaluated"]].groupby("bin")},
        }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, sort_keys=True))
    for q, v in out["quantities"].items():
        print(q, "league-mean shift on refit (pts):", [round(100 * r["league_mean_shift"], 2) for r in v["refit_on_all_observed_player_seasons"]], "tau ratio:", [round(r["tau_ratio"], 2) for r in v["refit_on_all_observed_player_seasons"]])
        for k, s in v["by_reliability_all_who_played"].items():
            print(f"   rel {k:12s} n={s['players']:4d} predicted {s['predicted']:.3f} realised {s['realised_pooled']:.3f} bias {100 * s['bias_predicted_minus_realised']:+.1f} pts")


if __name__ == "__main__":
    main()
