"""R9.1 — the pre-registered protocol: development vs. holdout, the decision rule, and the ledger."""

import copy
import json

import pytest

from app.modeling.backtest import BacktestConfig, run_backtest
from app.modeling.experiment import (
    COVERAGE_BAND,
    DEFAULT_PROTOCOL,
    HOLDOUT_ORIGINS,
    PRACTICAL_FLOOR,
    PREREGISTRATION,
    HoldoutPrerequisiteError,
    HoldoutReadRefused,
    decide,
    run_stage,
    split_origins,
)
from app.modeling.panel import PlayerSeasonPanel

from .modeling_support import make_frame, make_panel, make_standings


@pytest.fixture(scope="module")
def panel():
    return make_panel(n_players=110, n_seasons=10, seed=21)


@pytest.fixture(scope="module")
def standings(panel):
    return make_standings(panel)


@pytest.fixture(scope="module")
def dev_run(panel, standings, tmp_path_factory):
    """One development run (audited at two origins), shared by the tests that only inspect it."""
    out = tmp_path_factory.mktemp("experiments")
    return out, run_stage(panel, standings, "development", "pie", out, bootstrap_draws=100, audit_origins=2)


class TestFoldsAndHoldoutLock:
    def test_the_holdout_is_the_registered_pair_and_development_is_everything_before_it(self, panel):
        dev, hold = split_origins(panel)
        assert hold == list(HOLDOUT_ORIGINS) == ["2023-24", "2024-25"]
        assert dev == ["2018-19", "2019-20", "2020-21", "2021-22", "2022-23"]

    def test_ingesting_a_later_season_does_not_turn_the_holdout_into_development_data(self):
        longer = make_panel(n_players=60, n_seasons=11, seed=3)  # adds 2026-27
        dev, hold = split_origins(longer)
        assert hold == list(HOLDOUT_ORIGINS)
        assert max(dev) < min(hold)
        assert "2025-26" not in dev + hold, "a fold beyond the registered holdout belongs to a new protocol"

    def test_a_panel_that_cannot_fold_the_registered_holdout_is_refused(self):
        with pytest.raises(ValueError, match="not foldable"):
            split_origins(make_panel(n_players=60, n_seasons=8, seed=1))


class TestDevelopmentStage:
    def test_it_never_scores_a_holdout_fold_and_its_audit_scrambles_them(self, dev_run):
        _, outcome = dev_run
        assert set(outcome.result.predictions["origin"]).isdisjoint(HOLDOUT_ORIGINS)
        audit = json.loads((outcome.directory / "leakage_audit.json").read_text())
        assert len(audit) == 2 and all(a["passed"] for a in audit)
        assert {"2023-24", "2024-25"} <= set(audit[-1]["scrambled_seasons"])

    def test_the_artifacts_make_the_run_reproducible_and_inspectable(self, dev_run):
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

    def test_a_second_run_reproduces_the_results_exactly(self, panel, standings, dev_run, tmp_path):
        _, first = dev_run
        again = run_stage(panel, standings, "development", "pie", tmp_path, bootstrap_draws=100, audit_origins=0)
        assert again.directory.name == first.directory.name, "identical inputs must give identical experiment ids"
        for name in ("metrics.json", "decisions.json", "fitted_parameters.json", "folds.json", "data_snapshot.json"):
            assert (again.directory / name).read_text() == (first.directory / name).read_text(), name

    def test_changing_the_data_changes_the_experiment_id(self, dev_run, tmp_path):
        _, first = dev_run
        other = make_panel(n_players=110, n_seasons=10, seed=22)
        changed = run_stage(other, None, "development", "pie", tmp_path, bootstrap_draws=100, audit_origins=0)
        assert changed.directory.name != first.directory.name

    def test_the_evaluation_cut_can_move_without_moving_training(self, panel, tmp_path):
        base = run_stage(panel, None, "development", "ts_pct", tmp_path, bootstrap_draws=50, audit_origins=0)
        wide = run_stage(panel, None, "development", "ts_pct", tmp_path, bootstrap_draws=50, audit_origins=0, eval_min_target_minutes=100)
        assert "evalmin100" in wide.directory.name
        assert wide.result.attrition["train_pairs"].tolist() == base.result.attrition["train_pairs"].tolist()
        assert (wide.result.attrition["evaluated"] > base.result.attrition["evaluated"]).all()


