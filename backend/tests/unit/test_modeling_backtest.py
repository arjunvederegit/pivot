"""R9.1 — the rolling-origin harness: folds, leakage, determinism, and what the metrics mean."""

import numpy as np
import pandas as pd
import pytest

from app.modeling.backtest import (
    BacktestConfig,
    leakage_audit,
    paired_comparison,
    rolling_origin_folds,
    run_backtest,
)
from app.modeling.pairs import FeatureConfig, PairCache
from app.modeling.panel import season_key
from app.modeling.predictors import PREDICTORS

from .modeling_support import make_panel, make_standings

FAST = {"bootstrap_draws": 200}


@pytest.fixture(scope="module")
def panel():
    return make_panel(n_players=120, n_seasons=8, seed=11)


@pytest.fixture(scope="module")
def result(panel):
    return run_backtest(panel, BacktestConfig(target="ts_pct", **FAST), standings=make_standings(panel))


class TestFolds:
    def test_every_fold_trains_only_on_transitions_that_finished_by_the_cutoff(self, panel):
        for fold in rolling_origin_folds(panel.seasons, 2):
            assert all(season_key(s) < season_key(fold.origin) for s in fold.train_origins)
            # the last training pair's outcome season is the cutoff itself — never later
            assert season_key(fold.train_origins[-1]) + 1 == season_key(fold.origin)
            assert season_key(fold.target_season) == season_key(fold.origin) + 1

    def test_the_first_fold_needs_the_requested_number_of_transitions(self, panel):
        folds = rolling_origin_folds(panel.seasons, 3)
        assert len(folds[0].train_origins) == 3
        assert folds[0].origin == panel.seasons[3]

    def test_a_gap_in_the_seasons_is_refused(self):
        with pytest.raises(ValueError, match="not consecutive"):
            rolling_origin_folds(["2016-17", "2017-18", "2019-20"], 1)


class TestLeakage:
    def test_scrambling_every_later_season_does_not_move_a_single_forecast(self, panel):
        audit = leakage_audit(panel, BacktestConfig(target="ts_pct", **FAST), panel.seasons[3])
        assert audit["passed"], audit
        assert audit["max_abs_difference"] == 0.0
        assert audit["scrambled_seasons"]

    def test_the_audit_catches_a_feature_that_reads_the_future(self, panel, monkeypatch):
        """Mutation test: inject the defect the audit exists to find and require it to fail.

        The injected leak reads the NEXT season's value straight from the run's full panel — the
        shape of the real mistake (a join on the whole table instead of the as-of view)."""
        original = PairCache.features

        def leaky(self, origin, target):
            feats = original(self, origin, target).copy()
            ahead = self.panel.frame[self.panel.frame["season"] == _next(origin)].set_index("player_id")["TS_PCT"]
            feats["x_t"] = feats["player_id"].map(ahead).fillna(feats["x_t"])
            return feats

        monkeypatch.setattr(PairCache, "features", leaky)
        audit = leakage_audit(panel, BacktestConfig(target="ts_pct", models=("persistence",), **FAST), panel.seasons[3])
        assert audit["passed"] is False
        assert audit["max_abs_difference"] > 0

    def test_training_pairs_never_include_a_target_after_the_cutoff(self, panel, monkeypatch):
        seen = []
        original = PairCache.pairs

        def spy(self, origin, target):
            seen.append(origin)
            return original(self, origin, target)

        monkeypatch.setattr(PairCache, "pairs", spy)
        run_backtest(panel, BacktestConfig(target="ts_pct", models=("persistence",), test_origins=(panel.seasons[3],), **FAST))
        # pairs() is asked for the training origins and the test origin, never anything beyond it
        assert max(season_key(s) for s in seen) == season_key(panel.seasons[3])

    def test_league_averages_use_each_seasons_own_players_only(self, panel):
        """Standardisation by season: changing a later season must not move an earlier z-score."""
        from app.modeling.targets import get_target

        spec = get_target("impact_proxy")
        early = spec.measure(panel.season_rows("2017-18"))
        altered = panel.frame.copy()
        altered.loc[altered["season"] == "2019-20", "PIE"] *= 3
        again = spec.measure(altered[altered["season"] == "2017-18"])
        np.testing.assert_allclose(early.to_numpy(), again.to_numpy())

    def test_team_membership_comes_from_the_season_row_not_the_present(self, panel):
        """A player who changed team is on the team they played for that season."""
        movers = panel.frame.groupby("player_id")["team_id"].nunique()
        mover = movers[movers > 1].index[0]
        rows = panel.frame[panel.frame["player_id"] == mover]
        from app.modeling.targets import get_target

        first = rows.iloc[0]
        feats = PairCache(panel, FeatureConfig()).features(first["season"], get_target("ts_pct"))
        got = feats.loc[feats["player_id"] == mover, "team_id"]
        assert len(got) == 1 and got.iloc[0] == first["team_id"]


