"""R8.2 — refreshing and verifying the roster season.

Covers the operational half of the roster/statistics split: the default roster refresh targets the
league year; it never disturbs the previous season's rows; a failure or an empty answer leaves the
last valid snapshot alone; players who change teams are moved once; and the verification report
says what is whole and what is not.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.tool_seasons import roster_verification
from app.config import get_settings
from app.db.models import DataSyncRun, PlayerSeasonStats, RosterEntry
from app.ingestion import jobs
from tests.unit.test_ingestion_jobs import FakeProvider, _roster
from tests.unit.test_season_readiness import at

ATL, BOS = "1610612737", "1610612738"


@pytest.fixture()
def synced(db: Session):
    provider = FakeProvider()
    jobs.sync_teams(db, provider)
    jobs.sync_players(db, provider)
    return provider


@pytest.fixture()
def clock(monkeypatch, request):
    def _set(day: date) -> None:
        monkeypatch.setattr("app.canonical.calendar.today_in_league", lambda now=None: day)
        get_settings.cache_clear()

    request.addfinalizer(get_settings.cache_clear)
    return _set


def _current(db: Session, season: str) -> set[tuple[str, int]]:
    return {
        (e.team.nba_team_id and str(e.team.nba_team_id), e.player.nba_player_id)
        for e in db.scalars(
            select(RosterEntry).where(RosterEntry.season == season, RosterEntry.is_current)
        ).all()
    }


class TestRosterRefreshTargetsTheLeagueYear:
    def test_the_default_refresh_in_october_2026_is_the_2026_27_roster(self, db, synced, clock):
        clock(date(2026, 10, 18))
        provider = FakeProvider(rosters=_roster((ATL, 201939), (BOS, 999999)))
        jobs.sync_rosters(db, provider=provider)
        assert provider.calls[-1] == "rosters:2026-27"
        seasons = {e.season for e in db.scalars(select(RosterEntry)).all()}
        assert seasons == {"2026-27"}

    def test_refreshing_2026_27_leaves_the_2025_26_snapshot_exactly_as_it_was(self, db, synced, clock):
        clock(date(2026, 10, 18))
        jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=_roster((ATL, 201939), (BOS, 999999))))
        before = _current(db, "2025-26")
        jobs.sync_rosters(db, provider=FakeProvider(rosters=_roster((BOS, 201939), (BOS, 999999), (ATL, 300001))))
        assert _current(db, "2025-26") == before, "last season's rows are history; nothing closes them"
        assert len(_current(db, "2026-27")) == 3

    def test_a_player_who_changes_teams_is_on_one_team_in_each_season(self, db, synced, clock):
        clock(date(2026, 10, 18))
        jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=_roster((ATL, 201939), (BOS, 999999))))
        jobs.sync_rosters(
            db, provider=FakeProvider(rosters=_roster((BOS, 201939), (BOS, 999999), (ATL, 300001)))
        )
        rows = db.scalars(select(RosterEntry).where(RosterEntry.is_current)).all()
        by_season = {(e.season, e.player.nba_player_id): str(e.team.nba_team_id) for e in rows}
        assert by_season[("2025-26", 201939)] == ATL
        assert by_season[("2026-27", 201939)] == BOS
        assert len(rows) == len(by_season), "no duplicated membership within a season"

    def test_a_second_refresh_is_idempotent(self, db, synced, clock):
        clock(date(2026, 10, 18))
        provider = FakeProvider(rosters=_roster((ATL, 201939), (BOS, 999999)))
        jobs.sync_rosters(db, provider=provider)
        jobs.sync_rosters(db, provider=provider)
        run = db.scalars(
            select(DataSyncRun).where(DataSyncRun.job_name == "sync_rosters")
            .order_by(DataSyncRun.started_at.desc())
        ).first()
        assert run.detail["added"] == 0 and run.detail["confirmed"] == 2 and run.detail["closed"] == 0
        assert run.detail["season"] == "2026-27"

    def test_an_empty_answer_does_not_empty_the_roster(self, db, synced, clock):
        clock(date(2026, 10, 18))
        jobs.sync_rosters(db, provider=FakeProvider(rosters=_roster((ATL, 201939), (BOS, 999999))))
        with pytest.raises(jobs.SnapshotRejectedError):
            jobs.sync_rosters(db, provider=FakeProvider(rosters=[]))
        assert len(_current(db, "2026-27")) == 2, "the previous valid snapshot survives"
        failed = db.scalars(select(DataSyncRun).where(DataSyncRun.status == "failed")).all()
        assert len(failed) == 1 and failed[0].detail == {"season": "2026-27"}

    def test_a_provider_outage_is_a_failure_and_keeps_the_snapshot(self, db, synced, clock):
        clock(date(2026, 10, 18))
        jobs.sync_rosters(db, provider=FakeProvider(rosters=_roster((ATL, 201939), (BOS, 999999))))

        class Down(FakeProvider):
            async def fetch_rosters(self, season):
                raise ConnectionError("stats.nba.com unreachable")

        with pytest.raises(Exception):  # noqa: B017 - the class is the provider layer's business
            jobs.sync_rosters(db, provider=Down())
        assert len(_current(db, "2026-27")) == 2

    def test_a_player_nba_com_lists_that_pivot_has_never_seen_gets_an_identity(self, db, synced, clock):
        clock(date(2026, 10, 18))
        jobs.sync_rosters(
            db, provider=FakeProvider(rosters=_roster((ATL, 201939), (BOS, 999999), (BOS, 4242001)))
        )
        assert (str(BOS), 4242001) in _current(db, "2026-27")


class TestRosterVerification:
    def test_the_report_counts_what_changed_since_last_season_and_who_has_no_history(
        self, db, synced, clock
    ):
        clock(date(2026, 10, 18))
        jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=_roster((ATL, 201939), (BOS, 999999))))
        jobs.sync_rosters(
            db,
            provider=FakeProvider(
                rosters=_roster((BOS, 201939), (BOS, 999999), (ATL, 4242001))
            ),
        )
        report = roster_verification(db)
        assert report["season"] == "2026-27" and report["compared_with"] == "2025-26"
        assert report["players"] == 3 and report["teams_covered"] == 2
        assert report["players_on_a_different_team_than_previous_season"] == 1
        assert report["players_new_to_the_roster_listing"] == 1
        assert report["players_without_performance_season_statistics"] == 3
        assert report["last_refresh"]["added"] == 3
        assert report["players_on_multiple_current_rosters"] == 0
        assert report["provisional_note"]
        assert any("unusual roster sizes" in i for i in report["unresolved"])
        assert report["complete"] is True

    def test_an_empty_database_reports_what_to_run(self, db, clock):
        clock(date(2026, 10, 18))
        report = roster_verification(db)
        assert report["complete"] is False
        assert any("make refresh DATASETS=rosters" in i for i in report["unresolved"])

    def test_a_missing_team_and_a_contradiction_are_named_not_hidden(self, db, synced, clock):
        clock(date(2026, 10, 18))
        jobs.sync_rosters(db, provider=FakeProvider(rosters=_roster((ATL, 201939), (BOS, 999999))))
        extra = db.scalars(select(RosterEntry)).first()
        from app.db.models import Team

        other = db.scalars(select(Team).where(Team.id != extra.team_id)).first()
        db.add(RosterEntry(team_id=other.id, player_id=extra.player_id, season="2026-27", is_current=True))
        db.commit()
        report = roster_verification(db)
        assert report["players_on_multiple_current_rosters"] == 1
        assert report["complete"] is False

    def test_players_who_did_play_last_season_are_not_counted_as_missing_history(self, db, synced, clock):
        clock(date(2026, 10, 18))
        jobs.sync_rosters(db, provider=FakeProvider(rosters=_roster((ATL, 201939), (BOS, 999999))))
        from app.db.models import Player

        player = db.scalars(select(Player).where(Player.nba_player_id == 201939)).one()
        db.add(
            PlayerSeasonStats(
                player_id=player.id, season="2025-26", stat_type="base", games_played=70,
                minutes=30.0, stats={"PTS": 20.0}, source_retrieved_at=at(2026, 8, 24),
            )
        )
        db.commit()
        assert roster_verification(db)["players_without_performance_season_statistics"] == 1


class TestRosterOnlyMode:
    """Activation safety: memberships and new identities, never a silent rewrite of known players' bio."""

    def _rows(self):
        rows = _roster((ATL, 201939), (BOS, 999999), (BOS, 4242001))
        for row in rows:
            row.update({"height_inches": 99, "weight_lbs": 300, "birth_date": date(1990, 1, 1),
                        "years_experience": 9, "position": "C"})
        return rows

    def test_default_refresh_still_enriches_existing_players(self, db, synced, clock):
        clock(date(2026, 10, 18))
        from app.db.models import Player

        before = db.scalars(select(Player).where(Player.nba_player_id == 201939)).one().height_inches
        jobs.sync_rosters(db, provider=FakeProvider(rosters=self._rows()))
        after = db.scalars(select(Player).where(Player.nba_player_id == 201939)).one().height_inches
        assert after == 99 and after != before

    def test_roster_only_writes_memberships_and_new_identities_but_leaves_known_bio_alone(
        self, db, synced, clock
    ):
        clock(date(2026, 10, 18))
        from app.db.models import Player

        known = db.scalars(select(Player).where(Player.nba_player_id == 201939)).one()
        snapshot = (known.position, known.height_inches, known.weight_lbs, known.birth_date, known.years_experience)
        jobs.sync_rosters(db, provider=FakeProvider(rosters=self._rows()), update_existing_bio=False)
        db.refresh(known)
        assert (known.position, known.height_inches, known.weight_lbs, known.birth_date, known.years_experience) == snapshot
        assert (str(ATL), 201939) in _current(db, "2026-27") and len(_current(db, "2026-27")) == 3
        new = db.scalars(select(Player).where(Player.nba_player_id == 4242001)).one()
        assert (new.height_inches, new.position, new.years_experience) == (99, "C", 9), "a new identity is complete"
        run = db.scalars(select(DataSyncRun).where(DataSyncRun.job_name == "sync_rosters")).first()
        assert run.detail["existing_player_bio_updated"] is False
