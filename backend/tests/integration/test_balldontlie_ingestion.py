"""R8.3: BALLDONTLIE -> canonical tables, against live-SHAPED synthetic responses.

What these tests establish is the *behaviour of Pivot's ingestion* (idempotence, non-destruction,
quarantine, honest missing values, provenance). They are fixture-tested: whether the live provider
returns these shapes was established separately, on 2026-10-18, and is recorded in docs/r8-3.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.canonical.provider_stats import provider_stat_rows
from app.canonical.rosters import resolve_roster_season
from app.canonical.seasons import SeasonType
from app.cba import resolver
from app.config import get_settings
from app.db.models import (
    Contract,
    ContractYear,
    DataQualityIssue,
    DataSyncRun,
    Injury,
    ProviderEntityId,
    ProviderStatSnapshot,
)
from app.ingestion import balldontlie_jobs as jobs
from app.integrations.balldontlie.client import BalldontlieClient
from app.integrations.balldontlie.provider import BalldontlieProvider
from tests.conftest import make_player, make_team

LEAGUE_YEAR = "2026-27"


def bdl_team(i: int, city: str, nick: str, abbr: str) -> dict:
    return {"id": i, "conference": "East", "division": "Southeast", "city": city, "name": nick,
            "full_name": f"{city} {nick}", "abbreviation": abbr}


def bdl_player(i: int, first: str, last: str, team_id: int | None = None) -> dict:
    row = {"id": i, "first_name": first, "last_name": last, "position": "G", "height": "6-6",
           "weight": "200", "jersey_number": "1", "college": "X", "country": "USA",
           "draft_year": 2020, "draft_round": 1, "draft_number": 5}
    if team_id is not None:
        row["team_id"] = team_id
    return row


class Upstream:
    def __init__(self, routes):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        handler = self.routes.get(request.url.path)
        if handler is None:
            return httpx.Response(404, json={"error": "no route"})
        body = handler(request) if callable(handler) else handler
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, json=body)


def provider_for(routes: dict) -> tuple[BalldontlieProvider, Upstream]:
    upstream = Upstream(routes)
    client = BalldontlieClient(
        "not-a-real-key-ccccccc",
        tier="goat",
        max_retries=0,
        transport=httpx.MockTransport(upstream),
        sleep=lambda s: None,
    )
    return BalldontlieProvider(client), upstream


@pytest.fixture()
def world(db: Session):
    season = resolve_roster_season(db)
    atl = make_team(db, 1, "ATL", "Atlanta Hawks")
    bos = make_team(db, 2, "BOS", "Boston Celtics")
    stars = make_player(db, 1001, "Alex Fixture", atl, season=season)
    bench = make_player(db, 1002, "Blake Fixture", atl, season=season)
    camp = make_player(db, 1003, "Casey Fixture", bos, season=season)
    ghost = make_player(db, 1004, "Dana Fixture", bos, season=season)  # BDL has no row for them
    for p in (stars, bench, camp, ghost):
        p.draft_year, p.draft_number, p.height_inches = 2020, 5, 78
    db.commit()
    teams = [bdl_team(1, "Atlanta", "Hawks", "ATL"), bdl_team(2, "Boston", "Celtics", "BOS")]
    teams[1]["division"] = "Atlantic"
    players = [bdl_player(11, "Alex", "Fixture"), bdl_player(12, "Blake", "Fixture"),
               bdl_player(13, "Casey", "Fixture")]
    for row, team in zip(players, (teams[0], teams[0], teams[1]), strict=True):
        row["team"] = team
    contracts = {
        1: [
            {"id": 1, "player_id": 11, "season": 2026, "team_id": 1, "cap_hit": 30_000_000,
             "base_salary": 28_000_000, "total_cash": 29_000_000, "rank": 1,
             "player": bdl_player(11, "Alex", "Fixture"), "team": teams[0]},
            {"id": 2, "player_id": 12, "season": 2026, "team_id": 1, "cap_hit": None,
             "base_salary": None, "total_cash": 678_882, "rank": 0,
             "player": bdl_player(12, "Blake", "Fixture"), "team": teams[0]},
        ],
        2: [
            {"id": 3, "player_id": 13, "season": 2026, "team_id": 2, "cap_hit": 5_000_000,
             "base_salary": 5_000_000, "total_cash": 5_000_000, "rank": 9,
             "player": bdl_player(13, "Casey", "Fixture"), "team": teams[1]},
            {"id": 4, "player_id": 99, "season": 2026, "team_id": 2, "cap_hit": 1_000_000,
             "base_salary": 1_000_000, "total_cash": 1_000_000, "rank": 20,
             "player": bdl_player(99, "Zed", "Nobody"), "team": teams[1]},
        ],
    }

    def contracts_route(request: httpx.Request):
        assert request.url.params["season"] == "2026"
        return {"data": contracts[int(request.url.params["team_id"])]}

    routes = {
        "/nba/v1/teams": {"data": teams},
        "/nba/v1/players/active": {"data": players, "meta": {"per_page": 100}},
        "/nba/v1/contracts/teams": contracts_route,
    }
    return {"routes": routes, "season": season, "players": {"stars": stars, "bench": bench,
            "camp": camp, "ghost": ghost}, "contracts": contracts}


def linked(db: Session) -> dict[str, str]:
    return {
        r.provider_id: r.internal_id
        for r in db.scalars(select(ProviderEntityId).where(ProviderEntityId.entity_type == "player"))
    }


# --------------------------------------------------------------------------- identities


def test_identity_sync_links_and_reports_what_it_could_not(db: Session, world):
    provider, _ = provider_for(world["routes"])
    detail = jobs.sync_identities(db, provider)
    assert detail["teams"]["linked"] == 2 and detail["players"]["linked"] == 3
    assert linked(db)["11"] == world["players"]["stars"].id
    # Idempotent: a second run creates nothing.
    again = jobs.sync_identities(db, provider)
    assert again["players"]["created"] == 0 and again["players"]["unchanged"] == 3


# ---------------------------------------------------------------------------- contracts


def test_contract_sync_writes_cap_hit_as_salary_and_keeps_the_other_figures(db: Session, world):
    provider, upstream = provider_for(world["routes"])
    jobs.sync_identities(db, provider)
    detail = jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    assert len(upstream.requests) >= 2 + 2  # teams, active, and one contracts request per franchise
    star = world["players"]["stars"]
    year = db.scalar(
        select(ContractYear).join(Contract).where(Contract.player_id == star.id)
    )
    assert (year.season, year.salary, year.salary_basis) == (LEAGUE_YEAR, 30_000_000, "cap_hit")
    assert (year.base_salary, year.total_cash) == (28_000_000, 29_000_000)
    assert year.source_provider == "balldontlie" and year.source_retrieved_at is not None
    contract = db.get(Contract, year.contract_id)
    assert contract.contract_type is None and contract.no_trade_clause is None, "nothing inferred"
    assert detail["outcome"] == "complete" and detail["years_created"] == 2


def test_a_missing_cap_hit_is_unpriced_never_zero_and_never_the_cash_figure(db: Session, world):
    provider, _ = provider_for(world["routes"])
    jobs.sync_identities(db, provider)
    detail = jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    bench = world["players"]["bench"]
    assert db.scalar(select(Contract).where(Contract.player_id == bench.id)) is None
    assert [u["name"] for u in detail["unpriced_players"]] == ["Blake Fixture"]
    assert detail["unpriced_players"][0]["total_cash"] == 678_882
    salary, contract = resolver.player_salary(db, bench.id, LEAGUE_YEAR)
    assert salary is None and contract is None


def test_unmapped_provider_players_are_quarantined_not_guessed(db: Session, world):
    provider, _ = provider_for(world["routes"])
    jobs.sync_identities(db, provider)
    detail = jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    assert detail["unmapped_players_total"] == 1
    assert detail["unmapped_players"][0]["name"] == "Zed Nobody"
    assert db.scalar(select(func.count(Contract.id))) == 2  # Alex and Casey only


def test_contract_sync_is_idempotent_and_updates_in_place(db: Session, world):
    provider, _ = provider_for(world["routes"])
    jobs.sync_identities(db, provider)
    jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    second = jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    assert second["years_created"] == 0 and second["years_updated"] == 0
    assert second["years_unchanged"] == 2
    world["contracts"][1][0]["cap_hit"] = 31_000_000  # the provider corrects a figure
    third = jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    assert third["years_updated"] == 1 and third["years_created"] == 0
    assert db.scalar(select(func.count(Contract.id))) == 2
    assert db.scalar(select(func.count(ContractYear.id))) == 2


def test_a_contract_the_provider_stops_listing_is_kept_not_deleted(db: Session, world):
    provider, _ = provider_for(world["routes"])
    jobs.sync_identities(db, provider)
    jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    world["contracts"][2] = []
    detail = jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    assert detail["not_reseen"] == 1
    assert db.scalar(select(func.count(ContractYear.id))) == 2


def test_another_providers_salary_is_never_overwritten(db: Session, world):
    star = world["players"]["stars"]
    other = Contract(player_id=star.id, source_provider="bbref_snapshot", source_name="snapshot")
    db.add(other)
    db.flush()
    db.add(ContractYear(contract_id=other.id, season=LEAGUE_YEAR, salary=29_000_000,
                        source_provider="bbref_snapshot"))
    db.commit()
    provider, _ = provider_for(world["routes"])
    jobs.sync_identities(db, provider)
    detail = jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    year = db.scalar(select(ContractYear).where(ContractYear.contract_id == other.id))
    assert year.salary == 29_000_000 and year.source_provider == "bbref_snapshot"
    assert detail["provider_conflicts_total"] == 1
    assert db.scalar(
        select(func.count(DataQualityIssue.id)).where(
            DataQualityIssue.check_name == "contract_provider_conflict"
        )
    ) == 1


def test_one_failed_team_makes_the_run_partial_and_still_keeps_the_rest(db: Session, world):
    routes = dict(world["routes"])
    good = routes["/nba/v1/contracts/teams"]

    def flaky(request: httpx.Request):
        if request.url.params["team_id"] == "2":
            return httpx.Response(503, json={"error": "down"})
        return good(request)

    routes["/nba/v1/contracts/teams"] = flaky
    provider, _ = provider_for(routes)
    jobs.sync_identities(db, provider)
    detail = jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    assert detail["outcome"] == "partial" and len(detail["teams_failed"]) == 1
    assert db.scalar(select(func.count(ContractYear.id))) == 1


def test_coverage_is_reported_from_the_roster_side(db: Session, world):
    provider, _ = provider_for(world["routes"])
    jobs.sync_identities(db, provider)
    detail = jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    assert "2/4 rostered players" in detail["summary"] or "2 of 4" in detail["summary"] or "50.0%" in detail["summary"]


def test_contract_run_is_recorded_with_provenance(db: Session, world):
    provider, _ = provider_for(world["routes"])
    jobs.sync_identities(db, provider)
    jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    run = db.scalar(select(DataSyncRun).where(DataSyncRun.job_name == "sync_contracts"))
    assert run.status == "succeeded" and run.detail["provider"] == "balldontlie"
    assert run.detail["season"] == LEAGUE_YEAR and "BALLDONTLIE_API_KEY" not in str(run.detail)


# ----------------------------------------------------------------------------- injuries


def test_injuries_open_update_close_and_never_claim_health(db: Session, world):
    feed = {"data": [
        {"player": bdl_player(11, "Alex", "Fixture", 1), "return_date": "2026-11-03",
         "description": "Ankle", "status": "Out"},
        {"player": bdl_player(12, "Blake", "Fixture", 1), "return_date": None,
         "description": None, "status": "Day-To-Day"},
    ], "meta": {"per_page": 100}}
    routes = {**world["routes"], "/nba/v1/player_injuries": lambda r: feed}
    provider, _ = provider_for(routes)
    jobs.sync_identities(db, provider)
    first = jobs.sync_injuries_from_balldontlie(db, provider)
    assert first["opened"] == 2
    star = world["players"]["stars"]
    row = db.scalar(select(Injury).where(Injury.player_id == star.id))
    assert str(row.return_date) == "2026-11-03" and row.valid_to is None
    assert jobs.sync_injuries_from_balldontlie(db, provider)["unchanged"] == 2
    feed["data"][0]["status"] = "Day-To-Day"
    assert jobs.sync_injuries_from_balldontlie(db, provider)["updated"] == 1
    feed["data"].pop(0)
    last = jobs.sync_injuries_from_balldontlie(db, provider)
    assert last["closed"] == 1 and "not that the player is healthy" in last["note"]
    db.refresh(row)
    assert row.valid_to is not None
    assert db.scalar(select(func.count(Injury.id))) == 2, "closed, not deleted"


# ---------------------------------------------------------------------------- statistics


def stat_row(pid: int, season_type: str, **stats) -> dict:
    return {"player": bdl_player(pid, "P", f"X{pid}"), "season": 2025, "season_type": season_type,
            "stats": {"gp": 70, "pts": 20.5, "pts_rank": 33, "fg3_pct": None, **stats}}


def test_stats_are_stored_per_scope_with_aggregation_and_without_ranks(db: Session, world):
    rows = [
        {**stat_row(11, "regular"), "player": bdl_player(11, "Alex", "Fixture")},
        {**stat_row(12, "regular"), "player": bdl_player(12, "Blake", "Fixture")},
    ]
    team_rows = [{"team": bdl_team(1, "Atlanta", "Hawks", "ATL"), "season": 2025,
                  "season_type": "regular", "stats": {"pts": 112.4}}]
    routes = {**world["routes"],
              "/nba/v1/season_averages/general": {"data": rows, "meta": {"per_page": 100}},
              "/nba/v1/team_season_averages/general": {"data": team_rows}}
    provider, _ = provider_for(routes)
    jobs.sync_identities(db, provider)
    detail = jobs.sync_stats_from_balldontlie(db, provider, "2025-26", groups=["general/base"])
    assert detail["outcome"] == "complete"
    star = world["players"]["stars"]
    got = provider_stat_rows(db, [star.id, world["players"]["ghost"].id], season="2025-26",
                             stat_group="general/base")
    assert got.aggregation == "per_game" and got.value(star.id, "pts") == 20.5
    assert "pts_rank" not in got.rows[star.id].values, "provider-relative ranks are dropped"
    assert got.rows[star.id].values["fg3_pct"] is None, "missing stays null"
    assert got.value(star.id, "fg3_pct") is None
    assert got.missing_ids == [world["players"]["ghost"].id], "absent, not zero"
    # A regular-season question never sees another scope.
    playoffs = provider_stat_rows(db, [star.id], season="2025-26", season_type=SeasonType.PLAYOFFS,
                                  stat_group="general/base")
    assert playoffs.rows == {} and playoffs.missing_ids == [star.id]
    # Idempotent.
    again = jobs.sync_stats_from_balldontlie(db, provider, "2025-26", groups=["general/base"])
    assert again["groups"]["general/base"]["player"]["unchanged"] == 2
    assert db.scalar(select(func.count(ProviderStatSnapshot.id))) == 3


def test_an_incomplete_read_is_not_persisted(db: Session, world):
    page = {"data": [stat_row(11, "regular")], "meta": {"next_cursor": 5, "per_page": 100}}
    routes = {**world["routes"], "/nba/v1/season_averages/general": page,
              "/nba/v1/team_season_averages/general": {"data": []}}
    provider, _ = provider_for(routes)
    jobs.sync_identities(db, provider)
    # next_cursor never ends: the client stops on the repeated cursor and warns.
    detail = jobs.sync_stats_from_balldontlie(db, provider, "2025-26", groups=["general/base"])
    assert detail["groups"]["general/base"]["player"] == {"persisted": False, "reason": "incomplete read"}
    assert db.scalar(select(func.count(ProviderStatSnapshot.id))) == 0
    assert detail["outcome"] == "partial"


def test_stats_scope_mismatch_rows_are_rejected_not_relabelled(db: Session, world):
    rows = [stat_row(11, "playoffs")]  # asked for regular
    routes = {**world["routes"], "/nba/v1/season_averages/general": {"data": rows, "meta": {}},
              "/nba/v1/team_season_averages/general": {"data": []}}
    provider, _ = provider_for(routes)
    jobs.sync_identities(db, provider)
    detail = jobs.sync_stats_from_balldontlie(db, provider, "2025-26", groups=["general/base"], teams=False)
    assert detail["groups"]["general/base"]["player"]["persisted"] is False
    assert db.scalar(select(func.count(ProviderStatSnapshot.id))) == 0


# ------------------------------------------------------------------- existing consumers


def test_payroll_service_reads_the_new_rows_and_discloses_the_gap(db: Session, world, cap_params):
    from app.db.models import Team
    from app.services.payroll import team_payroll_summary

    provider, _ = provider_for(world["routes"])
    jobs.sync_identities(db, provider)
    jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    assert get_settings().cap_league_year == LEAGUE_YEAR
    team = db.scalar(select(Team).where(Team.abbreviation == "ATL"))
    summary = team_payroll_summary(db, team)
    assert summary["payroll_known"] == 30_000_000, "Alex counts; Blake (no cap hit) is not 0, not cash"
    assert summary["payroll_available"] is False, "a roster with an unpriced player has no verified payroll"
    assert "Blake Fixture" in str(summary), "the unpriced player is named"


def test_years_remaining_is_unknown_not_one_for_a_single_season_contract(db: Session, world):
    """The per-season endpoint loads ONE season for every player. Counting it would label a
    five-year contract 'expiring' on the roster payload (found in the R8.3 cross-tool run)."""
    from app.api.v1.teams import _remaining_contract_years

    provider, _ = provider_for(world["routes"])
    jobs.sync_identities(db, provider)
    jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    ids = [c.id for c in db.scalars(select(Contract))]
    assert ids and _remaining_contract_years(db, ids, LEAGUE_YEAR) == {}
    # A multi-season source (the file snapshots) is still counted exactly as before.
    star = world["players"]["ghost"]
    other = Contract(player_id=star.id, source_provider="file", source_name="snapshot")
    db.add(other)
    db.flush()
    for season in ("2026-27", "2027-28"):
        db.add(ContractYear(contract_id=other.id, season=season, salary=1, source_provider="file"))
    db.commit()
    assert _remaining_contract_years(db, [other.id], LEAGUE_YEAR) == {other.id: 2}


def test_opting_in_makes_the_contract_provider_truthfully_configured(monkeypatch):
    from app.integrations.contracts import get_contract_provider

    get_settings.cache_clear()
    monkeypatch.setenv("CONTRACT_DATA_PROVIDER", "balldontlie")
    try:
        provider = get_contract_provider()
        assert provider is not None and provider.name == "balldontlie"
        assert provider.fetch_contracts() == []
    finally:
        get_settings.cache_clear()


def test_cap_outlook_does_not_call_a_single_season_contract_expiring(db: Session, world, cap_params):
    """Found by the R8.3 browser smoke test: Cap Center labelled every BALLDONTLIE contract
    'expiring' (a five-year deal included) because only one season was on file."""
    from fastapi.testclient import TestClient

    from app.db.base import get_db
    from app.db.models import Team
    from app.main import app

    provider, _ = provider_for(world["routes"])
    jobs.sync_identities(db, provider)
    jobs.sync_contracts_from_balldontlie(db, provider, LEAGUE_YEAR)
    team = db.scalar(select(Team).where(Team.abbreviation == "ATL"))
    app.dependency_overrides[get_db] = lambda: db
    try:
        body = TestClient(app).get(f"/api/v1/teams/{team.id}/cap-outlook").json()
    finally:
        app.dependency_overrides.pop(get_db, None)
    rows = body["players"]
    assert rows and all(r["expiring"] is None and r["contract_length_known"] is False for r in rows)
    assert body["single_season_contracts"] == len(rows)
    assert "contract length, options and expiry are not known" in body["note"]
