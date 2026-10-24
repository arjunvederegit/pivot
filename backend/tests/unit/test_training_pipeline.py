"""R5-5. `train_all` end to end, including the fallbacks nobody exercised.

`analytics/train.py` sat at **36 % coverage** — the module that writes every model version
the serving path reads. The parts that were untested are exactly the ones that matter when
data is thin: what happens when a calibration cannot be fitted, whether an uncalibrated
fallback is *labelled* as one, and whether retraining on unchanged data creates a second
identity for the same model.
"""

from datetime import UTC, datetime

import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.train import (
    _content_version,
    _gc_superseded_estimates,
    _stable,
    _team_tei_transitions,
    train_all,
)
from app.db.models import (
    ModelVersion,
    PlayerArchetype,
    PlayerImpactEstimate,
    Standing,
    TeamSeasonStats,
)


@pytest.fixture()
def trainable(db: Session, seeded_league: dict) -> dict:
    """The seeded league plus the team-level rows the calibrations read."""
    for season in ("2023-24", "2024-25", "2025-26"):
        for offset, team in enumerate((seeded_league["team_a"], seeded_league["team_b"])):
            db.add(
                TeamSeasonStats(
                    team_id=team.id,
                    season=season,
                    stat_type="advanced",
                    stats={"NET_RATING": -2.0 + offset * 4.0 + len(season) * 0.1},
                )
            )
            db.add(
                Standing(
                    team_id=team.id,
                    season=season,
                    wins=35 + offset * 10,
                    losses=47 - offset * 10,
                    win_pct=(35 + offset * 10) / 82,
                )
            )
    db.commit()
    return seeded_league


class TestTrainAll:
    def test_an_empty_database_is_an_error_not_a_model(self, db: Session):
        assert "no ingested player stats" in train_all(db)["error"]
        assert db.query(ModelVersion).count() == 0

    def test_it_registers_every_model_the_serving_path_reads(
        self, db: Session, trainable: dict
    ):
        train_all(db)
        names = {m.model_name for m in db.query(ModelVersion).filter_by(is_active=True)}
        assert names == {
            "player_impact",
            "player_archetype",
            "tei_to_net_rating",
            "team_projection",
            "pick_value_curve",
        }

    def test_every_registered_algorithm_label_fits_its_column(self, db: Session, trainable: dict):
        """SQLite never enforced VARCHAR(50), so 51- and 97-character labels shipped; PostgreSQL
        rejects them and `make train` could not register a model there (found in R8.1)."""
        train_all(db)
        limit = ModelVersion.__table__.c.algorithm.type.length
        labels = {m.algorithm for m in db.query(ModelVersion)}
        assert labels and all(len(label) <= limit for label in labels), {
            label: len(label) for label in labels if len(label) > limit
        }
        assert max(len(label) for label in labels) > 50, "the case that broke Postgres is exercised"

    def test_it_writes_impact_estimates_and_archetypes(self, db: Session, trainable: dict):
        summary = train_all(db)
        assert summary["impact"]["players_scored"] > 0
        assert db.query(PlayerImpactEstimate).count() == summary["impact"]["players_scored"]
        assert db.query(PlayerArchetype).count() > 0

    def test_retraining_unchanged_data_reuses_the_same_version(
        self, db: Session, trainable: dict
    ):
        """A version string must identify a model, not the minute it was trained. Before
        the content hash, three models trained in one run shared one string."""
        first = train_all(db)["version"]
        after_first = db.query(ModelVersion).filter_by(model_name="player_impact").count()
        second = train_all(db)["version"]
        assert first == second
        # The count must not grow. It is not 1: the fixture registers its own
        # `test-impact-v1` so the evaluation path has something to read.
        assert db.query(ModelVersion).filter_by(model_name="player_impact").count() == (
            after_first
        )

    def test_only_one_version_of_each_model_stays_active(self, db: Session, trainable: dict):
        train_all(db)
        for season_shift in (1.0, 2.0):
            for stats in db.scalars(
                select(TeamSeasonStats).where(TeamSeasonStats.stat_type == "advanced")
            ).all():
                stats.stats = {"NET_RATING": stats.stats["NET_RATING"] + season_shift}
            db.commit()
            train_all(db)
        for name in ("player_impact", "team_projection", "pick_value_curve"):
            active = db.query(ModelVersion).filter_by(model_name=name, is_active=True).all()
            assert len(active) == 1, name

    def test_superseded_impact_estimates_are_garbage_collected(
        self, db: Session, trainable: dict
    ):
        """Nothing removed them, so every retrain added a full copy: 1,536 rows for 512
        players across three versions."""
        train_all(db)
        first_count = db.query(PlayerImpactEstimate).count()
        for stats in db.scalars(
            select(TeamSeasonStats).where(TeamSeasonStats.stat_type == "advanced")
        ).all():
            stats.stats = {"NET_RATING": stats.stats["NET_RATING"] + 3.0}
        db.commit()
        train_all(db)
        assert db.query(PlayerImpactEstimate).count() == first_count

    def test_an_unfittable_calibration_is_labelled_not_hidden(
        self, db: Session, trainable: dict
    ):
        """Two teams cannot support a 30-team wins regression, and the fallback must say
        `calibrated: false` rather than presenting 2.7 as a measurement."""
        train_all(db)
        projection = db.scalar(
            select(ModelVersion).where(
                ModelVersion.model_name == "team_projection", ModelVersion.is_active
            )
        )
        assert projection is not None
        assert projection.validation_metrics["calibrated"] is False
        assert projection.validation_metrics["slope"] == pytest.approx(2.7)

    def test_the_pick_curve_reports_why_it_could_not_be_fitted(
        self, db: Session, trainable: dict
    ):
        """The fixture players carry no draft position, so there is no estimation set —
        and the model version must say so instead of registering the committed constants
        as though they had been re-measured here."""
        train_all(db)
        curve = db.scalar(
            select(ModelVersion).where(
                ModelVersion.model_name == "pick_value_curve", ModelVersion.is_active
            )
        )
        assert curve is not None
        assert curve.validation_metrics["calibrated"] is False
        assert "reason" in curve.validation_metrics

    def test_a_fitted_pick_curve_carries_the_diagnostic_that_fails(
        self, db: Session, trainable: dict
    ):
        """When the curve *can* be fitted, the model version must carry the round-only
        comparison — the diagnostic the curve does not clearly win."""
        for index, player in enumerate(trainable["roster_a"] + trainable["roster_b"]):
            player.draft_year = 2016 + (index % 8)
            player.draft_round = 1 if index % 2 == 0 else 2
            player.draft_number = 1 + (index % 60)
        db.commit()
        train_all(db)
        metrics = db.scalar(
            select(ModelVersion).where(
                ModelVersion.model_name == "pick_value_curve", ModelVersion.is_active
            )
        ).validation_metrics
        if metrics.get("calibrated"):
            assert "round_only_baseline" in metrics
            assert "curve_minus_round_only" in metrics
            assert "does not significantly beat" in metrics["not_established"]
        else:
            assert "reason" in metrics

    def test_the_wins_mapping_is_returned_for_the_operator(self, db: Session, trainable: dict):
        summary = train_all(db)
        assert "slope" in summary["wins_mapping"]
        assert summary["archetypes"]["players_labeled"] > 0


