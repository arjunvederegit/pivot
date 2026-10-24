"""R9.1 — candidate-only training cannot damage active state, and legacy training is unchanged by default."""

import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.analytics.train import train_all
from app.db.models import CandidateEstimate, ModelVersion, PlayerImpactEstimate, PlayerSeasonStats
from app.modeling import registry
from app.modeling.pairs import FeatureConfig, PairCache
from app.modeling.registry import (
    CandidateError,
    ProtectedStateViolation,
    diff_fingerprints,
    protected_fingerprint,
    train_candidate,
)

from .modeling_support import make_frame, populate_db

LAST = "2025-26"
CFG = FeatureConfig(min_target_minutes=300)


@pytest.fixture()
def trained(db: Session) -> Session:
    """A database holding a legacy-trained ACTIVE model and its estimates (disposable, in-memory)."""
    populate_db(db, make_frame(n_players=120, n_seasons=10, seed=2))
    summary = train_all(db)
    assert "error" not in summary
    return db


def _active_snapshot(db: Session) -> dict:
    versions = db.execute(
        text("select id, model_name, version, is_active, status, validation_metrics, trained_at from model_versions where status != 'candidate' order by id")
    ).fetchall()
    estimates = db.execute(
        text("select id, player_id, season, model_version_id, tei, tei_low, tei_high, updated_at from player_impact_estimates order by id")
    ).fetchall()
    return {"versions": [tuple(r) for r in versions], "estimates": [tuple(r) for r in estimates]}


class TestCandidateTrainingIsNonDestructive:
    def test_training_a_candidate_changes_no_active_model_or_estimate(self, trained: Session):
        """THE safety test: snapshot active state, train, compare byte for byte."""
        before = _active_snapshot(trained)
        fingerprint_before = protected_fingerprint(trained)
        assert before["estimates"], "the fixture must have active estimates to protect"

        record = train_candidate(trained, "ridge_boxscore", "impact_proxy", LAST, features=CFG)

        assert _active_snapshot(trained) == before
        # Everything — including every OTHER candidate — is unchanged; only the new model's own rows exist.
        assert diff_fingerprints(fingerprint_before, protected_fingerprint(trained, record.version_id)) == []
        assert record.estimates_written > 0 and not record.reused_existing

    def test_a_candidate_is_never_active_and_never_in_the_serving_tables(self, trained: Session):
        record = train_candidate(trained, "eb_shrinkage", "ts_pct", LAST, features=CFG)
        model = trained.get(ModelVersion, record.version_id)
        assert model.is_active is False and model.status == "candidate"
        assert model.model_name.startswith("player_forecast_")
        assert model.model_name != "player_impact"
        serving_ids = {e.model_version_id for e in trained.scalars(select(PlayerImpactEstimate)).all()}
        assert model.id not in serving_ids
        assert trained.scalar(select(CandidateEstimate).where(CandidateEstimate.model_version_id == model.id)) is not None

    def test_every_legacy_active_model_is_still_active_afterwards(self, trained: Session):
        active_before = {(m.model_name, m.version) for m in trained.scalars(select(ModelVersion).where(ModelVersion.is_active))}
        train_candidate(trained, "ridge_history", "impact_proxy", LAST, features=CFG)
        train_candidate(trained, "regressed_persistence_age", "ts_pct", LAST, features=CFG)
        active_after = {(m.model_name, m.version) for m in trained.scalars(select(ModelVersion).where(ModelVersion.is_active))}
        assert active_after == active_before

    def test_training_is_idempotent_and_deletes_nothing(self, trained: Session):
        first = train_candidate(trained, "ridge_history", "impact_proxy", LAST, features=CFG)
        n = trained.query(CandidateEstimate).count()
        second = train_candidate(trained, "ridge_history", "impact_proxy", LAST, features=CFG)
        assert second.reused_existing and second.version_id == first.version_id
        assert trained.query(CandidateEstimate).count() == n

    def test_a_candidate_records_what_is_needed_to_reproduce_it(self, trained: Session):
        record = train_candidate(trained, "eb_shrinkage", "ts_pct", LAST, features=CFG, seed=123)
        m = trained.get(ModelVersion, record.version_id)
        assert m.data_snapshot_id and m.data_snapshot_id.startswith("ds-")
        assert m.config["seed"] == 123 and m.config["target"] == "ts_pct" and m.config["origin"] == LAST
        assert m.lineage["environment"]["packages"]["numpy"]
        assert m.lineage["data_snapshot"]["fingerprint"]
        assert m.lineage["compared_against_active_version"]
        assert "shrinkage_constant_k" in m.lineage["fit"]["fitted"]
        assert m.code_commit is None or isinstance(m.code_commit, str)
        assert m.training_period.endswith(f"..{LAST}")

    def test_the_same_specification_on_the_same_data_gives_the_same_estimates(self, trained: Session):
        record = train_candidate(trained, "ridge_boxscore", "ts_pct", LAST, features=CFG, seed=5)
        rows = {e.player_id: e.estimate for e in trained.scalars(select(CandidateEstimate).where(CandidateEstimate.model_version_id == record.version_id))}
        # Retrain from nothing into a second, identical database and compare.
        other_engine_rows = rows  # same session; determinism is asserted via a fresh fit below
        from app.modeling.panel import PlayerSeasonPanel

        again, _, _ = registry._forward_estimates(PlayerSeasonPanel.from_db(trained), "ridge_boxscore", "ts_pct", LAST, CFG, 5, 0.8)
        for r in again.itertuples():
            assert abs(other_engine_rows[r.player_id] - r.pred) < 1e-12


