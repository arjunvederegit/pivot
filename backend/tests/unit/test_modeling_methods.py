"""R9.1 — the metrics, the interval method and the transparent predictors, each on data with a known answer."""

import numpy as np
import pandas as pd
import pytest

from app.modeling.intervals import fit_interval
from app.modeling.metrics import (
    bias,
    coverage,
    mae,
    paired_cluster_bootstrap,
    r_squared,
    rmse,
    spearman,
    summarise,
)
from app.modeling.predictors import (
    PREDICTORS,
    EmpiricalBayesShrinkage,
    FitContext,
    HistoryOLS,
    RegressedPersistence,
    ols,
)
from app.modeling.provenance import DataSnapshot, frame_fingerprint
from app.modeling.targets import get_target

from .modeling_support import make_frame


class TestMetrics:
    def test_point_metrics_match_hand_calculation(self):
        y, p = np.array([1.0, 2.0, 4.0]), np.array([1.0, 3.0, 2.0])
        assert mae(y, p) == pytest.approx(1.0)
        assert rmse(y, p) == pytest.approx(np.sqrt((0 + 1 + 4) / 3))
        assert bias(y, p) == pytest.approx(-1 / 3)  # prediction minus outcome
        assert mae(y, p, np.array([1.0, 0.0, 0.0])) == 0.0

    def test_spearman_is_undefined_not_zero_for_a_constant_forecast(self):
        assert spearman(np.array([1.0, 2.0, 3.0]), np.array([5.0, 5.0, 5.0])) is None
        assert spearman(np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.0])) == pytest.approx(1.0)

    def test_coverage_counts_inclusive_bounds(self):
        assert coverage(np.array([1.0, 2.0, 3.0]), np.array([1.0, 3.0, 0.0]), np.array([1.0, 4.0, 2.9])) == pytest.approx(1 / 3)

    def test_the_bootstrap_resamples_players_not_rows(self):
        """Two rows per player with identical differences: row-level resampling would report a CI
        half as wide as the truth. With players as clusters the effective n is the player count."""
        rng = np.random.default_rng(0)
        players = np.repeat(np.arange(40), 2)
        d = np.repeat(rng.normal(-0.1, 0.5, 40), 2)
        clustered = paired_cluster_bootstrap(d, np.zeros_like(d), players, 2000, 1)
        by_row = paired_cluster_bootstrap(d, np.zeros_like(d), np.arange(len(d)), 2000, 1)
        assert clustered["n_clusters"] == 40 and by_row["n_clusters"] == 80
        assert (clustered["ci95"][1] - clustered["ci95"][0]) > 1.2 * (by_row["ci95"][1] - by_row["ci95"][0])

    def test_the_bootstrap_interval_contains_zero_for_identical_models_and_excludes_it_for_a_real_gap(self):
        players = np.repeat(np.arange(100), 3)
        base = np.abs(np.random.default_rng(1).normal(0.5, 0.2, 300))
        same = paired_cluster_bootstrap(base, base, players)
        assert same["mean_diff"] == 0 and not same["excludes_zero"]
        better = paired_cluster_bootstrap(base - 0.1, base, players)
        assert better["excludes_zero"] and better["ci95"][1] < 0

    def test_r_squared_is_squared_correlation_and_none_when_undefined(self):
        x = np.arange(10.0)
        assert r_squared(x, 2 * x + 1) == pytest.approx(1.0)
        assert r_squared(np.ones(10), x) is None

    def test_summarise_reports_width_only_beside_coverage(self):
        frame = pd.DataFrame({"origin": "a", "y": [1.0, 2.0, 3.0, 4.0], "pred": [1.1, 1.9, 3.2, 3.7], "lo": 0.0, "hi": 5.0})
        out = summarise(frame)
        assert out["coverage"] == 1.0 and out["mean_width"] == 5.0


class TestIntervals:
    def test_the_interval_widens_where_evidence_is_thin_and_covers_near_its_level(self):
        rng = np.random.default_rng(0)
        exposure = rng.uniform(200, 3000, 4000)
        resid = rng.normal(0, np.sqrt(0.02 + 150 / exposure))
        model = fit_interval(pd.DataFrame({"resid": resid, "exposure": exposure}), 0.8)
        assert model.b > 0
        assert model.half_width(np.array([250.0]))[0] > 1.5 * model.half_width(np.array([2800.0]))[0]
        test_exposure = rng.uniform(200, 3000, 20000)
        test_resid = rng.normal(0, np.sqrt(0.02 + 150 / test_exposure))
        lo, hi = model.apply(np.zeros_like(test_resid), test_exposure)
        assert 0.77 <= coverage(test_resid, lo, hi) <= 0.83

    def test_a_degenerate_residual_set_does_not_produce_a_nan_or_zero_width_interval(self):
        model = fit_interval(pd.DataFrame({"resid": np.zeros(50), "exposure": np.linspace(100, 900, 50)}), 0.8)
        lo, hi = model.apply(np.array([1.0]), np.array([500.0]))
        assert np.isfinite(lo).all() and np.isfinite(hi).all()


