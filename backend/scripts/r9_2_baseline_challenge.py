"""R9.2 — would the served models still advance against the strongest *attempts-aware* simple baseline?

    python -m scripts.r9_2_baseline_challenge --db sqlite:///copy.db --out docs/r9-2/results/baseline_challenge.json

The pre-registered reference was ``ols_history`` (last season, windowed history, age — no attempts). The R9.2 statistical review
(round 1, finding 2) showed that is not the strongest simple baseline for a made/attempted rate: a regression that also knows how
much evidence each player has captures most of the gain. This script runs the opt-in diagnostic baselines on the **development
folds only** (the holdout is not read), applies the pre-registered advance checks against the *best* baseline of the set, and
reports bias (the review's finding 4: a plug-in sampling variance biases levels; ``*_fixedvar`` removes it).

It changes no decision and serves nothing: it exists so that the claims in the documents state what the evidence supports.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modeling.backtest import BacktestConfig, paired_comparison, run_backtest
from app.modeling.experiment import (
    COVERAGE_BAND,
    MIN_FOLD_SHARE_IMPROVED,
    PRACTICAL_FLOOR,
    R92_PROTOCOL,
    split_origins,
)
from app.player_intelligence.panel import build_skill_panel

BASELINES = ("ols_history", "ols_attempts", "ols_attempts_role", "ols_eb", "ols_eb_role")
CANDIDATES = ("state_space", "state_space_role", "beta_binomial", "state_space_fixedvar", "state_space_role_fixedvar")
SERVED = {"fg3_pct": "state_space", "ft_pct": "state_space_role", "ts_pct": "state_space_role"}


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
        models = tuple(m for m in (*BASELINES, *CANDIDATES) if not (quantity == "ts_pct" and m in ("beta_binomial", "state_space_fixedvar", "state_space_role_fixedvar")))
        result = run_backtest(panel, BacktestConfig(target=quantity, models=models, test_origins=tuple(dev), bootstrap_draws=2000))
        rows, overall = result.eval_rows(), result.summary["overall"]
        best_baseline = min((m for m in BASELINES if m in overall), key=lambda m: overall[m]["mae"])
        entry: dict = {
            "n": overall["ols_history"]["n"], "pre_registered_reference": "ols_history", "strongest_baseline_in_this_set": best_baseline,
            "models": {m: {"mae": overall[m]["mae"], "bias_pred_minus_outcome": overall[m]["bias"]} for m in models}, "served_model": SERVED[quantity], "checks": {},
        }
        for m in models:
            if m in BASELINES:
                cmp = paired_comparison(rows, m, "ols_history", 2000) if m != "ols_history" else None
                entry["models"][m]["relative_to_ols_history"] = cmp["relative_diff"] if cmp else None
        for cand in (c for c in CANDIDATES if c in overall):
            cmp = paired_comparison(rows, cand, best_baseline, 2000)
            assert cmp is not None
            fold = cmp.get("fold_level")
            n_folds = cmp["folds"]
            checks = {
                "player_ci_below_zero": cmp["ci95"][1] < 0,
                "season_ci_below_zero": bool(fold and fold["ci95"][1] < 0),
                "folds_improved": cmp["folds_improved"] >= -(-int(MIN_FOLD_SHARE_IMPROVED * 100) * n_folds // 100),
                "practical_floor": cmp["relative_diff"] <= -PRACTICAL_FLOOR,
                "interval_coverage_in_band": COVERAGE_BAND[0] <= overall[cand].get("coverage", 0.0) <= COVERAGE_BAND[1],
            }
            entry["checks"][cand] = {
                "vs": best_baseline, "relative_diff": cmp["relative_diff"], "player_ci95": cmp["ci95"],
                "fold_level_ci95": fold["ci95"] if fold else None, "folds_improved": f"{cmp['folds_improved']}/{n_folds}",
                "would_advance_against_it": all(checks.values()), "checks": checks,
            }
        out["quantities"][quantity] = entry
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, sort_keys=True))
    for q, e in out["quantities"].items():
        print(q, "strongest baseline:", e["strongest_baseline_in_this_set"], {m: round(v["mae"], 5) for m, v in e["models"].items()})
        for c, v in e["checks"].items():
            print(f"   {c:28s} vs {v['vs']:18s} {100 * v['relative_diff']:+.2f}% folds {v['folds_improved']} advance={v['would_advance_against_it']} bias={e['models'][c]['bias_pred_minus_outcome']:+.4f}")


if __name__ == "__main__":
    main()
