"""R8 canonical layer: seasons, statistics semantics, freshness, identity, roster membership."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.canonical.identity import (
    FRANCHISE_ERAS,
    TEAM_CODE_ALIASES,
    PlayerHints,
    PlayerIdentityIndex,
    ProviderIdMap,
    canonical_team_code,
    franchise_era,
    franchise_for_historical_code,
)
from app.canonical.provenance import (
    Freshness,
    assess_freshness,
    build_source_meta,
    stale_after_seconds,
)
from app.canonical.records import (
    CallMeta,
    ProviderResult,
    ProviderStatus,
    ProviderUnavailableError,
)
from app.canonical.rosters import (
    current_roster,
    current_roster_clause,
    current_team_of,
    resolve_season,
)
from app.canonical.seasons import (
    InvalidSeasonError,
    Season,
    SeasonType,
    StatScope,
    game_date_from_utc,
    is_valid_season_label,
)
from app.canonical.stat_access import player_stat_rows
from app.canonical.stats import (
    Aggregation,
    Basis,
    IncompatibleStatsError,
    UnknownStatTypeError,
    minutes_per_game,
    numeric,
    per_game_from_total,
    require_comparable,
    spec_for,
)
from app.db.models import Player, PlayerSeasonStats, RosterEntry
from tests.conftest import make_player, make_team

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------- seasons


class TestSeason:
    def test_label_and_provider_year_are_the_same_season(self):
        assert Season.parse("2025-26") == Season(2025) == Season.from_provider_year(2025)
        assert Season(2025).label == "2025-26"
        assert Season(1999).label == "1999-00", "the century boundary"
        assert Season.coerce(2025) == Season.coerce("2025-26") == Season.coerce(Season(2025))

    @pytest.mark.parametrize(
        "bad", ["2025", "25-26", "2025-27", "2025/26", "2025-2026", "", "abcd-ef", "2025-25"]
    )
    def test_malformed_labels_are_errors_not_guesses(self, bad: str):
        with pytest.raises(InvalidSeasonError):
            Season.parse(bad)
        assert not is_valid_season_label(bad)

    def test_a_provider_year_must_be_an_integer(self):
        for bad in (True, "2025", 2025.0):
            with pytest.raises(InvalidSeasonError):
                Season.from_provider_year(bad)  # type: ignore[arg-type]

    def test_arithmetic_and_ordering(self):
        assert Season(2025).next().label == "2026-27"
        assert Season(2025).previous().label == "2024-25"
        assert Season(2024) < Season(2025)


class TestSeasonType:
    def test_provider_vocabularies_map_to_one_canonical_type(self):
        assert SeasonType.from_nba_com("Regular Season") is SeasonType.REGULAR
        assert SeasonType.from_balldontlie("playoffs") is SeasonType.PLAYOFFS
        assert SeasonType.from_balldontlie("playin") is SeasonType.PLAY_IN
        assert SeasonType.REGULAR.nba_com_label == "Regular Season"
        assert SeasonType.IN_SEASON_TOURNAMENT.balldontlie_label == "ist"

    def test_every_type_round_trips_through_both_providers(self):
        for kind in SeasonType:
            assert SeasonType.from_nba_com(kind.nba_com_label) is kind
            assert SeasonType.from_balldontlie(kind.balldontlie_label) is kind

    def test_an_unknown_label_is_an_error(self):
        with pytest.raises(InvalidSeasonError):
            SeasonType.from_nba_com("Summer League")

    def test_scope_requires_both_parts(self):
        assert str(StatScope.regular("2025-26")) == "2025-26/regular"
        assert StatScope.regular(2025) != StatScope(Season(2025), SeasonType.PLAYOFFS)


def test_a_late_game_is_filed_under_the_league_calendar_date():
    # 9:30 pm Eastern on 14 Jan is 02:30 UTC on 15 Jan.
    assert game_date_from_utc(datetime(2026, 1, 15, 2, 30, tzinfo=UTC)) == date(2026, 1, 14)
    with pytest.raises(ValueError):
        game_date_from_utc(datetime(2026, 1, 15, 2, 30))


# ----------------------------------------------------------------------- statistics


class TestStatDefinitions:
    def test_every_stored_stat_type_has_a_definition(self):
        assert spec_for("base").aggregation is Aggregation.PER_GAME
        assert spec_for("totals").aggregation is Aggregation.TOTALS
        assert spec_for("estimated").basis is Basis.ESTIMATED
        assert spec_for("csv_totals").synthetic is True

    def test_an_unknown_stat_type_is_an_error_not_a_default(self):
        with pytest.raises(UnknownStatTypeError):
            spec_for("per36")

    def test_per_game_and_totals_cannot_be_combined(self):
        assert require_comparable("base", "advanced") is Aggregation.PER_GAME
        with pytest.raises(IncompatibleStatsError, match="factor of games played"):
            require_comparable("base", "totals")

    def test_the_minutes_column_is_normalised_to_a_per_game_unit(self):
        """34.8 (base) and 2,265 (totals) are the same player's MIN — measured on the dev DB."""
        assert minutes_per_game(34.8, 65, "base") == 34.8
        assert minutes_per_game(2265.0, 65, "totals") == pytest.approx(34.846, abs=1e-3)
        assert minutes_per_game(2265.0, 0, "totals") is None
        assert minutes_per_game(None, 65, "base") is None

    def test_missing_is_none_never_zero(self):
        for missing in (None, float("nan"), float("inf"), "n/a", "", True, [], {}):
            assert numeric(missing) is None
        assert numeric("12.5") == 12.5 and numeric(0) == 0.0
        assert per_game_from_total(100, 0) is None
        assert per_game_from_total(None, 10) is None
        assert per_game_from_total(100, 4) == 25.0