class TestVersioning:
    def test_the_version_identifies_the_model_not_the_minute(self):
        a = _content_version("m", "algo", "2023..2025", ["x"], {"r2": 0.5})
        b = _content_version("m", "algo", "2023..2025", ["x"], {"r2": 0.5})
        c = _content_version("m", "algo", "2023..2025", ["x"], {"r2": 0.9})
        assert a == b
        assert a != c

    def test_two_models_trained_together_get_different_strings(self):
        assert _content_version("impact", "a", "p", [], {}) != _content_version(
            "archetype", "a", "p", [], {}
        )

    def test_metrics_are_rounded_so_noise_does_not_mint_a_version(self):
        assert _stable({"r2": 0.123456789}) == {"r2": 0.123457}
        assert _stable([1.000000004, {"x": 2.0000000001}]) == [1.0, {"x": 2.0}]
        assert _stable("text") == "text"


class TestTransitions:
    def test_a_frame_without_team_membership_yields_no_transitions(self, db: Session):
        frame = pd.DataFrame([{"player_id": "p", "season": "2024-25", "total_minutes": 100.0}])
        assert _team_tei_transitions(db, frame, ["2024-25"], {}).empty

    def test_transitions_need_a_net_rating_on_both_ends(self, db: Session, trainable: dict):
        from app.analytics.features import build_player_season_features

        frame = build_player_season_features(db)
        transitions = _team_tei_transitions(db, frame, ["2023-24", "2024-25"], {})
        assert transitions.empty, "no net ratings supplied means no pairs"


class TestGarbageCollection:
    def test_it_keeps_the_active_version_and_removes_the_rest(
        self, db: Session, seeded_league: dict
    ):
        stale = ModelVersion(
            model_name="player_impact",
            version="stale",
            algorithm="x",
            training_period="p",
            trained_at=datetime.now(UTC),
            is_active=False,
        )
        db.add(stale)
        db.flush()
        db.add(
            PlayerImpactEstimate(
                player_id=seeded_league["roster_a"][0].id,
                season="2025-26",
                model_version_id=stale.id,
                tei=1.0,
            )
        )
        db.commit()
        before = db.query(PlayerImpactEstimate).count()
        removed = _gc_superseded_estimates(db)
        db.commit()
        assert removed == 1
        assert db.query(PlayerImpactEstimate).count() == before - 1
