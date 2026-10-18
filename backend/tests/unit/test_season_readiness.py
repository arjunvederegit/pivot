"""R8.1 — season-aware freshness and the 2026-27 rollover.

Every test here names a date. The calendar is a pure function of ``as_of`` and the freshness
assessment takes ``now``, so nothing depends on the day the suite happens to run — which matters,
because the suite will be run on both sides of 20 October 2026.

The cases map to the R8.1 brief: a completed historical season, an active regular season, preseason
data, a future season before its first game, a delayed update, a stale live roster, a historical
correction, missing player statistics, an upstream failure, and a refresh that changes a status.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.calendar import (
    KNOWN_SCHEDULES,
    ScopeState,
    SeasonPhase,
    _first_tuesday_on_or_after,
    phase_on,
    schedule_for,
    season_context,
    state_of_scope,
)
from app.canonical.provenance import Freshness, source_meta_from_aggregate
from app.canonical.rosters import roster_as_of
from app.canonical.seasons import Season, SeasonType, StatScope
from app.canonical.stat_access import player_stat_rows
from app.config import Settings
from app.db.models import (
    DataSyncRun,
    Player,
    PlayerSeasonStats,
    RosterEntry,
    Standing,
    Team,
    TeamSeasonStats,
)
from app.ingestion import jobs
from app.integrations.nba_api.exceptions import ProviderBlockedError, ProviderEmptyError
from app.services.data_provenance import dataset_freshness, rollover_readiness
from tests.conftest import make_player, make_team
from tests.unit.test_ingestion_jobs import NOW, FakeProvider, _roster


def at(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


# ------------------------------------------------------------------------------ the calendar


class TestSeasonCalendar:
    def test_on_18_october_2026_the_league_is_in_2026_27_but_the_tools_read_2025_26(self):
        ctx = season_context(date(2026, 10, 18))
        assert ctx.league_season.label == "2026-27"
        assert ctx.phase is SeasonPhase.PRESEASON
        assert ctx.analysis_season.label == "2025-26"
        assert ctx.last_completed_season.label == "2025-26"
        assert ctx.analysis_is_baseline is True
        assert ctx.regular_stats_state is ScopeState.UPCOMING
        assert ctx.regular_season_stats_expected is False
        assert ctx.schedule.regular_start == date(2026, 10, 20)
        assert ctx.schedule.basis == "league_published"

    def test_opening_night_starts_the_season_but_not_the_rollover(self):
        ctx = season_context(date(2026, 10, 20))
        assert ctx.phase is SeasonPhase.REGULAR_SEASON
        assert ctx.regular_season_stats_expected is True
        # Two weeks of games are a thin sample; the tools stay on the completed season.
        assert ctx.analysis_season.label == "2025-26"
        assert ctx.rollover_date == date(2026, 11, 3)

    def test_the_tools_move_to_2026_27_once_the_grace_period_has_passed(self):
        assert season_context(date(2026, 11, 2)).analysis_season.label == "2025-26"
        later = season_context(date(2026, 11, 3))
        assert later.analysis_season.label == "2026-27"
        assert later.analysis_is_baseline is False

    def test_the_grace_period_is_configuration(self):
        assert season_context(date(2026, 10, 21), grace_days=0).analysis_season.label == "2026-27"

    @pytest.mark.parametrize(
        ("day", "season", "phase"),
        [
            (date(2025, 10, 10), "2025-26", SeasonPhase.PRESEASON),
            (date(2026, 1, 15), "2025-26", SeasonPhase.REGULAR_SEASON),
            (date(2026, 4, 14), "2025-26", SeasonPhase.PLAY_IN),
            (date(2026, 5, 20), "2025-26", SeasonPhase.PLAYOFFS),
            (date(2026, 6, 25), "2025-26", SeasonPhase.OFFSEASON),
            (date(2026, 7, 20), "2026-27", SeasonPhase.OFFSEASON),
            (date(2027, 4, 12), "2026-27", SeasonPhase.PLAY_IN),
        ],
    )
    def test_phases_across_a_year(self, day, season, phase):
        ctx = season_context(day)
        assert ctx.league_season.label == season
        assert ctx.phase is phase

    def test_the_league_year_turns_over_on_1_july_not_at_the_start_of_games(self):
        assert season_context(date(2026, 6, 30)).league_season.label == "2025-26"
        assert season_context(date(2026, 7, 1)).league_season.label == "2026-27"
        # ... and a roster lookup in August is about 2026-27 while the statistics are 2025-26's.
        august = season_context(date(2026, 8, 15))
        assert (august.league_season.label, august.analysis_season.label) == ("2026-27", "2025-26")

    def test_a_season_with_no_published_dates_is_estimated_and_says_so(self):
        schedule = schedule_for(Season(2027))
        assert schedule.basis == "estimated" and schedule.is_estimated
        assert schedule.regular_start == date(2027, 10, 19)  # first Tuesday on/after 18 Oct
        published = schedule_for(Season(2026))
        assert published.basis == "league_published" and not published.is_estimated

    def test_the_estimate_reproduces_every_known_opening_night(self):
        """The heuristic is only worth having if it would have been right."""
        for year, (start, _end) in KNOWN_SCHEDULES.items():
            assert _first_tuesday_on_or_after(date(year, 10, 18)) == start
        assert _first_tuesday_on_or_after(date(2024, 10, 18)) == date(2024, 10, 22)  # actual
        assert _first_tuesday_on_or_after(date(2022, 10, 18)) == date(2022, 10, 18)  # actual

    def test_an_ingested_calendar_is_used_for_seasons_with_no_published_dates(self):
        schedule = schedule_for(Season(2023), ingested=(date(2023, 10, 24), date(2024, 4, 14)))
        assert schedule.basis == "ingested_games"
        assert schedule.regular_end == date(2024, 4, 14)

    def test_an_ingested_calendar_for_a_season_in_progress_does_not_pretend_to_be_its_end(self):
        schedule = schedule_for(Season(2027), ingested=(date(2027, 10, 19), date(2027, 11, 20)))
        assert schedule.basis == "estimated"
        assert schedule.regular_end > date(2028, 3, 1)

    def test_preseason_and_regular_season_are_different_windows(self):
        schedule = schedule_for(Season(2026))
        pre = schedule.window(SeasonType.PRESEASON)
        reg = schedule.window(SeasonType.REGULAR)
        assert pre is not None and reg is not None
        assert pre[1] < reg[0], "preseason ends before the regular season begins"
        assert state_of_scope(StatScope(Season(2026), SeasonType.PRESEASON), date(2026, 10, 18)) is (
            ScopeState.IN_PROGRESS
        )
        assert state_of_scope(StatScope(Season(2026), SeasonType.REGULAR), date(2026, 10, 18)) is (
            ScopeState.UPCOMING
        )

    def test_play_in_follows_the_regular_season_directly(self):
        schedule = schedule_for(Season(2025))
        assert phase_on(schedule, schedule.regular_end + timedelta(days=1)) is SeasonPhase.PLAY_IN


class TestSettingsDeriveTheSeason:
    @pytest.fixture()
    def on(self, monkeypatch):
        def go(day: date, **env) -> Settings:
            monkeypatch.setattr("app.canonical.calendar.today_in_league", lambda now=None: day)
            return Settings(CURRENT_SEASON=env.pop("pinned", ""), **env)

        return go

    def test_blank_means_the_calendar_decides(self, on):
        assert on(date(2026, 10, 18)).current_season == "2025-26"
        assert on(date(2026, 11, 3)).current_season == "2026-27"

    def test_a_pinned_season_still_wins_and_is_validated(self, on):
        assert on(date(2026, 12, 1), pinned="2025-26").current_season == "2025-26"
        with pytest.raises(ValueError):
            _ = on(date(2026, 12, 1), pinned="2025").current_season

    def test_the_modelling_window_follows_the_analysis_season(self, on):
        assert on(date(2026, 10, 18)).history_season_list == ["2023-24", "2024-25", "2025-26"]
        assert on(date(2026, 11, 10)).history_season_list == ["2024-25", "2025-26", "2026-27"]
        assert len(on(date(2026, 10, 18)).corpus_season_list) == 10

    def test_routine_refreshes_pick_up_the_new_season_before_the_tools_move_to_it(self, on):
        assert "2026-27" not in on(date(2026, 10, 19)).sync_season_list
        during_grace = on(date(2026, 10, 25))
        assert during_grace.current_season == "2025-26"
        assert during_grace.sync_season_list[-1] == "2026-27"


# --------------------------------------------------------------------- freshness by lifecycle


def meta(dataset="player_stats", *, count=500, oldest, newest=None, season, season_type="regular",
         now, **kw):
    return source_meta_from_aggregate(
        dataset,
        count=count,
        oldest=oldest,
        newest=newest or oldest,
        providers=["nba_api"],
        season=season,
        season_type=season_type,
        now=now,
        **kw,
    )


class TestFreshnessFollowsTheLifecycle:
    def test_1_a_completed_season_is_finalized_however_old_the_retrieval(self):
        # 2025-26 ended 12 Apr 2026; retrieved 24 Aug; read on 18 Oct — 55 days later.
        m = meta(oldest=at(2026, 8, 24), season="2025-26", now=at(2026, 10, 18))
        assert m.freshness is Freshness.FINALIZED
        assert m.scope_state == "completed"
        assert m.age_seconds and m.age_seconds > 48 * 3600, "older than the 48 h window and still fine"
        assert not m.needs_attention
        assert m.is_usable

    def test_1b_a_season_retrieved_before_it_ended_is_not_finalized(self):
        m = meta(oldest=at(2026, 3, 1), season="2025-26", now=at(2026, 10, 18))
        assert m.freshness is Freshness.STALE
        assert "before 2025-26 settled" in (m.status_reason or "")
        assert m.needs_attention

    def test_1c_one_unsettled_row_is_enough(self):
        """The oldest row decides, as it always did."""
        m = meta(oldest=at(2026, 4, 1), newest=at(2026, 8, 24), season="2025-26", now=at(2026, 10, 18))
        assert m.freshness is Freshness.STALE

    def test_1d_finalized_is_not_immutable_a_very_old_verification_earns_a_note_not_a_warning(self):
        m = meta(oldest=at(2026, 4, 20), season="2025-26", now=at(2028, 1, 1))
        assert m.freshness is Freshness.FINALIZED
        assert any("can still be corrected upstream" in n for n in m.notes)
        assert not m.needs_attention

    def test_2_an_active_season_ages_on_the_dataset_window(self):
        jan = at(2027, 1, 15)
        assert meta(oldest=jan - timedelta(hours=30), season="2026-27", now=jan).freshness is Freshness.FRESH
        assert meta(oldest=jan - timedelta(days=3), season="2026-27", now=jan).freshness is Freshness.STALE

    def test_3_preseason_rows_are_judged_as_preseason(self):
        now = at(2026, 10, 18)
        m = meta(oldest=now - timedelta(hours=5), season="2026-27", season_type="preseason", now=now)
        assert m.scope_state == "in_progress"
        assert m.season_type == "preseason"
        assert m.freshness is Freshness.FRESH

    def test_4_a_season_that_has_not_started_has_no_rows_and_that_is_not_a_failure(self):
        now = at(2026, 10, 18)
        m = meta(count=0, oldest=None, season="2026-27", now=now)
        assert m.freshness is Freshness.NOT_YET_AVAILABLE
        assert m.scope_state == "upcoming"
        assert "2026-10-20" in (m.status_reason or "")
        assert not m.needs_attention and not m.is_usable
        assert m.present_count == 0

    def test_4b_the_same_empty_answer_after_the_season_started_is_missing(self):
        m = meta(count=0, oldest=None, season="2026-27", now=at(2026, 10, 30))
        assert m.freshness is Freshness.MISSING
        assert m.needs_attention

    def test_4c_a_completed_season_with_no_rows_is_missing_not_unavailable(self):
        m = meta(count=0, oldest=None, season="2025-26", now=at(2026, 10, 18))
        assert m.freshness is Freshness.MISSING

    def test_5_a_delayed_update_in_season_goes_stale(self):
        now = at(2027, 1, 20)
        m = meta(oldest=now - timedelta(days=5), season="2026-27", now=now)
        assert m.freshness is Freshness.STALE
        assert "stale after 2 days" in (m.status_reason or "")

    def test_6_a_live_roster_goes_stale_in_the_offseason_too(self):
        now = at(2026, 10, 18)
        m = meta("rosters", count=30, oldest=now - timedelta(days=89), season="2025-26", now=now)
        assert m.freshness is Freshness.STALE, "membership describes now; it never finalizes"
        assert m.needs_attention

    def test_6b_a_past_seasons_roster_is_history(self):
        now = at(2026, 10, 18)
        m = meta("rosters", count=30, oldest=at(2025, 7, 1), season="2024-25", now=now)
        assert m.freshness is Freshness.FINALIZED

    def test_contracts_and_injuries_are_never_finalized(self):
        now = at(2026, 10, 18)
        assert meta("contracts", oldest=now - timedelta(days=60), season="2026-27", now=now).freshness is (
            Freshness.STALE
        )
        assert meta("injuries", oldest=now - timedelta(hours=7), season=None, now=now).freshness is (
            Freshness.STALE
        )

    def test_prior_season_data_is_labelled_a_baseline(self):
        now = at(2026, 10, 18)
        m = meta(oldest=at(2026, 8, 24), season="2025-26", now=now)
        assert m.is_baseline is True
        assert any("not 2026-27 performance" in n for n in m.notes)

    def test_the_new_season_is_not_a_baseline_after_the_rollover(self):
        now = at(2026, 11, 10)
        assert meta(oldest=now, season="2026-27", now=now).is_baseline is False

    def test_a_dataset_with_no_policy_keeps_the_original_rule(self):
        now = at(2026, 10, 18)
        m = source_meta_from_aggregate("something_else", count=3, oldest=now - timedelta(days=9),
                                       newest=now, now=now)
        assert m.freshness is Freshness.STALE and m.scope_state is None


# ----------------------------------------------------- missing stays missing, never zero


class TestMissingIsNotZero:
    def test_a_rookie_has_no_prior_statistics_and_no_zero(self, db: Session):
        team = make_team(db, 1, "AAA")
        rookie = make_player(db, 10, "Rookie", team, season="2026-27")
        rows = player_stat_rows(db, [rookie.id], season="2025-26")
        assert rows.missing_player_ids == [rookie.id]
        assert rows.value(rookie.id, "PTS") is None

    def test_2026_27_statistics_before_opening_night_are_not_yet_available_never_substituted(
        self, db: Session
    ):
        team = make_team(db, 1, "AAA")
        veteran = make_player(db, 11, "Veteran", team, season="2025-26")
        db.add(PlayerSeasonStats(player_id=veteran.id, season="2025-26", season_type="regular",
                                 stat_type="base", games_played=70, minutes=30.0, stats={"PTS": 21.5},
                                 source_retrieved_at=at(2026, 8, 24)))
        db.commit()
        rows = player_stat_rows(db, [veteran.id], season="2026-27", now=at(2026, 10, 18))
        assert rows.rows == {} and rows.missing_player_ids == [veteran.id]
        assert rows.value(veteran.id, "PTS") is None, "last season's 21.5 is not this season's"
        assert rows.meta.freshness is Freshness.NOT_YET_AVAILABLE
        # and last season is still there, labelled for what it is
        prior = player_stat_rows(db, [veteran.id], season="2025-26", now=at(2026, 10, 18))
        assert prior.value(veteran.id, "PTS") == 21.5
        assert prior.meta.freshness is Freshness.FINALIZED and prior.meta.is_baseline

    def test_preseason_and_regular_rows_for_one_player_never_mix(self, db: Session):
        team = make_team(db, 1, "AAA")
        p = make_player(db, 12, "Both", team, season="2026-27")
        for kind, pts in (("regular", 20.0), ("preseason", 4.0)):
            db.add(PlayerSeasonStats(player_id=p.id, season="2026-27", season_type=kind,
                                     stat_type="base", stats={"PTS": pts},
                                     source_retrieved_at=at(2026, 10, 18)))
        db.commit()
        reg = player_stat_rows(db, [p.id], season="2026-27", season_type=SeasonType.REGULAR)
        pre = player_stat_rows(db, [p.id], season="2026-27", season_type=SeasonType.PRESEASON)
        assert reg.value(p.id, "PTS") == 20.0 and pre.value(p.id, "PTS") == 4.0


# ------------------------------------------------------------------------- the jobs


@pytest.fixture()
def synced(db: Session):
    provider = FakeProvider()
    jobs.sync_teams(db, provider)
    jobs.sync_players(db, provider)
    return provider


def run_on(monkeypatch, day: date) -> None:
    monkeypatch.setattr(jobs, "today_in_league", lambda now=None: day)


class _Raises(FakeProvider):
    def __init__(self, exc: Exception, **kw):
        super().__init__(**kw)
        self.exc = exc

    async def fetch_player_stats(self, season, **kw):
        raise self.exc

    async def fetch_team_stats(self, season, **kw):
        raise self.exc

    async def fetch_standings(self, season):
        raise self.exc

    async def fetch_games(self, season):
        raise self.exc


class _TypedProvider(FakeProvider):
    """Accepts ``season_type`` and records it, as the real provider does."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.types: list[str] = []

    async def fetch_player_stats(self, season, season_type=SeasonType.REGULAR):
        self.types.append(SeasonType(season_type).value)
        return await super().fetch_player_stats(season)

    async def fetch_team_stats(self, season, season_type=SeasonType.REGULAR):
        self.types.append(SeasonType(season_type).value)
        return await super().fetch_team_stats(season)