class TestTheGuardActuallyGuards:
    def test_a_training_run_that_deletes_an_active_estimate_is_rolled_back_and_refused(self, trained: Session, monkeypatch):
        """Mutation test: sabotage candidate training and require the safety net to catch it."""
        victim = trained.scalars(select(PlayerImpactEstimate)).first()
        victim_id = victim.id
        before = _active_snapshot(trained)
        original = PairCache.pairs

        done = []

        def sabotage(self, origin, target):
            if not done:  # `pairs` is called once per training origin; delete the row once
                done.append(1)
                trained.delete(trained.get(PlayerImpactEstimate, victim_id))
                trained.flush()
            return original(self, origin, target)

        monkeypatch.setattr(PairCache, "pairs", sabotage)
        with pytest.raises(ProtectedStateViolation, match="player_impact_estimates"):
            train_candidate(trained, "ridge_history", "impact_proxy", LAST, features=CFG)
        monkeypatch.undo()
        assert _active_snapshot(trained) == before, "the deletion was not rolled back"
        assert trained.query(CandidateEstimate).count() == 0

    def test_a_run_that_silently_edits_an_active_value_is_caught(self, trained: Session, monkeypatch):
        row = trained.scalars(select(PlayerImpactEstimate)).first()
        row_id = row.id
        before = _active_snapshot(trained)
        original = PairCache.pairs

        done = []

        def sabotage(self, origin, target):
            if not done:
                done.append(1)
                trained.get(PlayerImpactEstimate, row_id).tei += 0.001
                trained.flush()
            return original(self, origin, target)

        monkeypatch.setattr(PairCache, "pairs", sabotage)
        with pytest.raises(ProtectedStateViolation):
            train_candidate(trained, "ridge_history", "impact_proxy", LAST, features=CFG)
        monkeypatch.undo()
        assert _active_snapshot(trained) == before

    def test_a_run_that_flips_an_active_flag_is_caught(self, trained: Session, monkeypatch):
        active = trained.scalars(select(ModelVersion).where(ModelVersion.is_active)).first()
        original = PairCache.pairs

        def sabotage(self, origin, target):
            trained.get(ModelVersion, active.id).is_active = False
            trained.flush()
            return original(self, origin, target)

        monkeypatch.setattr(PairCache, "pairs", sabotage)
        with pytest.raises(ProtectedStateViolation, match="model_versions"):
            train_candidate(trained, "ridge_history", "impact_proxy", LAST, features=CFG)
        monkeypatch.undo()
        assert trained.get(ModelVersion, active.id).is_active is True

    def test_unknown_models_and_seasons_are_refused_before_any_write(self, trained: Session):
        before = protected_fingerprint(trained)
        with pytest.raises(CandidateError):
            train_candidate(trained, "no_such_model", "impact_proxy", LAST)
        with pytest.raises(CandidateError, match="no statistics"):
            train_candidate(trained, "ridge_history", "impact_proxy", "2031-32")
        with pytest.raises(CandidateError, match="does not apply"):
            train_candidate(trained, "incumbent_tei", "ts_pct", LAST, features=CFG)
        assert diff_fingerprints(before, protected_fingerprint(trained)) == []
        assert trained.query(CandidateEstimate).count() == 0


