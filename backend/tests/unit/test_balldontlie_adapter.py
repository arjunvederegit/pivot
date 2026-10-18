"""The BALLDONTLIE adapter, against SYNTHETIC fixtures and a mock transport.

Nothing here touches the network and nothing here is evidence that the live API behaves this
way. The fixtures (tests/fixtures/balldontlie/) are shaped after the provider's published
OpenAPI document and say so in a `_fixture` field; a test below fails if one stops saying so.
Live behaviour is the trial evaluation's job (docs/r8/balldontlie-goat-evaluation.md).
"""

import json
import logging
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from app.canonical.records import ProviderStatus
from app.canonical.seasons import SeasonType
from app.config import Settings
from app.db.models import DataQualityIssue, Player, ProviderEntityId
from app.integrations.balldontlie.client import BalldontlieClient
from app.integrations.balldontlie.crosswalk import crosswalk_players, crosswalk_teams
from app.integrations.balldontlie.provider import BalldontlieProvider, tier_allows
from tests.conftest import make_player, make_team

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "balldontlie"
KEY = "bdl-test-key-DO-NOT-LOG-0123456789"


def fixture(name: str) -> dict:
    body = json.loads((FIXTURES / name).read_text())
    body.pop("_fixture")
    return body


class Upstream:
    """A scripted upstream: path -> list of (status, body, headers) consumed in order."""

    def __init__(self, routes: dict[str, list]):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get(request.url.path)
        if not queue:
            return httpx.Response(404, json={"error": "no such route in the fixture"})
        status, body, headers = (queue.pop(0) if len(queue) > 1 else queue[0])
        if isinstance(body, str):
            return httpx.Response(status, text=body, headers=headers)
        return httpx.Response(status, json=body, headers=headers)


def make_provider(
    routes: dict[str, list], *, key: str = KEY, tier: str = "goat", retries: int = 2
) -> tuple[BalldontlieProvider, Upstream, list[float]]:
    upstream = Upstream(routes)
    sleeps: list[float] = []
    clock = {"t": 0.0}

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += seconds

    client = BalldontlieClient(
        key,
        tier=tier,
        max_retries=retries,
        transport=httpx.MockTransport(upstream),
        sleep=sleep,
        clock=lambda: clock["t"],
    )
    return BalldontlieProvider(client), upstream, sleeps


def ok(name: str) -> tuple[int, dict, dict]:
    return 200, fixture(name), {}


# ------------------------------------------------------------------------ guard rails


def test_every_fixture_declares_itself_synthetic():
    files = sorted(FIXTURES.glob("*.json"))
    assert files, "no fixtures found"
    for path in files:
        note = json.loads(path.read_text()).get("_fixture", "")
        assert note.startswith("SYNTHETIC FIXTURE"), f"{path.name} must say it is synthetic"


def test_no_key_means_no_request_and_a_clear_status():
    provider, upstream, _ = make_provider({}, key="")
    result = provider.teams()
    assert result.status == ProviderStatus.NOT_CONFIGURED
    assert not result.has_answer
    assert upstream.requests == []


def test_the_key_is_sent_as_the_authorization_header_only():
    provider, upstream, _ = make_provider({"/nba/v1/teams": [ok("teams.json")]})
    provider.teams()
    request = upstream.requests[0]
    assert request.headers["Authorization"] == KEY
    assert KEY not in str(request.url)


def test_the_key_never_reaches_logs_errors_or_settings_repr(caplog):
    provider, _, _ = make_provider(
        {"/nba/v1/teams": [(500, f"upstream echoed {KEY} in its error page", {})]}, retries=1
    )
    with caplog.at_level(logging.DEBUG):
        result = provider.teams()
    assert result.status == ProviderStatus.UNAVAILABLE
    assert KEY not in (result.error or "")
    assert KEY not in caplog.text
    settings = Settings(balldontlie_api_key=KEY)
    assert KEY not in repr(settings) and KEY not in str(settings.model_dump())
    assert settings.balldontlie_configured


# ----------------------------------------------------------------------- normalisation


def test_teams_become_canonical_records():
    provider, _, _ = make_provider({"/nba/v1/teams": [ok("teams.json")]})
    result = provider.teams()
    assert result.status == ProviderStatus.OK
    assert [t.abbreviation for t in result.records] == ["ATL", "BOS", "NWP"]
    assert all(t.provider == "balldontlie" for t in result.records)


def test_players_paginate_by_cursor_and_reject_rows_without_an_id():
    provider, upstream, _ = make_provider(
        {"/nba/v1/players": [ok("players_page1.json"), ok("players_page2.json")]}
    )
    result = provider.players()
    assert [r.url.params.get("cursor") for r in upstream.requests] == [None, "104"]
    assert all(r.url.params["per_page"] == "100" for r in upstream.requests)
    assert result.meta.pages == 2 and result.meta.requests == 2
    assert [p.provider_id for p in result.records] == ["101", "102", "103", "104", "105", "106"]
    assert any("1 of 6 rows rejected" in w or "1 of 7 rows rejected" in w for w in result.warnings)