class _Empty(FakeProvider):
    async def fetch_player_stats(self, season, **kw):
        raise ProviderEmptyError("LeagueDashPlayerStats", "response contained zero rows")


class TestExpectedEmptyIsNotAFailure:
    def test_9_a_provider_with_nothing_to_say_before_opening_night_is_a_clean_run(
        self, db: Session, synced, monkeypatch
    ):
        run_on(monkeypatch, date(2026, 10, 18))
        assert jobs.sync_player_stats(db, ["2026-27"], _Empty()) == 0
        run = db.scalars(select(DataSyncRun).where(DataSyncRun.job_name == "sync_player_stats")).one()
        assert run.status == "succeeded"
        assert run.detail["outcome"] == "expected_empty"
        assert run.detail["outcomes"] == {"2026-27": "expected_empty"}
        assert db.query(PlayerSeasonStats).count() == 0

    def test_the_identical_answer_after_the_season_started_is_a_rejected_snapshot(
        self, db: Session, synced, monkeypatch
    ):
        run_on(monkeypatch, date(2026, 10, 30))
        with pytest.raises(jobs.SnapshotRejectedError):
            jobs.sync_player_stats(db, ["2026-27"], _Empty())
        run = db.scalars(select(DataSyncRun).where(DataSyncRun.job_name == "sync_player_stats")).one()
        assert run.status == "failed"

    def test_empty_for_a_completed_season_is_a_defect_whatever_the_provider_says(
        self, db: Session, synced, monkeypatch
    ):
        run_on(monkeypatch, date(2026, 10, 18))
        with pytest.raises(jobs.SnapshotRejectedError):
            jobs.sync_player_stats(db, ["2025-26"], _Empty())

    def test_a_blocked_provider_is_never_mistaken_for_expected_empty(
        self, db: Session, synced, monkeypatch
    ):
        """The dangerous confusion: 'nothing yet' must not be the cover story for 'blocked'."""
        run_on(monkeypatch, date(2026, 10, 18))
        with pytest.raises(ProviderBlockedError):
            jobs.sync_player_stats(db, ["2026-27"], _Raises(ProviderBlockedError("X", "403")))
        run = db.scalars(select(DataSyncRun).where(DataSyncRun.job_name == "sync_player_stats")).one()
        assert run.status == "failed" and run.error_class == "ProviderBlockedError"

    def test_standings_and_games_for_an_unplayed_season_are_also_expected_empty(
        self, db: Session, synced, monkeypatch
    ):
        run_on(monkeypatch, date(2026, 10, 18))
        provider = FakeProvider(standings=[], games=[])
        assert jobs.sync_standings(db, "2026-27", provider) == 0
        assert jobs.sync_games(db, "2026-27", provider) == 0
        statuses = {r.job_name: (r.status, (r.detail or {}).get("outcome"))
                    for r in db.scalars(select(DataSyncRun)).all() if r.job_name in {"sync_standings", "sync_games"}}
        assert statuses == {"sync_standings": ("succeeded", "expected_empty"),
                            "sync_games": ("succeeded", "expected_empty")}

    def test_the_season_calendar_skips_an_unplayed_season_without_filing_an_issue(
        self, db: Session, synced, monkeypatch
    ):
        from app.db.models import DataQualityIssue

        run_on(monkeypatch, date(2026, 10, 18))
        assert jobs.sync_season_calendar(db, ["2026-27"], FakeProvider(games=[])) == 0
        assert db.query(DataQualityIssue).count() == 0

    def test_the_empty_error_is_not_retried_and_does_not_open_the_circuit(self):
        import pandas as pd

        from app.integrations.nba_api.client import NBAApiClient
        from app.integrations.nba_api.retry import get_circuit_breaker

        calls = {"n": 0}

        class Endpoint:
            def get_data_frames(self):
                calls["n"] += 1
                return [pd.DataFrame(columns=["PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "GP", "MIN"])]

        client = NBAApiClient()
        for _ in range(4):
            with pytest.raises(ProviderEmptyError):
                client.fetch_dataframe("LeagueDashPlayerStats", lambda common: Endpoint())
        assert calls["n"] == 4, "one request per call — no retries"
        get_circuit_breaker().check("LeagueDashPlayerStats")  # would raise if the circuit had opened


class TestSeasonTypesStaySeparate:
    def test_a_preseason_refresh_writes_preseason_rows_and_leaves_the_regular_season_alone(
        self, db: Session, synced
    ):
        provider = _TypedProvider()
        jobs.sync_player_stats(db, ["2026-27"], provider)
        jobs.sync_player_stats(db, ["2026-27"], provider, season_type=SeasonType.PRESEASON)
        types = {r.season_type for r in db.scalars(select(PlayerSeasonStats)).all()}
        assert types == {"regular", "preseason"}
        assert provider.types == ["regular", "regular", "preseason", "preseason"] or provider.types[-1] == "preseason"
        preseason = {r.stat_type for r in db.query(PlayerSeasonStats).filter_by(season_type="preseason")}
        regular = {r.stat_type for r in db.query(PlayerSeasonStats).filter_by(season_type="regular")}
        assert preseason == {"base", "advanced"}, "estimated metrics are a regular-season enrichment"
        assert regular == {"base", "advanced", "estimated"}


class TestHistoricalCorrection:
    def test_7_a_corrected_season_is_updated_counted_and_still_finalized(
        self, db: Session, synced, monkeypatch
    ):
        run_on(monkeypatch, date(2026, 10, 18))
        first = FakeProvider()
        jobs.sync_player_stats(db, ["2025-26"], first)
        corrected_rows = [
            {**r, "stats": {**r["stats"], "PTS": 27.4} if r["stat_type"] == "base" and r["nba_player_id"] == 201939 else r["stats"],
             "source_retrieved_at": at(2026, 10, 17)}
            for r in await_rows(first)
        ]
        jobs.sync_player_stats(db, ["2025-26"], FakeProvider(player_stats=corrected_rows))
        base = db.scalars(select(PlayerSeasonStats).where(PlayerSeasonStats.stat_type == "base")).one()
        assert base.stats["PTS"] == 27.4, "the source is the authority; the correction lands"
        second = db.scalars(select(DataSyncRun).where(DataSyncRun.job_name == "sync_player_stats")
                            .order_by(DataSyncRun.started_at.desc())).first()
        assert second.detail["rows"]["changed"] == 1
        assert second.detail["rows"]["unchanged"] >= 1
        meta_ = dataset_freshness(db, "2025-26", now=at(2026, 10, 18))["player_stats"]
        assert meta_.freshness is Freshness.FINALIZED


def await_rows(provider: FakeProvider) -> list[dict]:
    import asyncio

    return asyncio.run(provider.fetch_player_stats("2025-26"))


# ------------------------------------------------------- status over a real refresh cycle


def _seed_league(db: Session, retrieved: datetime, *, with_stats: bool = True) -> tuple[Team, Player]:
    team = make_team(db, 1, "AAA")
    player = make_player(db, 20, "Starter", team, season="2025-26")
    db.query(RosterEntry).update({"source_retrieved_at": retrieved})
    if with_stats:
        db.add(PlayerSeasonStats(player_id=player.id, season="2025-26", season_type="regular",
                                 stat_type="base", stats={"PTS": 18.0}, source_retrieved_at=at(2026, 8, 24)))
        db.add(TeamSeasonStats(team_id=team.id, season="2025-26", season_type="regular",
                               stat_type="base", stats={}, source_retrieved_at=at(2026, 8, 24)))
        db.add(Standing(team_id=team.id, season="2025-26", wins=41, losses=41, win_pct=0.5,
                        source_retrieved_at=at(2026, 8, 24)))
    db.commit()
    return team, player


class TestDatasetStatusOverTime:
    NOW = at(2026, 10, 18)

    def test_the_real_database_shape_completed_stats_are_finalized_and_only_the_roster_needs_attention(
        self, db: Session
    ):
        """The R8 report called every dataset stale. Judged by the calendar, one is."""
        _seed_league(db, at(2026, 7, 21))
        ds = dataset_freshness(db, now=self.NOW)
        assert ds["rosters"].freshness is Freshness.STALE
        assert ds["standings"].freshness is Freshness.FINALIZED
        assert ds["player_stats"].freshness is Freshness.FINALIZED
        assert ds["team_stats"].freshness is Freshness.FINALIZED
        assert ds["injuries"].freshness is Freshness.MISSING
        needing = {n for n, m in ds.items() if m.needs_attention}
        assert {"rosters", "injuries"} <= needing
        assert not needing & {"standings", "player_stats", "team_stats"}

    def test_10_a_successful_refresh_moves_the_roster_from_stale_to_fresh(
        self, db: Session, synced, monkeypatch
    ):
        both = ("1610612737", 201939), ("1610612738", 999999)
        old_rows = [{**r, "source_retrieved_at": at(2026, 7, 21)} for r in _roster(*both)]
        jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=old_rows))
        before = dataset_freshness(db, now=self.NOW)["rosters"]
        assert before.freshness is Freshness.STALE
        fresh_rows = [{**r, "source_retrieved_at": self.NOW} for r in _roster(*both)]
        jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=fresh_rows))
        after = dataset_freshness(db, now=self.NOW + timedelta(minutes=1))["rosters"]
        assert after.freshness is Freshness.FRESH
        assert after.last_refresh["status"] == "succeeded"
        assert after.refresh_failing is False

    def test_8_players_without_statistics_are_counted_as_absent(self, db: Session):
        team, _ = _seed_league(db, self.NOW)
        make_player(db, 21, "Rookie", team, season="2025-26")
        ds = dataset_freshness(db, now=self.NOW)["player_stats"]
        assert ds.completeness == 0.5
        assert any("absent, not zero" in n for n in ds.notes)

    def test_9_a_failed_refresh_is_visible_while_the_stored_rows_are_left_untouched(
        self, db: Session, synced, monkeypatch
    ):
        run_on(monkeypatch, date(2026, 10, 18))
        _seed_league(db, self.NOW - timedelta(hours=1))
        stored = db.query(PlayerSeasonStats).one().stats
        with pytest.raises(ProviderBlockedError):
            jobs.sync_player_stats(db, ["2025-26"], _Raises(ProviderBlockedError("LeagueDashPlayerStats", "403")))
        assert db.query(PlayerSeasonStats).one().stats == stored
        meta_ = dataset_freshness(db, now=self.NOW)["player_stats"]
        assert meta_.freshness is Freshness.FINALIZED, "the data is still the last good snapshot"
        assert meta_.refresh_failing is True, "...and the failure is not hidden"
        assert meta_.needs_attention
        assert "ProviderBlockedError" in meta_.last_refresh["error"]

    def test_an_expected_empty_run_does_not_count_as_failing(self, db: Session, synced, monkeypatch):
        run_on(monkeypatch, date(2026, 10, 18))
        jobs.sync_player_stats(db, ["2026-27"], _Empty())
        ds = dataset_freshness(db, "2026-27", now=self.NOW)["player_stats"]
        assert ds.freshness is Freshness.NOT_YET_AVAILABLE
        assert ds.refresh_failing is False
        assert ds.last_refresh["outcome"] == "expected_empty"
        assert not ds.needs_attention

    def test_while_the_tools_read_the_prior_season_the_league_season_is_reported_beside_it(
        self, db: Session
    ):
        from app.api.v1.provenance import get_freshness

        _seed_league(db, at(2026, 7, 21))
        body = get_freshness(db)
        assert body["season"]["analysis_season"] == "2025-26"
        assert body["season"]["league_season"] == "2026-27"
        assert body["season"]["analysis_is_baseline"] is True
        assert set(body["league_season_datasets"]) == {
            "rosters", "standings", "player_stats", "team_stats", "games"
        }

    def test_rollover_readiness_lists_exactly_what_is_missing(self, db: Session):
        _seed_league(db, self.NOW)
        report = rollover_readiness(db, now=self.NOW)
        assert report["league_season"] == "2026-27"
        assert report["checks"]["rosters_for_league_season"]["present"] is False
        assert report["checks"]["regular_season_player_stats"]["expected_yet"] is False
        assert "rosters_for_league_season" in report["missing_before_rollover"]
        assert "regular_season_player_stats" not in report["missing_before_rollover"], (
            "statistics that cannot exist before opening night are not a blocker"
        )
        assert report["ready"] is False

    def test_a_cap_year_that_has_fallen_behind_the_league_year_is_flagged(self, db: Session, monkeypatch):
        """1 July 2027 turns the league year to 2027-28; CAP_LEAGUE_YEAR is still a hand edit."""
        from app.config import get_settings

        report = rollover_readiness(db, now=at(2027, 7, 15))
        check = report["checks"]["cap_parameters_for_league_year"]
        assert check["present"] is False and check["league_year"] == "2027-28"
        assert check["configured_cap_league_year"] == get_settings().cap_league_year
        assert "cap_parameters_for_league_year" in report["missing_before_rollover"] or report["ready"] is False


