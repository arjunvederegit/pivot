"""R9.1 — candidate-only training cannot damage active state, and legacy training is unchanged by default."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.analytics.train import train_all
from app.db.models import CandidateEstimate, ModelVersion, PlayerImpactEstimate
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
        assert diff_fingerprints(fingerprint_before, protected_fingerprint(trained)) == []
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
        assert changed == ["model_versions: rows {} -> {}".format(before["model_versions"]["rows"], before["model_versions"]["rows"] + 1)]
        with pytest.raises(CandidateError):
            registry.reject_candidate(trained, "nonexistent", "x")
