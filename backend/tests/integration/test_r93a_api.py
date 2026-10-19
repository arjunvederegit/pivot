"""R9.3A — the API surfaces read the one financial service and stay additive.

Cap Center (`/teams/{id}/cap-outlook`), the roster payload, the player contract, the shot-zone profile and the trade
routes. Each assertion is about a way a tool could have shown an unsupported financial or statistical conclusion.
"""

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from app.canonical.records import SINGLE_SEASON_CONTRACT_SOURCE
from app.db.base import get_db
from app.db.models import (
    Contract,
    ContractYear,
    DataSyncRun,
    PlayerSeasonStats,
    ProviderStatSnapshot,
    Standing,
    TeamNeed,
    TeamSeasonStats,
)
from tests.conftest import make_player, make_team

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


@pytest.fixture()
def client(db, cap_params):
    from app.main import app

    cap_params.extras = {"two_way_salary": 678_882}
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _contract(db, player, seasons):
    contract = Contract(
        player_id=player.id,
        contract_type=None,
        source_provider="balldontlie",
        source_name=SINGLE_SEASON_CONTRACT_SOURCE,
        source_date=date(2026, 10, 19),
    )
    db.add(contract)
    db.flush()
    for season, salary in seasons.items():
        db.add(
            ContractYear(
                contract_id=contract.id, season=season, salary=salary, base_salary=salary,
                total_cash=salary, salary_basis="cap_hit", source_provider="balldontlie", source_retrieved_at=NOW,
            )
        )
    db.commit()


def _horizon(db, through="2028-29"):
    for season in ("2026-27", "2027-28", "2028-29", "2029-30", "2030-31"):
        db.add(
            DataSyncRun(
                job_name="sync_contracts", status="succeeded", finished_at=NOW,
                detail={"season": season, "outcome": "complete", "teams_failed": [], "unpriced_players": []},
            )
        )
        if season == through:
            break
    db.commit()


@pytest.fixture()
def world(db):
    a, b = make_team(db, 1, "AAA", "Alpha Test Club"), make_team(db, 2, "BBB", "Beta Test Club")
    players = {
        "star": make_player(db, 101, "Star Alpha", a, salary=None, contract_type=None),
        "mid": make_player(db, 102, "Mid Alpha", a, salary=None, contract_type=None),
        "ghost": make_player(db, 103, "Ghost Alpha", a, salary=None),  # no contract at all
        "b1": make_player(db, 201, "Beta One", b, salary=None, contract_type=None),
    }
    _contract(db, players["star"], {"2026-27": 40_000_000, "2027-28": 42_000_000, "2028-29": 44_000_000})
    _contract(db, players["mid"], {"2026-27": 10_000_000})
    _contract(db, players["b1"], {"2026-27": 38_000_000, "2027-28": 38_000_000})
    _horizon(db)
    for team in (a, b):
        db.add(Standing(team_id=team.id, season="2025-26", wins=41, losses=41, win_pct=0.5, conference="East", source_retrieved_at=NOW))
        db.add(TeamSeasonStats(team_id=team.id, season="2025-26", stat_type="advanced", stats={"OFF_RATING": 114.0, "DEF_RATING": 114.0, "NET_RATING": 0.0, "PACE": 99.0, "TS_PCT": 0.57}, source_retrieved_at=NOW))
        db.add(TeamSeasonStats(team_id=team.id, season="2025-26", stat_type="base", stats={"PTS": 114.0, "FG3A": 35.0, "STL": 7.5, "BLK": 5.0}, source_retrieved_at=NOW))
        db.add(TeamNeed(team_id=team.id, season="2025-26", need_key="three_point_volume", severity=0.6, percentile=20.0, explanation="test fixture need"))
    for player in players.values():
        db.add(PlayerSeasonStats(player_id=player.id, season="2025-26", stat_type="base", games_played=70, minutes=30.0, stats={"PTS": 18.0, "REB": 5.0, "AST": 4.0}, source_retrieved_at=NOW))
    db.commit()
    return {"a": a, "b": b, **players}