def test_blank_provider_fields_are_none_not_empty_or_zero():
    provider, _, _ = make_provider(
        {"/nba/v1/players": [ok("players_page1.json"), ok("players_page2.json")]}
    )
    blank = next(p for p in provider.players().records if p.provider_id == "104")
    assert blank.position is None and blank.height_inches is None and blank.weight_lbs is None
    assert blank.draft_year is None and blank.team_provider_id is None


def test_height_and_weight_parsing():
    from app.integrations.balldontlie.normalizers import parse_height_inches, parse_weight_lbs

    assert parse_height_inches("6-8") == 80
    assert parse_height_inches("7-0") == 84
    assert parse_height_inches("") is None and parse_height_inches("203cm") is None
    assert parse_weight_lbs("225") == 225
    assert parse_weight_lbs("") is None and parse_weight_lbs("0") is None


def test_games_always_send_an_explicit_season_type_and_reject_contradicting_rows():
    provider, upstream, _ = make_provider({"/nba/v1/games": [ok("games_regular.json")]})
    result = provider.games("2025-26", SeasonType.REGULAR)
    params = upstream.requests[0].url.params
    assert params["season_type"] == "regular"
    assert params.get_list("seasons[]") == ["2025"]
    assert [g.provider_id for g in result.records] == ["5001", "5002"]
    assert all(g.season.label == "2025-26" and g.season_type == SeasonType.REGULAR for g in result.records)
    assert "2 of 4 rows rejected" in result.warnings[0]


def test_standings_are_filed_under_the_requested_scope():
    provider, upstream, _ = make_provider({"/nba/v1/standings": [ok("standings.json")]})
    result = provider.standings(2025)
    assert upstream.requests[0].url.params["season"] == "2025"
    assert str(result.records[0].scope) == "2025-26/regular"
    assert (result.records[0].wins, result.records[0].losses) == (40, 42)


def test_season_averages_keep_missing_as_none_and_reject_a_wrong_season_type():
    provider, upstream, _ = make_provider(
        {"/nba/v1/season_averages/general": [ok("season_averages_general_base.json")]}
    )
    result = provider.player_season_stats("2025-26", SeasonType.REGULAR, player_ids=[101, 103])
    params = upstream.requests[0].url.params
    assert (params["type"], params["season_type"], params["season"]) == ("base", "regular", "2025")
    assert params.get_list("player_ids") == ["101", "103"]
    assert len(result.records) == 1  # the playoffs row contradicts the request
    row = result.records[0]
    assert row.values["fg3_pct"] is None, "an absent figure is None, never 0"
    assert "label" not in row.values
    assert row.games_played == 70
    assert row.aggregation == "unspecified"


def test_contract_figures_stay_separate_and_nulls_stay_null():
    provider, upstream, _ = make_provider({"/nba/v1/contracts/teams": [ok("contracts_team.json")]})
    result = provider.team_contracts(1, "2025-26")
    assert upstream.requests[0].url.params["season"] == "2025"
    funded, unknown = result.records
    assert (funded.cap_hit, funded.base_salary, funded.total_cash) == (30_000_000, 28_000_000, 29_000_000)
    assert (unknown.cap_hit, unknown.base_salary, unknown.total_cash) == (None, None, None)


def test_contract_aggregate_does_not_invent_options_or_clauses():
    provider, _, _ = make_provider(
        {"/nba/v1/contracts/players/aggregate": [ok("contract_aggregate.json")]}
    )
    (agg,) = provider.contract_aggregate(101).records
    assert agg.total_guaranteed == 120_000_000 and agg.signed_using == "Bird Rights"
    assert not hasattr(agg, "no_trade_clause") and not hasattr(agg, "player_option")
    assert agg.notes == {"note": "fixture"}


# ------------------------------------------------------------------- failure semantics


def test_a_genuinely_empty_answer_is_not_a_failure():
    provider, _, _ = make_provider({"/nba/v1/player_injuries": [(200, {"data": [], "meta": {}}, {})]})
    result = provider.injuries()
    assert result.status == ProviderStatus.EMPTY
    assert result.has_answer and result.require_data() == ()


def test_a_404_is_an_answer_not_an_outage():
    provider, _, _ = make_provider({"/nba/v1/contracts/players/aggregate": [(404, {"error": "x"}, {})]})
    assert provider.contract_aggregate(1).status == ProviderStatus.EMPTY