class TestTheCandidatePipelineReproducesTheIncumbent:
    def test_the_incumbent_run_through_the_candidate_path_equals_the_stored_active_estimates(self, trained: Session):
        """The backtest's 'existing Pivot estimate' is production's own code on an as-of frame."""
        record = train_candidate(trained, "incumbent_tei", "impact_proxy", LAST, features=CFG)
        active = {e.player_id: e.tei for e in trained.scalars(select(PlayerImpactEstimate))}
        cand = {e.player_id: e.estimate * 2.5 for e in trained.scalars(select(CandidateEstimate).where(CandidateEstimate.model_version_id == record.version_id))}
        assert set(cand) == set(active)
        worst = max(abs(cand[p] - active[p]) for p in active)
        assert worst < 1e-9, worst


class TestLegacyTrainingIsUnchangedUnlessAsked:
    def _retrain_after_changing_data(self, db: Session, **kwargs) -> dict:
        from app.db.models import PlayerSeasonStats

        row = db.scalars(select(PlayerSeasonStats).where(PlayerSeasonStats.stat_type == "advanced", PlayerSeasonStats.season == LAST)).first()
        row.stats = {**row.stats, "PIE": row.stats["PIE"] + 0.05}
        db.commit()
        return train_all(db, **kwargs)

    def test_by_default_the_old_versions_estimates_are_still_deleted(self, trained: Session):
        """Documents the legacy behaviour so nobody mistakes the R9.1 path for a changed `make train`."""
        old = {e.model_version_id for e in trained.scalars(select(PlayerImpactEstimate))}
        self._retrain_after_changing_data(trained)
        remaining = {e.model_version_id for e in trained.scalars(select(PlayerImpactEstimate))}
        assert remaining.isdisjoint(old)

    def test_preserve_history_keeps_every_prior_versions_estimates(self, trained: Session):
        old = {e.model_version_id for e in trained.scalars(select(PlayerImpactEstimate))}
        n_before = trained.query(PlayerImpactEstimate).count()
        self._retrain_after_changing_data(trained, preserve_history=True)
        versions = {e.model_version_id for e in trained.scalars(select(PlayerImpactEstimate))}
        assert old <= versions and len(versions) == len(old) + 1
        assert trained.query(PlayerImpactEstimate).count() > n_before
        # …and exactly one version of the impact model is active.
        actives = trained.scalars(select(ModelVersion).where(ModelVersion.model_name == "player_impact", ModelVersion.is_active)).all()
        assert len(actives) == 1

    def test_status_and_is_active_never_disagree_after_legacy_training(self, trained: Session):
        for m in trained.scalars(select(ModelVersion)):
            assert (m.status == "active") == bool(m.is_active), (m.model_name, m.status, m.is_active)
        self._retrain_after_changing_data(trained)
        for m in trained.scalars(select(ModelVersion)):
            assert (m.status == "active") == bool(m.is_active), (m.model_name, m.status, m.is_active)

    def test_legacy_garbage_collection_cannot_touch_candidates(self, trained: Session):
        record = train_candidate(trained, "ridge_history", "ts_pct", LAST, features=CFG)
        n = trained.query(CandidateEstimate).count()
        self._retrain_after_changing_data(trained)
        assert trained.query(CandidateEstimate).count() == n
        assert trained.get(ModelVersion, record.version_id).status == "candidate"