class TestTheDecisionRule:
    @pytest.fixture(scope="class")
    def result(self, panel, standings):
        dev, _ = split_origins(panel)
        return run_backtest(panel, BacktestConfig(target="pie", test_origins=tuple(dev), bootstrap_draws=200), standings=standings)

    def test_every_candidate_is_judged_on_every_check(self, result):
        decisions = decide(result, "pie")
        assert decisions["family"] == "exploratory"
        for verdict in decisions["candidates"].values():
            assert set(verdict["checks"]) == {
                "player_ci_below_zero", "season_ci_below_zero", "folds_improved", "practical_floor",
                "interval_coverage_in_band", "team_gate",
            }
            assert verdict["advance"] == all(verdict["checks"].values())

    def test_the_primary_target_is_labelled_confirmatory(self, result):
        assert decide(result, "impact_proxy")["family"] == "confirmatory"

    def test_the_reference_is_the_best_baseline_and_includes_the_history_ols(self, result):
        decisions = decide(result, "pie")
        overall = result.summary["overall"]
        baselines = {m: o["mae"] for m, o in overall.items() if m in {"league_mean", "persistence", "regressed_persistence", "regressed_persistence_age", "ols_history", "incumbent_tei", "incumbent_tei_recalibrated"}}
        assert "ols_history" in baselines
        assert decisions["reference_baseline"] == min(baselines, key=baselines.get)

    def test_a_missing_team_gate_fails_closed_for_the_gated_targets(self, result):
        """Review finding H1: the gate used to be skipped silently, so a candidate passed vacuously."""
        no_standings = copy.copy(result)
        no_standings.standings = None
        verdicts = decide(no_standings, "pie")["candidates"]
        assert all(v["checks"]["team_gate"] is False for v in verdicts.values())
        assert all(not v["advance"] for v in verdicts.values())

    def test_the_team_gate_does_not_apply_to_a_rate_target(self, panel, standings):
        dev, _ = split_origins(panel)
        res = run_backtest(panel, BacktestConfig(target="ts_pct", test_origins=tuple(dev), bootstrap_draws=100), standings=standings)
        assert all("team_gate" not in v["checks"] for v in decide(res, "ts_pct")["candidates"].values())

    @pytest.mark.parametrize(
        ("mutate", "failed_check"),
        [
            (lambda r, c: r.summary["overall"][c].__setitem__("coverage", COVERAGE_BAND[0] - 0.2), "interval_coverage_in_band"),
        ],
    )
    def test_each_check_can_veto_an_otherwise_good_candidate(self, result, mutate, failed_check):
        broken = copy.deepcopy(result)
        broken.standings = result.standings
        for cand in ("eb_shrinkage", "ridge_history", "ridge_boxscore"):
            mutate(broken, cand)
        verdicts = decide(broken, "pie")["candidates"]
        assert all(not v["checks"][failed_check] and not v["advance"] for v in verdicts.values())

    def test_a_tiny_but_consistent_gain_does_not_clear_the_practical_floor(self, result):
        verdicts = decide(result, "pie")["candidates"]
        for v in verdicts.values():
            rel = v["vs_reference"]["relative_diff"]
            assert v["checks"]["practical_floor"] == (rel <= -PRACTICAL_FLOOR)

    def test_the_season_level_interval_is_reported_for_every_comparison(self, result):
        for v in decide(result, "pie")["candidates"].values():
            fl = v["vs_reference"]["fold_level"]
            assert fl["df"] == v["vs_reference"]["folds"] - 1 and fl["ci95"][0] <= fl["mean"] <= fl["ci95"][1]


