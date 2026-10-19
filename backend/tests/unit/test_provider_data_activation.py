"""R9.3A — activating provider data on another database is dry-run first, additive, allow-listed and reversible.

Each test builds two real SQLite files (a "source" holding provider data, a "target" that is the owner's database
before activation) with the application's own schema, so the tool is exercised against the real tables, constraints
and JSON/datetime columns — not against stubs.
"""

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db.models import (
    Base,
    CandidateEstimate,
    Contract,
    ContractYear,
    DataSyncRun,
    Injury,
    ModelVersion,
    Player,
    PlayerImpactEstimate,
    ProviderEntityId,
    ProviderStatSnapshot,
    Team,
)
from app.services import provider_data_activation as act

REVISION = "a7d3e1f9c204"
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def _make_db(path: Path) -> str:
    url = f"sqlite:///{path}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("create table alembic_version (version_num varchar(32) not null)"))
        conn.execute(text("insert into alembic_version values (:r)"), {"r": REVISION})
    engine.dispose()
    return url


def _seed_core(url: str, *, player_id: str = "p1", team_id: str = "t1") -> None:
    engine = create_engine(url)
    with Session(engine) as db:
        db.add(Team(id=team_id, nba_team_id=1, full_name="Alpha", abbreviation="AAA", nickname="A", city="A"))
        db.add(Player(id=player_id, nba_player_id=11, full_name="Player One", is_active=True))
        db.commit()
    engine.dispose()


def _seed_provider_rows(url: str) -> None:
    engine = create_engine(url)
    with Session(engine) as db:
        db.add(ProviderEntityId(id="pe1", entity_type="player", provider="balldontlie", provider_id="9", internal_id="p1", match_method="x", confidence="high", evidence={"k": [1, 2]}, observed_at=NOW))
        db.add(ProviderEntityId(id="pe2", entity_type="team", provider="balldontlie", provider_id="3", internal_id="t1", match_method="x", confidence="high", evidence={}, observed_at=NOW))
        db.add(Contract(id="c1", player_id="p1", contract_type=None, source_provider="balldontlie", source_name="BALLDONTLIE /contracts/teams", source_date=date(2026, 10, 19)))
        db.flush()
        db.add(ContractYear(id="cy1", contract_id="c1", season="2026-27", salary=10_000_000, salary_basis="cap_hit", source_provider="balldontlie", source_retrieved_at=NOW))
        db.add(Injury(id="i1", player_id="p1", status="Out", source_provider="balldontlie", source_retrieved_at=NOW))
        db.add(ProviderStatSnapshot(id="s1", provider="balldontlie", entity_type="player", internal_id="p1", season="2025-26", season_type="regular", stat_group="general/base", aggregation="per_game", games_played=10, values={"pts": 20.5}, retrieved_at=NOW))
        db.add(DataSyncRun(id="r1", job_name="sync_contracts", status="succeeded", detail={"season": "2026-27", "outcome": "complete"}))
        db.add(DataSyncRun(id="r2", job_name="train", status="succeeded", detail={"note": "the owner's own run"}))
        db.add(ModelVersion(id="m1", model_name="player_forecast_fg3_pct", version="cand-x", algorithm="a", status="candidate", is_active=False))
        db.add(ModelVersion(id="m2", model_name="player_impact", version="serving-y", algorithm="a", status="active", is_active=True))
        db.flush()
        db.add(CandidateEstimate(id="ce1", model_version_id="m1", player_id="p1", season="2025-26", target_season="2026-27", target="fg3_pct", output_kind="projected", estimate=0.36))
        db.commit()
    engine.dispose()