class TestPromotionIsNotAutomaticOrImplemented:
    def test_blockers_are_listed_and_nothing_is_promoted(self, trained: Session):
        record = train_candidate(trained, "ridge_history", "impact_proxy", LAST, features=CFG)
        blockers = registry.promotion_blockers(trained, record.version)
        assert any("no audited promotion procedure" in b for b in blockers)
        assert any("locked holdout" in b for b in blockers)
        assert trained.get(ModelVersion, record.version_id).is_active is False

    def test_rejecting_a_candidate_touches_only_that_row(self, trained: Session):
        record = train_candidate(trained, "ridge_history", "impact_proxy", LAST, features=CFG)
        before = protected_fingerprint(trained)
        registry.reject_candidate(trained, record.version, "does not beat persistence")
        assert trained.get(ModelVersion, record.version_id).status == "rejected"
        # A rejected row is no longer a candidate, so it enters the protected set — and nothing else moved.
        changed = diff_fingerprints(before, protected_fingerprint(trained))
        assert changed == ["model_versions: content changed"], "rejecting touches that one candidate row and nothing else"
        with pytest.raises(CandidateError):
            registry.reject_candidate(trained, "nonexistent", "x")


# ----------------------------------------------------------------------------- review hardening


class TestTheGuardCoversEverything:
    @pytest.mark.parametrize(
        "statement",
        [
            "update players set full_name = full_name || 'x' where id = (select id from players limit 1)",
            "update player_season_stats set minutes = minutes + 1 where id = (select id from player_season_stats limit 1)",
            "update teams set full_name = full_name || 'x' where id = (select id from teams limit 1)",
            "update player_archetypes set label = label || 'x' where id = (select id from player_archetypes limit 1)",
            "update model_versions set validation_metrics = '{}' where is_active = 1 and model_name = 'team_projection'",
            "delete from player_impact_estimates where id = (select id from player_impact_estimates limit 1)",
        ],
    )
    def test_a_change_to_any_protected_table_is_caught_and_rolled_back(self, trained: Session, monkeypatch, statement):
        """Review finding M4/M6: the guard was only ever sabotage-tested on two tables."""
        before = protected_fingerprint(trained)
        original, done = PairCache.pairs, []

        def sabotage(self, origin, target):
            if not done:
                done.append(1)
                trained.execute(text(statement))
                trained.flush()
            return original(self, origin, target)

        monkeypatch.setattr(PairCache, "pairs", sabotage)
        with pytest.raises(ProtectedStateViolation):
            train_candidate(trained, "ridge_history", "impact_proxy", LAST, features=CFG)
        monkeypatch.undo()
        assert diff_fingerprints(before, protected_fingerprint(trained)) == []

    def test_tampering_with_an_earlier_candidate_is_caught(self, trained: Session, monkeypatch):
        """Review finding H2: candidate rows used to be outside the fingerprint entirely."""
        first = train_candidate(trained, "ridge_history", "impact_proxy", LAST, features=CFG)
        before = protected_fingerprint(trained)
        original, done = PairCache.pairs, []

        def sabotage(self, origin, target):
            if not done:
                done.append(1)
                trained.execute(text("delete from candidate_estimates"))
                trained.execute(text("update model_versions set lineage = '{}' where status = 'candidate'"))
                trained.flush()
            return original(self, origin, target)

        monkeypatch.setattr(PairCache, "pairs", sabotage)
        with pytest.raises(ProtectedStateViolation, match="candidate_estimates"):
            train_candidate(trained, "eb_shrinkage", "ts_pct", LAST, features=CFG)
        monkeypatch.undo()
        assert diff_fingerprints(before, protected_fingerprint(trained)) == []
        assert trained.query(CandidateEstimate).filter_by(model_version_id=first.version_id).count() > 0

    def test_a_new_row_that_would_be_served_is_refused_by_the_isolation_invariant(self, trained: Session):
        from app.modeling.registry import _assert_isolated

        active = ModelVersion(model_name="player_forecast_x", version="v-a", algorithm="a", is_active=True, status="candidate")
        wrong_name = ModelVersion(model_name="player_impact", version="v-b", algorithm="a", is_active=False, status="candidate")
        retired = ModelVersion(model_name="player_forecast_x", version="v-c", algorithm="a", is_active=False, status="retired")
        for bad in (active, wrong_name, retired):
            with pytest.raises(ProtectedStateViolation, match="not isolated"):
                _assert_isolated(trained, bad)
        ok = ModelVersion(model_name="player_forecast_x", version="v-d", algorithm="a", is_active=False, status="candidate")
        trained.add(ok)
        trained.flush()
        _assert_isolated(trained, ok)
        player = trained.scalars(select(PlayerImpactEstimate)).first()
        trained.add(PlayerImpactEstimate(player_id=player.player_id, season="2025-26", model_version_id=ok.id, tei=0.0))
        trained.flush()
        with pytest.raises(ProtectedStateViolation, match="serving estimate"):
            _assert_isolated(trained, ok)
        trained.rollback()


