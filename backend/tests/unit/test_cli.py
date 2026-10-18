"""R5-5. The operational CLI.

`app/cli.py` was at **0 % coverage** — every documented operational command
(`make train`, `make score`, `make import-draft-picks`, `make seed-config`) enters here,
and nothing checked that a command name reaches the function it claims to, that an
unrecognised command exits non-zero, or that a refusal exits with the code its caller
branches on.

The commands that require network access are exercised through the dispatch table rather
than by calling a provider: what is tested is the CLI's own behaviour — routing, argument
handling and exit codes — not the jobs, which have their own suite.
"""

import json

import pytest
from sqlalchemy.orm import Session

from app import cli


@pytest.fixture()
def cli_db(monkeypatch, db: Session):
    """Point `SessionLocal` at the test session so commands act on the fixture database."""

    class _Session:
        def __enter__(self):
            return db

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cli, "SessionLocal", _Session)
    return db


def run(monkeypatch, *argv: str) -> None:
    monkeypatch.setattr(cli.sys, "argv", ["cli", *argv])
    cli.main()


def printed_json(out: str):
    """The command's own output, separated from the structured log lines beside it.

    `configure_logging` writes single-line JSON to stdout; the commands print
    `json.dumps(..., indent=2)`, whose first line is exactly `{` or `[`. Splitting there
    is what a reader of the terminal does, and it keeps these tests honest about what the
    operator actually sees.
    """
    lines = out.splitlines()
    # Column zero, not stripped: with `indent=2` a nested object's `{` is indented, so an
    # unindented one is the top level. Log lines start `{"ts": ...` on one line.
    starts = [i for i, line in enumerate(lines) if line in ("{", "[")]
    if not starts:
        return json.loads(out)
    block = "\n".join(lines[starts[-1] :])
    decoder = json.JSONDecoder()
    return decoder.raw_decode(block)[0]


