"""R8.1 — the R8 schema and the canonical layer on PostgreSQL.

Everything else in the suite runs on SQLite. The R8 migration rebuilds unique constraints with
``batch_alter_table`` — SQLite's table-copy mechanism — so "it works on SQLite" proved nothing
about Postgres. These tests run the real migration chain and the canonical write/read paths against a
live PostgreSQL server.

**Skipped unless ``PIVOT_TEST_POSTGRES_URL`` is set**, so CI and a plain ``pytest`` are unchanged:

    PIVOT_TEST_POSTGRES_URL=postgresql+psycopg://user@127.0.0.1:55432/postgres pytest tests/integration/test_postgres_compat.py

Safety: each test run creates its own uniquely named database on that server and drops *only that
database* afterwards. Point the URL at a disposable server, never at one holding data you want.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR

ADMIN_URL = os.environ.get("PIVOT_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="set PIVOT_TEST_POSTGRES_URL to run")

PRE_R8 = "f2c8b41d6a05"
R8 = "a9d4c6e18b53"
HEAD = "c5e2b7d91f34"


def alembic(url: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url, "CURRENT_SEASON": "2025-26"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, check=check,
    )


@pytest.fixture()
def pg_url():
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    name = f"r8test_{uuid.uuid4().hex[:12]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture()
def pg_engine(pg_url):
    engine = create_engine(pg_url)
    yield engine
    engine.dispose()


def _revision(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("select version_num from alembic_version")).scalar_one()


class TestMigrationChain:
    def test_the_whole_chain_applies_and_matches_the_models(self, pg_url, pg_engine):
        alembic(pg_url, "upgrade", "head")
        assert _revision(pg_engine) == HEAD
        checked = alembic(pg_url, "check")
        assert "No new upgrade operations detected" in checked.stdout + checked.stderr

    def test_upgrading_twice_is_a_no_op(self, pg_url, pg_engine):
        alembic(pg_url, "upgrade", "head")
        alembic(pg_url, "upgrade", "head")
        assert _revision(pg_engine) == HEAD


def _seed_pre_r8(engine) -> None:
    """Rows written through the PRE-R8 schema (no season_type column)."""
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(text(
            "insert into teams (id, nba_team_id, full_name, abbreviation, nickname, city, source_provider,"
            " created_at, updated_at) values ('t1', 1, 'Test One', 'ONE', 'One', 'X', 'nba_api', :n, :n)"), {"n": now})
        conn.execute(text(
            "insert into players (id, nba_player_id, full_name, is_active, source_provider, created_at, updated_at)"
            " values ('p1', 11, 'Pat One', true, 'nba_api', :n, :n), ('p2', 12, 'Sam Two', true, 'nba_api', :n, :n)"),
            {"n": now})
        for pid, stat in (("p1", "base"), ("p1", "advanced"), ("p2", "base")):
            conn.execute(text(
                "insert into player_season_stats (id, player_id, season, stat_type, games_played, minutes, stats,"
                " source_provider, created_at, updated_at) values (:id, :p, '2025-26', :s, 70, 30.5, '{\"PTS\": 20.5}',"
                " 'nba_api', :n, :n)"), {"id": f"s-{pid}-{stat}", "p": pid, "s": stat, "n": now})
        conn.execute(text(
            "insert into team_season_stats (id, team_id, season, stat_type, stats, source_provider, created_at, updated_at)"
            " values ('ts1', 't1', '2025-26', 'base', '{}', 'nba_api', :n, :n)"), {"n": now})


class TestModelRegistryOnPostgres:
    def test_the_real_algorithm_labels_fit_after_the_widening_migration(self, pg_url, pg_engine):
        alembic(pg_url, "upgrade", "head")
        long_labels = (
            "weighted z-score index (transparent, fixed weights)",  # 51
            "exponential decay in draft slot, fitted on within-class-normalised above-replacement window value",  # 97
        )
        with pg_engine.begin() as conn:
            for i, label in enumerate(long_labels):
                conn.execute(text(
                    "insert into model_versions (id, model_name, version, algorithm, training_period, feature_list, "
                    "validation_metrics, is_active, created_at, updated_at, trained_at) values "
                    "(:id, :n, 'v1', :a, 'x', '[]', '{}', true, now(), now(), now())"), {"id": f"m{i}", "n": f"model{i}", "a": label})
        with pg_engine.connect() as conn:
            assert conn.execute(text("select max(length(algorithm)) from model_versions")).scalar_one() == 97

    def test_before_the_widening_postgres_refuses_the_real_labels(self, pg_url, pg_engine):
        """The defect this migration fixes, demonstrated rather than asserted."""
        alembic(pg_url, "upgrade", R8)
        with pytest.raises(Exception, match="too long"), pg_engine.begin() as conn:
            conn.execute(text(
                "insert into model_versions (id, model_name, version, algorithm, training_period, feature_list, "
                "validation_metrics, is_active, created_at, updated_at, trained_at) values "
                "('m', 'p', 'v1', :a, 'x', '[]', '{}', true, now(), now(), now())"), {"a": "x" * 51})


class TestR8MigrationOnPostgres:
    def test_existing_rows_survive_and_are_backfilled_as_regular_season(self, pg_url, pg_engine):
        alembic(pg_url, "upgrade", PRE_R8)
        _seed_pre_r8(pg_engine)
        alembic(pg_url, "upgrade", R8)
        with pg_engine.connect() as conn:
            rows = conn.execute(text("select stat_type, season_type, minutes, stats->>'PTS' from player_season_stats order by id")).all()
            team_rows = conn.execute(text("select season_type from team_season_stats")).all()
        assert len(rows) == 3 and all(r[1] == "regular" for r in rows)
        assert {r[2] for r in rows} == {30.5} and {r[3] for r in rows} == {"20.5"}
        assert team_rows == [("regular",)]

    def test_columns_constraints_and_indexes_are_what_the_models_say(self, pg_url, pg_engine):
        alembic(pg_url, "upgrade", "head")
        insp = inspect(pg_engine)
        pss = {c["name"]: c for c in insp.get_columns("player_season_stats")}
        assert pss["season_type"]["nullable"] is False
        assert "regular" in str(pss["season_type"]["default"])
        uniques = {u["name"]: u["column_names"] for u in insp.get_unique_constraints("player_season_stats")}
        assert uniques == {"uq_pss_scope": ["player_id", "season", "season_type", "stat_type"]}
        tss = {u["name"]: u["column_names"] for u in insp.get_unique_constraints("team_season_stats")}
        assert tss == {"uq_tss_scope": ["team_id", "season", "season_type", "stat_type"]}
        pe = {u["name"]: u["column_names"] for u in insp.get_unique_constraints("provider_entity_ids")}
        assert pe == {"uq_provider_entity": ["entity_type", "provider", "provider_id"]}
        assert any(i["name"] == "ix_provider_entity_internal" for i in insp.get_indexes("provider_entity_ids"))
        media = {c["name"]: c for c in insp.get_columns("media_assets")}
        assert {"usage_status", "license_ref", "source_url", "width", "height"} <= set(media)
        assert media["usage_status"]["nullable"] is False

    def test_season_types_coexist_and_a_duplicate_scope_is_refused(self, pg_url, pg_engine):
        alembic(pg_url, "upgrade", PRE_R8)
        _seed_pre_r8(pg_engine)
        alembic(pg_url, "upgrade", R8)
        insert = text(
            "insert into player_season_stats (id, player_id, season, season_type, stat_type, stats, source_provider,"
            " created_at, updated_at) values (:id, 'p1', '2025-26', :t, 'base', '{}', 'nba_api', now(), now())")
        with pg_engine.begin() as conn:
            conn.execute(insert, {"id": "po", "t": "playoffs"})
            conn.execute(insert, {"id": "pre", "t": "preseason"})
        with pytest.raises(IntegrityError), pg_engine.begin() as conn:
            conn.execute(insert, {"id": "dup", "t": "playoffs"})
        with pg_engine.connect() as conn:
            kinds = conn.execute(text("select season_type from player_season_stats where player_id='p1' and stat_type='base' order by 1")).scalars().all()
        assert kinds == ["playoffs", "preseason", "regular"]

    def test_the_provider_crosswalk_refuses_a_second_binding_of_one_provider_id(self, pg_url, pg_engine):
        alembic(pg_url, "upgrade", "head")
        insert = text(
            "insert into provider_entity_ids (id, entity_type, provider, provider_id, internal_id, match_method,"
            " confidence, evidence, observed_at, created_at, updated_at) values (:id, 'player', 'balldontlie', '237', :i,"
            " 'name', 'high', '{}', now(), now(), now())")
        with pg_engine.begin() as conn:
            conn.execute(insert, {"id": "a", "i": "p1"})
        with pytest.raises(IntegrityError), pg_engine.begin() as conn:
            conn.execute(insert, {"id": "b", "i": "p2"})

    def test_downgrade_then_upgrade_on_disposable_data_loses_nothing(self, pg_url, pg_engine):
        alembic(pg_url, "upgrade", PRE_R8)
        _seed_pre_r8(pg_engine)
        alembic(pg_url, "upgrade", R8)
        alembic(pg_url, "downgrade", PRE_R8)
        assert _revision(pg_engine) == PRE_R8
        assert "season_type" not in {c["name"] for c in inspect(pg_engine).get_columns("player_season_stats")}
        alembic(pg_url, "upgrade", R8)
        with pg_engine.connect() as conn:
            assert conn.execute(text("select count(*) from player_season_stats where season_type='regular'")).scalar_one() == 3

    def test_a_downgrade_that_would_destroy_data_is_refused_and_leaves_the_schema_intact(self, pg_url, pg_engine):
        """Postgres DDL is transactional: the failed downgrade must roll back completely."""
        alembic(pg_url, "upgrade", PRE_R8)
        _seed_pre_r8(pg_engine)
        alembic(pg_url, "upgrade", R8)
        with pg_engine.begin() as conn:
            conn.execute(text(
                "insert into player_season_stats (id, player_id, season, season_type, stat_type, stats, source_provider,"
                " created_at, updated_at) values ('po', 'p1', '2025-26', 'playoffs', 'base', '{}', 'nba_api', now(), now())"))
        failed = alembic(pg_url, "downgrade", PRE_R8, check=False)
        assert failed.returncode != 0
        assert _revision(pg_engine) == R8, "the failed downgrade must not leave a half-applied schema"
        assert "season_type" in {c["name"] for c in inspect(pg_engine).get_columns("player_season_stats")}
        with pg_engine.connect() as conn:
            assert conn.execute(text("select count(*) from player_season_stats")).scalar_one() == 4


@pytest.fixture()
def pg_session(pg_url, pg_engine, monkeypatch):
    alembic(pg_url, "upgrade", "head")
    with Session(pg_engine, expire_on_commit=False) as session:
        yield session


class TestCanonicalLayerOnPostgres:
    """The same jobs and readers the SQLite suite exercises, against real Postgres semantics
    (timestamptz, JSONB-comparable JSON, strict types, real unique constraints)."""

    def test_ingestion_is_idempotent_and_keeps_season_types_apart(self, pg_session, monkeypatch):
        from app.canonical.seasons import SeasonType
        from app.ingestion import jobs
        from tests.unit.test_ingestion_jobs import FakeProvider, _roster

        db, provider = pg_session, FakeProvider()
        jobs.sync_teams(db, provider)
        jobs.sync_players(db, provider)
        rows = _roster(("1610612737", 201939), ("1610612738", 999999))
        for _ in range(2):
            jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=rows))
        from app.db.models import PlayerSeasonStats, RosterEntry
        assert db.query(RosterEntry).count() == 2, "second run confirms, never duplicates"
        for _ in range(2):
            jobs.sync_player_stats(db, ["2025-26"], provider)
        first = db.query(PlayerSeasonStats).count()
        jobs.sync_player_stats(db, ["2025-26"], provider)
        assert db.query(PlayerSeasonStats).count() == first
        class Typed(FakeProvider):
            async def fetch_player_stats(self, season, season_type=SeasonType.REGULAR):
                return await super().fetch_player_stats(season)
        jobs.sync_player_stats(db, ["2026-27"], Typed(), season_type=SeasonType.PRESEASON)
        kinds = {r.season_type for r in db.scalars(select(PlayerSeasonStats)).all()}
        assert kinds == {"regular", "preseason"}

    def test_a_rejected_snapshot_leaves_postgres_untouched(self, pg_session):
        from app.db.models import DataSyncRun, RosterEntry
        from app.ingestion import jobs
        from tests.unit.test_ingestion_jobs import FakeProvider, _roster

        db = pg_session
        jobs.sync_teams(db, FakeProvider())
        jobs.sync_players(db, FakeProvider())
        jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=_roster(("1610612737", 201939), ("1610612738", 999999))))
        with pytest.raises(jobs.SnapshotRejectedError):
            jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=[]))
        assert db.query(RosterEntry).filter_by(is_current=True).count() == 2
        assert db.query(DataSyncRun).filter_by(job_name="sync_rosters", status="failed").count() == 1

    def test_freshness_reads_timestamptz_correctly(self, pg_session):
        from app.canonical.provenance import Freshness
        from app.ingestion import jobs
        from app.services.data_provenance import dataset_freshness
        from tests.unit.test_ingestion_jobs import FakeProvider, _roster

        db = pg_session
        jobs.sync_teams(db, FakeProvider())
        jobs.sync_players(db, FakeProvider())
        now = datetime.now(UTC)
        rows = [{**r, "source_retrieved_at": now} for r in _roster(("1610612737", 201939), ("1610612738", 999999))]
        jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=rows))
        meta = dataset_freshness(db, "2025-26", now=now)["rosters"]
        assert meta.freshness is Freshness.FRESH
        assert meta.oldest_retrieved_at is not None and meta.oldest_retrieved_at.tzinfo is not None
        assert meta.last_refresh["status"] == "succeeded"
        upcoming = dataset_freshness(db, "2026-27", now=datetime(2026, 10, 18, tzinfo=UTC))["player_stats"]
        assert upcoming.freshness is Freshness.NOT_YET_AVAILABLE

    def test_roster_history_can_be_reconstructed(self, pg_session):
        from app.canonical.rosters import roster_as_of
        from app.db.models import Team
        from app.ingestion import jobs
        from tests.unit.test_ingestion_jobs import FakeProvider, _roster

        db = pg_session
        jobs.sync_teams(db, FakeProvider())
        jobs.sync_players(db, FakeProvider())
        both = ("1610612737", 201939), ("1610612738", 999999)
        jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=_roster(*both, ("1610612737", 300001))))
        jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=_roster(*both)))
        atl = db.query(Team).filter_by(nba_team_id=1610612737).one()
        assert len(roster_as_of(db, datetime.now(UTC), atl.id, "2025-26").entries) == 1
        assert len(roster_as_of(db, date(2020, 1, 1) and datetime(2020, 1, 1, tzinfo=UTC), atl.id, "2025-26").entries) == 0
