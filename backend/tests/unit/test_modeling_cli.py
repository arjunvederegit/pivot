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
    assert "usage: backtest" in printed_json(capsys.readouterr().out)["error"]


def test_backtest_on_an_empty_database_refuses_instead_of_inventing_a_result(monkeypatch, capsys, cli_db):
    assert _exit_code(monkeypatch, "backtest", "development") == 2
    assert "PanelValidationError" in printed_json(capsys.readouterr().out)["error"]


def test_a_holdout_run_without_development_decisions_is_refused(monkeypatch, capsys, cli_db):
    populate_db(cli_db, make_frame(n_players=80, n_seasons=8, seed=4))
    assert _exit_code(monkeypatch, "backtest", "holdout") == 2
    assert "--decisions" in printed_json(capsys.readouterr().out)["error"]


def test_candidates_lists_nothing_on_a_fresh_database(monkeypatch, capsys, cli_db):
    run(monkeypatch, "candidates")
    assert printed_json(capsys.readouterr().out) == []


def test_train_candidate_requires_a_model_and_a_target(monkeypatch, capsys, cli_db):
    assert _exit_code(monkeypatch, "train-candidate") == 2
    assert "usage: train-candidate" in printed_json(capsys.readouterr().out)["error"]


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
