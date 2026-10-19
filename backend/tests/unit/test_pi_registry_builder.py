"""R9.2 — the validation registry is derived from experiment artifacts by fixed rules, never typed in.

Each case fabricates the minimal artifacts of a development run and a holdout run and checks what the builder
concludes — in particular that a candidate that merely advanced is *not* recommended, that a holdout re-read cannot
raise a status, and that an ability model that fails its serving rule is withheld."""

import json
from pathlib import Path

import pytest

from scripts.r9_2_build_registry import build

FP = "f" * 64


def write(path: Path, payload):
    path.write_text(json.dumps(payload))


def make_run(root: Path, stage: str, quantity: str, *, overall, decisions, by_fold=None, attrition=None):
    d = root / f"exp-{stage}-{quantity}-abcd1234-{FP[:8]}"
    d.mkdir(parents=True)
    write(d / "decisions.json", decisions)
    write(d / "metrics.json", {"overall": overall, "by_fold": by_fold or {m: {"2018-19": {}, "2019-20": {}} for m in overall}})
    write(d / "attrition.json", attrition or [{"scored": 600, "target_observed": 420, "evaluated": 280}, {"scored": 600, "target_observed": 430, "evaluated": 290}])
    write(d / "data_snapshot.json", {"fingerprint": FP})
    write(d / "config.json", {"stage": stage})
    return d


def metric(mae, coverage=0.8, native=None, n=500):
    out = {"mae": mae, "n": n, "coverage": coverage}
    if native is not None:
        out["native_interval"] = {"coverage": native}
    return out


def cmp(rel, ci):
    return {"relative_diff": rel, "ci95": ci}


@pytest.fixture()
def exp(tmp_path):
    return tmp_path


def dev_decisions(quantity, advanced, rels):
    return {
        "target": quantity, "reference_baseline": "ols_history", "advanced": advanced,
        "candidates": {c: {"advance": c in advanced, "vs_reference": cmp(r, [r * 1.5, r * 0.4])} for c, r in rels.items()},
    }


def test_a_candidate_that_advanced_and_was_confirmed_is_recommended_but_stays_experimental(exp):
    overall = {"ols_history": metric(0.040), "state_space": metric(0.038, native=0.80), "eb_shrinkage": metric(0.039)}
    make_run(exp, "development", "fg3_pct", overall=overall, decisions=dev_decisions("fg3_pct", ["state_space"], {"state_space": -0.05, "eb_shrinkage": -0.02}))
    make_run(exp, "holdout", "fg3_pct", overall={"ols_history": metric(0.041), "state_space": metric(0.039, native=0.82)}, decisions={"target": "fg3_pct", "confirmed": ["state_space"], "candidates": {"state_space": {"confirmed": True, "vs_reference": cmp(-0.04, [-0.003, -0.0001])}}})
    reg = build(exp, None)["quantities"]["fg3_pct"]
    assert reg["projected"]["method"] == "state_space" and reg["projected"]["tier"] == "experimental"
    assert reg["projected"]["evaluation_status"] == "holdout_confirmed"
    assert reg["projected"]["calibration"]["empirical_coverage"] == pytest.approx(0.8) and reg["projected"]["calibration"]["n"] == 500
    assert reg["projected"]["candidate_comparisons"]["eb_shrinkage"]["advanced_in_development"] is False
    assert reg["inferred"]["tier"] == "experimental" and reg["inferred"]["evaluation_status"] == "development_only"  # ability is never 'confirmed'


def test_a_candidate_that_advanced_but_failed_the_holdout_is_not_recommended(exp):
    overall = {"ols_history": metric(0.040), "state_space": metric(0.039, native=0.79)}
    make_run(exp, "development", "ts_pct", overall=overall, decisions=dev_decisions("ts_pct", ["state_space"], {"state_space": -0.025}))
    make_run(exp, "holdout", "ts_pct", overall={"ols_history": metric(0.030), "state_space": metric(0.0295)}, decisions={"target": "ts_pct", "confirmed": [], "candidates": {"state_space": {"confirmed": False, "vs_reference": cmp(-0.019, [-0.0013, 0.0002])}}})
    reg = build(exp, None)["quantities"]["ts_pct"]
    assert reg["projected"]["method"] == "ols_history" and reg["projected"]["tier"] == "benchmarked"
    assert reg["projected"]["evaluation_status"] == "benchmarked"
    assert "none was confirmed on the holdout" in reg["projected"]["reason"]
    assert "improvement" in " ".join(reg["projected"]["limitations"])  # says it is not one


def test_a_holdout_reread_cannot_raise_a_status_above_development_only(exp):
    overall = {"ols_history": metric(0.040), "state_space": metric(0.038, native=0.80)}
    make_run(exp, "development", "ts_pct", overall=overall, decisions=dev_decisions("ts_pct", ["state_space"], {"state_space": -0.05}))
    make_run(exp, "holdout", "ts_pct", overall={"ols_history": metric(0.040), "state_space": metric(0.038)}, decisions={"target": "ts_pct", "confirmed": ["state_space"], "candidates": {"state_space": {"confirmed": True, "vs_reference": cmp(-0.05, [-0.003, -0.001])}}})
    write(exp / "holdout_ledger.json", [{"target": "ts_pct", "reread": True}])
    reg = build(exp, None)["quantities"]["ts_pct"]
    assert reg["projected"]["evaluation_status"] == "development_only" and "re-read" in reg["projected"]["reason"]


def test_an_ability_model_that_forecasts_worse_than_the_baseline_is_withheld(exp):
    overall = {"ols_history": metric(0.040), "state_space": metric(0.0415, native=0.80)}  # +3.75%: worse than the 2% margin
    make_run(exp, "development", "ft_pct", overall=overall, decisions=dev_decisions("ft_pct", [], {"state_space": 0.0375}))
    reg = build(exp, None)["quantities"]["ft_pct"]
    assert reg["inferred"]["tier"] == "withheld" and "non-inferiority" in reg["inferred"]["reason"]
    assert reg["projected"]["method"] == "ols_history" and reg["projected"]["tier"] == "benchmarked"


def test_an_ability_model_with_an_uncalibrated_predictive_interval_is_withheld(exp):
    overall = {"ols_history": metric(0.040), "state_space": metric(0.039, native=0.62)}
    make_run(exp, "development", "ft_pct", overall=overall, decisions=dev_decisions("ft_pct", [], {"state_space": -0.02}))
    reg = build(exp, None)["quantities"]["ft_pct"]
    assert reg["inferred"]["tier"] == "withheld" and "outside" in reg["inferred"]["reason"]


def test_participation_shares_come_from_the_attrition_artifacts(exp):
    overall = {"ols_history": metric(0.040), "state_space": metric(0.038, native=0.80)}
    make_run(exp, "development", "fg3_pct", overall=overall, decisions=dev_decisions("fg3_pct", [], {"state_space": -0.02}))
    reg = build(exp, None)["quantities"]["fg3_pct"]
    a = reg["projected"]["numbers"]["attrition"]
    assert a["share_evaluated_of_scored"] == pytest.approx((280 + 290) / 1200)