# ---------------------------------------------------------------------- freshness


class TestFreshness:
    def test_the_oldest_row_decides(self):
        window = 86_400
        fresh = NOW - timedelta(hours=1)
        old = NOW - timedelta(days=90)
        state, age = assess_freshness(old, 30, window, NOW)
        assert state is Freshness.STALE and age == 90 * 86_400
        assert assess_freshness(fresh, 30, window, NOW)[0] is Freshness.FRESH

    def test_no_rows_is_missing_not_fresh(self):
        assert assess_freshness(None, 0, 86_400, NOW) == (Freshness.MISSING, None)

    def test_rows_without_timestamps_are_unknown(self):
        assert assess_freshness(None, 5, 86_400, NOW)[0] is Freshness.UNKNOWN

    def test_a_table_that_is_mostly_current_but_partly_stale_is_stale(self):
        class Row:
            def __init__(self, hours_old: int):
                self.source_retrieved_at = NOW - timedelta(hours=hours_old)
                self.source_provider = "nba_api"
                self.valid_from = None

        rows = [Row(1)] * 29 + [Row(24 * 90)]
        meta = build_source_meta("rosters", rows, now=NOW)
        assert meta.freshness is Freshness.STALE
        assert meta.age_seconds == 90 * 86_400 and meta.present_count == 30
        assert meta.retrieved_at > meta.oldest_retrieved_at

    def test_naive_sqlite_timestamps_are_treated_as_utc(self):
        class Row:
            source_retrieved_at = datetime(2026, 10, 18, 11, 0)  # naive, as SQLite returns
            source_provider = "nba_api"
            valid_from = None

        meta = build_source_meta("rosters", [Row()], now=NOW)
        assert meta.freshness is Freshness.FRESH and meta.age_seconds == 3600

    def test_completeness_is_only_claimed_against_a_stated_expectation(self):
        class Row:
            source_retrieved_at = NOW
            source_provider = "nba_api"
            valid_from = None

        assert build_source_meta("rosters", [Row()] * 27, expected_count=30, now=NOW).completeness == 0.9
        assert build_source_meta("rosters", [Row()] * 27, now=NOW).completeness is None

    def test_windows_are_configuration_and_differ_by_dataset(self):
        assert stale_after_seconds("rosters") < stale_after_seconds("player_stats")
        assert stale_after_seconds("contracts") > stale_after_seconds("player_stats")

    def test_the_upstream_is_read_from_the_rows_not_assumed(self):
        class Row:
            source_retrieved_at = NOW
            source_provider = "demo_seed"
            valid_from = None

        meta = build_source_meta("rosters", [Row()], now=NOW)
        assert meta.providers == ["demo_seed"] and "NBA.com" not in meta.upstream