class TestHoldoutDiscipline:
    def test_a_holdout_must_be_recorded_and_must_name_the_development_run_it_confirms(self, panel, dev_run, tmp_path):
        out, dev = dev_run
        with pytest.raises(ValueError, match="out_dir is required"):
            run_stage(panel, None, "holdout", "pie", None, development_dir=dev.directory)
        with pytest.raises(ValueError, match="development_dir"):
            run_stage(panel, None, "holdout", "pie", tmp_path)

    def test_the_development_run_is_verified_not_trusted(self, panel, dev_run, tmp_path):
        _, dev = dev_run
        other_data = make_panel(n_players=110, n_seasons=10, seed=99)
        with pytest.raises(HoldoutPrerequisiteError, match="data changed"):
            run_stage(other_data, None, "holdout", "pie", tmp_path, development_dir=dev.directory)
        with pytest.raises(HoldoutPrerequisiteError, match="not 'ts_pct'|for 'pie'"):
            run_stage(panel, None, "holdout", "ts_pct", tmp_path, development_dir=dev.directory)
        with pytest.raises(HoldoutPrerequisiteError, match="cannot read"):
            run_stage(panel, None, "holdout", "pie", tmp_path, development_dir=tmp_path / "nowhere")

    def test_holdout_scores_only_the_locked_folds_and_only_advanced_candidates(self, panel, dev_run, tmp_path):
        _, dev = dev_run
        hold = run_stage(panel, None, "holdout", "pie", tmp_path, development_dir=dev.directory, bootstrap_draws=100, audit_origins=0)
        assert set(hold.result.predictions["origin"]) == set(HOLDOUT_ORIGINS)
        scored = set(hold.result.predictions["model"])
        assert {m for m in scored if m in {"eb_shrinkage", "ridge_history", "ridge_boxscore"}} == set(dev.decisions["advanced"])
        assert hold.decisions["reference_baseline_from_development"] == dev.decisions["reference_baseline"]
        assert dev.decisions["reference_baseline"] in scored

    def test_the_second_read_is_refused_unless_acknowledged_and_is_then_recorded(self, panel, dev_run, tmp_path):
        _, dev = dev_run
        run_stage(panel, None, "holdout", "pie", tmp_path, development_dir=dev.directory, bootstrap_draws=100, audit_origins=0)
        with pytest.raises(HoldoutReadRefused, match="already read"):
            run_stage(panel, None, "holdout", "pie", tmp_path, development_dir=dev.directory, bootstrap_draws=100, audit_origins=0)
        run_stage(panel, None, "holdout", "pie", tmp_path, development_dir=dev.directory, bootstrap_draws=100, audit_origins=0, acknowledge_reread=True)
        ledger = json.loads((tmp_path / "holdout_ledger.json").read_text())
        assert [e["reread"] for e in ledger] == [False, True]
        assert all(e["target"] == "pie" and e["development_run"] == dev.directory.name for e in ledger)

    def test_a_sensitivity_variant_cannot_be_used_to_read_the_holdout_again(self, panel, dev_run, tmp_path):
        """Review finding M2: the ledger used to be keyed on (target, cut), so each cut got a fresh read."""
        _, dev = dev_run
        run_stage(panel, None, "holdout", "pie", tmp_path, development_dir=dev.directory, bootstrap_draws=100, audit_origins=0)
        with pytest.raises(HoldoutReadRefused):
            run_stage(panel, None, "holdout", "pie", tmp_path, development_dir=dev.directory, bootstrap_draws=100, audit_origins=0, eval_min_target_minutes=250)


def test_registered_amendments_all_say_which_way_they_moved_the_bar():
    assert PREREGISTRATION["amendments"]
    assert all(a["direction"].startswith("more conservative") for a in PREREGISTRATION["amendments"])
    assert DEFAULT_PROTOCOL.holdout_origins == HOLDOUT_ORIGINS


def test_the_frame_helper_is_importable_for_downstream_tests():
    assert isinstance(PlayerSeasonPanel.from_frame(make_frame(n_players=30, n_seasons=4)), PlayerSeasonPanel)