class TestCapOutlook:
    def test_financials_block_is_additive_and_a_partial_team_is_a_subtotal(self, client, world) -> None:
        body = client.get(f"/api/v1/teams/{world['a'].id}/cap-outlook").json()
        assert body["available"] is True
        # every pre-R9.3A key is still there
        assert {"seasons", "players", "cap_parameters", "note", "complete", "players_with_contracts"} <= set(body)
        fin = body["financials"]
        assert fin["subtotal_label"] == "Known salary subtotal"
        assert fin["known_subtotal"] == 50_000_000 and fin["payroll"] is None
        assert fin["coverage"]["unpriced"] == 1
        assert [u["name"] for u in fin["unpriced_players"]] == ["Ghost Alpha"]
        assert fin["unpriced_players"][0]["status"] == "no_contract"
        assert any(w["conclusion"] == "Apron position" for w in fin["withheld"])
        assert fin["thresholds"]["season"] == "2026-27"

    def test_contract_length_comes_from_the_horizon_not_from_the_row_count(self, client, world) -> None:
        body = client.get(f"/api/v1/teams/{world['a'].id}/cap-outlook").json()
        rows = {p["name"]: p for p in body["players"]}
        star, mid = rows["Star Alpha"], rows["Mid Alpha"]
        assert star["contract_end"]["status"] == "through_horizon" and star["contract_end"]["years_remaining"] == 3
        assert star["expiring"] is False or star["expiring"] is None
        assert mid["contract_end"]["status"] == "ends" and mid["contract_end"]["final_season"] == "2026-27"
        assert mid["expiring"] is True and mid["contract_length_known"] is True

    def test_future_commitments_are_not_a_payroll(self, client, world) -> None:
        fin = client.get(f"/api/v1/teams/{world['a'].id}/cap-outlook").json()["financials"]
        by = {c["season"]: c for c in fin["commitments"]}
        assert by["2027-28"]["known_total"] == 42_000_000
        assert "never a payroll" in fin["commitments_note"]
        assert any(w["conclusion"] == "Seasons after 2026-27" for w in fin["withheld"])


class TestRosterAndPlayerContract:
    def test_roster_salary_is_the_cap_hit_and_years_flag_a_lower_bound(self, client, world) -> None:
        body = client.get(f"/api/v1/teams/{world['a'].id}/roster").json()
        rows = {p["name"]: p for p in body["roster"]}
        assert rows["Star Alpha"]["salary"] == 40_000_000
        assert rows["Star Alpha"]["salary_season"] == "2026-27"
        assert rows["Star Alpha"]["contract_years_remaining"] == 3
        assert rows["Star Alpha"]["contract_years_is_lower_bound"] is True
        assert rows["Star Alpha"]["contract_final_season"] == "2028-29"
        assert rows["Mid Alpha"]["contract_years_remaining"] == 1
        assert rows["Mid Alpha"]["contract_years_is_lower_bound"] is False
        assert rows["Ghost Alpha"]["salary"] is None and rows["Ghost Alpha"]["salary_season"] is None
        assert rows["Ghost Alpha"]["contract_years_remaining"] is None

    def test_player_contract_carries_the_same_record_and_the_team_context(self, client, world) -> None:
        body = client.get(f"/api/v1/players/{world['star'].id}/contract").json()
        assert body["available"] is True and body["financial_season"] == "2026-27"
        assert body["current_team"]["abbreviation"] == "AAA"
        assert body["financials"]["cap_hit"] == 40_000_000
        assert body["financials"]["not_two_way"] is True
        assert body["financials"]["contract_end"]["status"] == "through_horizon"
        # the pre-existing keys survive
        assert body["years"][0]["season"] == "2026-27" and body["source_name"] == SINGLE_SEASON_CONTRACT_SOURCE

    def test_a_player_without_a_contract_is_unavailable_with_a_reason_never_zero(self, client, world) -> None:
        body = client.get(f"/api/v1/players/{world['ghost'].id}/contract").json()
        assert body["available"] is False
        assert body["financials"]["status"] == "no_contract" and body["financials"]["cap_hit"] is None

    def test_the_three_surfaces_agree_on_a_salary(self, client, world) -> None:
        roster = {p["player_id"]: p for p in client.get(f"/api/v1/teams/{world['a'].id}/roster").json()["roster"]}
        cap = {p["player_id"]: p for p in client.get(f"/api/v1/teams/{world['a'].id}/cap-outlook").json()["players"]}
        contract = client.get(f"/api/v1/players/{world['star'].id}/contract").json()
        sid = world["star"].id
        season = next(s for s in cap[sid]["seasons"] if s["season"] == "2026-27")
        assert roster[sid]["salary"] == season["salary"] == contract["financials"]["cap_hit"] == 40_000_000