# ------------------------------------------------------------- provider result envelope


class TestProviderResult:
    META = CallMeta("p", "e")

    def test_empty_is_an_answer_and_unavailable_is_not(self):
        empty = ProviderResult.ok([], self.META)
        assert empty.status is ProviderStatus.EMPTY and empty.has_answer
        assert empty.require_data() == ()
        down = ProviderResult.failed(ProviderStatus.UNAVAILABLE, self.META, "timeout")
        assert not down.has_answer
        with pytest.raises(ProviderUnavailableError):
            down.require_data()

    def test_ok_is_not_a_failure_status(self):
        with pytest.raises(ValueError):
            ProviderResult.failed(ProviderStatus.OK, self.META, "x")


# ------------------------------------------------------------------------- identity


class TestTeamCodes:
    def test_aliases_resolve_to_the_nba_abbreviation(self):
        assert canonical_team_code("brk") == "BKN"
        assert canonical_team_code("PHO", "bbref") == "PHX"
        assert canonical_team_code("phl", "asset_filename") == "PHI"
        assert canonical_team_code("ATL") == "ATL"

    def test_no_alias_shadows_a_real_abbreviation(self):
        real = {
            "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND",
            "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX",
            "POR", "SAC", "SAS", "TOR", "UTA", "WAS",
        }  # fmt: skip
        assert not set(TEAM_CODE_ALIASES) & real

    def test_the_three_formerly_duplicated_maps_are_one_map(self):
        from app.assets.indexer import LOGO_STEM_OVERRIDES
        from app.ingestion.transactions.importer import ABBREVIATION_ALIASES
        from app.integrations.contracts.bbref_provider import BBREF_TO_NBA_TEAM

        assert ABBREVIATION_ALIASES == BBREF_TO_NBA_TEAM == {"BRK": "BKN", "CHO": "CHA", "PHO": "PHX"}
        assert LOGO_STEM_OVERRIDES == {"phl": "PHI", "uth": "UTA"}


class TestFranchiseHistory:
    def test_a_season_gets_the_name_the_franchise_carried_then(self):
        assert franchise_era("OKC", Season(2007)).abbreviation == "SEA"
        assert franchise_era("OKC", Season(2008)).abbreviation == "OKC"
        assert franchise_era("BKN", Season(2011)).name == "New Jersey Nets"
        assert franchise_era("BKN", Season(2012)).name == "Brooklyn Nets"

    def test_no_recorded_rename_means_none_not_a_missing_franchise(self):
        assert franchise_era("BOS", Season(2020)) is None

    def test_historical_codes_map_to_the_current_franchise(self):
        assert franchise_for_historical_code("NJN") == "BKN"
        assert franchise_for_historical_code("SEA", Season(2005)) == "OKC"
        assert franchise_for_historical_code("SEA", Season(2015)) is None, "SEA did not exist then"
        assert franchise_for_historical_code("ZZZ") is None

    def test_eras_for_one_franchise_do_not_overlap(self):
        by_franchise: dict[str, list] = {}
        for era in FRANCHISE_ERAS:
            by_franchise.setdefault(era.franchise, []).append(era)
        for eras in by_franchise.values():
            eras.sort(key=lambda e: e.first_season)
            for earlier, later in zip(eras, eras[1:], strict=False):
                assert earlier.last_season is not None and earlier.last_season < later.first_season


