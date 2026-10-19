"""R9.2 — regression tests for every defect the two independent reviews found (docs/r9-2/qa-and-regression-report.md).

Each test names the finding it pins. They run on the shared synthetic league (``pi_support.build_league``), on PostgreSQL
when ``PIVOT_TEST_DB_URL`` is set."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select, update

from app.core.errors import NotFoundError
from app.db.models import CandidateEstimate, ModelVersion, PlayerSeasonStats
from app.modeling.contract import EvaluationStatus, OutputKind
from app.modeling.experiment import HoldoutPrerequisiteError, Protocol, run_stage
from app.modeling.pairs import FeatureConfig, features_only
from app.modeling.registry import train_candidate
from app.player_intelligence import rate_models as rm
from app.player_intelligence.components import reconstruct
from app.player_intelligence.measured import load_season_rows, measured_for
from app.player_intelligence.service import (
    INFERRED_MIN_RELIABILITY,
    PlayerIntelligenceService,
)
from app.player_intelligence.validation import ValidationRegistry, status_from_stored

from .pi_support import RUN_ID, build_league, make_registry, provider_rows

CUTOFF = "2023-24"


@pytest.fixture(scope="module")
def league(tmp_path_factory):
    info = build_league(tmp_path_factory, seed=21, n_players=120, cutoff=CUTOFF)
    yield info
    info["db"].close()
    info["engine"].dispose()


def top_player(league, rank: int = 0) -> str:
    frame = league["frame"]
    at = frame[frame["season"] == CUTOFF].sort_values("total_minutes", ascending=False)
    return league["players"][at.iloc[rank]["player_id"]].id


def svc(league, registry=None):
    return PlayerIntelligenceService(league["db"], registry or make_registry(league["fingerprint"]))


# ------------------------------------------------------------------ round 2, finding 1: idempotence


class TestIdempotence:
    def test_retraining_a_model_with_an_ability_posterior_reuses_it(self, league):
        db = league["db"]
        before = db.query(CandidateEstimate).count()
        again = train_candidate(db, "state_space", "fg3_pct", CUTOFF, experiment_dir=league["experiment_dir"])
        assert again.reused_existing is True
        assert db.query(CandidateEstimate).count() == before  # nothing written, nothing deleted

    def test_materialising_twice_does_not_fail_or_duplicate(self, league, monkeypatch, tmp_path):
        from app.player_intelligence import materialise

        reg = make_registry(league["fingerprint"])
        monkeypatch.setattr(materialise, "load_registry", lambda *a, **k: reg)
        # the registry names state_space for both kinds of fg3_pct output
        first = materialise.materialise_recommended(league["db"], CUTOFF, evidence_root=league["experiment_dir"].parent)
        second = materialise.materialise_recommended(league["db"], CUTOFF, evidence_root=league["experiment_dir"].parent)
        assert [r["version"] for r in first] == [r["version"] for r in second]
        assert all(r["reused_existing"] for r in second)

    def test_a_changed_ability_estimate_under_the_same_id_is_still_refused(self, league):
        db = league["db"]
        v = db.scalar(select(ModelVersion).where(ModelVersion.version.like("cand-state_space-fg3_pct-%")))
        row = db.scalars(select(CandidateEstimate).where(CandidateEstimate.model_version_id == v.id, CandidateEstimate.target == "fg3_pct@ability")).first()
        saved = row.estimate
        try:
            row.estimate = saved + 0.05
            db.flush()
            from app.modeling.registry import CandidateError

            with pytest.raises(CandidateError, match="different ability estimates"):
                train_candidate(db, "state_space", "fg3_pct", CUTOFF, experiment_dir=league["experiment_dir"])
        finally:
            db.rollback()


# ------------------------------------------------ round 2, findings 2-4: evidence and the gates


class TestRegistryEvidenceIsNotBorrowed:
    def test_a_model_never_evaluated_gets_no_registry_label_calibration_or_reference(self, league):
        db = league["db"]
        train_candidate(db, "state_space", "fg3_pct", "2022-23")  # no experiment: unevaluated
        r = svc(league).get(top_player(league), ["fg3_pct"], as_of="2022-23", kinds=["projected"], include_experimental=True)
        rec = r.records[0]
        assert rec.model.evaluation_status is EvaluationStatus.UNEVALUATED
        assert rec.model.evaluation_ref is None
        assert rec.interval.calibration is None
        assert any("evidence is NOT attached" in lim for lim in rec.limitations)
        assert "has not been measured" in rec.reliability_statement()

    def test_a_model_trained_under_another_configuration_gets_no_evidence(self, league):
        db = league["db"]
        train_candidate(db, "state_space", "fg3_pct", "2021-22", features=FeatureConfig(min_target_minutes=300), experiment_dir=league["experiment_dir"])
        v = db.scalar(select(ModelVersion).where(ModelVersion.config["origin"].as_string() == "2021-22", ModelVersion.model_name == "player_forecast_fg3_pct"))
        assert v.validation_metrics["config_matches"] is False
        r = svc(league).get(top_player(league), ["fg3_pct"], as_of="2021-22", kinds=["projected"], include_experimental=True)
        assert r.records and r.records[0].model.evaluation_status is not EvaluationStatus.DEVELOPMENT_ONLY or "evidence is NOT attached" in " ".join(r.records[0].limitations)
        assert r.records[0].interval.calibration is None

    def test_the_matching_model_still_gets_the_evidence(self, league):
        rec = svc(league).get(top_player(league), ["fg3_pct"], as_of=CUTOFF, kinds=["projected"], include_experimental=True).records[0]
        assert rec.model.evaluation_status is EvaluationStatus.DEVELOPMENT_ONLY and rec.model.evaluation_ref == RUN_ID
        assert rec.interval.calibration is not None and not any("NOT attached" in lim for lim in rec.limitations)


class TestStaleCannotLiftAFailure:
    @pytest.mark.parametrize(
        ("stored", "expected"),
        [
            ("stale:failed_development_rule", EvaluationStatus.FAILED),
            ("stale:holdout_not_confirmed", EvaluationStatus.FAILED),
            ("stale:development_only", EvaluationStatus.UNEVALUATED),
            ("stale:holdout_confirmed", EvaluationStatus.UNEVALUATED),
            ("stale:not_evaluated_as_candidate", EvaluationStatus.UNEVALUATED),
            ("failed_development_rule", EvaluationStatus.FAILED),
            (None, EvaluationStatus.UNEVALUATED),
        ],
    )
    def test_the_mapping(self, stored, expected):
        assert status_from_stored(stored) is expected

    def test_a_stale_failed_projection_is_refused_even_when_a_version_is_named(self, league):
        db = league["db"]
        v = db.scalar(select(ModelVersion).where(ModelVersion.version.like("cand-state_space-fg3_pct-%"), ModelVersion.config["origin"].as_string() == CUTOFF))
        saved = dict(v.validation_metrics)
        try:
            v.validation_metrics = {**saved, "evaluation_status": "stale:failed_development_rule"}
            db.flush()
            r = svc(league).get(top_player(league), ["fg3_pct"], as_of=CUTOFF, kinds=["projected"], model_version=v.version, include_experimental=True)
            assert r.records == [] and "failed its pre-registered evaluation" in r.unavailable[0].reason
        finally:
            db.rollback()


class TestWithheldMeansNobody:
    def test_naming_a_model_version_does_not_bypass_a_withheld_output(self, league):
        reg = make_registry(league["fingerprint"], fg3_pct={
            "inferred": {"method": "state_space_role", "tier": "withheld", "evaluation_status": "failed", "evaluation_ref": "x", "reason": "r"},
            "projected": {"method": "state_space", "tier": "experimental", "evaluation_status": "development_only", "evaluation_ref": RUN_ID, "reason": "r"},
        })
        v = league["db"].scalar(select(ModelVersion).where(ModelVersion.version.like("cand-state_space-fg3_pct-%"), ModelVersion.config["origin"].as_string() == CUTOFF))
        r = PlayerIntelligenceService(league["db"], reg).get(top_player(league), ["fg3_pct"], as_of=CUTOFF, kinds=["inferred"], model_version=v.version, include_experimental=True)
        assert r.records == [] and "withheld" in r.unavailable[0].reason

    def test_a_named_version_from_another_cutoff_or_that_does_not_exist_says_so(self, league):
        other = league["db"].scalar(select(ModelVersion).where(ModelVersion.config["origin"].as_string() == "2022-23", ModelVersion.model_name == "player_forecast_fg3_pct"))
        s = svc(league)
        r = s.get(top_player(league), ["fg3_pct"], as_of=CUTOFF, kinds=["projected"], model_version=other.version, include_experimental=True)
        assert "trained at cutoff 2022-23, not 2023-24" in r.unavailable[0].reason
        r = s.get(top_player(league), ["fg3_pct"], as_of=CUTOFF, kinds=["projected"], model_version="nope", include_experimental=True)
        assert "no stored model version 'nope'" in r.unavailable[0].reason

    def test_rejecting_the_newest_version_falls_back_to_an_older_valid_one(self, league):
        db = league["db"]
        v = db.scalar(select(ModelVersion).where(ModelVersion.version.like("cand-ols_history-fg3_pct-%"), ModelVersion.config["origin"].as_string() == CUTOFF))
        twin = ModelVersion(
            model_name=v.model_name, version="cand-ols_history-fg3_pct-newerrejected", algorithm=v.algorithm, feature_list=v.feature_list,
            target=v.target, validation_metrics={}, is_active=False, status="rejected", config=v.config, lineage={},
            trained_at=v.trained_at.replace(year=v.trained_at.year + 1) if v.trained_at else None,
        )
        db.add(twin)
        db.flush()
        try:
            chosen, _ = svc(league)._resolve_version("fg3_pct", OutputKind.PROJECTED, CUTOFF, "ols_history", None)
            assert chosen is not None and chosen.version == v.version
        finally:
            db.rollback()


# --------------------------------------------------------- round 1: inferred ability's reliability floor


class TestInferredFloor:
    def test_below_the_floor_is_withheld_with_the_reason_and_the_measured_value_remains(self, league):
        db = league["db"]
        v = db.scalar(select(ModelVersion).where(ModelVersion.version.like("cand-state_space-fg3_pct-%"), ModelVersion.config["origin"].as_string() == CUTOFF))
        row = db.scalars(select(CandidateEstimate).where(CandidateEstimate.model_version_id == v.id, CandidateEstimate.target == "fg3_pct@ability")).first()
        saved = dict(row.inputs)
        try:
            row.inputs = {**saved, "reliability": INFERRED_MIN_RELIABILITY - 0.01}
            db.flush()
            r = svc(league).get(row.player_id, ["fg3_pct"], as_of=CUTOFF, kinds=["measured", "inferred"], include_experimental=True)
            assert [x.output_kind for x in r.records] == [OutputKind.MEASURED] or all(x.output_kind is OutputKind.MEASURED for x in r.records)
            assert any("below the serving floor" in u.reason for u in r.unavailable)
            row.inputs = {**saved, "reliability": INFERRED_MIN_RELIABILITY}
            db.flush()
            served = svc(league).get(row.player_id, ["fg3_pct"], as_of=CUTOFF, kinds=["inferred"], include_experimental=True)
            assert [x.output_kind for x in served.records] == [OutputKind.INFERRED]
        finally:
            db.rollback()

    def test_the_floor_is_the_value_the_selection_check_justifies(self):
        assert INFERRED_MIN_RELIABILITY == 0.30


# ------------------------------------------- round 1 findings 5/7 and round 2 finding 5: weights and attempts


class TestReliabilityFieldsMeanWhatTheyDo:
    PARAMS = rm.StateSpaceParams(beta=(0.35, 0.0, 0.0), rho=0.8, tau2=0.0009, phi=1.0, unit_variance=0.22)

    def _one(self, lags_back):
        y = np.full((1, 6), np.nan)
        s2 = np.full((1, 6), np.nan)
        y[0, lags_back], s2[0, lags_back] = 0.40, 0.4 * 0.6 / 300
        return y, s2

    @pytest.mark.parametrize("lag", [0, 1, 3])
    def test_the_weight_is_the_filters_response_to_the_evidence(self, lag):
        y, s2 = self._one(lag)
        age = np.array([27.0])
        w = rm.observation_weight(y, s2, age, None, self.PARAMS)[0]
        shifted = np.where(np.isfinite(y), y + 1.0, y)
        manual = rm.filter_histories(shifted, s2, age, None, self.PARAMS).level_now[0] - rm.filter_histories(y, s2, age, None, self.PARAMS).level_now[0]
        assert w == pytest.approx(manual)
        rel = float(rm.reliability(rm.filter_histories(y, s2, age, None, self.PARAMS).var_now, self.PARAMS.tau2)[0])
        if lag == 0:
            assert w == pytest.approx(rel)  # equal only when the cutoff season itself is observed
        else:
            assert w > rel  # older evidence: reliability understates how much the estimate is his own record

    def test_a_parameter_on_its_bound_is_reported(self):
        on_bound = rm.StateSpaceParams(beta=(0.35, 0, 0), rho=0.9995, tau2=0.0009, phi=1.0)
        assert on_bound.at_bound == ["rho"] and on_bound.as_dict()["at_bound"] == ["rho"]
        assert rm.StateSpaceParams(beta=(0.35, 0, 0), rho=0.9, tau2=0.0009, phi=1.0).at_bound == []

    def test_stored_ability_rows_carry_the_true_weight_and_history_exposure(self, league):
        db = league["db"]
        v = db.scalar(select(ModelVersion).where(ModelVersion.version.like("cand-state_space-fg3_pct-%"), ModelVersion.config["origin"].as_string() == CUTOFF))
        rows = db.scalars(select(CandidateEstimate).where(CandidateEstimate.model_version_id == v.id, CandidateEstimate.target == "fg3_pct@ability")).all()
        assert any(abs(r.inputs["weight_on_player_record"] - r.inputs["reliability"]) > 1e-6 for r in rows)
        assert all(r.exposure is not None and r.exposure >= 0 for r in rows)
        # the exposure the estimate rests on is at least the three-season window used to decide who is scored
        assert all(r.exposure + 1e-6 >= r.inputs["window_exposure"] - 5 for r in rows)

    def test_the_served_sample_block_describes_the_evidence_the_estimate_used(self, league):
        r = svc(league).get(top_player(league), ["fg3_pct"], as_of=CUTOFF, kinds=["inferred", "projected"], include_experimental=True)
        inferred = next(x for x in r.records if x.output_kind is OutputKind.INFERRED)
        projected = next(x for x in r.records if x.output_kind is OutputKind.PROJECTED)
        assert "across the seasons the estimate used" in inferred.sample.unit
        assert "over the three seasons ending at the cutoff" in projected.sample.unit
        assert inferred.sample.attempts >= projected.sample.attempts - 5


# ------------------------------------------------------------------------- round 2: measured path


class TestMeasuredEdgeCases:
    def _row(self, **kw):
        base, adv = provider_rows(**kw)
        from app.player_intelligence.measured import SeasonRow

        gp = kw["gp"]
        return SeasonRow(season="2024-25", games=gp, minutes_per_game=20.0, team_id=None, team_count=1, base=base, advanced=adv, retrieved_at=None, recon=reconstruct(base, adv, gp))

    def test_attempts_the_rounding_hides_are_undetermined_not_none(self):
        # 2 attempts, 0 made over 82 games: 0.0 per game and 0.000 published
        row = self._row(gp=82, fgm=300, fga=700, fg3m=0, fg3a=2, ftm=100, fta=140)
        recs, missing = measured_for("p", ["fg3_pct"], {"2024-25": row}, "2024-25")
        assert recs == [] and "undetermined" in missing[0].reason and "neither 0% nor known" in missing[0].reason

    def test_truly_no_attempts_is_still_undefined(self):
        row = self._row(gp=12, fgm=40, fga=90, fg3m=0, fg3a=0, ftm=10, fta=14)
        _, missing = measured_for("p", ["fg3_pct"], {"2024-25": row}, "2024-25")
        assert "no attempts this season" in missing[0].reason and "not 0%" in missing[0].reason

    def test_a_legitimate_true_shooting_above_one_is_served(self):
        row = self._row(gp=1, fgm=1, fga=1, fg3m=1, fg3a=1, ftm=0, fta=0)  # one made three: TS = 1.5
        recs, missing = measured_for("p", ["ts_pct"], {"2024-25": row}, "2024-25")
        assert recs and recs[0].value == pytest.approx(1.5) and not missing

    def test_a_player_who_played_no_games_says_so(self):
        base, adv = provider_rows(gp=1, fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0)
        from app.player_intelligence.measured import SeasonRow

        row = SeasonRow(season="2024-25", games=0, minutes_per_game=0.0, team_id=None, team_count=None, base={**base, "GP": 0}, advanced=adv, retrieved_at=None, recon=None)
        _, missing = measured_for("p", ["fg3_pct", "ts_pct"], {"2024-25": row}, "2024-25")
        assert all("played no games" in m.reason for m in missing)

    def test_no_advanced_row_still_recovers_counts_from_base_data(self):
        base, _ = provider_rows(gp=40, fgm=200, fga=420, fg3m=52, fg3a=150, ftm=80, fta=100)
        rec = reconstruct(base, {}, 40)
        assert rec.quality != "unconstrained" and rec.fg3a_min <= 150 <= rec.fg3a_max and rec.fta_min <= 100 <= rec.fta_max

    def test_the_cutoff_is_honoured_when_loading_rows(self, league):
        db = league["db"]
        player = top_player(league)
        early = load_season_rows(db, player, "2019-20")
        assert early and max(early) <= "2019-20"
        assert "2023-24" in load_season_rows(db, player, "2023-24")

    def test_the_traded_player_note_is_emitted(self, league):
        db = league["db"]
        player = top_player(league)
        rows = db.scalars(select(PlayerSeasonStats).where(PlayerSeasonStats.player_id == player, PlayerSeasonStats.season == CUTOFF, PlayerSeasonStats.stat_type == "base")).all()
        try:
            for r in rows:
                r.stats = {**r.stats, "TEAM_COUNT": 2}
            db.flush()
            rec = svc(league).get(player, ["usg_pct", "fg3_pct"], as_of=CUTOFF).records[0]
            assert any("appeared for 2 teams" in lim for lim in rec.limitations)
        finally:
            db.rollback()


class TestIntervalsStayInTheFeasibleRange:
    def test_a_rate_interval_is_clipped_to_zero_one(self, league):
        db = league["db"]
        v = db.scalar(select(ModelVersion).where(ModelVersion.version.like("cand-state_space-fg3_pct-%"), ModelVersion.config["origin"].as_string() == CUTOFF))
        row = db.scalars(select(CandidateEstimate).where(CandidateEstimate.model_version_id == v.id, CandidateEstimate.target == "fg3_pct")).first()
        saved = (row.interval_low, row.interval_high)
        try:
            row.interval_low, row.interval_high = -0.02, 1.0024
            db.flush()
            rec = svc(league).get(row.player_id, ["fg3_pct"], as_of=CUTOFF, kinds=["projected"], include_experimental=True).records[0]
            assert rec.interval.low == 0.0 and rec.interval.high == 1.0
        finally:
            db.rollback()
            row.interval_low, row.interval_high = saved


# --------------------------------------------------------------- batching and the batch contract


class TestBatch:
    def test_a_batch_uses_far_fewer_statements_than_separate_calls(self, league):
        from sqlalchemy import event

        db = league["db"]
        ids = [top_player(league, i) for i in range(8)]
        counts = {"n": 0}

        def count(*_a, **_k):
            counts["n"] += 1

        event.listen(db.get_bind(), "before_cursor_execute", count)
        try:
            counts["n"] = 0
            s = svc(league)
            for pid in ids:
                s.get(pid, ["fg3_pct"], as_of=CUTOFF, kinds=["inferred", "projected"], include_experimental=True)
            separate = counts["n"]
            counts["n"] = 0
            svc(league).get_many(ids, ["fg3_pct"], as_of=CUTOFF, kinds=["inferred", "projected"], include_experimental=True)
            batched = counts["n"]
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", count)
        assert batched < separate

    def test_an_unknown_id_names_itself_and_computes_nothing(self, league):
        with pytest.raises(NotFoundError, match="no-such-id"):
            svc(league).get_many([top_player(league), "no-such-id"], ["fg3_pct"], as_of=CUTOFF)


# ---------------------------------------------------------------- protocol and harness (round 1 / 2)


class TestProtocolAndHarness:
    def test_a_missing_prior_ledger_refuses_a_holdout_read(self, tmp_path):
        from .modeling_support import make_panel

        protocol = Protocol(prior_ledgers=(tmp_path / "does-not-exist.json",))
        with pytest.raises(HoldoutPrerequisiteError, match="prior holdout ledger"):
            run_stage(make_panel(n_players=40, n_seasons=10, seed=2), None, "holdout", "ts_pct", tmp_path, development_dir=tmp_path, protocol=protocol)

    def test_a_predictor_cannot_see_the_outcome_when_forecasting(self):
        frame = pd.DataFrame({"x_t": [0.3], "y": [0.4], "y_exposure": [10.0], "y_sv": [0.01], "target_eligible": [True], "player_id": ["p"]})
        visible = features_only(frame)
        assert list(visible.columns) == ["x_t", "player_id"]

    def test_the_panel_build_is_invariant_to_later_seasons(self, league):
        """Deleting later seasons from the database must leave every earlier row of the skill panel unchanged."""
        from app.player_intelligence.panel import build_skill_panel

        db = league["db"]
        full = build_skill_panel(db).frame
        try:
            db.execute(update(PlayerSeasonStats).where(PlayerSeasonStats.season > "2021-22").values(season_type="playoffs"))  # hide later seasons
            db.flush()
            truncated = build_skill_panel(db).frame
        finally:
            db.rollback()
        keys = ["player_id", "season"]
        early = full[full["season"] <= "2021-22"].sort_values(keys).reset_index(drop=True)
        same = truncated[truncated["season"] <= "2021-22"].sort_values(keys).reset_index(drop=True)
        cols = [c for c in early.columns if c in same.columns]
        pd.testing.assert_frame_equal(early[cols], same[cols], check_dtype=False)


# --------------------------------------------------------------------- API schema (round 2, finding 7)


def test_the_openapi_schema_reflects_the_flag_at_process_start_in_a_fresh_process():
    code = (
        "import json,os;"
        "from app.main import app;"
        "paths=app.openapi()['paths'];"
        "print(json.dumps(sorted(p for p in paths if 'intelligence' in p and ('catalog' in p or 'players' in p))))"
    )
    backend = Path(__file__).resolve().parents[2]
    env = {**__import__("os").environ, "PYTHONPATH": str(backend), "DATABASE_URL": "sqlite://", "PIVOT_TEST_DB_URL": ""}
    for flag, expected in (("false", False), ("true", True)):
        out = subprocess.run([sys.executable, "-c", code], env={**env, "PLAYER_INTELLIGENCE_PREVIEW": flag}, capture_output=True, text=True, cwd=str(backend), check=True)
        paths = json.loads(out.stdout.strip().splitlines()[-1])
        listed = any("/players/{player_id}/intelligence" in p for p in paths) and any("catalog" in p for p in paths)
        assert listed is expected, (flag, paths)


def test_registry_rejects_unknown_status_strings_safely():
    reg = ValidationRegistry({"version": "t", "quantities": {}})
    assert reg.facts("fg3_pct", "projected").tier == "withheld"
