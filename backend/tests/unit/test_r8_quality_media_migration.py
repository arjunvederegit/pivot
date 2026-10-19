"""R8: the new data-quality checks, media-rights metadata, and the additive migration."""

from datetime import UTC, datetime, timedelta

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.assets.indexer import coverage
from app.config import BACKEND_DIR, get_settings
from app.db.models import (
    Contract,
    MediaAsset,
    PlayerSeasonStats,
    ProviderEntityId,
    RosterEntry,
)
from app.ingestion.quality import validate_data
from tests.conftest import make_player, make_team


def _checks(db) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for issue in validate_data(db):
        found.setdefault(issue["check"], []).append(issue)
    return found


class TestR8DataQualityChecks:
    def test_a_stale_dataset_is_flagged_with_its_age(self, db, cap_params):
        team = make_team(db, 1, "AAA")
        make_player(db, 1, "One", team)
        for entry in db.query(RosterEntry):
            entry.source_retrieved_at = datetime.now(UTC) - timedelta(days=89)
        db.commit()
        issue = _checks(db)["stale_dataset"][0]
        assert "rosters" in issue["message"] and "89 days" in issue["message"]

    def test_a_fresh_dataset_raises_nothing(self, db, cap_params):
        team = make_team(db, 1, "AAA")
        make_player(db, 1, "One", team)
        assert not any(
            "rosters" in i["message"] for i in _checks(db).get("stale_dataset", [])
        )

    def test_a_stat_type_with_no_definition_is_an_error(self, db, cap_params):
        team = make_team(db, 1, "AAA")
        player = make_player(db, 1, "One", team)
        db.add(
            PlayerSeasonStats(
                player_id=player.id, season="2025-26", stat_type="per36", stats={}
            )
        )
        db.commit()
        issue = _checks(db)["unknown_stat_type"][0]
        assert issue["severity"] == "error" and "per36" in issue["message"]

    def test_two_contract_headers_for_one_player_are_flagged(self, db, cap_params):
        team = make_team(db, 1, "AAA")
        player = make_player(db, 1, "One", team, salary=1_000_000)
        db.add(Contract(player_id=player.id, source_name="second source"))
        db.commit()
        assert "duplicate_contract_header" in _checks(db)

    def test_a_provider_link_to_a_deleted_player_is_an_error(self, db, cap_params):
        db.add(
            ProviderEntityId(
                entity_type="player",
                provider="balldontlie",
                provider_id="1",
                internal_id="gone",
                match_method="exact_name",
            )
        )
        db.commit()
        assert _checks(db)["dangling_provider_link"][0]["severity"] == "error"


class TestMediaRightsMetadata:
    def test_new_assets_default_to_unverified_and_say_so_in_coverage(self, db):
        team = make_team(db, 1, "AAA")
        db.add(
            MediaAsset(
                entity_type="team",
                team_id=team.id,
                nba_id=1,
                file_path="aaa.png",
                match_method="abbreviation",
                source_label="aaa",
                alt_text="AAA logo",
            )
        )
        db.commit()
        report = coverage(db)
        assert report["usage_status"] == {"unverified": 1}
        assert report["assets_cleared_for_public_display"] == 0
        assert "local development only" in report["rights_note"]

    def test_a_licensed_asset_is_counted_and_clears_the_warning(self, db):
        db.add(
            MediaAsset(
                entity_type="team",
                file_path="aaa.png",
                match_method="abbreviation",
                source_label="aaa",
                alt_text="AAA logo",
                usage_status="licensed",
                license_ref="agreement-123",
            )
        )
        db.commit()
        report = coverage(db)
        assert report["assets_cleared_for_public_display"] == 1 and report["rights_note"] is None

    def test_reindexing_does_not_reset_a_recorded_licence(self, db, tmp_path, monkeypatch):
        """`index_assets` upserts on (entity_type, file_path); it must not clobber usage_status."""
        from PIL import Image

        from app.assets.indexer import index_assets

        logos = tmp_path / "logos"
        logos.mkdir()
        Image.new("RGBA", (40, 30), (10, 20, 30, 255)).save(logos / "aaa.png")
        monkeypatch.setenv("ASSET_LOGOS_DIR", str(logos))
        monkeypatch.setenv("ASSET_PLAYER_IMAGES_DIR", str(tmp_path / "none"))
        get_settings.cache_clear()
        try:
            make_team(db, 1, "AAA")
            index_assets(db)
            asset = db.query(MediaAsset).one()
            assert (asset.width, asset.height) == (40, 30)
            assert asset.usage_status == "unverified"
            asset.usage_status, asset.license_ref = "licensed", "agreement-123"
            db.commit()
            index_assets(db)
            asset = db.query(MediaAsset).one()
            assert (asset.usage_status, asset.license_ref) == ("licensed", "agreement-123")
        finally:
            get_settings.cache_clear()