@pytest.fixture()
def people(db: Session):
    team = make_team(db, 1, "AAA", "Test AAA")
    rostered = make_player(db, 100, "Pat Namesake", team)
    rostered.draft_year, rostered.draft_number = 2022, 12
    rostered.birth_date = date(2002, 5, 5)
    retired = make_player(db, 101, "Pat Namesake", None)
    retired.draft_year, retired.draft_number = 2012, 30
    retired.birth_date = date(1990, 3, 3)
    retired.height_inches = 79
    db.commit()
    return {"team": team, "rostered": rostered, "retired": retired}


class TestNameResolutionWithHints:
    def test_biography_separates_namesakes_in_either_direction(self, db, people):
        index = PlayerIdentityIndex(db, "2025-26")
        old = index.resolve(name="Pat Namesake", hints=PlayerHints(draft_year=2012), prefer_rostered=False)
        new = index.resolve(name="Pat Namesake", hints=PlayerHints(draft_year=2022))
        assert old.player.id == people["retired"].id and old.method == "exact_name+bio"
        assert new.player.id == people["rostered"].id

    def test_a_birth_date_confirms_when_draft_data_is_absent(self, db, people):
        index = PlayerIdentityIndex(db, "2025-26")
        found = index.resolve(
            name="Pat Namesake", hints=PlayerHints(birth_date=date(1990, 3, 3)), prefer_rostered=False
        )
        assert found.player.id == people["retired"].id

    def test_hints_that_contradict_every_candidate_refuse_to_match(self, db, people):
        index = PlayerIdentityIndex(db, "2025-26")
        found = index.resolve(name="Pat Namesake", hints=PlayerHints(draft_year=2001))
        assert found.player is None and found.method == "conflicting_bio"
        assert found.status.value == "unresolved"

    def test_a_single_name_match_that_nothing_contradicts_resolves(self, db, people):
        solo = make_player(db, 102, "Unique Person", None)
        index = PlayerIdentityIndex(db, "2025-26")
        found = index.resolve(name="Unique Person", hints=PlayerHints(draft_year=None))
        assert found.player.id == solo.id

    def test_a_single_name_match_whose_draft_year_conflicts_is_not_accepted(self, db, people):
        index = PlayerIdentityIndex(db, "2025-26")
        found = index.resolve(name="Unique Person", hints=PlayerHints(draft_year=2000))
        assert found.player is None

    def test_namesakes_with_no_usable_hints_stay_ambiguous_without_roster_preference(self, db, people):
        index = PlayerIdentityIndex(db, "2025-26")
        found = index.resolve(name="Pat Namesake", hints=PlayerHints(), prefer_rostered=False)
        assert found.status.value == "ambiguous" and len(found.ambiguous_candidates) == 2

    def test_a_small_height_disagreement_is_not_a_conflict_but_a_large_one_is(self, db, people):
        index = PlayerIdentityIndex(db, "2025-26")
        near = index.resolve(
            name="Pat Namesake", hints=PlayerHints(draft_year=2012, height_inches=77), prefer_rostered=False
        )
        far = index.resolve(
            name="Pat Namesake", hints=PlayerHints(draft_year=2012, height_inches=72), prefer_rostered=False
        )
        assert near.player is not None
        assert far.player is None, "the namesake whose draft year matches is 7 inches off: refuse"

    def test_a_provider_link_beats_a_name(self, db, people):
        ProviderIdMap(db).link("player", "other", "42", people["retired"].id, method="manual")
        db.flush()
        index = PlayerIdentityIndex(db, "2025-26")
        found = index.resolve(name="Pat Namesake", provider="other", provider_id=42)
        assert found.player.id == people["retired"].id and found.method == "provider_id"


