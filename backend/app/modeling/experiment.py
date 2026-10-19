"""Pre-registered experiments over the rolling-origin backtest.

An experiment is a *stage* of a fixed protocol, not a free-form notebook:

``development``  Runs every registered model on the development folds only. Applies the decision
                 rule below, and records which candidates advance.
``holdout``      Runs once, after development, on the folds development never touched. Only the
                 baselines and the candidates that advanced are evaluated, against the baseline that
                 was best *on development* (not re-picked on the holdout). Each read is written to a
                 ledger; a second read of the same holdout is refused unless explicitly acknowledged,
                 and is then marked as a re-read.

The protocol is a constant in this module (``PREREGISTRATION``) so that it is committed *before* the
run that uses it and is copied verbatim into every artifact.

**What the decision rule can and cannot support.** Passing it means "on these seasons, this
candidate forecast this observed target with lower absolute error than the best simple baseline, by
a margin the player-cluster bootstrap does not attribute to chance, and its intervals covered at
about the nominal rate". It does not mean the candidate measures ability, that it will do so in
future seasons, or that it should replace anything. Promotion is a separate human decision.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from .backtest import (
    BacktestConfig,
    BacktestResult,
    leakage_audit,
    paired_comparison,
    rolling_origin_folds,
    run_backtest,
)
from .pairs import FeatureConfig, PairCache
from .panel import PlayerSeasonPanel
from .predictors import PREDICTORS
from .provenance import DataSnapshot, config_hash, environment, feature_manifest
from .targets import get_target

#: Test origins whose outcomes development may see / may not see. With ten seasons of data the
#: folds are origins 2018-19 … 2024-25; the last two are locked away.
HOLDOUT_FOLDS = 2

PREREGISTRATION = {
    "version": "r9.1-prereg-1",
    "primary_target": "impact_proxy",
    "secondary_targets": ["pie", "ts_pct", "ast_pct", "reb_pct", "usg_pct"],
    "population": {
        "scored": "players with >= 200 minutes over the last 3 seasons ending at the cutoff",
        "evaluated": "scored players who played >= 500 minutes in the target season (same rows for every model)",
        "sensitivity_min_target_minutes": [250, 1000],
    },
    "folds": f"rolling origin; >= 2 training transitions; the last {HOLDOUT_FOLDS} folds are the locked holdout",
    "reference_baseline": "the baseline with the lowest pooled development MAE for that target",
    "advance_if_all": {
        "ci": "player-cluster bootstrap 95% interval of MAE(candidate) - MAE(reference) lies entirely below 0 on development folds",
        "folds": "candidate has lower MAE than the reference in at least 4 of the 5 development folds",
        "interval_coverage": "pooled development coverage of the nominal 80% interval lies in [0.75, 0.85]",
        "team_gate": "for pie and impact_proxy: concurrent team win% R^2 is not more than 0.05 below the incumbent's",
    },
    "confirm_on_holdout_if": "pooled holdout bootstrap interval vs the development-chosen reference lies entirely below 0, and the candidate improves MAE in every holdout fold",
    "claims_not_made": [
        "that any model measures underlying ability",
        "that results hold for seasons after 2025-26",
        "that any candidate should be promoted",
    ],
}

MIN_FOLDS_IMPROVED = 4
COVERAGE_BAND = (0.75, 0.85)
TEAM_GATE_TOLERANCE = 0.05
TEAM_GATE_TARGETS = ("impact_proxy", "pie")


class HoldoutReadRefused(RuntimeError):
    """The holdout for this target has been read already, and a re-read was not acknowledged."""


def split_origins(panel: PlayerSeasonPanel, min_train_transitions: int = 2) -> tuple[list[str], list[str]]:
    folds = rolling_origin_folds(panel.seasons, min_train_transitions)
    origins = [f.origin for f in folds]
    if len(origins) <= HOLDOUT_FOLDS + 2:
        raise ValueError(
            f"{len(origins)} folds are too few to keep {HOLDOUT_FOLDS} untouched; add seasons or lower the requirement"
        )
    return origins[:-HOLDOUT_FOLDS], origins[-HOLDOUT_FOLDS:]


@dataclass
class StageOutcome:
    stage: str
    target: str
    result: BacktestResult
    decisions: dict
    directory: Path | None


# ------------------------------------------------------------------------------ decisions


def decide(result: BacktestResult, panel_target: str) -> dict:
    """Apply the pre-registered advance rule to a development run."""
    rows = result.eval_rows()
    overall = result.summary["overall"]
    baselines = [m for m in overall if PREDICTORS[m].family == "baseline"]
    candidates = [m for m in overall if PREDICTORS[m].family == "candidate"]
    reference = min(baselines, key=lambda m: overall[m]["mae"])
    incumbent_r2 = None
    gate = result.summary.get("team_gate", {})
    if "incumbent_tei" in gate:
        incumbent_r2 = gate["incumbent_tei"]["concurrent"]["r2_pooled"]

    verdicts = {}
    for cand in candidates:
        cmp = paired_comparison(rows, cand, reference, result.config.bootstrap_draws, result.config.seed)
        coverage = overall[cand].get("coverage")
        checks = {
            "ci_below_zero": bool(cmp and cmp["ci95"][1] < 0),
            "folds_improved": bool(cmp and cmp["folds_improved"] >= MIN_FOLDS_IMPROVED),
            "interval_coverage_in_band": bool(
                coverage is not None and COVERAGE_BAND[0] <= coverage <= COVERAGE_BAND[1]
            ),
        }
        if panel_target in TEAM_GATE_TARGETS and incumbent_r2 is not None and cand in gate:
            r2 = gate[cand]["concurrent"]["r2_pooled"]
            checks["team_gate"] = bool(r2 is not None and r2 >= incumbent_r2 - TEAM_GATE_TOLERANCE)
        verdicts[cand] = {
            "advance": all(checks.values()),
            "checks": checks,
            "vs_reference": cmp,
            "pooled_mae": overall[cand]["mae"],
        }
    return {
        "reference_baseline": reference,
        "reference_pooled_mae": overall[reference]["mae"],
        "candidates": verdicts,
        "advanced": [c for c, v in verdicts.items() if v["advance"]],
    }


# ------------------------------------------------------------------------------ artifacts


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n")


def _json_default(value):
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return value.tolist()
    except ImportError:  # pragma: no cover
        pass
    return str(value)


def experiment_id(stage: str, target: str, cfg: BacktestConfig, snapshot: DataSnapshot) -> str:
    return f"exp-{stage}-{target}-{config_hash(cfg.as_dict())[:8]}-{snapshot.fingerprint[:8]}"


def write_artifacts(
    directory: Path,
    stage: str,
    result: BacktestResult,
    snapshot: DataSnapshot,
    decisions: dict | None,
    audit: list[dict],
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    cfg = result.config
    _write_json(directory / "config.json", {"stage": stage, "backtest": cfg.as_dict(), "preregistration": PREREGISTRATION})
    _write_json(directory / "environment.json", environment())
    _write_json(directory / "data_snapshot.json", snapshot.as_dict())
    _write_json(directory / "target.json", result.target.describe())
    _write_json(
        directory / "feature_manifest.json",
        feature_manifest(cfg.features.window, cfg.features.decay),
    )
    _write_json(
        directory / "folds.json",
        [
            {"origin": f.origin, "target_season": f.target_season, "train_origins": list(f.train_origins)}
            for f in result.folds
        ],
    )
    _write_json(directory / "models.json", [PREDICTORS[m].spec() for m in result.summary.get("models", [])])
    _write_json(directory / "metrics.json", result.summary)
    _write_json(directory / "fitted_parameters.json", result.fitted_params)
    _write_json(directory / "attrition.json", result.attrition.to_dict(orient="records"))
    _write_json(directory / "leakage_audit.json", audit)
    if decisions is not None:
        _write_json(directory / "decisions.json", decisions)
    _write_json(
        directory / "run.json",
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "runtime_seconds": result.runtime_seconds,
            "note": "metrics are deterministic; generated_at and runtime are the only fields that differ between identical runs",
        },
    )
    with gzip.open(directory / "predictions.csv.gz", "wt") as handle:
        result.predictions.to_csv(handle, index=False, float_format="%.10g")


# ------------------------------------------------------------------------------ the stages


def run_stage(
    panel: PlayerSeasonPanel,
    standings: pd.DataFrame | None,
    stage: str,
    target_name: str,
    out_dir: Path | None,
    *,
    models: tuple[str, ...] | None = None,
    advanced_from: dict | None = None,
    min_target_minutes: float = 500.0,
    bootstrap_draws: int = 2000,
    acknowledge_reread: bool = False,
    audit_origins: int = 1,
) -> StageOutcome:
    if stage not in ("development", "holdout"):
        raise ValueError(f"unknown stage {stage!r}")
    get_target(target_name)
    dev, hold = split_origins(panel)
    features = FeatureConfig(min_target_minutes=min_target_minutes)

    if stage == "holdout":
        if advanced_from is None:
            raise ValueError("a holdout run needs the development decisions it is confirming")
        models = tuple(
            sorted(
                {m for m in PREDICTORS if PREDICTORS[m].family == "baseline"}
                | set(advanced_from["advanced"])
                | {advanced_from["reference_baseline"]}
            )
        )
        origins = tuple(hold)
        ledger = out_dir / "holdout_ledger.json" if out_dir else None
        if ledger is not None and ledger.exists() and not acknowledge_reread:
            entries = json.loads(ledger.read_text())
            if any(e["target"] == target_name and e["min_target_minutes"] == min_target_minutes for e in entries):
                raise HoldoutReadRefused(
                    f"the {target_name} holdout was already read; pass acknowledge_reread=True to read it again "
                    "(the re-read will be recorded)"
                )
    else:
        origins = tuple(dev)

    config = BacktestConfig(
        target=target_name,
        models=models,
        test_origins=origins,
        bootstrap_draws=bootstrap_draws,
        features=features,
    )
    cache = PairCache(panel, features)
    result = run_backtest(panel, config, standings=standings, cache=cache)

    audits = [leakage_audit(panel, config, o) for o in list(origins)[-audit_origins:]] if audit_origins else []
    if any(not a["passed"] for a in audits):
        raise AssertionError(f"leakage audit failed: {audits}")

    if stage == "development":
        decisions = decide(result, target_name)
    else:
        assert advanced_from is not None  # checked above
        decisions = _confirm(result, advanced_from)
    snapshot = DataSnapshot.of_panel(panel.frame)

    directory = None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        directory = out_dir / experiment_id(
            stage + (f"-min{int(min_target_minutes)}" if min_target_minutes != 500.0 else ""),
            target_name, config, snapshot,
        )
        write_artifacts(directory, stage, result, snapshot, decisions, audits)
        if stage == "holdout":
            ledger = out_dir / "holdout_ledger.json"
            entries = json.loads(ledger.read_text()) if ledger.exists() else []
            entries.append(
                {
                    "experiment": directory.name,
                    "target": target_name,
                    "min_target_minutes": min_target_minutes,
                    "models": list(models or ()),
                    "config_hash": config_hash(config.as_dict()),
                    "data_fingerprint": snapshot.fingerprint,
                    "read_at": datetime.now(UTC).isoformat(),
                    "reread": any(e["target"] == target_name and e["min_target_minutes"] == min_target_minutes for e in entries),
                }
            )
            _write_json(ledger, entries)
    return StageOutcome(stage, target_name, result, decisions, directory)


def _confirm(result: BacktestResult, dev_decisions: dict) -> dict:
    rows = result.eval_rows()
    reference = dev_decisions["reference_baseline"]
    out = {"reference_baseline_from_development": reference, "candidates": {}, "confirmed": []}
    for cand in dev_decisions["advanced"]:
        cmp = paired_comparison(rows, cand, reference, result.config.bootstrap_draws, result.config.seed)
        ok = bool(cmp and cmp["ci95"][1] < 0 and cmp["folds_improved"] == cmp["folds"])
        out["candidates"][cand] = {"confirmed": ok, "vs_reference": cmp}
        if ok:
            out["confirmed"].append(cand)
    return out