class TestThePositivePath:
    """The negative paths are tested above; a rule that can never say yes would pass all of them.

    Plant a relationship the baselines cannot see: next season's true shooting depends on THIS season's
    free-throw rate, a box-score feature that is in `ridge_boxscore`'s inputs but in no baseline's.
    A sound pipeline must find it, advance the candidate, and confirm it on the holdout."""

    @pytest.fixture(scope="class")
    def planted(self):
        import numpy as np

        frame = make_frame(n_players=150, n_seasons=10, seed=31).copy()
        frame = frame.sort_values(["player_id", "season"]).reset_index(drop=True)
        rng = np.random.default_rng(5)
        previous = frame.groupby("player_id")["fta_rate"].shift(1)
        has_prev = previous.notna()
        # the planted effect is large relative to the noise so the test is about the pipeline, not about power
        frame.loc[has_prev, "TS_PCT"] = (
            0.55 + 0.35 * (previous[has_prev] - 0.26) + rng.normal(0, 0.012, int(has_prev.sum()))
        ).clip(0.3, 0.8)
        return PlayerSeasonPanel.from_frame(frame)

    def test_a_real_signal_advances_in_development_and_is_confirmed_on_the_holdout(self, planted, tmp_path):
        dev = run_stage(planted, None, "development", "ts_pct", tmp_path, bootstrap_draws=300, audit_origins=1)
        assert "ridge_boxscore" in dev.decisions["advanced"], dev.decisions["candidates"]["ridge_boxscore"]
        assert dev.decisions["candidates"]["ridge_boxscore"]["advance"] is True
        hold = run_stage(planted, None, "holdout", "ts_pct", tmp_path, development_dir=dev.directory, bootstrap_draws=300, audit_origins=0)
        assert "ridge_boxscore" in hold.decisions["confirmed"]
        assert hold.decisions["candidates"]["ridge_boxscore"]["vs_reference"]["folds_improved"] == 2
        # …and the pure-history candidates, which cannot see the planted feature, do not advance
        assert "ridge_history" not in dev.decisions["advanced"]


class TestEveryCheckIsLoadBearing:
    """Hand each check a crafted comparison: all-good must advance; breaking any single field must not.

    Without this, a check could be silently disabled (`always true`) and the suite would still pass —
    which is what the code review's mutation run found."""

    GOOD = {
        "mean_diff": -0.03, "relative_diff": -0.05, "ci95": [-0.05, -0.01], "folds": 5, "folds_improved": 5,
        "fold_level": {"mean": -0.03, "ci95": [-0.05, -0.01], "df": 4}, "reference_mae": 0.6,
        "per_fold_mean_diff": {}, "n": 100, "n_clusters": 50, "excludes_zero": True,
    }

    @pytest.fixture()
    def base(self, panel, standings):
        dev, _ = split_origins(panel)
        return run_backtest(panel, BacktestConfig(target="ts_pct", models=("league_mean", "ridge_history"), test_origins=tuple(dev), bootstrap_draws=50), standings=standings)

    def _decide(self, base, monkeypatch, cmp, coverage=0.8):
        from app.modeling import experiment

        monkeypatch.setattr(experiment, "paired_comparison", lambda *a, **k: cmp)
        broken = copy.deepcopy(base)
        broken.summary["overall"]["ridge_history"]["coverage"] = coverage
        return decide(broken, "ts_pct")["candidates"]["ridge_history"]

    def test_all_good_advances(self, base, monkeypatch):
        verdict = self._decide(base, monkeypatch, copy.deepcopy(self.GOOD))
        assert verdict["advance"] is True and all(verdict["checks"].values())

    @pytest.mark.parametrize(
        ("field", "value", "check"),
        [
            ("ci95", [-0.05, 0.001], "player_ci_below_zero"),
            ("fold_level", {"mean": -0.03, "ci95": [-0.05, 0.002], "df": 4}, "season_ci_below_zero"),
            ("folds_improved", 3, "folds_improved"),
            ("relative_diff", -0.01, "practical_floor"),
        ],
    )
    def test_breaking_any_one_comparison_field_stops_the_advance(self, base, monkeypatch, field, value, check):
        cmp = copy.deepcopy(self.GOOD)
        cmp[field] = value
        verdict = self._decide(base, monkeypatch, cmp)
        assert verdict["checks"][check] is False and verdict["advance"] is False
        assert all(ok for name, ok in verdict["checks"].items() if name != check)

    def test_coverage_outside_the_band_stops_the_advance_from_either_side(self, base, monkeypatch):
        for coverage in (COVERAGE_BAND[0] - 0.01, COVERAGE_BAND[1] + 0.01):
            verdict = self._decide(base, monkeypatch, copy.deepcopy(self.GOOD), coverage)
            assert verdict["checks"]["interval_coverage_in_band"] is False and not verdict["advance"]

    def test_a_missing_comparison_never_advances(self, base, monkeypatch):
        verdict = self._decide(base, monkeypatch, None)
        assert verdict["advance"] is False