class TestUsage:
    def test_no_command_prints_usage_and_exits_one(self, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exit_info:
            run(monkeypatch)
        assert exit_info.value.code == 1
        assert "Usage: python -m app.cli" in capsys.readouterr().out

    def test_an_unknown_command_exits_one_and_names_itself(self, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exit_info:
            run(monkeypatch, "sync-the-moon")
        assert exit_info.value.code == 1
        assert "unknown command: sync-the-moon" in capsys.readouterr().out

    def test_every_documented_command_is_reachable(self, monkeypatch, capsys):
        """The docstring is the operator's manual; a command named there that the
        dispatcher does not recognise is a broken instruction."""
        documented = {
            line.strip().split()[0]
            for line in (cli.__doc__ or "").splitlines()
            if line.startswith("  ") and line.strip() and not line.startswith("    ")
        }
        documented -= {"sync-<job>"}  # a placeholder for the individual sync jobs
        for command in sorted(documented):
            monkeypatch.setattr(cli.sys, "argv", ["cli", command])
            try:
                cli.main()
            except SystemExit as exited:
                # `exited` is the exception, not pytest's ExceptionInfo, so the code is
                # `exited.code`. It read `.value.code` until R6, and never fired because
                # no documented command happened to exit in this loop on a machine with
                # every optional snapshot present — which is not the machine CI runs on.
                assert exited.code != 1 or "unknown command" not in (
                    capsys.readouterr().out
                ), command
            except Exception:
                # Reaching an implementation and failing there is fine here; being
                # rejected as unknown is not.
                pass
            assert "unknown command" not in capsys.readouterr().out, command


class TestAnalyticsCommands:
    def test_build_features_reports_an_empty_database(self, monkeypatch, capsys, cli_db):
        run(monkeypatch, "build-features")
        assert "no ingested player stats" in printed_json(capsys.readouterr().out)["error"]

    def test_train_reports_an_empty_database(self, monkeypatch, capsys, cli_db):
        run(monkeypatch, "train")
        assert "error" in printed_json(capsys.readouterr().out)

    def test_score_reports_an_empty_database(self, monkeypatch, capsys, cli_db):
        run(monkeypatch, "score")
        assert "run `make sync-data`" in printed_json(capsys.readouterr().out)["error"]

    def test_validate_data_emits_json(self, monkeypatch, capsys, cli_db):
        run(monkeypatch, "validate-data")
        payload = printed_json(capsys.readouterr().out)
        assert isinstance(payload, list)
        assert any(item["check"] == "team_count" for item in payload)

    def test_train_on_a_seeded_league_registers_models(
        self, monkeypatch, capsys, cli_db, seeded_league
    ):
        run(monkeypatch, "train")
        summary = printed_json(capsys.readouterr().out)
        assert summary["impact"]["players_scored"] > 0


class TestDraftPickCommands:
    def test_import_draft_picks_exits_two_when_the_snapshot_is_absent(
        self, monkeypatch, capsys, cli_db, tmp_path
    ):
        """Exit 2 rather than 1: a missing optional dataset is not a usage error, and
        `make` branches on the difference."""
        with pytest.raises(SystemExit) as exit_info:
            run(monkeypatch, "import-draft-picks", str(tmp_path / "absent.html"))
        assert exit_info.value.code == 2
        payload = printed_json(capsys.readouterr().out)
        assert "no draft-pick snapshot" in payload["error"]
        assert "gitignored" in payload["hint"]

    def test_import_draft_picks_reads_the_path_it_is_given(
        self, monkeypatch, capsys, cli_db, tmp_path
    ):
        from tests.conftest import make_team
        from tests.unit.test_draft_pick_ownership import ROW, SECTION, _para

        make_team(cli_db, 1610612737, "ATL", "Atlanta Hawks")
        make_team(cli_db, 1610612759, "SAS", "San Antonio Spurs")
        markup = SECTION.format(
            team="Atlanta Hawks",
            rows=ROW.format(
                year=2027,
                incoming="",
                outgoing=_para(
                    "2027 first round draft pick to San Antonio",
                    "Atlanta's 2027 1st round pick to San Antonio [x]",
                ),
            ),
        )
        path = tmp_path / "realgm.html"
        path.write_text(markup, encoding="utf-8")
        run(monkeypatch, "import-draft-picks", str(path))
        assert printed_json(capsys.readouterr().out)["verified"] == 1

    def test_pick_ownership_reports_a_year(self, monkeypatch, capsys, cli_db):
        from tests.conftest import make_team

        make_team(cli_db, 1610612737, "ATL", "Atlanta Hawks")
        run(monkeypatch, "pick-ownership", "2029", "1")
        payload = printed_json(capsys.readouterr().out)
        assert payload["draft_year"] == 2029
        assert payload["teams"]["ATL"]["own_pick_retained"] is True

    def test_pick_ownership_defaults_to_next_year(self, monkeypatch, capsys, cli_db):
        from datetime import date

        run(monkeypatch, "pick-ownership")
        assert printed_json(capsys.readouterr().out)["draft_year"] == date.today().year + 1


class TestMaintenanceCommands:
    def test_purge_fixtures_is_a_dry_run_by_default(self, monkeypatch, capsys, cli_db):
        run(monkeypatch, "purge-fixtures")
        out = capsys.readouterr().out
        assert "Dry run" in out
        assert "--apply to delete" in out

    def test_purge_fixtures_with_apply_says_nothing_about_a_dry_run(
        self, monkeypatch, capsys, cli_db
    ):
        run(monkeypatch, "purge-fixtures", "--apply")
        assert "Dry run" not in capsys.readouterr().out

    def test_contract_coverage_reports_without_importing(
        self, monkeypatch, capsys, cli_db, seeded_league
    ):
        run(monkeypatch, "contract-coverage")
        out = capsys.readouterr().out
        payload = printed_json(out)
        assert "roster_players_without_salary" in payload

    def test_seed_demo_refuses_a_database_holding_provider_rows(
        self, monkeypatch, capsys, cli_db, seeded_league
    ):
        """Exit 2, and the refusal is printed rather than raised as a traceback."""
        with pytest.raises(SystemExit) as exit_info:
            run(monkeypatch, "seed-demo")
        assert exit_info.value.code == 2
        assert "seed-demo refused" in capsys.readouterr().out

    def test_index_assets_runs_against_missing_directories(
        self, monkeypatch, capsys, cli_db, tmp_path
    ):
        from app.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("ASSET_LOGOS_DIR", str(tmp_path / "nope"))
        monkeypatch.setenv("ASSET_PLAYER_IMAGES_DIR", str(tmp_path / "also-nope"))
        try:
            run(monkeypatch, "index-assets")
        finally:
            get_settings.cache_clear()
        summary = printed_json(capsys.readouterr().out)
        assert summary["teams_indexed"] == 0
        assert summary["player_dirs_scanned"] == 0


class TestSyncDispatch:
    def test_a_single_sync_job_prints_its_row_count(self, monkeypatch, capsys, cli_db):
        from app.ingestion import jobs

        monkeypatch.setattr(jobs, "sync_teams", lambda db: 30)
        run(monkeypatch, "sync-teams")
        assert "sync-teams: 30 rows" in capsys.readouterr().out

    def test_sync_all_prints_its_results(self, monkeypatch, capsys, cli_db):
        from app.ingestion import jobs

        monkeypatch.setattr(jobs, "sync_all", lambda db: {"teams": 30, "players": 500})
        run(monkeypatch, "sync-all")
        assert printed_json(capsys.readouterr().out)["players"] == 500


class TestNetworkCommandsAreRefusedOffline:
    """Some commands reach a third party, and the suite runs every documented command."""

    def test_declared_network_commands_refuse_when_offline(self, monkeypatch, capsys):
        for command in sorted(cli.NETWORK_COMMANDS):
            with pytest.raises(SystemExit) as exit_info:
                run(monkeypatch, command)
            assert exit_info.value.code == 3
            payload = printed_json(capsys.readouterr().out)
            assert payload["refused"] == command
            assert "third-party" in payload["reason"]

    def test_every_fetching_command_is_declared(self):
        """A new command that reaches a third party must be added to the set, or the
        suite will start making requests on someone else's servers."""
        assert set(cli.NETWORK_COMMANDS) == {
            "fetch-transactions",
            "lineup-availability",
            "refresh",
            "link-balldontlie",
            "balldontlie-evaluate",
        }

    def test_the_guard_is_off_by_default(self, monkeypatch):
        monkeypatch.delenv("ROSTERLAB_OFFLINE", raising=False)
        assert cli.offline() is False
        monkeypatch.setenv("ROSTERLAB_OFFLINE", "0")
        assert cli.offline() is False
        monkeypatch.setenv("ROSTERLAB_OFFLINE", "1")
        assert cli.offline() is True


class TestR8Commands:
    def test_data_status_reports_every_dataset_and_provider(self, monkeypatch, capsys, cli_db):
        run(monkeypatch, "data-status")
        payload = printed_json(capsys.readouterr().out)
        assert set(payload["datasets"]) >= {"rosters", "standings", "player_stats", "contracts"}
        assert payload["datasets"]["rosters"]["freshness"] == "missing"
        assert {p["provider"] for p in payload["providers"]} >= {"nba_api", "balldontlie"}

    def test_refresh_with_no_dataset_names_the_choices(self, monkeypatch, capsys, cli_db):
        monkeypatch.delenv("ROSTERLAB_OFFLINE", raising=False)
        with pytest.raises(SystemExit) as exit_info:
            run(monkeypatch, "refresh")
        assert exit_info.value.code == 2
        assert "rosters" in printed_json(capsys.readouterr().out)["datasets"]

    def test_refresh_rejects_an_unknown_dataset(self, monkeypatch, capsys, cli_db):
        monkeypatch.delenv("ROSTERLAB_OFFLINE", raising=False)
        with pytest.raises(SystemExit):
            run(monkeypatch, "refresh", "rosters,the-moon")
        assert "the-moon" in printed_json(capsys.readouterr().out)["error"]

    def test_refresh_runs_the_named_jobs_and_reports_freshness(self, monkeypatch, capsys, cli_db):
        monkeypatch.delenv("ROSTERLAB_OFFLINE", raising=False)
        from app.ingestion import jobs

        monkeypatch.setattr(jobs, "sync_rosters", lambda db: 7)
        monkeypatch.setattr(jobs, "sync_standings", lambda db: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(SystemExit) as exit_info:
            run(monkeypatch, "refresh", "rosters,standings")
        payload = printed_json(capsys.readouterr().out)
        assert payload["results"]["rosters"] == {"status": "succeeded", "rows": 7}
        assert payload["results"]["standings"]["status"] == "failed"
        assert "boom" in payload["results"]["standings"]["error"]
        assert exit_info.value.code == 2, "a failed dataset must fail the command"

    def test_the_trial_plan_prints_cost_without_a_key(self, monkeypatch, capsys):
        run(monkeypatch, "balldontlie-plan", "trial")
        plan = printed_json(capsys.readouterr().out)
        assert plan["tier"] == "trial" and plan["estimated_requests"] > 0

    def test_evaluate_and_link_refuse_without_a_key_and_request_nothing(
        self, monkeypatch, capsys, cli_db
    ):
        monkeypatch.delenv("ROSTERLAB_OFFLINE", raising=False)
        monkeypatch.delenv("BALLDONTLIE_API_KEY", raising=False)
        from app.config import get_settings

        get_settings.cache_clear()
        for command in ("balldontlie-evaluate", "link-balldontlie"):
            with pytest.raises(SystemExit) as exit_info:
                run(monkeypatch, command)
            assert exit_info.value.code == 2
            assert "BALLDONTLIE_API_KEY is not set" in printed_json(capsys.readouterr().out)["error"]