# ----------------------------------------------------------------- rosters across the rollover


class TestRosterRollover:
    def test_players_who_moved_keep_their_history_and_the_new_season_starts_clean(
        self, db: Session, synced
    ):
        jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=_roster(("1610612737", 201939), ("1610612738", 203507))))
        # July: 203507 is traded; a rookie and a free agent appear on 2026-27 rosters.
        jobs.sync_rosters(db, "2026-27", FakeProvider(rosters=_roster(
            ("1610612737", 201939), ("1610612737", 203507), ("1610612738", 999001), ("1610612738", 999002))))
        old = db.query(RosterEntry).filter_by(season="2025-26", is_current=True).count()
        new = db.query(RosterEntry).filter_by(season="2026-27", is_current=True).count()
        assert (old, new) == (2, 4), "last season's roster is intact beside the new one"
        traded = db.query(Player).filter_by(nba_player_id=203507).one()
        seasons_teams = {(r.season, r.team.abbreviation) for r in
                         db.query(RosterEntry).filter_by(player_id=traded.id).all()}
        assert seasons_teams == {("2025-26", "BOS"), ("2026-27", "ATL")}

    def test_rookies_have_no_history_and_are_not_given_one(self, db: Session, synced):
        jobs.sync_rosters(db, "2026-27", FakeProvider(rosters=_roster(("1610612737", 999001), ("1610612738", 999002))))
        rookie = db.query(Player).filter_by(nba_player_id=999001).one()
        rows = player_stat_rows(db, [rookie.id], season="2025-26")
        assert rows.rows == {} and rows.missing_player_ids == [rookie.id]

    def test_a_roster_as_of_a_past_date_is_reconstructed_from_sightings(self, db: Session, synced):
        jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=_roster(("1610612737", 201939), ("1610612737", 300001), ("1610612738", 999999))))
        jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=_roster(("1610612737", 201939), ("1610612738", 999999))))
        atl = db.query(Team).filter_by(nba_team_id=1610612737).one()
        first_seen = min(e.valid_from for e in db.query(RosterEntry).all())
        first_seen = first_seen if first_seen.tzinfo else first_seen.replace(tzinfo=UTC)
        then = roster_as_of(db, first_seen + timedelta(microseconds=1), atl.id, "2025-26")
        now = roster_as_of(db, datetime.now(UTC) + timedelta(days=1), atl.id, "2025-26")
        assert len(then.entries) == 2 and len(now.entries) == 1
        before = roster_as_of(db, first_seen - timedelta(days=30), atl.id, "2025-26")
        assert before.entries == []
        assert any("unknown, not empty" in n for n in before.meta.notes)

    def test_a_player_listed_by_two_teams_in_one_fetch_is_placed_once_on_the_newer_sighting(
        self, db: Session, synced
    ):
        """The 30 rosters are fetched one after another; a trade in the gap used to put the
        player on two current rosters at once."""
        rows = _roster(("1610612737", 201939), ("1610612738", 201939), ("1610612737", 300001))
        rows[0]["source_retrieved_at"] = NOW
        rows[1]["source_retrieved_at"] = NOW + timedelta(minutes=2)
        jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=rows))
        mover = db.query(Player).filter_by(nba_player_id=201939).one()
        entry = db.query(RosterEntry).filter_by(is_current=True, player_id=mover.id).one()
        assert entry.team.abbreviation == "BOS"
        run = db.scalars(select(DataSyncRun).where(DataSyncRun.job_name == "sync_rosters")).one()
        assert run.detail["player_on_two_teams"] == 1

    def test_the_same_row_twice_is_one_roster_spot(self, db: Session, synced):
        rows = _roster(("1610612737", 201939), ("1610612738", 999999)) * 2
        jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=rows))
        assert db.query(RosterEntry).count() == 2

    def test_a_first_snapshot_of_a_new_season_must_still_look_like_a_league(
        self, db: Session
    ):
        provider = FakeProvider(teams=[
            {"nba_team_id": 1000 + i, "full_name": f"Team {i}", "abbreviation": f"T{i:02d}",
             "nickname": f"T{i}", "city": "X"} for i in range(30)])
        jobs.sync_teams(db, provider)
        one_team = FakeProvider(rosters=_roster(("1000", 5001)))
        with pytest.raises(jobs.SnapshotRejectedError, match="first snapshot covers 1 of 30"):
            jobs.sync_rosters(db, "2026-27", one_team)
        assert db.query(RosterEntry).count() == 0

    def test_a_failed_league_season_roster_does_not_mark_the_roster_being_read_as_failing(
        self, db: Session, synced, monkeypatch
    ):
        monkeypatch.setattr("app.config.Settings.season_context",
                            property(lambda self: season_context(date(2026, 10, 18))))
        jobs.sync_rosters(db, "2025-26", FakeProvider(rosters=_roster(("1610612737", 201939), ("1610612738", 999999))))
        with pytest.raises(jobs.SnapshotRejectedError):
            jobs.prepare_league_season(db, FakeProvider(rosters=[]))
        jobs_failed = {r.job_name for r in db.scalars(select(DataSyncRun)).all() if r.status == "failed"}
        assert jobs_failed == {"prepare_league_rosters"}
        reading = dataset_freshness(db, "2025-26", now=at(2026, 10, 18))["rosters"]
        assert reading.refresh_failing is False
        staged = dataset_freshness(db, "2026-27", now=at(2026, 10, 18))["rosters"]
        assert staged.refresh_failing is True


class TestPartialRefreshesAreNamed:
    def test_some_seasons_written_and_one_failed_is_reported_with_both_lists(
        self, db: Session, synced, monkeypatch
    ):
        run_on(monkeypatch, date(2026, 10, 18))

        class FailsOne(FakeProvider):
            async def fetch_player_stats(self, season, **kw):
                if season == "2024-25":
                    raise ProviderBlockedError("LeagueDashPlayerStats", "403")
                return await super().fetch_player_stats(season)

        with pytest.raises(jobs.PartialRefreshError, match="2 of 3 seasons written"):
            jobs.sync_player_stats(db, ["2023-24", "2024-25", "2025-26"], FailsOne())
        stored = {r.season for r in db.scalars(select(PlayerSeasonStats)).all()}
        assert stored == {"2023-24", "2025-26"}, "what succeeded is kept"
        run = db.scalars(select(DataSyncRun).where(DataSyncRun.job_name == "sync_player_stats")).one()
        assert run.status == "failed" and "2024-25" in run.error_message
