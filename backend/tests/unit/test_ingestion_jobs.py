"""R5-5. The ingestion jobs, against a fake provider.

`ingestion/jobs.py` was at **0 % coverage** — 235 statements, and the module every
provider-backed row in the product passes through. Its central claim is in its own
docstring: *"Every job upserts on natural keys, so re-running after a partial failure is
safe and a failed endpoint never destroys the last valid snapshot."* Nothing tested it.

Each job takes an optional `provider`, so the whole module is testable without a network:
the fake below returns the shapes the real normalizers produce. Every job is run **twice**,
because idempotency is the property that matters and it is invisible on a first run.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    DataSyncRun,
    Game,
    Player,
    PlayerSeasonStats,
    PlayerTeamHistory,
    RosterEntry,
    Standing,
    Team,
    TeamSeasonStats,
)
from app.ingestion import jobs

NOW = datetime(2026, 7, 28, tzinfo=UTC)
SEASON = "2025-26"


class FakeProvider:
    """Returns the shapes `integrations.nba_api.normalizers` produce."""

    def __init__(self, **overrides):
        self.calls: list[str] = []
        self.overrides = overrides
        self.estimated_metrics_raise = overrides.get("estimated_metrics_raise", False)

    async def fetch_teams(self):
        self.calls.append("teams")
        return self.overrides.get(
            "teams",
            [
                {
                    "nba_team_id": 1610612737,
                    "full_name": "Atlanta Hawks",
                    "abbreviation": "ATL",
                    "nickname": "Hawks",
                    "city": "Atlanta",
                    "state": "Georgia",
                    "year_founded": 1949,
                },
                {
                    "nba_team_id": 1610612738,
                    "full_name": "Boston Celtics",
                    "abbreviation": "BOS",
                    "nickname": "Celtics",
                    "city": "Boston",
                    "state": "Massachusetts",
                    "year_founded": 1946,
                },
            ],
        )

    async def fetch_players(self):
        self.calls.append("players")
        return self.overrides.get(
            "players",
            [
                {
                    "nba_player_id": 201939,
                    "full_name": "Test Curry",
                    "first_name": "Test",
                    "last_name": "Curry",
                    "is_active": True,
                },
                {
                    "nba_player_id": 203507,
                    "full_name": "Test Antetokounmpo",
                    "first_name": "Test",
                    "last_name": "Antetokounmpo",
                    "is_active": True,
                },
            ],
        )

    async def fetch_rosters(self, season):
        self.calls.append(f"rosters:{season}")
        return self.overrides.get(
            "rosters",
            [
                {
                    "nba_team_id": 1610612737,
                    "nba_player_id": 201939,
                    "player_name": "Test Curry",
                    "jersey_number": "30",
                    "position": "G",
                    "age": 37.0,
                    "height_inches": 74,
                    "weight_lbs": 185,
                    "years_experience": 16,
                    "birth_date": date(1988, 3, 14),
                    "source_record_id": "roster:1610612737:201939",
                    "source_retrieved_at": NOW,
                },
                {
                    # A player NBA.com has on a roster but static data does not know.
                    "nba_team_id": 1610612738,
                    "nba_player_id": 999999,
                    "player_name": "Brand New Signing",
                    "jersey_number": "0",
                    "position": "F",
                    "age": 22.0,
                    "source_record_id": "roster:1610612738:999999",
                    "source_retrieved_at": NOW,
                },
                {
                    # A team the database does not have: skipped, never invented.
                    "nba_team_id": 1610699999,
                    "nba_player_id": 203507,
                    "player_name": "Test Antetokounmpo",
                    "source_record_id": "roster:ghost",
                },
            ],
        )

    async def fetch_standings(self, season):
        self.calls.append(f"standings:{season}")
        return self.overrides.get(
            "standings",
            [
                {
                    "nba_team_id": 1610612737,
                    "wins": 40,
                    "losses": 42,
                    "win_pct": 0.488,
                    "conference": "East",
                    "division": "Southeast",
                    "conference_rank": 9,
                    "playoff_rank": 9,
                    "details": {"streak": "W1"},
                    "source_record_id": "standings:ATL",
                    "source_retrieved_at": NOW,
                },
                {"nba_team_id": 1610699999, "wins": 1, "losses": 1, "win_pct": 0.5},
            ],
        )

    async def fetch_player_stats(self, season):
        self.calls.append(f"player_stats:{season}")
        return self.overrides.get(
            "player_stats",
            [
                {
                    "nba_player_id": 201939,
                    "nba_team_id": 1610612737,
                    "stat_type": "base",
                    "games_played": 70,
                    "minutes": 32.5,
                    "stats": {"PTS": 26.0, "AST": 6.0},
                    "source_record_id": "stats:base:201939",
                    "source_retrieved_at": NOW,
                },
                {
                    "nba_player_id": 201939,
                    "nba_team_id": 1610612737,
                    "stat_type": "advanced",
                    "games_played": 70,
                    "minutes": 32.5,
                    "stats": {"TS_PCT": 0.62},
                    "source_record_id": "stats:adv:201939",
                    "source_retrieved_at": NOW,
                },
                {"nba_player_id": 777777, "stat_type": "base", "stats": {}},
            ],
        )

    async def fetch_player_estimated_metrics(self, season):
        self.calls.append(f"estimated:{season}")
        if self.estimated_metrics_raise:
            raise RuntimeError("endpoint unavailable")
        return self.overrides.get(
            "estimated",
            [
                {
                    "nba_player_id": 201939,
                    "stat_type": "estimated",
                    "stats": {"E_NET_RATING": 5.1},
                    "source_record_id": "stats:est:201939",
                    "source_retrieved_at": NOW,
                }
            ],
        )

    async def fetch_team_stats(self, season):
        self.calls.append(f"team_stats:{season}")
        return self.overrides.get(
            "team_stats",
            [
                {
                    "nba_team_id": 1610612737,
                    "stat_type": "advanced",
                    "stats": {"NET_RATING": 1.2},
                    "source_record_id": "team:adv:ATL",
                    "source_retrieved_at": NOW,
                },
                {"nba_team_id": 1610699999, "stat_type": "advanced", "stats": {}},
            ],
        )

    async def fetch_games(self, season):
        self.calls.append(f"games:{season}")
        return self.overrides.get(
            "games",
            [
                {
                    "nba_game_id": "0022500001",
                    "game_date": date(2025, 10, 21),
                    "home_nba_team_id": 1610612737,
                    "away_nba_team_id": 1610612738,
                    "home_score": 110,
                    "away_score": 108,
                    "status": "final",
                    "source_record_id": "game:0022500001",
                    "source_retrieved_at": NOW,
                }
            ],
        )


def _roster(*pairs: tuple[str, int]) -> list[dict]:
    """Roster rows for (nba_team_id, nba_player_id) pairs."""
    return [
        {
            "nba_team_id": int(team),
            "nba_player_id": player,
            "player_name": f"Player {player}",
            "jersey_number": "1",
            "position": "G",
            "age": 25.0,
            "source_record_id": f"roster:{team}:{player}",
            "source_retrieved_at": NOW,
        }
        for team, player in pairs
    ]


_BASE_ROSTER = _roster(("1610612737", 201939), ("1610612737", 300001), ("1610612738", 999999))


@pytest.fixture()
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture()
def synced(db: Session, provider: FakeProvider) -> FakeProvider:
    jobs.sync_teams(db, provider)
    jobs.sync_players(db, provider)
    return provider


class TestSyncTeamsAndPlayers:
    def test_teams_are_created_then_updated_not_duplicated(
        self, db: Session, provider: FakeProvider
    ):
        assert jobs.sync_teams(db, provider) == 2
        assert jobs.sync_teams(db, provider) == 2
        assert db.query(Team).count() == 2

    def test_a_changed_field_is_applied_on_the_second_run(self, db: Session):
        first = FakeProvider()
        jobs.sync_teams(db, first)
        renamed = FakeProvider(
            teams=[
                {
                    "nba_team_id": 1610612737,
                    "full_name": "Atlanta Hawks",
                    "abbreviation": "ATL",
                    "nickname": "Hawks",
                    "city": "Atlanta",
                    "state": "Georgia",
                    "year_founded": 1950,
                }
            ]
        )
        jobs.sync_teams(db, renamed)
        assert db.query(Team).filter_by(nba_team_id=1610612737).one().year_founded == 1950

    def test_players_upsert_on_the_external_id(self, db: Session, provider: FakeProvider):
        jobs.sync_players(db, provider)
        jobs.sync_players(db, provider)
        assert db.query(Player).count() == 2

    def test_a_run_row_records_what_was_written(self, db: Session, provider: FakeProvider):
        jobs.sync_teams(db, provider)
        run = db.scalars(select(DataSyncRun).where(DataSyncRun.job_name == "sync_teams")).one()
        assert run.status == "succeeded"
        assert run.rows_written == 2
        assert run.finished_at is not None


class TestSyncRosters:
    def test_an_unchanged_roster_is_confirmed_not_duplicated(
        self, db: Session, synced: FakeProvider
    ):
        """R8: membership that has not changed keeps its row and its `valid_from`.

        Before R8 every run superseded and re-inserted every row (~1.5M rows a year at the
        worker's six-hour cadence) and `valid_from` was reset to "now" each time, so it could
        never mean "first observed on this team".
        """
        jobs.sync_rosters(db, SEASON, synced)
        first = {e.player_id: e.valid_from for e in db.query(RosterEntry).all()}
        jobs.sync_rosters(db, SEASON, synced)
        jobs.sync_rosters(db, SEASON, synced)
        entries = db.query(RosterEntry).all()
        assert len(entries) == 2, "no new rows for unchanged membership"
        assert all(e.is_current and e.valid_to is None for e in entries)
        assert {e.player_id: e.valid_from for e in entries} == first

    def test_a_player_who_changes_team_closes_one_row_and_opens_another(
        self, db: Session, synced: FakeProvider
    ):
        jobs.sync_rosters(db, SEASON, FakeProvider(rosters=_BASE_ROSTER))
        # Curry moves ATL -> BOS; both teams stay populated, as a real league's would.
        moved = FakeProvider(
            rosters=_roster(("1610612737", 300001), ("1610612738", 201939), ("1610612738", 999999))
        )
        jobs.sync_rosters(db, SEASON, moved)
        curry = db.query(Player).filter_by(nba_player_id=201939).one()
        rows = db.query(RosterEntry).filter_by(player_id=curry.id).all()
        assert sorted((r.is_current, r.valid_to is not None) for r in rows) == [
            (False, True),
            (True, False),
        ]
        current = next(r for r in rows if r.is_current)
        assert current.team.nba_team_id == 1610612738
        assert db.query(RosterEntry).filter_by(is_current=True).count() == 3

    def test_a_player_no_longer_listed_is_closed_not_deleted(
        self, db: Session, synced: FakeProvider
    ):
        jobs.sync_rosters(db, SEASON, FakeProvider(rosters=_BASE_ROSTER))
        jobs.sync_rosters(
            db, SEASON, FakeProvider(rosters=_roster(("1610612737", 201939), ("1610612738", 999999)))
        )
        gone = db.query(Player).filter_by(nba_player_id=300001).one()
        entry = db.query(RosterEntry).filter_by(player_id=gone.id).one()
        assert entry.is_current is False and entry.valid_to is not None

    def test_an_empty_provider_answer_does_not_erase_the_league(
        self, db: Session, synced: FakeProvider
    ):
        """The provider *answered* with nothing. That must not supersede every roster."""
        from app.ingestion.jobs import SnapshotRejectedError

        jobs.sync_rosters(db, SEASON, synced)
        with pytest.raises(SnapshotRejectedError):
            jobs.sync_rosters(db, SEASON, FakeProvider(rosters=[]))
        assert db.query(RosterEntry).filter_by(is_current=True).count() == 2
        run = (
            db.query(DataSyncRun)
            .filter_by(job_name="sync_rosters")
            .order_by(DataSyncRun.started_at.desc())
            .first()
        )
        assert run.status == "failed" and run.error_class == "SnapshotRejectedError"

    def test_a_snapshot_covering_a_fraction_of_the_league_is_rejected(
        self, db: Session, synced: FakeProvider
    ):
        from app.ingestion.jobs import SnapshotRejectedError

        jobs.sync_rosters(db, SEASON, synced)
        # One of the two previously covered teams only: 50 % < the 90 % coverage floor.
        with pytest.raises(SnapshotRejectedError, match="teams"):
            jobs.sync_rosters(db, SEASON, FakeProvider(rosters=_roster(("1610612737", 201939))))
        assert db.query(RosterEntry).filter_by(is_current=True).count() == 2

    def test_a_player_unknown_to_static_data_is_created_from_the_roster_row(
        self, db: Session, synced: FakeProvider
    ):
        jobs.sync_rosters(db, SEASON, synced)
        created = db.query(Player).filter_by(nba_player_id=999999).one()
        assert created.full_name == "Brand New Signing"
        assert created.is_active is True

    def test_an_unknown_team_is_skipped_not_invented(self, db: Session, synced: FakeProvider):
        jobs.sync_rosters(db, SEASON, synced)
        assert db.query(Team).count() == 2
        assert db.query(Player).filter_by(nba_player_id=203507).one().position is None

    def test_bio_fields_are_enriched_from_the_roster_row(self, db: Session, synced: FakeProvider):
        jobs.sync_rosters(db, SEASON, synced)
        curry = db.query(Player).filter_by(nba_player_id=201939).one()
        assert (curry.height_inches, curry.weight_lbs, curry.years_experience) == (74, 185, 16)
        assert curry.birth_date == date(1988, 3, 14)

    def test_team_history_records_each_sighting_once(self, db: Session, synced: FakeProvider):
        """Three syncs of the same roster: two history rows, not six (R8 audit W3)."""
        for _ in range(3):
            jobs.sync_rosters(db, SEASON, synced)
        assert db.query(PlayerTeamHistory).count() == 2

    def test_history_gains_a_row_only_for_a_new_team(self, db: Session, synced: FakeProvider):
        jobs.sync_rosters(db, SEASON, FakeProvider(rosters=_BASE_ROSTER))
        jobs.sync_rosters(
            db,
            SEASON,
            FakeProvider(
                rosters=_roster(("1610612737", 300001), ("1610612738", 201939), ("1610612738", 999999))
            ),
        )
        curry = db.query(Player).filter_by(nba_player_id=201939).one()
        assert db.query(PlayerTeamHistory).filter_by(player_id=curry.id).count() == 2


class TestEmptySeasonAnswersAreRejectedNotRecordedAsSuccess:
    @pytest.mark.parametrize(
        ("job", "override"),
        [
            (jobs.sync_standings, "standings"),
            (jobs.sync_player_stats, "player_stats"),
            (jobs.sync_team_stats, "team_stats"),
            (jobs.sync_games, "games"),
        ],
    )
    def test_zero_rows_fail_the_run(self, db: Session, job, override: str):
        from app.ingestion.jobs import SnapshotRejectedError

        provider = FakeProvider(**{override: []})
        jobs.sync_teams(db, provider)
        jobs.sync_players(db, provider)
        with pytest.raises(SnapshotRejectedError):
            job(db, provider=provider) if job is not jobs.sync_player_stats else job(
                db, [SEASON], provider
            )

    def test_season_type_is_written_explicitly(self, db: Session, synced: FakeProvider):
        jobs.sync_rosters(db, SEASON, synced)
        jobs.sync_player_stats(db, [SEASON], synced)
        assert {r.season_type for r in db.query(PlayerSeasonStats).all()} == {"regular"}


class TestSyncStandings:
    def test_it_upserts_on_team_and_season(self, db: Session, synced: FakeProvider):
        jobs.sync_standings(db, SEASON, synced)
        jobs.sync_standings(db, SEASON, synced)
        assert db.query(Standing).count() == 1
        row = db.query(Standing).one()
        assert (row.wins, row.losses) == (40, 42)
        assert row.details == {"streak": "W1"}

    def test_it_backfills_conference_and_division_onto_the_team(
        self, db: Session, synced: FakeProvider
    ):
        jobs.sync_standings(db, SEASON, synced)
        team = db.query(Team).filter_by(nba_team_id=1610612737).one()
        assert (team.conference, team.division) == ("East", "Southeast")


class TestSyncPlayerStats:
    def test_it_upserts_per_player_season_and_stat_type(self, db: Session, synced: FakeProvider):
        jobs.sync_player_stats(db, [SEASON], synced)
        jobs.sync_player_stats(db, [SEASON], synced)
        rows = db.query(PlayerSeasonStats).all()
        assert {r.stat_type for r in rows} == {"base", "advanced", "estimated"}
        assert len(rows) == 3

    def test_an_unknown_player_is_skipped(self, db: Session, synced: FakeProvider):
        jobs.sync_player_stats(db, [SEASON], synced)
        assert db.query(Player).filter_by(nba_player_id=777777).count() == 0

    def test_missing_estimated_metrics_do_not_fail_the_job(self, db: Session, synced: FakeProvider):
        """Documented fallback: base + advanced remain authoritative."""
        flaky = FakeProvider(estimated_metrics_raise=True)
        written = jobs.sync_player_stats(db, [SEASON], flaky)
        assert written == 2
        assert {r.stat_type for r in db.query(PlayerSeasonStats).all()} == {"base", "advanced"}
        run = db.scalars(
            select(DataSyncRun).where(DataSyncRun.job_name == "sync_player_stats")
        ).one()
        assert run.status == "succeeded"

    def test_multiple_seasons_are_each_committed(self, db: Session, synced: FakeProvider):
        written = jobs.sync_player_stats(db, ["2024-25", SEASON], synced)
        assert written == 6
        assert {r.season for r in db.query(PlayerSeasonStats).all()} == {"2024-25", SEASON}


class TestSyncTeamStatsAndGames:
    def test_team_stats_upsert(self, db: Session, synced: FakeProvider):
        jobs.sync_team_stats(db, [SEASON], synced)
        jobs.sync_team_stats(db, [SEASON], synced)
        assert db.query(TeamSeasonStats).count() == 1

    def test_games_upsert_on_the_nba_game_id(self, db: Session, synced: FakeProvider):
        jobs.sync_games(db, SEASON, synced)
        jobs.sync_games(db, SEASON, synced)
        game = db.query(Game).one()
        assert (game.home_score, game.away_score) == (110, 108)
        assert game.home_team_id is not None and game.away_team_id is not None

    def test_a_game_between_unknown_teams_still_records_the_result(
        self, db: Session, synced: FakeProvider
    ):
        """The score is real even when the team identity is not resolvable; dropping the
        row would lose data, and inventing a team would be worse."""
        ghost = FakeProvider(
            games=[
                {
                    "nba_game_id": "0022500002",
                    "game_date": date(2025, 10, 22),
                    "home_nba_team_id": 1610699999,
                    "away_nba_team_id": 1610699998,
                    "home_score": 99,
                    "away_score": 101,
                }
            ]
        )
        jobs.sync_games(db, SEASON, ghost)
        game = db.query(Game).filter_by(nba_game_id="0022500002").one()
        assert game.home_team_id is None and game.away_team_id is None
        assert game.home_score == 99


class TestFailureHandling:
    def test_a_failing_provider_marks_the_run_failed_and_raises(self, db: Session):
        class Broken(FakeProvider):
            async def fetch_teams(self):
                raise RuntimeError("NBA.com timed out")

        with pytest.raises(RuntimeError):
            jobs.sync_teams(db, Broken())
        run = db.scalars(select(DataSyncRun).where(DataSyncRun.job_name == "sync_teams")).one()
        assert run.status == "failed"
        assert run.error_class == "RuntimeError"
        assert "timed out" in (run.error_message or "")

    def test_a_failed_run_leaves_the_previous_snapshot_intact(self, db: Session):
        """The module's central claim: a failed endpoint never destroys the last valid
        snapshot."""
        jobs.sync_teams(db, FakeProvider())
        before = {t.nba_team_id for t in db.query(Team).all()}

        class Broken(FakeProvider):
            async def fetch_teams(self):
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            jobs.sync_teams(db, Broken())
        db.rollback()
        assert {t.nba_team_id for t in db.query(Team).all()} == before


class TestSyncContracts:
    def test_with_no_provider_it_records_an_explicit_no_op(self, db: Session, synced):
        """Salary features stay honestly unavailable rather than silently empty."""
        written = jobs.sync_contracts(db)
        assert written == 0
        run = db.scalars(
            select(DataSyncRun).where(DataSyncRun.job_name == "sync_contracts")
        ).one()
        assert run.status == "succeeded"
        assert run.detail.get("provider") is None
        assert "no contract provider configured" in run.detail.get("note", "")


# ------------------------------------------- R7: ingesting anything invalidates the cache


def test_a_successful_ingestion_bumps_the_data_version(db: Session) -> None:
    """`bump_data_version` used to live only at the end of `sync_all`, so every other
    route into an ingestion — the single-job CLI commands, the CSV, transaction and
    draft-pick imports, and R7's `sync-corpus-stats` — wrote rows and left the previous
    snapshot's derived values cached under the old namespace. `EvaluationService._skills()`
    is keyed on the data version, so a refresh of the modelling seasons through any of
    those paths served stale skill vectors."""
    from app.core.cache import get_cache
    from app.ingestion.runs import sync_run

    cache = get_cache()
    before = cache.get_data_version()
    with sync_run(db, "test_job") as run:
        run.rows_written = 5
    assert cache.get_data_version() != before


def test_a_no_op_ingestion_does_not_churn_the_namespace(db: Session) -> None:
    """A contracts sync with no provider configured writes nothing. Bumping on it would
    discard every cached derivation for a run that changed no data."""
    from app.core.cache import get_cache
    from app.ingestion.runs import sync_run

    cache = get_cache()
    cache.bump_data_version()
    before = cache.get_data_version()
    with sync_run(db, "test_job_empty") as run:
        run.rows_written = 0
    assert cache.get_data_version() == before


def test_a_failed_ingestion_does_not_bump(db: Session) -> None:
    """A job that raised did not produce a new snapshot to invalidate against."""
    from app.core.cache import get_cache
    from app.ingestion.runs import sync_run

    cache = get_cache()
    cache.bump_data_version()
    before = cache.get_data_version()
    with pytest.raises(RuntimeError), sync_run(db, "test_job_failed") as run:
        run.rows_written = 9
        raise RuntimeError("boom")
    assert cache.get_data_version() == before
