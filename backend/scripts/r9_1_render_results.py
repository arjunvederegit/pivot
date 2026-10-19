"""R9.1 — turn experiment artifacts into the tables in docs/r9-1/candidate-experiment-results.md.

    python scripts/r9_1_render_results.py <experiments-dir> <docs-results-dir>

Copies each experiment's *aggregate* files (metrics, decisions, attrition, leakage audit, data snapshot) into <docs-results-dir>/<stage>-<target>/ — never the per-player
predictions — and prints Markdown tables to stdout.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

AGGREGATE_FILES = ("metrics.json", "decisions.json", "attrition.json", "leakage_audit.json", "data_snapshot.json")
ORDER = [
    "league_mean", "persistence", "regressed_persistence", "regressed_persistence_age", "incumbent_tei",
    "incumbent_tei_recalibrated", "eb_shrinkage", "ridge_history", "ridge_boxscore",
]


def fmt(v, d=4):
    return "—" if v is None else f"{v:.{d}f}"


def render(directory: Path) -> str:
    metrics = json.loads((directory / "metrics.json").read_text())
    decisions = json.loads((directory / "decisions.json").read_text())
    target = json.loads((directory / "target.json").read_text())["name"]
    ref = decisions.get("reference_baseline") or decisions.get("reference_baseline_from_development")
    overall = metrics["overall"]
    models = [m for m in ORDER if m in overall]
    base = overall[ref]["mae"]
    lines = [
        f"#### {target} — {directory.name}",
        "",
        f"n = {overall[models[0]]['n']} evaluated player-seasons; reference baseline = `{ref}` (MAE {fmt(base)})",
        "",
        "| model | MAE | vs reference | RMSE | Spearman (fold mean) | 80 % interval coverage | mean width |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for m in models:
        o = overall[m]
        rel = (o["mae"] - base) / base * 100
        lines.append(
            f"| `{m}` | {fmt(o['mae'])} | {rel:+.1f} % | {fmt(o['rmse'])} | {fmt(o.get('spearman'), 3)} | "
            f"{fmt(o.get('coverage'), 3)} | {fmt(o.get('mean_width'), 3)} |"
        )
    if "incumbent_tei" in overall and "native_interval" in overall["incumbent_tei"]:
        n = overall["incumbent_tei"]["native_interval"]
        lines += ["", f"Incumbent's own shipped band: coverage {n['coverage']:.3f}, mean width {n['mean_width']:.3f} (nominal 0.80)."]
    lines += ["", "| candidate | Δ MAE vs reference [95 % cluster-bootstrap CI] | folds improved | advance / confirm |", "| --- | --- | --- | --- |"]
    for cand, v in decisions["candidates"].items():
        c = v["vs_reference"]
        verdict = v.get("advance", v.get("confirmed"))
        lines.append(
            f"| `{cand}` | {c['mean_diff']:+.4f} [{c['ci95'][0]:+.4f}, {c['ci95'][1]:+.4f}] "
            f"({c['mean_diff'] / base * 100:+.1f} %) | {c['folds_improved']}/{c['folds']} | {'yes' if verdict else 'no'} |"
        )
    return "\n".join(lines) + "\n"


def main(experiments: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for directory in sorted(p for p in experiments.iterdir() if p.is_dir() and p.name.startswith("exp-")):
        if not (directory / "decisions.json").exists():
            continue
        parts = directory.name.split("-")  # exp, stage[-minN], target, config, data
        label = "-".join(parts[1:-2])
        dest = out / label
        dest.mkdir(exist_ok=True)
        for name in AGGREGATE_FILES:
            if (directory / name).exists():
                shutil.copy(directory / name, dest / name)
        print(render(directory))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