def test_401_before_any_success_is_unauthorized():
    provider, _, _ = make_provider({"/nba/v1/standings": [(401, {"error": "nope"}, {})]})
    result = provider.standings(2025)
    assert result.status == ProviderStatus.UNAUTHORIZED
    assert not result.has_answer


def test_401_after_the_key_has_worked_is_reported_as_a_tier_restriction():
    provider, _, _ = make_provider(
        {
            "/nba/v1/teams": [ok("teams.json")],
            "/nba/v1/contracts/teams": [(401, {"error": "tier"}, {})],
        },
        tier="all_star",
    )
    assert provider.teams().status == ProviderStatus.OK
    result = provider.team_contracts(1)
    assert result.status == ProviderStatus.TIER_RESTRICTED
    assert "inferred" in (result.error or "")


def test_429_honours_retry_after_then_succeeds():
    provider, upstream, sleeps = make_provider(
        {"/nba/v1/teams": [(429, {"error": "slow"}, {"Retry-After": "7"}), ok("teams.json")]}
    )
    assert provider.teams().status == ProviderStatus.OK
    assert len(upstream.requests) == 2
    assert 7.0 in sleeps


def test_persistent_429_is_rate_limited_after_bounded_retries():
    provider, upstream, _ = make_provider(
        {"/nba/v1/teams": [(429, {"error": "slow"}, {})]}, retries=2
    )
    result = provider.teams()
    assert result.status == ProviderStatus.RATE_LIMITED
    assert len(upstream.requests) == 3  # 1 + 2 retries, then it stops


def test_5xx_retries_then_reports_unavailable_never_empty():
    provider, upstream, _ = make_provider({"/nba/v1/teams": [(503, "down", {})]}, retries=1)
    result = provider.teams()
    assert result.status == ProviderStatus.UNAVAILABLE
    assert result.records == () and not result.has_answer
    assert len(upstream.requests) == 2
    with pytest.raises(Exception, match="unavailable"):
        result.require_data()


def test_a_timeout_is_unavailable():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow upstream", request=request)

    client = BalldontlieClient(KEY, max_retries=0, transport=httpx.MockTransport(boom), sleep=lambda s: None)
    result = BalldontlieProvider(client).teams()
    assert result.status == ProviderStatus.UNAVAILABLE


def test_an_html_error_page_is_a_schema_error():
    provider, _, _ = make_provider({"/nba/v1/teams": [(200, "<html>Access Denied</html>", {})]})
    result = provider.teams()
    assert result.status == ProviderStatus.SCHEMA_ERROR
    assert "not JSON" in (result.error or "")


def test_a_400_means_our_request_was_wrong_and_is_not_retried():
    provider, upstream, _ = make_provider({"/nba/v1/games": [(400, {"error": "bad"}, {})]})
    assert provider.games(2025).status == ProviderStatus.SCHEMA_ERROR
    assert len(upstream.requests) == 1


def test_when_every_row_is_rejected_the_call_fails_instead_of_returning_nothing():
    bad = {"data": [{"first_name": "no", "last_name": "id"}], "meta": {}}
    provider, _, _ = make_provider({"/nba/v1/players": [(200, bad, {})]})
    result = provider.players()
    assert result.status == ProviderStatus.SCHEMA_ERROR
    assert "every one of 1 rows was rejected" in (result.error or "")


# --------------------------------------------------------- pacing and pagination guards


def test_trial_pacing_spaces_requests_at_least_fifteen_seconds_apart():
    """5 req/min published limit, held 20 % under: one request every 15 s."""
    provider, _, sleeps = make_provider(
        {"/nba/v1/teams": [ok("teams.json")]}, tier="trial"
    )
    for _ in range(3):
        provider.teams()
    assert sleeps == [15.0, 15.0]


def test_goat_pacing_is_much_faster_than_trial_pacing():
    provider, _, sleeps = make_provider({"/nba/v1/teams": [ok("teams.json")]}, tier="goat")
    for _ in range(3):
        provider.teams()
    assert sleeps and max(sleeps) <= 0.13  # 600/min * 0.8 -> 0.125 s


def test_a_repeating_cursor_cannot_loop_forever():
    page = {"data": [{"id": 1, "first_name": "A", "last_name": "B"}], "meta": {"next_cursor": 5}}
    provider, upstream, _ = make_provider({"/nba/v1/players": [(200, page, {})]})
    result = provider.players()
    assert len(upstream.requests) == 2
    assert any("repeated" in w for w in result.warnings)


def test_hitting_the_page_cap_is_disclosed_as_truncation():
    def pages():
        n = {"c": 0}

        def respond(request: httpx.Request) -> httpx.Response:
            n["c"] += 1
            return httpx.Response(
                200,
                json={
                    "data": [{"id": n["c"], "first_name": "A", "last_name": f"P{n['c']}"}],
                    "meta": {"next_cursor": n["c"] + 1},
                },
            )

        return respond

    client = BalldontlieClient(KEY, tier="goat", transport=httpx.MockTransport(pages()), sleep=lambda s: None)
    result = BalldontlieProvider(client).players(max_pages=3)
    assert len(result.records) == 3
    assert any("TRUNCATED" in w for w in result.warnings)


