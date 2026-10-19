"""Pre-registered experiments over the rolling-origin backtest.

An experiment is a *stage* of a fixed protocol, not a free-form notebook:

``development``  Runs every registered model on the development folds only. Applies the decision
                 rule below, and records which candidates advance.
``holdout``      Runs after development, on the folds development never touched. Only the baselines
                 and the candidates that advanced are evaluated, against the baseline that was best
                 *in development* (not re-picked on the holdout). The development run it confirms is
                 loaded from its artifacts and verified (same protocol version, same data
                 fingerprint). Every read is written to a ledger that cannot be skipped; a second
                 read of the same target's holdout is refused unless explicitly acknowledged, and is
                 then recorded as a re-read.

The protocol is a constant in this module (``PREREGISTRATION``) so that it is committed *before* the
run that uses it and is copied verbatim into every artifact. Amendments made after the first
development run, with their reasons and direction, are listed in ``PREREGISTRATION["amendments"]``.

**What the decision rule can and cannot support.** Passing it means "on these seasons, this
candidate forecast this observed target with lower absolute error than the best simple baseline, by
a margin that is not attributable to chance *either across players or across seasons*, large enough
to matter, with intervals that covered at about the nominal rate". It does not mean the candidate
measures ability, that it will do so in future seasons, or that it should replace anything.
Promotion is a separate human decision.
"""

from __future__ import annotations

import gzip
import json
import math
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
from .panel import PlayerSeasonPanel, season_key
from .predictors import PREDICTORS
from .provenance import DataSnapshot, config_hash, environment, feature_manifest
from .targets import get_target
from .team_gate import r2_difference

PROTOCOL_VERSION = "r9.1-prereg-2"

PREREGISTRATION = {
    "version": PROTOCOL_VERSION,
    "confirmatory_target": "impact_proxy",
    "exploratory_targets": ["pie", "ts_pct", "ast_pct", "reb_pct", "usg_pct"],
    "multiplicity": (
        "six targets x three candidates are examined. Only impact_proxy (the incumbent's own documented "
        "target) is confirmatory; every other target is exploratory and is reported without correction"
    ),
    "population": {
        "scored": "players with >= 200 minutes over the last 3 seasons ending at the cutoff (including those who missed the cutoff season)",
        "evaluated": "scored players who played >= 500 minutes in the target season (same rows for every model); training uses the same rule",
        "sensitivity": "evaluation-only cuts at 250 and 1000 target-season minutes, training unchanged",
    },
    "folds": (
        "rolling origin; >= 2 training transitions; development = origins 2018-19..2022-23; "
        "locked holdout = origins 2023-24 and 2024-25 (hard-coded, independent of later ingestion)"
    ),
    "reference_baseline": "the baseline with the lowest pooled development MAE for that target",
    "advance_if_all": {
        "player_ci": "player-cluster bootstrap 95% interval of MAE(candidate) - MAE(reference) lies entirely below 0 on the development folds",
        "season_ci": "the 95% t-interval (k-1 d.f.) of the per-fold MAE differences lies entirely below 0",
        "folds": "candidate has lower MAE than the reference in at least 80% of development folds (4 of 5)",
        "practical": "MAE is at least 2% lower than the reference's",
        "interval_coverage": "pooled development coverage of the nominal 80% interval lies in [0.75, 0.85]",
        "team_gate": (
            "pie and impact_proxy only: the 95% team-season bootstrap interval of R2(candidate) - R2(reference) for "
            "prospective roster-known win% is not entirely below 0; if it cannot be computed the check FAILS"
        ),
    },
    "confirm_on_holdout_if": (
        "pooled holdout player-cluster interval vs the development-chosen reference lies entirely below 0 "
        "and the candidate improves MAE in every holdout fold"
    ),
    "claims_not_made": [
        "that any model measures underlying ability",
        "that results hold for seasons after 2025-26",
        "that any candidate should be promoted",
        "that the incumbent's score measures impact",
    ],
    "disclosures": [
        "an engineering smoke test run before the development/holdout split was enforced in code scored all folds, "
        "including the holdout, for impact_proxy with the then-registered models. No model, parameter or rule was "
        "changed in response to it; but the impact_proxy holdout is NOT pristine and its confirmation is weaker evidence",
        "the incumbent's own stored validation used the 2024-25 -> 2025-26 transition (one of the two holdout folds)",
    ],
    "amendments": [
        {
            "id": 1, "when": "after the first development run, before any holdout read",
            "change": "added baseline ols_history (OLS on last season, windowed history, age); incumbent_tei_recalibrated now applies to every target",
            "reason": "independent methodology review: the baselines lacked the candidates' own information, so 'beats the best baseline' could mean only 'uses more than last season'",
            "direction": "more conservative (a stronger baseline set)",
        },
        {
            "id": 2, "when": "same",
            "change": "added season-level (fold) t-interval, a 2% practical-significance floor, and replaced the concurrent, incumbent-referenced team gate with a prospective, reference-baseline gate that fails closed",
            "reason": "review: the gate was silently skipped for pie, compared a forecast with the TEI on a circular target, and the player bootstrap ignores season-to-season dependence",
            "direction": "more conservative",
        },
        {
            "id": 3, "when": "same",
            "change": "holdout origins hard-coded; ledger mandatory and keyed by target; development decisions loaded and verified from artifacts; leakage audit strengthened (identity/team permutation, row deletion, population and eligibility compared) and run for every development origin of the primary target; sensitivity is evaluation-only",
            "reason": "review: holdout lock was policy not enforcement; audit could not detect survivorship or identity leaks",
            "direction": "more conservative / stricter process",
        },
    ],
}

