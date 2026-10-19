"""Build the committed validation registry (and the committed aggregate artifacts) from experiment runs.

    python -m scripts.r9_2_build_registry --exp-dir <dir holding the exp-* runs and holdout_ledger.json> \
        --analysis <shooting_analysis.json> --results-out docs/r9-2/results \
        --registry-out backend/app/player_intelligence/validation_registry.json

Everything in the registry is *derived from the artifacts*: which candidates advanced
(``decisions.json``), which were confirmed (holdout ``decisions.json``), the pooled errors
(``metrics.json``), the interval coverage and the participation (attrition) shares. Nothing is typed
in by hand, so a change in what the service serves is a reviewable diff that names its experiments.

Rules applied (all from the pre-registration, ``PREREGISTRATION_R92`` in ``app.modeling.experiment``):

* **projected** — a candidate is recommended only if it advanced in development *and* was confirmed on
  the holdout; among several, the lowest pooled development MAE (amendment 1). A holdout that was a
  re-read cannot raise a candidate above ``development_only`` (amendment 2). Otherwise the reference
  baseline is the recommended forecaster and is labelled a *benchmark*, never an improvement.
* **inferred** — served as experimental only if the ability model's own forecast is not worse than the
  reference baseline by more than 2% on the development folds and its predictive interval covered
  75–85% of development outcomes; otherwise withheld. Its status is capped at ``development_only``
  (ability is latent and is never "confirmed"; amendment 3).
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from app.modeling.experiment import COVERAGE_BAND, PREREGISTRATION_R92, R92_PROTOCOL
from app.modeling.predictors import PREDICTORS

QUANTITIES = ("ts_pct", "fg3_pct", "ft_pct")
NON_INFERIORITY = 0.02
ABILITY_MODELS = ("state_space", "state_space_role")
COPY = ("metrics.json", "decisions.json", "attrition.json", "leakage_audit.json", "data_snapshot.json", "fitted_parameters.json", "config.json", "run.json")


def _load(path: Path) -> dict | list:
    return json.loads(path.read_text())


def _find(exp: Path, stage: str, quantity: str) -> Path | None:
    runs = sorted(exp.glob(f"exp-{stage}-{quantity}-*"))
    return runs[0] if len(runs) == 1 else None


def _attrition(path: Path) -> dict:
    rows = _load(path / "attrition.json")
    scored = sum(r["scored"] for r in rows)
    return {
        "scored": scored, "target_observed": sum(r["target_observed"] for r in rows), "evaluated": sum(r["evaluated"] for r in rows),
        "share_observed_of_scored": sum(r["target_observed"] for r in rows) / scored,
        "share_evaluated_of_scored": sum(r["evaluated"] for r in rows) / scored,
    }


def _numbers(method: str, overall: dict, ref: str, attr: dict, hold_met: dict | None, reread: bool) -> dict:
    n = {
        "development": {"mae": overall[method]["mae"], "reference": ref, "reference_mae": overall[ref]["mae"],
                         "relative_to_reference": overall[method]["mae"] / overall[ref]["mae"] - 1, "n": overall[method]["n"]},
        "attrition": attr,
    }
    if hold_met and method in hold_met["overall"]:
        hm = hold_met["overall"]
        n["holdout"] = {"mae": hm[method]["mae"], "reference_mae": hm[ref]["mae"], "n": hm[method]["n"], "reread": reread,
                         "relative_to_reference": hm[method]["mae"] / hm[ref]["mae"] - 1}
    return n


def _calibration(method: str, dev_met: dict, dev_dir: Path, hold_met: dict | None, hold_dir: Path | None) -> dict | None:
    use_holdout = bool(hold_met and hold_dir and method in hold_met["overall"] and "coverage" in hold_met["overall"][method])
    src, stage_dir = (hold_met, hold_dir) if use_holdout else (dev_met, dev_dir)
    assert src is not None and stage_dir is not None
    if method not in src["overall"] or "coverage" not in src["overall"][method]:
        return None
    return {"folds": len(src["by_fold"][method]), "n": src["overall"][method]["n"],
            "empirical_coverage": src["overall"][method]["coverage"], "evaluation_ref": stage_dir.name}


def _challenge_limitation(q: str, challenge: dict | None) -> tuple[dict | None, list[str]]:
    """What the attempts-aware baseline challenge (development folds, exploratory) says about a served projection."""
    if not challenge or q not in challenge.get("quantities", {}):
        return None, []
    e = challenge["quantities"][q]
    served = e["served_model"]
    c = e["checks"].get(served)
    if c is None:
        return None, []
    best = e["strongest_baseline_in_this_set"]
    summary = {
        "served_model": served, "strongest_baseline_in_the_set": best, "relative_mae_vs_it": c["relative_diff"],
        "folds_improved": c["folds_improved"], "would_advance_against_it": c["would_advance_against_it"], "development_only": True,
    }
    if c["would_advance_against_it"]:
        text = (
            f"exploratory, development folds only: against the strongest attempts-aware regression ({best}) the model is still "
            f"{100 * c['relative_diff']:+.1f}% and would advance"
        )
    else:
        text = (
            f"exploratory, development folds only: a regression that also knows each player's attempts ({best}) captures most of this "
            f"model's gain over ols_history — the model is only {100 * c['relative_diff']:+.1f}% against it ({c['folds_improved']} folds), "
            "below the pre-registered 2% floor"
        )
    return summary, [text]


def build(exp: Path, analysis: dict | None, challenge: dict | None = None) -> dict:
    ledger = _load(exp / "holdout_ledger.json") if (exp / "holdout_ledger.json").exists() else []
    registry: dict = {
        "version": "r9.2-validation-1",
        "protocol": PREREGISTRATION_R92["version"],
        "generated_from": {},
        "quantities": {},
    }
    for q in QUANTITIES:
        dev_dir, hold_dir = _find(exp, "development", q), _find(exp, "holdout", q)
        if dev_dir is None:
            continue
        dev_dec, dev_met = _load(dev_dir / "decisions.json"), _load(dev_dir / "metrics.json")
        data_fp = _load(dev_dir / "data_snapshot.json")["fingerprint"]
        registry["generated_from"][q] = {"development": dev_dir.name, "holdout": hold_dir.name if hold_dir else None, "data_fingerprint": data_fp}
        overall = dev_met["overall"]
        ref = dev_dec["reference_baseline"]
        advanced = list(dev_dec["advanced"])
        hold_dec = _load(hold_dir / "decisions.json") if hold_dir else None
        hold_met = _load(hold_dir / "metrics.json") if hold_dir else None
        reread = any(e["target"] == q and e.get("reread") for e in ledger)
        confirmed = list(hold_dec["confirmed"]) if hold_dec else []
        attr = _attrition(dev_dir)

        def numbers(method: str, overall=overall, ref=ref, attr=attr, hold_met=hold_met, reread=reread) -> dict:
            return _numbers(method, overall, ref, attr, hold_met, reread)

        def calibration(method: str, dev_met=dev_met, dev_dir=dev_dir, hold_met=hold_met, hold_dir=hold_dir) -> dict | None:
            return _calibration(method, dev_met, dev_dir, hold_met, hold_dir)

        # ---- projected
        eligible = [c for c in advanced if c in confirmed]
        proj: dict
        if eligible:
            method = min(eligible, key=lambda c: overall[c]["mae"])
            status = "development_only" if reread else "holdout_confirmed"
            proj = {
                "method": method, "tier": "experimental", "evaluation_status": status,
                "evaluation_ref": hold_dir.name if hold_dir else dev_dir.name, "calibration": calibration(method), "numbers": numbers(method),
                "reason": (
                    f"{method} advanced under the pre-registered rule in development and was confirmed on the holdout"
                    + (" — but the holdout was a re-read (R9.1 had read it), so the status is capped at development_only" if reread else "")
                ),
                "limitations": ["a candidate model: experimental until a reviewed promotion"],
            }
            summary, texts = _challenge_limitation(q, challenge)
            if summary:
                proj["numbers"]["attempts_aware_baseline_challenge"] = summary
                proj["limitations"] += texts
        elif advanced:
            method = ref
            proj = {
                "method": method, "tier": "benchmarked", "evaluation_status": "benchmarked",
                "evaluation_ref": (hold_dir or dev_dir).name, "calibration": calibration(method), "numbers": numbers(method),
                "reason": f"candidates {advanced} advanced in development but none was confirmed on the holdout; the reference baseline {ref} is the recommended forecaster",
                "limitations": ["a benchmark (a regression on the player's own history), not an improvement over anything"],
            }
        else:
            method = ref
            proj = {
                "method": method, "tier": "benchmarked", "evaluation_status": "benchmarked",
                "evaluation_ref": (hold_dir or dev_dir).name, "calibration": calibration(method), "numbers": numbers(method),
                "reason": f"no candidate advanced; the reference baseline {ref} remains the recommended forecaster",
                "limitations": ["a benchmark (a regression on the player's own history), not an improvement over anything"],
            }
        # every candidate that was examined, with what happened to it (documentation; the service ignores it)
        proj["candidate_comparisons"] = {
            c: {
                "advanced_in_development": c in advanced,
                "development_relative_mae_vs_reference": dev_dec["candidates"][c]["vs_reference"]["relative_diff"],
                "development_player_ci95": dev_dec["candidates"][c]["vs_reference"]["ci95"],
                "holdout_relative_mae_vs_reference": (hold_dec["candidates"][c]["vs_reference"]["relative_diff"] if hold_dec and c in hold_dec["candidates"] else None),
                "holdout_player_ci95": (hold_dec["candidates"][c]["vs_reference"]["ci95"] if hold_dec and c in hold_dec["candidates"] else None),
                "confirmed_on_holdout": c in confirmed,
            }
            for c in dev_dec["candidates"]
        }
        registry["quantities"].setdefault(q, {})["projected"] = proj

        # ---- inferred
        carriers = [m for m in ABILITY_MODELS if m in overall]
        chosen = None
        advanced_carriers = [m for m in carriers if m in advanced]
        pool = advanced_carriers or carriers
        if pool:
            chosen = min(pool, key=lambda m: overall[m]["mae"])
        reason, ok = "", False
        if chosen is None:
            reason = "no model that carries a posterior of ability was evaluated"
        else:
            rel = overall[chosen]["mae"] / overall[ref]["mae"] - 1
            nat = overall[chosen].get("native_interval", {}).get("coverage")
            if rel > NON_INFERIORITY:
                reason = f"{chosen}'s own forecast is {rel:+.1%} vs {ref} on the development folds, worse than the {NON_INFERIORITY:.0%} non-inferiority margin"
            elif nat is None or not (COVERAGE_BAND[0] <= nat <= COVERAGE_BAND[1]):
                reason = f"{chosen}'s predictive interval covered {nat} of development outcomes, outside {COVERAGE_BAND}"
            else:
                ok = True
        if ok:
            # Ability is latent: it is never "confirmed", whatever the model's forecasts did on the holdout. The
            # inferred record is capped at development_only (the forecast skill of the same model is stated in
            # `numbers` and on the projected record). Amendment 3 to the pre-registration.
            hold_native = (hold_met["overall"].get(chosen, {}).get("native_interval", {}) if hold_met else {}).get("coverage")
            extra_limits = [
                "ability is latent: the estimate is validated only by what it predicts",
                "attempts are treated only as precision; that volume is itself information about ability is not used, so players who shoot rarely lean on a league curve that is too high — inferred values below reliability 0.30 are withheld",
            ]
            if chosen not in confirmed:
                extra_limits.append(
                    f"this model's own forecasts were not confirmed against the baseline on the holdout ({'a re-read' if reread else 'first read'}); "
                    "the ability estimate is served on the development-only non-inferiority and calibration rule"
                )
            if hold_native is not None and not (COVERAGE_BAND[0] <= hold_native <= COVERAGE_BAND[1]):
                extra_limits.append(f"the model's own predictive interval covered {hold_native:.3f} of holdout outcomes, outside the pre-registered band {COVERAGE_BAND}")
            registry["quantities"][q]["inferred"] = {
                "method": chosen, "tier": "experimental", "evaluation_status": "development_only",
                "evaluation_ref": dev_dir.name,
                "numbers": {**numbers(chosen), "predictive_interval_coverage_development": overall[chosen]["native_interval"]["coverage"], "predictive_interval_coverage_holdout": hold_native},
                "reason": (
                    f"{chosen} carries a posterior of ability; its own forecasts are {overall[chosen]['mae'] / overall[ref]['mae'] - 1:+.1%} vs the reference "
                    f"on development and its predictive interval covered {overall[chosen]['native_interval']['coverage']:.1%} of development outcomes (band {COVERAGE_BAND}). "
                    "This is evidence of predictive validity on players who later qualify, not a measurement of ability"
                ),
                "limitations": extra_limits,
            }
        else:
            registry["quantities"][q]["inferred"] = {
                "method": chosen, "tier": "withheld", "evaluation_status": "failed" if chosen else "unevaluated",
                "evaluation_ref": dev_dir.name, "reason": reason, "numbers": {},
            }
        if analysis and q in analysis.get("quantities", {}):
            a = analysis["quantities"][q]
            registry["quantities"][q]["supplementary"] = {
                "posterior_predictive_coverage": a["predictive_check"]["coverage"],
                "stability_of_measured_rate": a["stability_of_measured_rate"],
                "participation": {k: a["participation"][k] for k in ("scored", "appears_next_season", "meets_evaluation_condition")},
            }
    return registry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp-dir", required=True)
    ap.add_argument("--analysis")
    ap.add_argument("--results-out", required=True)
    ap.add_argument("--registry-out", required=True)
    ap.add_argument("--challenge", help="baseline_challenge.json (development-only attempts-aware baseline challenge)")
    ap.add_argument("--sensitivity-dir", help="runs made with --eval-min-target-* (copied as sensitivity-*; not used by the registry)")
    args = ap.parse_args()
    exp = Path(args.exp_dir)
    analysis = json.loads(Path(args.analysis).read_text()) if args.analysis else None
    challenge_path = Path(args.challenge) if args.challenge else None
    challenge = json.loads(challenge_path.read_text()) if challenge_path and challenge_path.exists() else None
    registry = build(exp, analysis, challenge)
    Path(args.registry_out).write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    out = Path(args.results_out)
    out.mkdir(parents=True, exist_ok=True)
    runs = [(run, "") for run in sorted(exp.glob("exp-*"))]
    if args.sensitivity_dir:
        runs += [(run, "sensitivity-") for run in sorted(Path(args.sensitivity_dir).glob("exp-*"))]
    for run, prefix in runs:
        quantity = next((p for p in QUANTITIES if f"-{p}-" in run.name), None)
        label = run.name[len("exp-"):].rsplit(f"-{quantity}-", 1)[0] if quantity else run.name
        target = out / f"{prefix}{label}-{quantity}"
        target.mkdir(parents=True, exist_ok=True)
        for name in COPY:
            if (run / name).exists():
                shutil.copy(run / name, target / name)
        (target / "run_id.txt").write_text(run.name + "\n")
    if (exp / "holdout_ledger.json").exists():
        shutil.copy(exp / "holdout_ledger.json", out / "holdout_ledger.json")
    if args.analysis:
        shutil.copy(args.analysis, out / "shooting_analysis.json")
    # the pre-registration as it stood at the last run (amendments included)
    (out / "preregistration.json").write_text(json.dumps(PREREGISTRATION_R92, indent=2, sort_keys=True) + "\n")
    print(f"registry -> {args.registry_out}; aggregates -> {out}; protocol {R92_PROTOCOL.version}; predictors {len(PREDICTORS)}")


if __name__ == "__main__":
    main()