@pytest.fixture()
def pair(tmp_path: Path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    surl, turl = _make_db(source), _make_db(target)
    _seed_core(surl)
    _seed_core(turl)
    _seed_provider_rows(surl)
    return source, target, turl


def _count(path: Path, table: str) -> int:
    con = sqlite3.connect(path)
    try:
        return con.execute(f'select count(*) from "{table}"').fetchone()[0]
    finally:
        con.close()


class TestDryRun:
    def test_reports_what_would_be_added_and_writes_nothing(self, pair) -> None:
        source, target, turl = pair
        report = act.plan(source, turl)
        by = {t["table"]: t for t in report["tables"]}
        assert report["mode"] == "dry-run" and report["blocked"] is False
        assert by["provider_entity_ids"]["to_add"] == 2
        assert by["contract_years"]["to_add"] == 1
        assert by["data_sync_runs"]["source_rows"] == 1, "only the provider jobs' runs are additive"
        assert by["model_versions"]["source_rows"] == 1, "only candidate models are additive"
        assert _count(target, "contracts") == 0 and _count(target, "provider_entity_ids") == 0

    def test_refuses_the_same_file_and_missing_files_and_other_schemes(self, pair, tmp_path) -> None:
        source, target, turl = pair
        with pytest.raises(act.ActivationRefused, match="same file"):
            act.plan(source, f"sqlite:///{source}")
        with pytest.raises(act.ActivationRefused, match="does not exist"):
            act.plan(tmp_path / "nope.db", turl)
        with pytest.raises(act.ActivationRefused, match="only SQLite"):
            act.plan(source, "postgresql://u:p@h/db")
        with pytest.raises(act.ActivationRefused, match="in-memory"):
            act.plan(source, "sqlite:///:memory:")

    def test_a_schema_revision_mismatch_is_refused(self, pair) -> None:
        source, target, turl = pair
        con = sqlite3.connect(target)
        con.execute("update alembic_version set version_num='older'")
        con.commit()
        con.close()
        with pytest.raises(act.ActivationRefused, match="revisions differ"):
            act.plan(source, turl)


class TestApply:
    def test_adds_exactly_the_allow_listed_rows_and_leaves_everything_else_identical(self, pair, tmp_path) -> None:
        source, target, turl = pair
        con = sqlite3.connect(target)
        before = {t: con.execute(f'select count(*) from "{t}"').fetchone()[0] for t in ("players", "teams", "model_versions", "player_impact_estimates", "data_sync_runs")}
        con.close()
        result = act.apply(source, turl, tmp_path / "bk")
        assert result["untouched_tables_identical"] is True
        assert result["written"]["contract_years"] == 1 and result["written"]["candidate_estimates"] == 1
        assert _count(target, "contracts") == 1 and _count(target, "provider_stat_snapshots") == 1
        # only the candidate model came across; the active serving model and the owner's own run did not
        con = sqlite3.connect(target)
        names = {r[0] for r in con.execute("select version from model_versions")}
        runs = {r[0] for r in con.execute("select job_name from data_sync_runs")}
        con.close()
        assert names == {"cand-x"} and runs == {"sync_contracts"}
        assert _count(target, "players") == before["players"] and _count(target, "player_impact_estimates") == before["player_impact_estimates"]

    def test_json_and_datetime_columns_round_trip_unchanged(self, pair, tmp_path) -> None:
        source, target, turl = pair
        act.apply(source, turl, tmp_path / "bk")
        with Session(create_engine(turl)) as db:
            snap = db.get(ProviderStatSnapshot, "s1")
            link = db.get(ProviderEntityId, "pe1")
            assert snap.values == {"pts": 20.5} and link.evidence == {"k": [1, 2]}
            assert snap.retrieved_at.replace(tzinfo=None) == NOW.replace(tzinfo=None)

    def test_applying_twice_changes_nothing(self, pair, tmp_path) -> None:
        source, target, turl = pair
        act.apply(source, turl, tmp_path / "bk")
        again = act.apply(source, turl, tmp_path / "bk")
        assert again["total_written"] == 0
        assert _count(target, "contract_years") == 1

    def test_a_verified_backup_is_taken_first_and_restores_the_original(self, pair, tmp_path) -> None:
        source, target, turl = pair
        result = act.apply(source, turl, tmp_path / "bk")
        backup = Path(result["backup"]["path"])
        assert backup.exists() and result["backup"]["integrity_check"] == "ok"
        assert _count(backup, "contracts") == 0, "the backup is the pre-activation state"
        assert "cp " in result["rollback"]
        # restoring it returns the target to its original rows
        backup_bytes = backup.read_bytes()
        target.write_bytes(backup_bytes)
        assert _count(target, "contracts") == 0

    def test_an_existing_row_with_different_content_is_a_conflict_and_nothing_is_written(self, pair, tmp_path) -> None:
        source, target, turl = pair
        with Session(create_engine(turl)) as db:
            db.add(Contract(id="c1", player_id="p1", contract_type="standard", source_provider="other", source_name="other"))
            db.commit()
        report = act.plan(source, turl)
        assert report["blocked"] is True
        assert [t for t in report["tables"] if t["table"] == "contracts"][0]["conflicts"] == 1
        with pytest.raises(act.ActivationRefused, match="conflicting or dangling"):
            act.apply(source, turl, tmp_path / "bk")
        assert _count(target, "provider_entity_ids") == 0 and _count(target, "contract_years") == 0

    def test_a_row_for_a_player_the_target_does_not_have_is_refused(self, pair, tmp_path) -> None:
        source, target, turl = pair
        con = sqlite3.connect(target)
        con.execute("delete from players")
        con.commit()
        con.close()
        report = act.plan(source, turl)
        assert report["blocked"] is True
        assert any(t["dangling"] for t in report["tables"])
        with pytest.raises(act.ActivationRefused):
            act.apply(source, turl, tmp_path / "bk")

    def test_a_failure_after_the_inserts_rolls_everything_back(self, pair, tmp_path, monkeypatch) -> None:
        """If anything outside the allow-list moved, the transaction is rolled back, not committed."""
        source, target, turl = pair
        real = act._fingerprint
        calls = {"n": 0}

        def tampered(conn, skip):
            out = real(conn, skip)
            calls["n"] += 1
            if calls["n"] == 2:  # the 'after' reading
                out["players"] = (999, "x")
            return out

        monkeypatch.setattr(act, "_fingerprint", tampered)
        with pytest.raises(act.ActivationRefused, match="outside the allow-list"):
            act.apply(source, turl, tmp_path / "bk")
        assert _count(target, "contracts") == 0 and _count(target, "provider_entity_ids") == 0

    def test_an_impact_estimate_of_the_target_is_never_touched(self, pair, tmp_path) -> None:
        source, target, turl = pair
        with Session(create_engine(turl)) as db:
            mv = ModelVersion(id="tei", model_name="player_impact", version="live", algorithm="a", status="active", is_active=True)
            db.add(mv)
            db.flush()
            db.add(PlayerImpactEstimate(player_id="p1", model_version_id="tei", season="2025-26", tei=1.23, availability=0.9))
            db.commit()
        act.apply(source, turl, tmp_path / "bk")
        with Session(create_engine(turl)) as db:
            row = db.query(PlayerImpactEstimate).one()
            assert row.tei == 1.23
            assert db.get(ModelVersion, "tei").status == "active"


def test_apply_reads_through_its_own_transaction_connection(pair, tmp_path, monkeypatch) -> None:
    """Regression: inside its write transaction apply read existing rows through a *fresh* connection. SQLite lets
    that pass until the writer's page cache spills (an exclusive lock), so the small fixtures never failed and the
    first rehearsal on a copy of the real 71 MB database died with "database is locked". The deterministic form of
    the defect is the call shape: every read made after the transaction opens must use the transaction's connection.
    """
    from sqlalchemy.engine import Engine

    source, target, turl = pair
    kinds: list[bool] = []
    real = act._existing_rows

    def spy(db, table, ids):
        kinds.append(isinstance(db, Engine))
        return real(db, table, ids)

    monkeypatch.setattr(act, "_existing_rows", spy)
    act.apply(source, turl, tmp_path / "bk")
    in_transaction = kinds[-len(act.ADDITIVE_TABLES):]
    assert in_transaction and not any(in_transaction), "a read inside the write transaction used a second connection"


def test_the_allow_list_names_no_serving_table() -> None:
    forbidden = {"players", "teams", "rosters", "player_impact_estimates", "trade_proposals", "trade_assets", "scenarios", "player_season_stats"}
    assert not forbidden & set(act.ADDITIVE_TABLES)