class TestTradeRoutes:
    def _moves(self, world):
        return {
            "team_ids": [world["a"].id, world["b"].id],
            "player_moves": [
                {"player_id": world["star"].id, "from_team_id": world["a"].id, "to_team_id": world["b"].id},
                {"player_id": world["b1"].id, "from_team_id": world["b"].id, "to_team_id": world["a"].id},
            ],
            "pick_moves": [],
        }

    def test_validate_returns_the_engine_result_unchanged_plus_the_financial_analysis(self, client, world) -> None:
        body = client.post("/api/v1/trades/validate", json=self._moves(world)).json()
        assert {"overall_status", "teams", "rule_results", "league_year"} <= set(body)
        fa = body["financial_analysis"]
        assert fa["legality_assessment"]["verdict"] in {"indeterminate", "prohibited", "passes_checked_rules"}
        assert fa["legality_assessment"]["verdict"] != "legal"
        assert fa["teams"][world["a"].id]["net_salary_change"] == -2_000_000
        assert fa["teams"][world["a"].id]["subtotal_label"] == "Known salary subtotal"  # AAA has an unpriced player

    def test_evaluate_carries_the_same_analysis(self, client, world) -> None:
        body = client.post("/api/v1/trades/evaluate", json={**self._moves(world), "strategy": "custom"}).json()
        assert "legality" in body and "evaluations" in body and "financial_analysis" in body
        assert body["financial_analysis"]["financial_season"] == "2026-27"
        # contract years for the assets, from the same record Cap Center shows
        star = next(p for p in body["financial_analysis"]["players"] if p["player_id"] == world["star"].id)
        assert star["financials"]["contract_end"]["final_season"] == "2028-29"

    def test_a_player_with_no_salary_is_unknown_in_the_comparison(self, client, world) -> None:
        moves = self._moves(world)
        moves["player_moves"][0]["player_id"] = world["ghost"].id
        fa = client.post("/api/v1/trades/validate", json=moves).json()["financial_analysis"]
        block = fa["teams"][world["a"].id]
        assert block["outgoing"]["unknown_players"] == ["Ghost Alpha"]
        assert block["net_salary_change"] is None
        assert block["commitments_by_season"][0]["outgoing"]["complete"] is False


def _zones(db, player, *, fga_pg=18.0, base_fga=17.9, games=16):
    values = {
        "restricted_area_fga": 4.0, "restricted_area_fgm": 2.4, "restricted_area_fg_pct": 0.6,
        "in_the_paint_(non-ra)_fga": 3.0, "in_the_paint_(non-ra)_fgm": 1.3, "in_the_paint_(non-ra)_fg_pct": 0.433,
        "mid-range_fga": 2.0, "mid-range_fgm": 0.8, "mid-range_fg_pct": 0.4,
        "left_corner_3_fga": 0.5, "left_corner_3_fgm": 0.3, "left_corner_3_fg_pct": 0.6,
        "right_corner_3_fga": 0.3, "right_corner_3_fgm": 0.1, "right_corner_3_fg_pct": 0.333,
        "above_the_break_3_fga": fga_pg - 9.8, "above_the_break_3_fgm": 2.6, "above_the_break_3_fg_pct": 0.31,
        "backcourt_fga": 0.0, "backcourt_fgm": 0.0,
    }
    for group, vals, agg in (("shooting/by_zone", values, "unspecified"), ("general/base", {"fga": base_fga, "gp": games}, "per_game")):
        db.add(
            ProviderStatSnapshot(
                provider="balldontlie", entity_type="player", internal_id=player.id, season="2025-26",
                season_type="regular", stat_group=group, aggregation=agg, games_played=games if group == "general/base" else None,
                values=vals, retrieved_at=NOW,
            )
        )
    db.commit()


