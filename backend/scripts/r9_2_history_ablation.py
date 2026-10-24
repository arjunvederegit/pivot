"""R9.2 diagnostic: is the state-space gain 'attempts-aware weighting' or merely 'it sees more seasons than the baseline'?

    python -m scripts.r9_2_history_ablation --db sqlite:///copy.db --out docs/r9-2/results/history_ablation.json

``ols_history`` reads a three-season window; the state-space candidates read up to ten seasons. Two diagnostics, both
**development folds only** (the holdout is not read) and neither part of the pre-registered rule or the default model sets:

* ``ols_history_long`` — OLS given the same full history (an exposure-weighted decayed mean over every available season);
* ``state_space_h3`` — the state-space model restricted to the same three seasons the baselines see.

If ``ols_history_long`` is no better than ``ols_history`` the baseline was not handicapped; if ``state_space_h3`` keeps most of
the gain the improvement comes from the method, not from extra history."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modeling.backtest import BacktestConfig, paired_comparison, run_backtest
from app.modeling.experiment import R92_PROTOCOL, split_origins
from app.player_intelligence.panel import build_skill_panel

MODELS = ("ols_history", "ols_history_long", "state_space", "state_space_h3", "state_space_role", "beta_binomial")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    with Session(create_engine(args.db)) as db:
        panel = build_skill_panel(db)
    dev, _ = split_origins(panel, R92_PROTOCOL)
    out: dict = {"protocol": R92_PROTOCOL.version, "development_origins": dev, "note": "development folds only; diagnostics, not part of the rule", "quantities": {}}
    for quantity in ("fg3_pct", "ft_pct", "ts_pct"):
        models = tuple(m for m in MODELS if not (m == "beta_binomial" and quantity == "ts_pct"))
        result = run_backtest(panel, BacktestConfig(target=quantity, models=models, test_origins=tuple(dev), bootstrap_draws=2000))
        rows = result.eval_rows()
        overall = result.summary["overall"]
        entry: dict = {"n": overall["ols_history"]["n"], "models": {}}
        for m in models:
            vs_ols = paired_comparison(rows, m, "ols_history", 2000) if m != "ols_history" else None
            vs_long = paired_comparison(rows, m, "ols_history_long", 2000) if m != "ols_history_long" else None
            entry["models"][m] = {
                "mae": overall[m]["mae"],
                "relative_to_ols_history": vs_ols["relative_diff"] if vs_ols else None,
                "relative_to_ols_history_long": vs_long["relative_diff"] if vs_long else None,
                "ci95_vs_ols_history_long": vs_long["ci95"] if vs_long else None,
                "folds_improved_vs_ols_history_long": f"{vs_long['folds_improved']}/{vs_long['folds']}" if vs_long else None,
            }
        ss, h3 = entry["models"]["state_space"]["relative_to_ols_history"], entry["models"]["state_space_h3"]["relative_to_ols_history"]
        entry["share_of_state_space_gain_kept_with_three_seasons"] = h3 / ss if ss else None
        out["quantities"][quantity] = entry
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, sort_keys=True))
    print(json.dumps({q: {m: (round(v["relative_to_ols_history"], 4) if v["relative_to_ols_history"] is not None else None) for m, v in e["models"].items()} | {"share_kept_h3": e["share_of_state_space_gain_kept_with_three_seasons"]} for q, e in out["quantities"].items()}, indent=1))


if __name__ == "__main__":
    main()
