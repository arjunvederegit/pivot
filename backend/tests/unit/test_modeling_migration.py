"""R9.1 migration: additive, backfills status from is_active, reverses cleanly."""

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.config import BACKEND_DIR, get_settings


def _config(url: str, monkeypatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return config


def test_existing_models_get_a_status_that_matches_is_active_and_nothing_else_changes(tmp_sqlite_url, monkeypatch):
    try:
        config = _config(tmp_sqlite_url, monkeypatch)
        command.upgrade(config, "e8b3f5c2a914")
        engine = create_engine(tmp_sqlite_url)
        with engine.begin() as conn:
            for mid, version, active in (("m1", "v1", 1), ("m2", "v2", 0)):
                conn.execute(
                    text(
                        "insert into model_versions (id, model_name, version, algorithm, feature_list,"
                        " validation_metrics, is_active, created_at, updated_at) values"
                        f" ('{mid}', 'player_impact', '{version}', 'a', '[]', '{{\"k\": 1}}', {active},"
                        " '2026-01-01', '2026-01-01')"
                    )
                )
        command.upgrade(config, "head")
        inspector = inspect(create_engine(tmp_sqlite_url))
        columns = {c["name"] for c in inspector.get_columns("model_versions")}
        assert {"status", "config", "lineage"} <= columns
        assert "candidate_estimates" in inspector.get_table_names()
        with create_engine(tmp_sqlite_url).connect() as conn:
            rows = {r[0]: r[1:] for r in conn.execute(text("select version, status, is_active, validation_metrics, config, lineage from model_versions"))}
        assert rows["v1"][0] == "active" and rows["v2"][0] == "retired"
        assert rows["v1"][2] == '{"k": 1}', "an existing row's metrics must be untouched"
        assert rows["v1"][3] in ("{}", {}) and rows["v1"][4] in ("{}", {}), "no lineage is invented for old models"
    finally:
        get_settings.cache_clear()


def test_the_migration_reverses_and_reapplies(tmp_sqlite_url, monkeypatch):
    try:
        config = _config(tmp_sqlite_url, monkeypatch)
        command.upgrade(config, "head")
        command.downgrade(config, "e8b3f5c2a914")
        inspector = inspect(create_engine(tmp_sqlite_url))
        assert "candidate_estimates" not in inspector.get_table_names()
        assert "status" not in {c["name"] for c in inspector.get_columns("model_versions")}
        command.upgrade(config, "head")
        command.check(config)  # no drift between the models and the migrations
    finally:
        get_settings.cache_clear()