class TestShotZones:
    def test_reconciled_zones_are_served_with_units_season_and_a_league_reference(self, client, db, world) -> None:
        _zones(db, world["star"])
        body = client.get(f"/api/v1/players/{world['star'].id}/shot-zones").json()
        assert body["available"] is True and body["season"] == "2025-26" and body["season_type"] == "regular"
        assert body["unit"] == "attempts and makes per game" and body["reconciliation"]["reconciled"] is True
        zones = {z["key"]: z for z in body["zones"]}
        assert zones["restricted_area"]["attempts_per_game"] == 4.0
        assert zones["restricted_area"]["approx_season_attempts"] == 64
        assert zones["left_corner_3"]["thin_sample"] is True
        assert abs(sum(z["share_of_attempts"] for z in body["zones"]) - 1.0) < 1e-9
        assert zones["restricted_area"]["league_fg_pct"] == pytest.approx(2.4 / 4.0)

    def test_zones_that_do_not_reconcile_are_withheld_with_the_reason(self, client, db, world) -> None:
        _zones(db, world["star"], base_fga=9.0)
        body = client.get(f"/api/v1/players/{world['star'].id}/shot-zones").json()
        assert body["available"] is False and "does not reconcile" in body["reason"]
        assert "zones" not in body

    def test_a_player_with_no_snapshot_is_unavailable_not_empty(self, client, world) -> None:
        body = client.get(f"/api/v1/players/{world['mid'].id}/shot-zones").json()
        assert body["available"] is False and "No BALLDONTLIE shot-zone snapshot" in body["reason"]

    def test_an_unknown_player_is_a_404(self, client) -> None:
        assert client.get("/api/v1/players/nope/shot-zones").status_code == 404

    def test_no_provider_is_called_by_a_read(self, client, db, world, monkeypatch) -> None:
        _zones(db, world["star"])
        import app.integrations.balldontlie.client as bdl

        monkeypatch.setattr(bdl.BalldontlieClient, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
        assert client.get(f"/api/v1/players/{world['star'].id}/shot-zones").status_code == 200


class TestQueryBudgets:
    """A tool that fans out per player (the N+1 the resolver exists to prevent) is a performance regression."""

    def _count(self, db, fn) -> int:
        from sqlalchemy import event

        statements: list[str] = []

        def before(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
            statements.append(statement)

        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", before)
        try:
            fn()
        finally:
            event.remove(engine, "before_cursor_execute", before)
        return len(statements)

    def test_roster_and_cap_outlook_do_not_scale_with_roster_size(self, client, db, world) -> None:
        a = world["a"]
        small = self._count(db, lambda: client.get(f"/api/v1/teams/{a.id}/cap-outlook"))
        for i in range(12):
            p = make_player(db, 900 + i, f"Bench {i}", a, salary=None, contract_type=None)
            _contract(db, p, {"2026-27": 2_000_000 + i})
        large = self._count(db, lambda: client.get(f"/api/v1/teams/{a.id}/cap-outlook"))
        assert large <= small + 4, f"cap-outlook issued {large} statements for 16 players vs {small} for 4"
        roster_small = self._count(db, lambda: client.get(f"/api/v1/teams/{world['b'].id}/roster"))
        roster_large = self._count(db, lambda: client.get(f"/api/v1/teams/{a.id}/roster"))
        assert roster_large <= roster_small + 6

    def test_validate_issues_a_bounded_number_of_statements(self, client, db, world) -> None:
        body = {
            "team_ids": [world["a"].id, world["b"].id],
            "player_moves": [
                {"player_id": world["star"].id, "from_team_id": world["a"].id, "to_team_id": world["b"].id},
                {"player_id": world["b1"].id, "from_team_id": world["b"].id, "to_team_id": world["a"].id},
            ],
            "pick_moves": [],
        }
        assert self._count(db, lambda: client.post("/api/v1/trades/validate", json=body)) <= 80