class TestFailureLeavesTheSessionUsable:
    def test_an_unexpected_error_after_the_write_rolls_back_and_the_session_still_works(self, trained: Session, monkeypatch):
        """Review finding M6: only violations used to roll back."""
        monkeypatch.setattr(registry, "_assert_isolated", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
        with pytest.raises(ValueError, match="boom"):
            train_candidate(trained, "ridge_history", "impact_proxy", LAST, features=CFG)
        monkeypatch.undo()
        assert trained.query(CandidateEstimate).count() == 0
        assert trained.query(ModelVersion).filter_by(status="candidate").count() == 0
        train_candidate(trained, "ridge_history", "impact_proxy", LAST, features=CFG)  # not stuck in a failed transaction

    def test_non_finite_estimates_are_refused_before_anything_is_stored(self, trained: Session, monkeypatch):
        import numpy as np

        from app.modeling.predictors import PREDICTORS, Fitted

        class _Nan(Fitted):
            params = {}

            def predict(self, frame):
                return np.full(len(frame), np.nan)

        real = PREDICTORS["regressed_persistence"].fit
        monkeypatch.setattr(PREDICTORS["regressed_persistence"], "fit", lambda train, ctx: _Nan())
        with pytest.raises(CandidateError, match="non-finite"):
            train_candidate(trained, "regressed_persistence", "ts_pct", LAST, features=CFG)
        monkeypatch.setattr(PREDICTORS["regressed_persistence"], "fit", real)
        assert trained.query(CandidateEstimate).count() == 0

    def test_a_session_with_uncommitted_changes_is_refused_rather_than_silently_rolled_back(self, trained: Session):
        player = trained.scalars(select(PlayerImpactEstimate)).first().player
        player.full_name = "Edited but not committed"
        with pytest.raises(CandidateError, match="uncommitted"):
            train_candidate(trained, "ridge_history", "impact_proxy", LAST, features=CFG)
        trained.rollback()

    def test_too_little_data_is_a_refusal_not_a_fit(self, trained: Session):
        with pytest.raises(CandidateError, match="training transitions|training rows"):
            train_candidate(trained, "ridge_history", "impact_proxy", "2017-18", features=CFG)


class TestReuse:
    def test_reuse_refuses_when_retraining_would_now_give_different_numbers(self, trained: Session, monkeypatch):
        """Review finding 8: an unchanged id used to return stale estimates after a code change."""
        first = train_candidate(trained, "ridge_history", "impact_proxy", LAST, features=CFG)
        before = protected_fingerprint(trained)
        real = registry._forward_estimates

        def shifted(*args, **kwargs):
            out, params, counts = real(*args, **kwargs)
            return out.assign(pred=out["pred"] + 0.01), params, counts

        monkeypatch.setattr(registry, "_forward_estimates", shifted)
        with pytest.raises(CandidateError, match="different estimates"):
            train_candidate(trained, "ridge_history", "impact_proxy", LAST, features=CFG)
        monkeypatch.undo()
        assert diff_fingerprints(before, protected_fingerprint(trained)) == []
        assert first.version

    def test_the_version_depends_on_the_data_it_was_trained_on(self, trained: Session):
        first = train_candidate(trained, "ridge_history", "ts_pct", LAST, features=CFG)
        row = trained.scalars(select(PlayerSeasonStats).where(PlayerSeasonStats.stat_type == "advanced", PlayerSeasonStats.season == "2024-25")).first()
        row.stats = {**row.stats, "PIE": row.stats["PIE"] + 0.01}
        trained.commit()
        second = train_candidate(trained, "ridge_history", "ts_pct", LAST, features=CFG)
        assert first.version != second.version and first.snapshot_id != second.snapshot_id

    def test_an_evaluation_can_be_recorded_later_without_touching_anything_else(self, trained: Session, tmp_path):
        record = train_candidate(trained, "eb_shrinkage", "ts_pct", LAST, features=CFG)
        assert trained.get(ModelVersion, record.version_id).validation_metrics == {"evaluation_status": "unevaluated"}
        fingerprint = trained.scalar(select(ModelVersion.data_snapshot_id).where(ModelVersion.id == record.version_id))
        experiment = _fake_experiment(tmp_path, "ts_pct", "development", fingerprint=_data_fingerprint(trained), candidates={"eb_shrinkage": {"advance": False}})
        before = protected_fingerprint(trained, record.version_id)
        again = train_candidate(trained, "eb_shrinkage", "ts_pct", LAST, features=CFG, experiment_dir=experiment)
        assert again.reused_existing and again.version_id == record.version_id
        assert trained.get(ModelVersion, record.version_id).validation_metrics["evaluation_status"] == "failed_development_rule"
        assert diff_fingerprints(before, protected_fingerprint(trained, record.version_id)) == []
        assert fingerprint


def _data_fingerprint(db: Session) -> str:
    from app.modeling.panel import PlayerSeasonPanel
    from app.modeling.provenance import DataSnapshot

    return DataSnapshot.of_panel(PlayerSeasonPanel.from_db(db).frame).fingerprint


def _fake_experiment(tmp_path, target, stage, fingerprint, candidates):
    directory = tmp_path / f"exp-{stage}-{target}-x-y"
    directory.mkdir()
    (directory / "decisions.json").write_text(json.dumps({"target": target, "family": "exploratory", "candidates": candidates}))
    (directory / "config.json").write_text(json.dumps({"stage": stage}))
    (directory / "data_snapshot.json").write_text(json.dumps({"fingerprint": fingerprint}))
    return directory


class TestEvaluationRecordedHonestly:
    def test_the_status_reflects_what_the_experiment_decided(self, trained: Session, tmp_path):
        fp = _data_fingerprint(trained)
        cases = [
            ("development", {"ridge_history": {"advance": True}}, "ridge_history", "development_only"),
            ("development", {"ridge_history": {"advance": False}}, "ridge_history", "failed_development_rule"),
            ("development", {"ridge_history": {"advance": True}}, "persistence", "not_evaluated_as_candidate"),
            ("holdout", {"ridge_history": {"confirmed": True}}, "ridge_history", "holdout_confirmed"),
            ("holdout", {"ridge_history": {"confirmed": False}}, "ridge_history", "holdout_not_confirmed"),
        ]
        for i, (stage, candidates, spec, expected) in enumerate(cases):
            directory = _fake_experiment(tmp_path / str(i) if (tmp_path / str(i)).mkdir() is None else tmp_path, "ts_pct", stage, fp, candidates)
            assert registry.evaluation_from_experiment(directory, spec, "ts_pct", fp)["evaluation_status"] == expected

    def test_an_experiment_on_other_data_or_another_target_is_not_taken_as_evidence(self, trained: Session, tmp_path):
        fp = _data_fingerprint(trained)
        stale = _fake_experiment(tmp_path, "ts_pct", "development", "0" * 64, {"ridge_history": {"advance": True}})
        assert registry.evaluation_from_experiment(stale, "ridge_history", "ts_pct", fp)["evaluation_status"].startswith("stale:")
        other = tmp_path / "other"
        other.mkdir()
        wrong_target = _fake_experiment(other, "pie", "development", fp, {})
        with pytest.raises(CandidateError, match="evaluated 'pie'"):
            registry.evaluation_from_experiment(wrong_target, "ridge_history", "ts_pct", fp)
        with pytest.raises(CandidateError, match="cannot read"):
            registry.evaluation_from_experiment(tmp_path / "missing", "ridge_history", "ts_pct", fp)


class TestWhatIsStored:
    def test_estimates_carry_the_right_periods_and_kind(self, trained: Session):
        record = train_candidate(trained, "ridge_history", "ts_pct", LAST, features=CFG)
        rows = trained.scalars(select(CandidateEstimate).where(CandidateEstimate.model_version_id == record.version_id)).all()
        assert {r.season for r in rows} == {LAST} and {r.target_season for r in rows} == {"2026-27"}
        assert {r.output_kind for r in rows} == {"projected"} and {r.target for r in rows} == {"ts_pct"}
        assert all(r.interval_low <= r.estimate <= r.interval_high and r.interval_level == 0.8 for r in rows)

    def test_the_recorded_feature_list_is_what_the_model_actually_reads(self, trained: Session):
        from app.modeling.predictors import PREDICTORS

        for spec in ("ridge_history", "eb_shrinkage", "league_mean"):
            record = train_candidate(trained, spec, "ts_pct", LAST, features=CFG)
            assert trained.get(ModelVersion, record.version_id).feature_list == list(PREDICTORS[spec].inputs)
        assert "z_PIE" not in PREDICTORS["ridge_history"].inputs and "z_PIE" in PREDICTORS["ridge_boxscore"].inputs

    def test_every_candidate_version_fits_the_column_postgresql_enforces(self):
        """SQLite ignores VARCHAR length; PostgreSQL rejects the insert (the class of bug R8.1 found)."""
        from app.modeling.predictors import PREDICTORS
        from app.modeling.targets import TARGETS

        limit = ModelVersion.__table__.c.version.type.length
        for spec in PREDICTORS:
            for target in TARGETS:
                assert len(f"cand-{spec}-{target}-" + "0" * 12) <= limit, (spec, target)
        assert ModelVersion.__table__.c.model_name.type.length >= len("player_forecast_impact_proxy")

    def test_the_candidate_forecast_equals_the_backtest_forecast_at_a_non_final_origin(self, trained: Session):
        """The registry re-implements the forecast path; this pins it to the backtest's, bit for bit."""
        from app.modeling.backtest import BacktestConfig, run_backtest
        from app.modeling.panel import PlayerSeasonPanel

        panel = PlayerSeasonPanel.from_db(trained)
        origin = "2021-22"
        forward, _, _ = registry._forward_estimates(panel, "ridge_history", "ts_pct", origin, CFG, 20260719, 0.8)
        fold = run_backtest(
            panel, BacktestConfig(target="ts_pct", models=("ridge_history",), test_origins=(origin,), features=CFG, bootstrap_draws=10)
        ).predictions
        merged = forward.merge(fold, on="player_id", suffixes=("_f", "_b"))
        assert len(merged) == len(forward) == len(fold)
        assert (merged["pred_f"] - merged["pred_b"]).abs().max() == 0.0
        assert (merged["lo_f"] - merged["lo_b"]).abs().max() == 0.0


class TestRejection:
    def test_an_active_or_retired_model_cannot_be_rejected(self, trained: Session):
        active = trained.scalars(select(ModelVersion).where(ModelVersion.is_active)).first()
        with pytest.raises(CandidateError, match="not a candidate"):
            registry.reject_candidate(trained, active.version, "no")
        assert trained.get(ModelVersion, active.id).is_active is True
