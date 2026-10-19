"""R9.1 — the pre-registered protocol: development vs. holdout, the decision rule, and the ledger."""

import json

import pytest

from app.modeling.backtest import BacktestConfig, run_backtest
from app.modeling.experiment import (
    COVERAGE_BAND,
    HOLDOUT_FOLDS,
    PREREGISTRATION,
    HoldoutReadRefused,
    decide,
    run_stage,
    split_origins,
)

from .modeling_support import make_panel, make_standings


@pytest.fixture(scope="module")
def panel():
    return make_panel(n_players=110, n_seasons=9, seed=21)


@pytest.fixture(scope="module")
def dev_run(panel, tmp_path_factory):
    """One development run (with its leakage audit), shared by the tests that only inspect it."""
    out = tmp_path_factory.mktemp("experiments")
    return out, run_stage(panel, make_standings(panel), "development", "ts_pct", out, bootstrap_draws=100)


def test_the_holdout_folds_are_the_last_ones_and_never_overlap_development(panel):
    dev, hold = split_origins(panel)
    assert len(hold) == HOLDOUT_FOLDS
    assert set(dev).isdisjoint(hold)
    assert max(dev) < min(hold)


def test_too_few_folds_to_keep_a_holdout_is_refused():
    with pytest.raises(ValueError, match="too few"):
        split_origins(make_panel(n_players=60, n_seasons=5, seed=1))


def test_development_never_scores_a_holdout_fold(panel, dev_run):
    _, outcome = dev_run
    _, hold = split_origins(panel)
    assert set(outcome.result.predictions["origin"]).isdisjoint(hold)
    audit = json.loads((outcome.directory / "leakage_audit.json").read_text())
    assert all(a["passed"] for a in audit)
    # …and the audit scrambled the seasons the holdout will use, proving development cannot see them.
    assert set(audit[0]["scrambled_seasons"]) >= {hold[-1]}


def test_the_artifacts_make_the_run_reproducible_and_inspectable(dev_run):
    _, outcome = dev_run
    names = {p.name for p in outcome.directory.iterdir()}
    assert {
        "config.json", "environment.json", "data_snapshot.json", "target.json", "feature_manifest.json",
        "folds.json", "models.json", "metrics.json", "fitted_parameters.json", "attrition.json",
        "leakage_audit.json", "decisions.json", "predictions.csv.gz", "run.json",
    } <= names
    config = json.loads((outcome.directory / "config.json").read_text())
    assert config["preregistration"] == PREREGISTRATION
    snap = json.loads((outcome.directory / "data_snapshot.json").read_text())
    assert snap["snapshot_id"].startswith("ds-") and len(snap["fingerprint"]) == 64


def test_a_second_run_reproduces_the_metrics_exactly(panel, dev_run, tmp_path):
    _, first = dev_run
    again = run_stage(panel, make_standings(panel), "development", "ts_pct", tmp_path, bootstrap_draws=100, audit_origins=0)
    assert again.directory.name == first.directory.name, "identical inputs must give identical experiment ids"
    for name in ("metrics.json", "decisions.json", "fitted_parameters.json", "folds.json", "data_snapshot.json"):
        assert (again.directory / name).read_text() == (first.directory / name).read_text(), name


def test_changing_the_data_changes_the_experiment_id(dev_run, tmp_path):
    _, first = dev_run
    other = make_panel(n_players=110, n_seasons=9, seed=22)
    changed = run_stage(other, None, "development", "ts_pct", tmp_path, bootstrap_draws=100, audit_origins=0)
    assert changed.directory.name != first.directory.name


class TestTheDecisionRule:
    def test_a_candidate_that_improves_everywhere_advances_and_a_noise_model_does_not(self, panel):
        dev, _ = split_origins(panel)
        result = run_backtest(panel, BacktestConfig(target="ts_pct", test_origins=tuple(dev), bootstrap_draws=300))
        decisions = decide(result, "ts_pct")
        assert decisions["reference_baseline"] in {
            "league_mean", "persistence", "regressed_persistence", "regressed_persistence_age"
        }
        for verdict in decisions["candidates"].values():
            assert set(verdict["checks"]) >= {"ci_below_zero", "folds_improved", "interval_coverage_in_band"}
            assert verdict["advance"] == all(verdict["checks"].values())

    def test_a_candidate_with_miscalibrated_intervals_cannot_advance_however_accurate(self, panel, monkeypatch):
        dev, _ = split_origins(panel)
        result = run_backtest(panel, BacktestConfig(target="ts_pct", test_origins=tuple(dev), bootstrap_draws=300))
        for cand in ("eb_shrinkage", "ridge_history", "ridge_boxscore"):
            result.summary["overall"][cand]["coverage"] = COVERAGE_BAND[0] - 0.2
        decisions = decide(result, "ts_pct")
        assert decisions["advanced"] == []
        assert all(not v["checks"]["interval_coverage_in_band"] for v in decisions["candidates"].values())


class TestHoldoutDiscipline:
    def test_holdout_needs_development_decisions(self, panel, tmp_path):
        with pytest.raises(ValueError, match="needs the development decisions"):
            run_stage(panel, None, "holdout", "ts_pct", tmp_path)

    def test_holdout_scores_only_the_locked_folds_and_only_advanced_candidates(self, panel, dev_run, tmp_path):
        _, dev = dev_run
        hold = run_stage(panel, None, "holdout", "ts_pct", tmp_path, advanced_from=dev.decisions, bootstrap_draws=100, audit_origins=0)
        _, locked = split_origins(panel)
        assert set(hold.result.predictions["origin"]) == set(locked)
        scored = set(hold.result.predictions["model"])
        candidates = {m for m in scored if m in {"eb_shrinkage", "ridge_history", "ridge_boxscore"}}
        assert candidates == set(dev.decisions["advanced"])
        assert hold.decisions["reference_baseline_from_development"] == dev.decisions["reference_baseline"]

    def test_the_second_read_of_a_holdout_is_refused_unless_acknowledged_and_is_then_recorded(self, panel, dev_run, tmp_path):
        _, dev = dev_run
        run_stage(panel, None, "holdout", "ts_pct", tmp_path, advanced_from=dev.decisions, bootstrap_draws=100, audit_origins=0)
        with pytest.raises(HoldoutReadRefused, match="already read"):
            run_stage(panel, None, "holdout", "ts_pct", tmp_path, advanced_from=dev.decisions, bootstrap_draws=100, audit_origins=0)
        run_stage(panel, None, "holdout", "ts_pct", tmp_path, advanced_from=dev.decisions, bootstrap_draws=100, audit_origins=0, acknowledge_reread=True)
        ledger = json.loads((tmp_path / "holdout_ledger.json").read_text())
        assert [e["reread"] for e in ledger] == [False, True]
        assert all(e["target"] == "ts_pct" for e in ledger)