def test_published_tier_table():
    assert tier_allows("free", "teams") and not tier_allows("free", "stats")
    assert tier_allows("all_star", "player_injuries") and not tier_allows("all_star", "contracts")
    assert tier_allows("trial", "contracts") and tier_allows("goat", "plays")


# -------------------------------------------------------------------------- crosswalk


@pytest.fixture()
def league(db: Session):
    atl = make_team(db, 1610612737, "ATL", "Atlanta Hawks")
    bos = make_team(db, 1610612738, "BOS", "Boston Celtics")
    alex = make_player(db, 1001, "Alex Fixture", atl)
    old = make_player(db, 1002, "Sam Williams", None)
    new = make_player(db, 1003, "Sam Williams", bos)
    old.draft_year, new.draft_year = 2015, 2022
    db.commit()
    return {"atl": atl, "bos": bos, "alex": alex, "old": old, "new": new}


def _players():
    provider, _, _ = make_provider(
        {"/nba/v1/players": [ok("players_page1.json"), ok("players_page2.json")]}
    )
    return list(provider.players().records)


def test_teams_link_by_abbreviation_and_name_and_an_unknown_team_is_reported(db, league):
    provider, _, _ = make_provider({"/nba/v1/teams": [ok("teams.json")]})
    report = crosswalk_teams(db, list(provider.teams().records))
    assert (report.created, len(report.unresolved), len(report.ambiguous)) == (2, 1, 0)
    assert report.unresolved[0]["abbreviation"] == "NWP"


def test_a_team_code_that_matches_but_a_name_that_does_not_is_not_linked(db, league):
    provider, _, _ = make_provider({"/nba/v1/teams": [ok("teams.json")]})
    records = [
        r.__class__(**{**r.__dict__, "full_name": "Atlanta Imposters"}) if r.abbreviation == "ATL" else r
        for r in provider.teams().records
    ]
    report = crosswalk_teams(db, records)
    assert report.ambiguous and report.ambiguous[0]["pivot_name"] == "Atlanta Hawks"
    assert db.query(ProviderEntityId).filter_by(entity_type="team").count() == 1


def test_namesakes_are_separated_by_draft_year_not_by_a_coin_flip(db, league):
    report = crosswalk_players(db, _players(), season="2025-26")
    links = {
        r.provider_id: r.internal_id
        for r in db.query(ProviderEntityId).filter_by(entity_type="player")
    }
    assert links["102"] == league["old"].id, "the 2015 draftee"
    assert links["103"] == league["new"].id, "the 2022 draftee"
    assert links["101"] == league["alex"].id
    # Never-matched and nameless-in-Pivot players are reported, not invented.
    assert {u["provider_id"] for u in report.unresolved} >= {"104", "105", "106"}


def test_crosswalk_is_idempotent(db, league):
    first = crosswalk_players(db, _players(), season="2025-26")
    second = crosswalk_players(db, _players(), season="2025-26")
    db.commit()
    assert second.created == 0 and second.unchanged == first.created
    assert db.query(ProviderEntityId).filter_by(entity_type="player").count() == first.created


def test_unseparable_namesakes_stay_ambiguous_and_unlinked(db, league):
    league["old"].draft_year = None
    league["new"].draft_year = None
    db.commit()
    report = crosswalk_players(db, _players(), season="2025-26")
    # 103 has a current team, so the rostered namesake wins; 102 (no team) cannot be separated.
    ambiguous_ids = {a["provider_id"] for a in report.ambiguous}
    assert "102" in ambiguous_ids
    assert db.query(ProviderEntityId).filter_by(provider_id="102").count() == 0


def test_a_provider_id_pointing_at_two_people_is_a_filed_conflict_not_an_overwrite(db, league):
    crosswalk_players(db, _players(), season="2025-26")
    db.commit()
    link = db.query(ProviderEntityId).filter_by(provider_id="101").one()
    original = link.internal_id
    impostor = Player(nba_player_id=9999, full_name="Alex Fixture", is_active=True)
    db.add(impostor)
    db.flush()
    from app.canonical.identity import ProviderIdMap

    outcome = ProviderIdMap(db).link(
        "player", "balldontlie", "101", impostor.id, method="exact_name"
    )
    db.commit()
    assert outcome.status == "conflict"
    assert db.query(ProviderEntityId).filter_by(provider_id="101").one().internal_id == original
    issue = db.query(DataQualityIssue).filter_by(check_name="provider_id_conflict").one()
    assert issue.severity == "error"