#: Origins whose outcomes development may never see. Hard-coded: ingesting 2026-27 must not turn them
#: into development data.
HOLDOUT_ORIGINS = ("2023-24", "2024-25")
MIN_FOLD_SHARE_IMPROVED = 0.8
PRACTICAL_FLOOR = 0.02
COVERAGE_BAND = (0.75, 0.85)
TEAM_GATE_TARGETS = ("impact_proxy", "pie")
MIN_TEAM_SEASONS = 30


@dataclass(frozen=True)
class Protocol:
    holdout_origins: tuple[str, ...] = HOLDOUT_ORIGINS


DEFAULT_PROTOCOL = Protocol()


class HoldoutReadRefused(RuntimeError):
    """The holdout for this target has been read already, and a re-read was not acknowledged."""


class HoldoutPrerequisiteError(RuntimeError):
    """The development run a holdout claims to confirm is missing, altered or stale."""


def split_origins(
    panel: PlayerSeasonPanel, protocol: Protocol = DEFAULT_PROTOCOL, min_train_transitions: int = 2
) -> tuple[list[str], list[str]]:
    """(development origins, holdout origins). Any fold later than the holdout is not used here."""
    origins = [f.origin for f in rolling_origin_folds(panel.seasons, min_train_transitions)]
    holdout = list(protocol.holdout_origins)
    missing = [o for o in holdout if o not in origins]
    if missing:
        raise ValueError(f"the protocol's holdout origins {missing} are not foldable in this panel ({origins})")
    first = season_key(min(holdout, key=season_key))
    development = [o for o in origins if season_key(o) < first]
    if len(development) < 3:
        raise ValueError(f"{len(development)} development folds are too few")
    return development, holdout


@dataclass
class StageOutcome:
    stage: str
    target: str
    result: BacktestResult
    decisions: dict
    directory: Path | None


# ------------------------------------------------------------------------------ decisions