def _next(label: str) -> str:
    from app.modeling.panel import season_label

    return season_label(season_key(label) + 1)


class TestPopulationAndAttrition:
    def test_every_model_is_scored_on_exactly_the_same_rows(self, result):
        rows = result.eval_rows()
        keys = {m: set(zip(g["origin"], g["player_id"], strict=True)) for m, g in rows.groupby("model")}
        assert len({frozenset(v) for v in keys.values()}) == 1

    def test_attrition_is_accounted_for_not_hidden(self, result):
        a = result.attrition
        assert (a["scored"] >= a["target_observed"]).all()
        assert (a["target_observed"] >= a["evaluated"]).all()
        assert (a["evaluated"] > 0).all()

    def test_only_players_with_enough_target_minutes_are_evaluated(self, result):
        rows = result.eval_rows()
        assert (rows["y_minutes"] >= 500).all()
        assert rows["y"].notna().all()

    def test_no_player_season_appears_twice_for_one_model(self, result):
        assert not result.predictions.duplicated(["model", "origin", "player_id"]).any()

    def test_a_player_without_a_target_is_scored_but_not_evaluated(self, result):
        p = result.predictions
        absent = p[~p["target_observed"].astype(bool)]
        assert len(absent) > 0 and not absent["target_eligible"].any()


class TestDeterminism:
    def test_the_same_inputs_give_identical_forecasts_and_metrics(self, panel):
        cfg = BacktestConfig(target="ts_pct", test_origins=(panel.seasons[4],), **FAST)
        a, b = run_backtest(panel, cfg), run_backtest(panel, cfg)
        pd.testing.assert_frame_equal(a.predictions, b.predictions)
        assert a.summary["overall"] == b.summary["overall"]
        assert a.summary["comparisons"] == b.summary["comparisons"]


class TestWhatTheNumbersMean:
    def test_a_planted_signal_is_recovered_and_noise_is_not_invented(self, result):
        overall = result.summary["overall"]
        # Shooting skill is planted and persistent: anything that uses history beats the league mean.
        assert overall["regressed_persistence"]["mae"] < overall["league_mean"]["mae"]
        # …and shrinking toward the mean beats using last season's raw value for noisy, low-volume players.
        assert overall["eb_shrinkage"]["mae"] < overall["persistence"]["mae"]

    def test_intervals_cover_near_their_nominal_level_out_of_time(self, result):
        for model, m in result.summary["overall"].items():
            assert 0.70 <= m["coverage"] <= 0.90, (model, m["coverage"])

    def test_a_constant_prediction_has_no_rank_correlation_not_zero(self, result):
        assert result.summary["overall"]["league_mean"]["spearman"] is None

    def test_paired_comparison_detects_a_real_difference_and_not_an_identical_model(self, result):
        rows = result.eval_rows()
        real = paired_comparison(rows, "regressed_persistence", "league_mean", 300)
        assert real["ci95"][1] < 0 and real["excludes_zero"]
        same = paired_comparison(rows, "league_mean", "league_mean", 300)
        assert same["mean_diff"] == 0.0 and not same["excludes_zero"]

    def test_the_incumbent_is_only_offered_the_target_it_was_built_for(self, result):
        assert "incumbent_tei" not in result.summary["overall"]
        assert not PREDICTORS["incumbent_tei"].applies_to(result.target)


def test_a_target_with_no_signal_gets_no_candidate_credit():
    """Pure noise in, no improvement over the mean out — the harness must not manufacture skill."""
    frame = make_panel(n_players=120, n_seasons=8, seed=5).frame.copy()
    rng = np.random.default_rng(0)
    frame["REB_PCT"] = rng.uniform(0.05, 0.2, len(frame))  # unrelated to anything, season to season
    from app.modeling.panel import PlayerSeasonPanel

    noisy = PlayerSeasonPanel(frame)
    res = run_backtest(noisy, BacktestConfig(target="reb_pct", models=("league_mean", "ridge_boxscore"), **FAST))
    cmp = paired_comparison(res.eval_rows(), "ridge_boxscore", "league_mean", 300)
    assert not (cmp["ci95"][1] < 0), "a noise target must not show a convincing improvement"
