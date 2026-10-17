"""The league impact reference: the population a TEI percentile is computed against.

All records are synthetic test fixtures."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.db.base import get_db
from app.db.models import ModelVersion, PlayerImpactEstimate
from app.services.impact_reference import percentile_of, rank_of
from tests.conftest import make_player, make_team


@pytest.fixture()
def client(db, cap_params):
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _model(db, *, active: bool, version: str) -> ModelVersion:
    model = ModelVersion(
        model_name="player_impact",
        version=version,
        algorithm="weighted z-score index (test fixture)",
        training_period="2023-24..2025-26",
        trained_at=datetime(2026, 8, 25, tzinfo=UTC),
        is_active=active,
    )
    db.add(model)
    db.flush()
    return model


def _estimate(db, player, model, tei, season="2025-26"):
    db.add(
        PlayerImpactEstimate(
            player_id=player.id,
            season=season,
            model_version_id=model.id,
            tei=tei,
            tei_low=tei - 1.0,
            tei_high=tei + 1.0,
        )
    )


@pytest.fixture()
def league(db):
    team = make_team(db, 1, "AAA")
    active = _model(db, active=True, version="active-v")
    stale = _model(db, active=False, version="stale-v")
    # Five rostered, scored players — two share +1.004 / +1.001, which serve as +1.00.
    teis = [2.5, 1.004, 1.001, -0.2, -1.75]
    players = [make_player(db, 100 + i, f"Fixture Scored {i}", team) for i in range(len(teis))]
    for player, tei in zip(players, teis, strict=True):
        _estimate(db, player, active, tei)
        _estimate(db, player, stale, tei + 9.0)  # a superseded version must never leak in
    # Rostered but unscored (below the minutes gate): counted, never placed.
    unscored = make_player(db, 200, "Fixture Unscored", team)
    # Scored but not on a roster (active free agent): part of the population.
    free_agent = make_player(db, 300, "Fixture Free Agent")
    _estimate(db, free_agent, active, 0.5)
    # An estimate for another season under the active model is not this season's league.
    _estimate(db, free_agent, active, 3.0, season="2024-25")
    db.commit()
    return {"team": team, "players": players, "unscored": unscored, "free_agent": free_agent}


def test_distribution_is_the_whole_scored_league_at_served_precision(client, league):
    body = client.get("/api/v1/players/impact-distribution").json()
    assert body["available"] is True
    assert body["season"] == "2025-26"
    assert body["values"] == [-1.75, -0.2, 0.5, 1.0, 1.0, 2.5]
    assert body["precision"] == 2
    pop = body["population"]
    assert pop["size"] == 6
    assert pop["rostered_scored"] == 5
    assert pop["rostered_unscored"] == 1
    assert pop["unrostered_scored"] == 1
    assert pop["min_window_minutes"] == 200
    assert pop["distinct_values"] == 5  # +1.004 and +1.001 both serve as +1.00
    assert body["model"]["version"] == "active-v"
    assert "Not points per 100" in body["scale"]
    assert body["percentile_method"].startswith("mid-rank")
    assert body["rank_method"].startswith("competition")


def test_route_is_not_captured_by_player_detail(client, league):
    # `/players/{player_id}` is declared after this route; a 404 here would mean it won.
    assert client.get("/api/v1/players/impact-distribution").status_code == 200


def test_no_active_model_is_unavailable_not_empty(client, db):
    team = make_team(db, 1, "AAA")
    make_player(db, 100, "Fixture Only", team)
    body = client.get("/api/v1/players/impact-distribution").json()
    assert body["available"] is False
    assert "make train" in body["reason"]
    assert "values" not in body


def test_roster_rows_carry_the_band_beside_the_point(client, league):
    roster = client.get(f"/api/v1/teams/{league['team'].id}/roster").json()["roster"]
    by_name = {row["name"]: row for row in roster}
    top = by_name["Fixture Scored 0"]
    assert (top["tei"], top["tei_low"], top["tei_high"]) == (2.5, 1.5, 3.5)
    unscored = by_name["Fixture Unscored"]
    assert unscored["tei"] is None and unscored["tei_low"] is None and unscored["tei_high"] is None


@pytest.mark.parametrize(
    ("value", "expected_pct", "expected_rank"),
    [
        (2.5, 100 * (5 + 0.5) / 6, 1),
        (1.0, 100 * (3 + 1.0) / 6, 2),  # two tied players share percentile and rank
        (-0.2, 100 * (1 + 0.5) / 6, 5),
        (-1.75, 100 * 0.5 / 6, 6),
    ],
)
def test_percentile_is_mid_rank_and_rank_is_competition(value, expected_pct, expected_rank):
    values = [-1.75, -0.2, 0.5, 1.0, 1.0, 2.5]
    assert percentile_of(value, values) == pytest.approx(expected_pct)
    assert rank_of(value, values) == expected_rank


def test_percentile_never_reaches_the_ends():
    values = [0.1 * i for i in range(512)]
    assert 0 < percentile_of(values[0], values) < 1
    assert 99 < percentile_of(values[-1], values) < 100


def test_percentile_refuses_an_empty_population():
    with pytest.raises(ValueError):
        percentile_of(1.0, [])