class TestPredictors:
    def _train(self, n=400, seed=0, noise_scale=1.0):
        rng = np.random.default_rng(seed)
        exposure = rng.uniform(150, 3000, n)
        truth = rng.normal(0, 1, n)
        x_bar = truth + rng.normal(0, 1, n) * np.sqrt(300 / exposure) * noise_scale
        y = truth + rng.normal(0, 0.3, n)
        return pd.DataFrame(
            {"origin": np.where(np.arange(n) % 2, "2018-19", "2019-20"), "y": y, "x_t": x_bar, "x_bar": x_bar,
             "window_exposure": exposure, "age": rng.uniform(20, 36, n)}
        )

    def test_empirical_bayes_shrinks_thin_evidence_more_than_thick(self):
        train = self._train()
        fitted = EmpiricalBayesShrinkage().fit(train, FitContext(get_target("impact_proxy")))
        probe = pd.DataFrame({"x_bar": [2.0, 2.0], "window_exposure": [150.0, 3000.0], "age": [27.0, 27.0]})
        thin, thick = fitted.predict(probe)
        assert 0 < thin < thick < 2.0

    def test_empirical_bayes_beats_raw_persistence_when_low_exposure_rows_are_noisy(self):
        train, test = self._train(seed=1), self._train(seed=2)
        fitted = EmpiricalBayesShrinkage().fit(train, FitContext(get_target("impact_proxy")))
        assert mae(test["y"].to_numpy(), fitted.predict(test)) < mae(test["y"].to_numpy(), test["x_t"].to_numpy())

    def test_regressed_persistence_recovers_a_planted_slope(self):
        rng = np.random.default_rng(3)
        x = rng.normal(0, 1, 2000)
        train = pd.DataFrame({"origin": "a", "x_t": x, "x_bar": x, "y": 0.4 + 0.6 * x + rng.normal(0, 0.2, 2000), "age": 27.0})
        fitted = RegressedPersistence().fit(train, FitContext(get_target("impact_proxy")))
        assert fitted.params["slope_last_season"] == pytest.approx(0.6, abs=0.03)
        assert fitted.params["intercept"] == pytest.approx(0.4, abs=0.03)

    def test_the_history_ols_recovers_a_planted_two_regressor_relationship(self):
        rng = np.random.default_rng(4)
        n = 3000
        last, bar = rng.normal(0, 1, n), rng.normal(0, 1, n)
        train = pd.DataFrame({"origin": "a", "x_t": last, "x_bar": bar, "age": rng.uniform(20, 36, n)})
        train["y"] = 0.2 + 0.3 * last + 0.5 * bar + rng.normal(0, 0.1, n)
        fitted = HistoryOLS().fit(train, FitContext(get_target("pie")))
        assert fitted.params["last_season"] == pytest.approx(0.3, abs=0.02)
        assert fitted.params["windowed_history"] == pytest.approx(0.5, abs=0.02)

    def test_the_history_ols_handles_a_player_who_missed_the_season(self):
        train = self._train()
        fitted = HistoryOLS().fit(train, FitContext(get_target("pie")))
        returner = pd.DataFrame({"x_t": [np.nan], "x_bar": [1.0], "age": [29.0], "window_exposure": [900.0]})
        assert np.isfinite(fitted.predict(returner)).all()

    def test_the_recalibrated_incumbent_is_offered_for_every_target_but_the_shipped_one_only_for_its_own(self):
        for name in ("impact_proxy", "pie", "ts_pct", "reb_pct"):
            assert PREDICTORS["incumbent_tei_recalibrated"].applies_to(get_target(name))
        assert PREDICTORS["incumbent_tei"].applies_to(get_target("impact_proxy"))
        assert not PREDICTORS["incumbent_tei"].applies_to(get_target("pie"))

    def test_least_squares_helper_leaves_the_intercept_unpenalised(self):
        X = np.column_stack([np.ones(5), np.arange(5.0)])
        beta = ols(X, 10 + 0 * np.arange(5.0), ridge=1e6)
        assert beta[0] == pytest.approx(10.0, abs=1e-3)


class TestProvenance:
    def test_the_fingerprint_is_stable_to_row_order_and_changes_with_a_single_value(self):
        frame = make_frame(n_players=30, n_seasons=4)
        shuffled = frame.sample(frac=1.0, random_state=1)
        assert frame_fingerprint(frame) == frame_fingerprint(shuffled)
        changed = frame.copy()
        changed.loc[changed.index[0], "PIE"] += 1e-4
        assert frame_fingerprint(changed) != frame_fingerprint(frame)

    def test_a_snapshot_names_its_seasons_and_size(self):
        frame = make_frame(n_players=30, n_seasons=4)
        snap = DataSnapshot.of_panel(frame)
        assert snap.seasons == tuple(sorted(frame["season"].unique())) and snap.rows == len(frame)
        assert snap.snapshot_id == f"ds-{snap.fingerprint[:16]}"