class TestR8Migration:
    def _config(self, url: str, monkeypatch) -> Config:
        monkeypatch.setenv("DATABASE_URL", url)
        get_settings.cache_clear()
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        return config

    def test_existing_statistics_are_preserved_and_backfilled_as_regular_season(
        self, tmp_sqlite_url, monkeypatch
    ):
        try:
            config = self._config(tmp_sqlite_url, monkeypatch)
            command.upgrade(config, "f2c8b41d6a05")  # the head before R8
            engine = create_engine(tmp_sqlite_url)
            now = "2026-08-24 00:00:00"
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO players (id, nba_player_id, full_name, is_active, "
                        "source_provider, created_at, updated_at) VALUES "
                        "('p1', 1, 'Pre R8', 1, 'nba_api', :n, :n)"
                    ),
                    {"n": now},
                )
                conn.execute(
                    text(
                        "INSERT INTO player_season_stats (id, player_id, season, stat_type, "
                        "games_played, minutes, stats, source_provider, created_at, updated_at) "
                        "VALUES ('s1', 'p1', '2025-26', 'base', 60, 30.0, '{}', 'nba_api', :n, :n)"
                    ),
                    {"n": now},
                )
            command.upgrade(config, "head")
            with engine.begin() as conn:
                row = conn.execute(
                    text("SELECT season_type, games_played FROM player_season_stats")
                ).one()
                assert tuple(row) == ("regular", 60)
                # The widened unique key admits a playoff row for the same player-season-type.
                conn.execute(
                    text(
                        "INSERT INTO player_season_stats (id, player_id, season, season_type, "
                        "stat_type, stats, source_provider, created_at, updated_at) VALUES "
                        "('s2', 'p1', '2025-26', 'playoffs', 'base', '{}', 'nba_api', :n, :n)"
                    ),
                    {"n": now},
                )
            columns = {c["name"] for c in inspect(engine).get_columns("media_assets")}
            assert {"usage_status", "license_ref", "source_url", "width", "height"} <= columns
            assert "provider_entity_ids" in inspect(engine).get_table_names()
        finally:
            get_settings.cache_clear()

    def test_the_migration_reverses_cleanly(self, tmp_sqlite_url, monkeypatch):
        try:
            config = self._config(tmp_sqlite_url, monkeypatch)
            command.upgrade(config, "head")
            command.downgrade(config, "f2c8b41d6a05")
            tables = set(inspect(create_engine(tmp_sqlite_url)).get_table_names())
            assert "provider_entity_ids" not in tables
            command.upgrade(config, "head")
        finally:
            get_settings.cache_clear()

    def test_the_r83_migration_is_additive_and_reverses_without_touching_existing_rows(
        self, tmp_sqlite_url, monkeypatch
    ):
        try:
            config = self._config(tmp_sqlite_url, monkeypatch)
            command.upgrade(config, "c5e2b7d91f34")
            engine = create_engine(tmp_sqlite_url)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "insert into players (id, nba_player_id, full_name, is_active, source_provider,"
                        " created_at, updated_at) values ('p1', 1, 'Pre Existing', 1, 'nba_api',"
                        " '2026-01-01', '2026-01-01')"
                    )
                )
                conn.execute(
                    text(
                        "insert into contracts (id, player_id, source_name, source_provider,"
                        " created_at, updated_at) values ('c1', 'p1', 's', 'file',"
                        " '2026-01-01', '2026-01-01')"
                    )
                )
                conn.execute(
                    text(
                        "insert into contract_years (id, contract_id, season, salary, source_provider,"
                        " created_at, updated_at) values ('y1', 'c1', '2026-27', 5, 'file',"
                        " '2026-01-01', '2026-01-01')"
                    )
                )
            command.upgrade(config, "head")
            inspector = inspect(create_engine(tmp_sqlite_url))
            years = {c["name"] for c in inspector.get_columns("contract_years")}
            assert {"base_salary", "total_cash", "salary_basis"} <= years
            assert "return_date" in {c["name"] for c in inspector.get_columns("injuries")}
            assert "provider_stat_snapshots" in inspector.get_table_names()
            with create_engine(tmp_sqlite_url).connect() as conn:
                row = conn.execute(
                    text("select salary, base_salary, total_cash, salary_basis from contract_years")
                ).one()
            assert tuple(row) == (5, None, None, None), "existing rows are untouched, new columns NULL"
            command.downgrade(config, "c5e2b7d91f34")
            inspector = inspect(create_engine(tmp_sqlite_url))
            assert "provider_stat_snapshots" not in inspector.get_table_names()
            assert "base_salary" not in {c["name"] for c in inspector.get_columns("contract_years")}
            with create_engine(tmp_sqlite_url).connect() as conn:
                assert conn.execute(text("select salary from contract_years")).scalar_one() == 5
        finally:
            get_settings.cache_clear()

    def test_models_and_migrations_have_not_drifted(self, tmp_sqlite_url, monkeypatch):
        """The same check CI runs as `alembic check`."""
        try:
            config = self._config(tmp_sqlite_url, monkeypatch)
            command.upgrade(config, "head")
            command.check(config)
        finally:
            get_settings.cache_clear()
