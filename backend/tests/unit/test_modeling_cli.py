"""R9.1 — the operator commands: routing, refusals and exit codes."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cli
from app.db.models import CandidateEstimate, ModelVersion

from .modeling_support import make_frame, populate_db
from .test_cli import printed_json, run


@pytest.fixture()
def cli_db(monkeypatch, db: Session):
    """Point the CLI's session factory at the test database."""

    class _Session:
        def __enter__(self):
            return db

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cli, "SessionLocal", _Session)
    return db


def _exit_code(monkeypatch, *argv: str) -> int:
    with pytest.raises(SystemExit) as info:
        run(monkeypatch, *argv)
    return info.value.code


def test_backtest_without_a_stage_is_a_usage_error(monkeypatch, capsys, cli_db):
    assert _exit_code(monkeypatch, "backtest") == 2
    out = printed_json(capsys.readouterr().out)
    assert out["error"] == "usage" and "backtest <development|holdout>" in out["message"]


def test_backtest_on_an_empty_database_refuses_instead_of_inventing_a_result(monkeypatch, capsys, cli_db):
    assert _exit_code(monkeypatch, "backtest", "development") == 2
    assert "PanelValidationError" in printed_json(capsys.readouterr().out)["error"]


def test_a_holdout_run_without_a_development_run_is_refused(monkeypatch, capsys, cli_db, tmp_path):
    populate_db(cli_db, make_frame(n_players=80, n_seasons=10, seed=4))
    assert _exit_code(monkeypatch, "backtest", "holdout", "--out", str(tmp_path)) == 2
    assert "exactly one development run" in printed_json(capsys.readouterr().out)["message"]


def test_candidates_lists_nothing_on_a_fresh_database(monkeypatch, capsys, cli_db):
    run(monkeypatch, "candidates")
    assert printed_json(capsys.readouterr().out) == []


def test_train_candidate_requires_a_model_and_a_target(monkeypatch, capsys, cli_db):
    assert _exit_code(monkeypatch, "train-candidate") == 2
    out = printed_json(capsys.readouterr().out)
    assert out["error"] == "usage" and "train-candidate" in out["message"]


def test_train_candidate_writes_a_candidate_and_candidates_lists_it(monkeypatch, capsys, cli_db: Session, tmp_path):
    populate_db(cli_db, make_frame(n_players=110, n_seasons=8, seed=4))
    run(monkeypatch, "train-candidate", "--model", "regressed_persistence", "--target", "ts_pct", "--origin", "2023-24")
    record = printed_json(capsys.readouterr().out)
    assert record["reused_existing"] is False and record["estimates_written"] > 0
    model = cli_db.get(ModelVersion, record["version_id"])
    assert model.status == "candidate" and model.is_active is False
    assert cli_db.query(CandidateEstimate).count() == record["estimates_written"]
    run(monkeypatch, "candidates")
    listed = printed_json(capsys.readouterr().out)
    assert [c["version"] for c in listed] == [record["version"]]
    assert listed[0]["status"] == "candidate"


def test_train_candidate_refuses_an_unknown_model_with_a_nonzero_exit(monkeypatch, capsys, cli_db):
    populate_db(cli_db, make_frame(n_players=80, n_seasons=8, seed=4))
    assert _exit_code(monkeypatch, "train-candidate", "--model", "no_such", "--target", "ts_pct") == 2
    assert printed_json(capsys.readouterr().out)["error"] == "CandidateError"
    assert cli_db.scalars(select(ModelVersion)).first() is None


def test_a_flag_is_never_taken_as_the_value_of_the_previous_flag(monkeypatch, capsys, cli_db):
    assert _exit_code(monkeypatch, "train-candidate", "--model", "--target", "ts_pct") == 2
    assert "usage" in printed_json(capsys.readouterr().out)["error"]


def test_unknown_targets_and_models_are_refusals_not_tracebacks(monkeypatch, capsys, cli_db):
    assert _exit_code(monkeypatch, "backtest", "development", "--target", "nope") == 2
    out = printed_json(capsys.readouterr().out)
    assert out["error"] == "CandidateError" and "registered" in out["message"]
    assert _exit_code(monkeypatch, "train-candidate", "--model", "ridge_history", "--target", "nope") == 2
    assert "unknown target" in printed_json(capsys.readouterr().out)["message"]


def test_a_non_numeric_evaluation_cut_is_a_usage_error(monkeypatch, capsys, cli_db):
    assert _exit_code(monkeypatch, "backtest", "development", "--eval-min-target-minutes", "abc") == 2
    assert "needs a number" in printed_json(capsys.readouterr().out)["message"]


def test_a_database_without_the_r91_migration_gets_an_actionable_hint(monkeypatch, capsys, cli_db):
    from sqlalchemy.exc import OperationalError

    from app.modeling import registry

    def boom(db):
        raise OperationalError("select", {}, Exception("no such column: model_versions.status"))

    monkeypatch.setattr(registry, "list_candidates", boom)
    assert _exit_code(monkeypatch, "candidates") == 2
    out = printed_json(capsys.readouterr().out)
    assert out["error"] == "DatabaseError" and "make migrate" in out["hint"]


def test_a_holdout_without_exactly_one_development_run_is_refused_and_a_stale_one_is_too(monkeypatch, capsys, cli_db, tmp_path):
    populate_db(cli_db, make_frame(n_players=90, n_seasons=10, seed=4))
    run(monkeypatch, "backtest", "development", "--target", "pie", "--out", str(tmp_path))
    first = printed_json(capsys.readouterr().out)["pie"]
    assert first["family"] == "exploratory" and first["advanced"] is not None
    # data changes after development: the holdout must refuse to confirm a stale run
    from sqlalchemy import select

    from app.db.models import PlayerSeasonStats

    row = cli_db.scalars(select(PlayerSeasonStats).where(PlayerSeasonStats.stat_type == "advanced")).first()
    row.stats = {**row.stats, "PIE": row.stats["PIE"] + 0.01}
    cli_db.commit()
    assert _exit_code(monkeypatch, "backtest", "holdout", "--target", "pie", "--out", str(tmp_path)) == 2
    assert printed_json(capsys.readouterr().out)["error"] == "HoldoutPrerequisiteError"


def test_train_candidate_records_what_the_experiment_actually_decided(monkeypatch, capsys, cli_db, tmp_path):
    populate_db(cli_db, make_frame(n_players=90, n_seasons=10, seed=4))
    run(monkeypatch, "backtest", "development", "--target", "pie", "--out", str(tmp_path))
    directory = printed_json(capsys.readouterr().out)["pie"]["directory"]
    run(monkeypatch, "train-candidate", "--model", "ridge_history", "--target", "pie", "--origin", "2024-25", "--experiment", directory)
    record = printed_json(capsys.readouterr().out)
    model = cli_db.get(ModelVersion, record["version_id"])
    assert model.validation_metrics["evaluation_status"] in {"development_only", "failed_development_rule"}
    assert model.validation_metrics["experiment"].startswith("exp-development-pie-")
    # an experiment for a different target is refused, not recorded
    assert _exit_code(monkeypatch, "train-candidate", "--model", "ridge_history", "--target", "ts_pct", "--origin", "2024-25", "--experiment", directory) == 2
    assert printed_json(capsys.readouterr().out)["error"] == "CandidateError"
