"""R8.3 adapter behaviour that the first live run (2026-10-18) made necessary.

Each test names the live observation it encodes. Fixture-tested: the shapes come from responses
seen on that date; these tests do not touch the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from app.canonical.rosters import resolve_roster_season
from app.canonical.seasons import SeasonType
from app.db.models import Player, ProviderEntityId, RosterEntry
from app.integrations.balldontlie.client import BalldontlieClient
from app.integrations.balldontlie.crosswalk import (
    crosswalk_players,
    crosswalk_teams,
    is_active_franchise,
)
from app.integrations.balldontlie.evaluation import cache_key, reindex_cache
from app.integrations.balldontlie.normalizers import player_record, team_record
from app.integrations.balldontlie.provider import (
    PLAYER_STAT_TYPES,
    TEAM_STAT_TYPES,
    BalldontlieProvider,
    InvalidStatPairingError,
    check_stat_pairing,
)
from tests.conftest import make_player, make_team

KEY = "not-a-real-key-ddddddd"


def client_for(handler, **kw) -> BalldontlieClient:
    return BalldontlieClient(
        KEY, tier="goat", max_retries=0, transport=httpx.MockTransport(handler),
        sleep=lambda s: None, **kw,
    )


# ---------------------------------------------------------------- category / type pairing


def test_an_unpublished_category_type_pair_is_refused_before_any_request():
    # Live: `defense_base` -> HTTP 400 "type 'defense_base' is not valid for category 'defense'".
    calls: list[httpx.Request] = []
    provider = BalldontlieProvider(client_for(lambda r: calls.append(r) or httpx.Response(200, json={"data": []})))
    with pytest.raises(InvalidStatPairingError):
        provider.player_season_stats(2025, SeasonType.REGULAR, category="defense", stat_type="base")
    with pytest.raises(InvalidStatPairingError):
        provider.team_season_stats(2025, SeasonType.REGULAR, category="shooting", stat_type="by_zone")
    assert calls == []


def test_published_pairs_pass_and_hustle_takes_no_type():
    check_stat_pairing(PLAYER_STAT_TYPES, "defense", "overall")
    check_stat_pairing(PLAYER_STAT_TYPES, "hustle", None)
    check_stat_pairing(TEAM_STAT_TYPES, "shooting", "by_zone_base")
    with pytest.raises(InvalidStatPairingError):
        check_stat_pairing(PLAYER_STAT_TYPES, "hustle", "base")


# ------------------------------------------------------------------------- pagination


def _paged(handler_pages: list[dict]):
    pages = list(handler_pages)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages.pop(0))

    return handler


def test_a_deliberate_first_page_sample_is_not_reported_as_truncated():
    pages = [{"data": [{"id": 1}], "meta": {"next_cursor": 2}}, {"data": [{"id": 2}], "meta": {}}]
    client = client_for(_paged(pages))
    sampled = client.paginate("/x", max_pages=1, sample=True)
    assert len(sampled.records) == 1 and sampled.warnings == [] and client.truncations == []


def test_an_unintended_cap_is_still_a_loud_truncation():
    pages = [{"data": [{"id": 1}], "meta": {"next_cursor": 2}}, {"data": [{"id": 2}], "meta": {}}]
    client = client_for(_paged(pages))
    capped = client.paginate("/x", max_pages=1)
    assert any("TRUNCATED" in w for w in capped.warnings) and client.truncations


# ------------------------------------------------------ ledger, limit header, redaction


def test_the_request_ledger_records_names_and_status_never_the_key_or_values(tmp_path: Path):
    ledger = tmp_path / "ledger.jsonl"
    client = client_for(
        lambda r: httpx.Response(200, json={"data": []}, headers={"x-ratelimit-limit": "600"}),
        ledger_path=ledger,
    )
    client.get("/season_averages/general", {"season": 2025, "player_ids[]": [246]})
    text = ledger.read_text()
    entry = json.loads(text.splitlines()[0])
    assert entry["status"] == 200 and entry["param_names"] == ["player_ids[]", "season"]
    assert KEY not in text and "246" not in text
    assert client.advertised_limit_per_minute == 600


# ------------------------------------------------------------------------ cache keys


def test_cache_key_ignores_parameters_that_are_never_sent(tmp_path: Path):
    assert cache_key("/x", {"a": 1, "b": None}) == cache_key("/x", {"a": 1})
    assert cache_key("/x", {"a": 1}) != cache_key("/x", {"a": 2})
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"path": "/x", "params": {"a": 1, "b": None}, "payload": {}}))
    assert reindex_cache(tmp_path) == 1
    assert (tmp_path / f"{cache_key('/x', {'a': 1})}.json").exists()
    assert reindex_cache(tmp_path) == 0, "idempotent"


# -------------------------------------------------------------------- team crosswalk


def _raw_team(i, city, nick, abbr, conf="West", div="Pacific"):
    return {"id": i, "conference": conf, "division": div, "city": city, "name": nick,
            "full_name": f"{city} {nick}", "abbreviation": abbr}


def test_historical_franchises_are_skipped_and_counted_not_failed(db: Session):
    make_team(db, 1, "ATL", "Atlanta Hawks")
    live = team_record(_raw_team(1, "Atlanta", "Hawks", "ATL", "East", "Southeast"))
    defunct = team_record(_raw_team(37, "", "Chicago Stags", "CHS", "    ", ""))
    assert is_active_franchise(live) and not is_active_franchise(defunct)
    report = crosswalk_teams(db, [live, defunct])
    assert (report.total, report.linked, report.skipped_historical) == (1, 1, 1)
    assert report.unresolved == [] and report.link_rate == 1.0


def test_la_clippers_and_los_angeles_clippers_are_one_franchise(db: Session):
    # Live: BALLDONTLIE says "LA Clippers", NBA.com/Pivot says "Los Angeles Clippers".
    make_team(db, 13, "LAC", "Los Angeles Clippers")
    report = crosswalk_teams(db, [team_record(_raw_team(13, "LA", "Clippers", "LAC"))])
    assert report.linked == 1 and report.ambiguous == []


def test_same_abbreviation_with_a_different_name_is_still_not_linked(db: Session):
    make_team(db, 5, "PHX", "Phoenix Suns")
    report = crosswalk_teams(db, [team_record(_raw_team(5, "Pheonix", "Impostors", "PHX"))])
    assert report.linked == 0 and len(report.ambiguous) == 1


# ------------------------------------------------------------------ player fallbacks


def _raw_player(i, first, last, *, h="6-8", dy=2024, dn=2, team=None):
    row = {"id": i, "first_name": first, "last_name": last, "position": "C", "height": h,
           "weight": "220", "jersey_number": "1", "college": "X", "country": "FRA",
           "draft_year": dy, "draft_round": 1, "draft_number": dn}
    if team:
        row["team"] = team
    return row


def test_a_nickname_is_linked_by_draft_slot_surname_and_height(db: Session):
    # Live: BALLDONTLIE "Nicolas Claxton" is Pivot's "Nic Claxton".
    p = make_player(db, 1629651, "Nic Claxton")
    p.draft_year, p.draft_number, p.height_inches = 2019, 31, 83
    db.commit()
    report = crosswalk_players(
        db, [player_record(_raw_player(501, "Nicolas", "Claxton", h="6-11", dy=2019, dn=31))],
        season=resolve_roster_season(db),
    )
    link = db.query(ProviderEntityId).one()
    assert report.linked == 1 and link.match_method == "draft_slot+surname" and link.confidence == "medium"


def test_a_draft_slot_shared_with_a_different_surname_is_not_linked(db: Session):
    p = make_player(db, 1, "Someone Else")
    p.draft_year, p.draft_number, p.height_inches = 2019, 31, 83
    db.commit()
    report = crosswalk_players(
        db, [player_record(_raw_player(501, "Nicolas", "Claxton", h="6-11", dy=2019, dn=31))],
        season=resolve_roster_season(db),
    )
    assert report.linked == 0 and len(report.unresolved) == 1


def test_a_recent_draftee_without_a_pivot_draft_slot_links_on_surname_team_and_height(db: Session):
    # Live: "Alexandre Sarr" is Pivot's "Alex Sarr", whose draft fields are blank.
    season = resolve_roster_season(db)
    team = make_team(db, 1, "SAS", "San Antonio Spurs")
    make_player(db, 1642259, "Alex Sarr", team, season=season).height_inches = 84
    db.commit()
    bdl_team = _raw_team(27, "San Antonio", "Spurs", "SAS")
    crosswalk_teams(db, [team_record(bdl_team)])
    rec = player_record(_raw_player(777, "Alexandre", "Sarr", h="7-0", dy=2024, dn=2, team=bdl_team))
    report = crosswalk_players(db, [rec], season=season)
    link = db.query(ProviderEntityId).filter_by(entity_type="player").one()
    assert report.linked == 1 and link.match_method == "surname+roster_team+height"


def test_two_players_with_one_surname_on_a_roster_are_not_guessed(db: Session):
    season = resolve_roster_season(db)
    team = make_team(db, 1, "SAS", "San Antonio Spurs")
    make_player(db, 1, "Al Sarr", team, season=season).height_inches = 84
    make_player(db, 2, "Bo Sarr", team, season=season).height_inches = 84
    db.commit()
    bdl_team = _raw_team(27, "San Antonio", "Spurs", "SAS")
    crosswalk_teams(db, [team_record(bdl_team)])
    rec = player_record(_raw_player(777, "Alexandre", "Sarr", h="7-0", dy=None, dn=None, team=bdl_team))
    report = crosswalk_players(db, [rec], season=season)
    assert report.linked == 0 and len(report.unresolved) == 1
    assert db.query(RosterEntry).count() == 2 and db.query(Player).count() == 2