class TestProviderIdMap:
    def test_linking_is_idempotent_and_never_overwrites(self, db, people):
        ids = ProviderIdMap(db)
        first = ids.link("player", "x", "1", people["rostered"].id, method="exact_name")
        again = ids.link("player", "x", "1", people["rostered"].id, method="exact_name")
        clash = ids.link("player", "x", "1", people["retired"].id, method="exact_name")
        assert (first.status, again.status, clash.status) == ("created", "unchanged", "conflict")
        assert ids.lookup("player", "x", 1) == people["rostered"].id

    def test_replacement_is_an_explicit_act(self, db, people):
        ids = ProviderIdMap(db)
        ids.link("player", "x", "1", people["rostered"].id, method="exact_name")
        done = ids.link(
            "player", "x", "1", people["retired"].id, method="manual", confidence="manual", replace=True
        )
        assert done.status == "replaced" and ids.lookup("player", "x", "1") == people["retired"].id

    def test_two_providers_may_name_one_player(self, db, people):
        ids = ProviderIdMap(db)
        ids.link("player", "a", "9", people["rostered"].id, method="manual")
        ids.link("player", "b", "9", people["rostered"].id, method="manual")
        assert len(ids.links_for("player", people["rostered"].id)) == 2

    def test_an_entity_type_outside_player_and_team_is_refused(self, db, people):
        with pytest.raises(ValueError):
            ProviderIdMap(db).link("coach", "a", "1", "x", method="manual")


# ----------------------------------------------------------------------- rosters


class TestRosterService:
    def test_the_current_season_comes_from_configuration_and_is_validated(self, monkeypatch):
        assert resolve_season() == "2025-26"
        assert resolve_season(2026) == "2026-27"
        with pytest.raises(InvalidSeasonError):
            resolve_season("2025")

    def test_clause_selects_only_the_latest_snapshot_of_the_requested_season(self, db: Session):
        team = make_team(db, 1, "AAA")
        make_player(db, 1, "Current Guy", team, season="2025-26")
        old = make_player(db, 2, "Old Season Guy", team, season="2024-25")
        db.add(
            RosterEntry(team_id=team.id, player_id=old.id, season="2025-26", is_current=False)
        )
        db.commit()
        rows = db.query(RosterEntry).filter(current_roster_clause("2025-26")).all()
        assert {r.player.full_name for r in rows} == {"Current Guy"}

    def test_a_snapshot_reports_age_completeness_and_what_membership_means(self, db: Session):
        a, b = make_team(db, 1, "AAA"), make_team(db, 2, "BBB")
        make_player(db, 1, "One", a)
        make_player(db, 2, "Two", b)
        for entry in db.query(RosterEntry).all():
            entry.source_retrieved_at = datetime.now(UTC) - timedelta(days=89)
            entry.source_provider = "nba_api"
        db.commit()
        league = current_roster(db)
        assert league.meta.freshness is Freshness.STALE
        assert league.meta.age_seconds >= 89 * 86_400
        assert league.meta.expected_count == 2 and league.meta.completeness == 1.0
        assert any("observed snapshot" in n for n in league.meta.notes)
        one_team = current_roster(db, a.id)
        assert [e.player.full_name for e in one_team.entries] == ["One"]
        assert one_team.meta.expected_count is None, "no external expectation for a roster size"

    def test_a_league_missing_a_teams_roster_says_so(self, db: Session):
        a = make_team(db, 1, "AAA")
        make_team(db, 2, "BBB")
        make_player(db, 1, "One", a)
        meta = current_roster(db).meta
        assert meta.completeness == 0.5
        assert any("1 of 2 teams" in n for n in meta.notes)

    def test_a_player_on_two_current_rosters_is_a_conflict_not_a_pick(self, db: Session):
        a, b = make_team(db, 1, "AAA"), make_team(db, 2, "BBB")
        player = make_player(db, 1, "Duplicated", a)
        db.add(RosterEntry(team_id=b.id, player_id=player.id, season="2025-26", is_current=True))
        db.commit()
        conflicts = current_roster(db).conflicts
        assert set(conflicts) == {player.id} and set(conflicts[player.id]) == {a.id, b.id}
        assert current_team_of(db, player.id).status == "conflict"
        assert current_team_of(db, player.id).team is None

    def test_unlisted_does_not_claim_free_agency_and_reports_the_snapshot_age(self, db: Session):
        a = make_team(db, 1, "AAA")
        make_player(db, 1, "Rostered", a)
        for entry in db.query(RosterEntry).all():
            entry.source_retrieved_at = datetime.now(UTC) - timedelta(days=10)
        stranger = make_player(db, 2, "Nobody", None)
        db.commit()
        membership = current_team_of(db, stranger.id)
        assert membership.status == "unlisted" and membership.team is None
        assert membership.meta.freshness is Freshness.STALE
        assert any("does not establish" in n for n in membership.meta.notes)

    def test_membership_is_season_scoped(self, db: Session):
        a = make_team(db, 1, "AAA")
        player = make_player(db, 1, "Mover", a, season="2024-25")
        assert current_team_of(db, player.id, "2024-25").team.id == a.id
        assert current_team_of(db, player.id, "2025-26").status == "unlisted"