def decide(result: BacktestResult, target_name: str) -> dict:
    """Apply the pre-registered advance rule to a development run."""
    rows = result.eval_rows()
    overall = result.summary["overall"]
    baselines = [m for m in overall if PREDICTORS[m].family == "baseline"]
    candidates = [m for m in overall if PREDICTORS[m].family == "candidate"]
    reference = min(baselines, key=lambda m: overall[m]["mae"])
    cfg = result.config

    verdicts = {}
    for cand in candidates:
        cmp = paired_comparison(rows, cand, reference, cfg.bootstrap_draws, cfg.seed)
        coverage = overall[cand].get("coverage")
        fold_level = cmp.get("fold_level") if cmp else None
        n_folds = cmp["folds"] if cmp else 0
        checks = {
            "player_ci_below_zero": bool(cmp and cmp["ci95"][1] < 0),
            "season_ci_below_zero": bool(fold_level and fold_level["ci95"][1] < 0),
            "folds_improved": bool(cmp and cmp["folds_improved"] >= math.ceil(MIN_FOLD_SHARE_IMPROVED * n_folds)),
            "practical_floor": bool(cmp and cmp["relative_diff"] <= -PRACTICAL_FLOOR),
            "interval_coverage_in_band": bool(
                coverage is not None and COVERAGE_BAND[0] <= coverage <= COVERAGE_BAND[1]
            ),
        }
        team = None
        if target_name in TEAM_GATE_TARGETS:
            team = (
                r2_difference(result.predictions, result.standings, cand, reference, draws=cfg.bootstrap_draws, seed=cfg.seed)
                if result.standings is not None
                else None
            )
            # Fails closed: a gate that could not be computed is not a gate that passed.
            checks["team_gate"] = bool(team is not None and team["ci95"][1] >= 0)
        verdicts[cand] = {
            "advance": all(checks.values()),
            "checks": checks,
            "vs_reference": cmp,
            "team_gate": team,
            "mean_width": overall[cand].get("mean_width"),
            "reference_mean_width": overall[reference].get("mean_width"),
            "pooled_mae": overall[cand]["mae"],
        }
    return {
        "target": target_name,
        "family": "confirmatory" if target_name == PREREGISTRATION["confirmatory_target"] else "exploratory",
        "reference_baseline": reference,
        "reference_pooled_mae": overall[reference]["mae"],
        "candidates": verdicts,
        "advanced": [c for c, v in verdicts.items() if v["advance"]],
        "examined_candidates": len(candidates),
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
    _write_json(directory / "feature_manifest.json", feature_manifest(cfg.features.window, cfg.features.decay))
    _write_json(
        directory / "folds.json",
        [{"origin": f.origin, "target_season": f.target_season, "train_origins": list(f.train_origins)} for f in result.folds],
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


def load_development_decisions(directory: Path, snapshot: DataSnapshot, target: str) -> dict:
    """Read and verify the development run a holdout stage is confirming."""
    try:
        decisions = json.loads((directory / "decisions.json").read_text())
        config = json.loads((directory / "config.json").read_text())
        recorded = json.loads((directory / "data_snapshot.json").read_text())
    except (OSError, ValueError) as exc:
        raise HoldoutPrerequisiteError(f"cannot read the development artifacts in {directory}: {exc}") from exc
    if config.get("stage") != "development":
        raise HoldoutPrerequisiteError(f"{directory} is not a development run (stage = {config.get('stage')!r})")
    if config["preregistration"].get("version") != PROTOCOL_VERSION:
        raise HoldoutPrerequisiteError("the development run was made under a different protocol version")
    if decisions.get("target") != target:
        raise HoldoutPrerequisiteError(f"the development run is for {decisions.get('target')!r}, not {target!r}")
    if recorded["fingerprint"] != snapshot.fingerprint:
        raise HoldoutPrerequisiteError(
            "the data changed since the development run (fingerprint differs); re-run development first"
        )
    return decisions


# ------------------------------------------------------------------------------ the stages


def run_stage(
    panel: PlayerSeasonPanel,
    standings: pd.DataFrame | None,
    stage: str,
    target_name: str,
    out_dir: Path | None,
    *,
    models: tuple[str, ...] | None = None,
    development_dir: Path | None = None,
    eval_min_target_minutes: float | None = None,
    bootstrap_draws: int = 2000,
    acknowledge_reread: bool = False,
    audit_origins: int | None = 1,
    protocol: Protocol = DEFAULT_PROTOCOL,
) -> StageOutcome:
    """Run one stage. ``audit_origins``: how many of the stage's last origins get a leakage audit
    (``None`` = all)."""
    if stage not in ("development", "holdout"):
        raise ValueError(f"unknown stage {stage!r}")
    get_target(target_name)
    dev, hold = split_origins(panel, protocol)
    snapshot = DataSnapshot.of_panel(panel.frame)
    features = FeatureConfig()
    dev_decisions: dict | None = None

    if stage == "holdout":
        if out_dir is None:
            raise ValueError("a holdout read must be recorded: out_dir is required")
        if development_dir is None:
            raise ValueError("a holdout run needs the development run it confirms (development_dir)")
        dev_decisions = load_development_decisions(development_dir, snapshot, target_name)
        models = tuple(
            sorted(
                {m for m in PREDICTORS if PREDICTORS[m].family == "baseline"}
                | set(dev_decisions["advanced"])
                | {dev_decisions["reference_baseline"]}
            )
        )
        origins = tuple(hold)
        ledger = out_dir / "holdout_ledger.json"
        entries = json.loads(ledger.read_text()) if ledger.exists() else []
        if any(e["target"] == target_name for e in entries) and not acknowledge_reread:
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
        eval_min_target_minutes=eval_min_target_minutes,
        bootstrap_draws=bootstrap_draws,
        features=features,
    )
    cache = PairCache(panel, features)
    result = run_backtest(panel, config, standings=standings, cache=cache)

    audited = list(origins) if audit_origins is None else list(origins)[-audit_origins:] if audit_origins else []
    audits = [leakage_audit(panel, config, o) for o in audited]
    if any(not a["passed"] for a in audits):
        raise AssertionError(f"leakage audit failed: {[a for a in audits if not a['passed']]}")

    if stage == "development":
        decisions = decide(result, target_name)
    else:
        assert dev_decisions is not None
        decisions = _confirm(result, dev_decisions)

    directory = None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        label = stage + (f"-evalmin{int(eval_min_target_minutes)}" if eval_min_target_minutes is not None else "")
        directory = out_dir / experiment_id(label, target_name, config, snapshot)
        write_artifacts(directory, stage, result, snapshot, decisions, audits)
        if stage == "holdout":
            ledger = out_dir / "holdout_ledger.json"
            entries = json.loads(ledger.read_text()) if ledger.exists() else []
            entries.append(
                {
                    "experiment": directory.name,
                    "target": target_name,
                    "models": list(models or ()),
                    "config_hash": config_hash(config.as_dict()),
                    "data_fingerprint": snapshot.fingerprint,
                    "development_run": development_dir.name if development_dir else None,
                    "read_at": datetime.now(UTC).isoformat(),
                    "reread": any(e["target"] == target_name for e in entries),
                }
            )
            _write_json(ledger, entries)
    return StageOutcome(stage, target_name, result, decisions, directory)


def _confirm(result: BacktestResult, dev_decisions: dict) -> dict:
    rows = result.eval_rows()
    reference = dev_decisions["reference_baseline"]
    out = {
        "target": dev_decisions["target"],
        "family": dev_decisions.get("family"),
        "reference_baseline_from_development": reference,
        "candidates": {},
        "confirmed": [],
    }
    for cand in dev_decisions["advanced"]:
        cmp = paired_comparison(rows, cand, reference, result.config.bootstrap_draws, result.config.seed)
        ok = bool(cmp and cmp["ci95"][1] < 0 and cmp["folds_improved"] == cmp["folds"])
        out["candidates"][cand] = {"confirmed": ok, "vs_reference": cmp}
        if ok:
            out["confirmed"].append(cand)
    return out