# --------------------------------------------------------------------- stat access


class TestStatAccess:
    def _stats(self, db: Session, player: Player, stat_type: str, season_type: str, pts: float):
        db.add(
            PlayerSeasonStats(
                player_id=player.id,
                season="2025-26",
                season_type=season_type,
                stat_type=stat_type,
                games_played=10,
                minutes=30.0,
                stats={"PTS": pts},
                source_provider="nba_api",
                source_retrieved_at=datetime.now(UTC),
            )
        )

    def test_a_playoff_row_is_never_returned_for_a_regular_season_question(self, db: Session):
        team = make_team(db, 1, "AAA")
        player = make_player(db, 1, "Both", team)
        self._stats(db, player, "base", "regular", 20.0)
        self._stats(db, player, "base", "playoffs", 31.0)
        db.commit()
        result = player_stat_rows(db, [player.id], stat_type="base")
        assert result.value(player.id, "PTS") == 20.0
        playoffs = player_stat_rows(db, [player.id], stat_type="base", season_type=SeasonType.PLAYOFFS)
        assert playoffs.value(player.id, "PTS") == 31.0
        assert str(playoffs.scope) == "2025-26/playoffs"

    def test_regular_and_playoff_rows_coexist_under_the_new_unique_key(self, db: Session):
        team = make_team(db, 1, "AAA")
        player = make_player(db, 1, "Both", team)
        self._stats(db, player, "base", "regular", 20.0)
        self._stats(db, player, "base", "playoffs", 31.0)
        db.commit()  # would violate the pre-R8 unique (player, season, stat_type)
        assert db.query(PlayerSeasonStats).count() == 2

    def test_a_player_without_a_row_is_missing_not_zero(self, db: Session):
        team = make_team(db, 1, "AAA")
        veteran = make_player(db, 1, "Veteran", team)
        rookie = make_player(db, 2, "Rookie", team)
        self._stats(db, veteran, "base", "regular", 20.0)
        db.commit()
        result = player_stat_rows(db, [veteran.id, rookie.id], stat_type="base")
        assert result.missing_player_ids == [rookie.id]
        assert result.value(rookie.id, "PTS") is None
        assert result.meta.completeness == 0.5
        assert any("absent, not zero" in n for n in result.meta.notes)

    def test_an_undefined_stat_type_is_refused(self, db: Session):
        with pytest.raises(UnknownStatTypeError):
            player_stat_rows(db, [], stat_type="per36")

    def test_definition_travels_with_the_rows(self, db: Session):
        team = make_team(db, 1, "AAA")
        player = make_player(db, 1, "One", team)
        self._stats(db, player, "totals", "regular", 2000.0)
        db.commit()
        result = player_stat_rows(db, [player.id], stat_type="totals")
        assert result.spec.aggregation is Aggregation.TOTALS
        assert result.meta.basis is Basis.OBSERVED
