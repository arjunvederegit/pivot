"""R9.2 — the preview route: off by default, a thin wrapper over the shared service, nothing else changes."""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.base import get_db
from app.modeling.registry import train_candidate
from app.player_intelligence.service import PlayerIntelligenceService
from tests.unit.pi_support import populate_shooting_db

CUTOFF = "2023-24"


@pytest.fixture()
def league(db):
    info = populate_shooting_db(db, n_players=110, n_seasons=9, seed=6)
    train_candidate(db, "state_space", "fg3_pct", CUTOFF)
    frame = info["frame"]
    top = frame[frame["season"] == CUTOFF].sort_values("total_minutes", ascending=False).iloc[0]["player_id"]
    info["player"] = info["players"][top]
    return info


def make_client(db, monkeypatch, enabled: bool):
    monkeypatch.setenv("PLAYER_INTELLIGENCE_PREVIEW", "true" if enabled else "false")
    get_settings.cache_clear()
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    yield
    get_settings.cache_clear()
    from app.main import app

    app.dependency_overrides.clear()


def test_the_preview_is_off_by_default_so_nothing_existing_changes(db, league, monkeypatch):
    client = make_client(db, monkeypatch, enabled=False)
    pid = league["player"].id
    assert client.get(f"/api/v1/players/{pid}/intelligence").status_code == 404
    assert client.get("/api/v1/player-intelligence/catalog").status_code == 404
    # (Absence from the OpenAPI schema is decided when the app is imported; it is asserted in a fresh process in
    # tests/unit/test_pi_review_fixes.py, which does not depend on test order.)


def test_with_the_flag_on_measured_records_are_served_by_default(db, league, monkeypatch):
    client = make_client(db, monkeypatch, enabled=True)
    body = client.get(f"/api/v1/players/{league['player'].id}/intelligence", params={"as_of": CUTOFF, "quantities": "fg3_pct,ts_pct"})
    assert body.status_code == 200
    j = body.json()
    assert j["as_of"] == CUTOFF and {r["quantity"] for r in j["records"]} == {"fg3_pct", "ts_pct"}
    assert all(r["output_kind"] == "measured" and r["model"] is None for r in j["records"])
    assert j["context"]["player"]["id"] == league["player"].id


def test_model_outputs_need_an_explicit_request_and_are_labelled_experimental(db, league, monkeypatch):
    client = make_client(db, monkeypatch, enabled=True)
    pid = league["player"].id
    plain = client.get(f"/api/v1/players/{pid}/intelligence", params={"as_of": CUTOFF, "quantities": "fg3_pct", "kinds": "inferred,projected"}).json()
    assert plain["records"] == [] and plain["unavailable"]
    opted = client.get(f"/api/v1/players/{pid}/intelligence", params={"as_of": CUTOFF, "quantities": "fg3_pct", "kinds": "inferred,projected", "include_experimental": "true"}).json()
    # No validation registry entry exists for the synthetic league's stored model with a real registry, so the
    # registry-derived tier decides: whatever is returned must be labelled a candidate.
    for r in opted["records"]:
        assert r["model"]["status"] == "candidate"
    assert all("reason" in u for u in opted["unavailable"])


def test_the_api_returns_exactly_what_the_service_returns(db, league, monkeypatch):
    client = make_client(db, monkeypatch, enabled=True)
    pid = league["player"].id
    via_api = client.get(f"/api/v1/players/{pid}/intelligence", params={"as_of": CUTOFF, "quantities": "usg_pct,fg3_pct,reb_pct"}).json()
    direct = PlayerIntelligenceService(db).get(pid, ["usg_pct", "fg3_pct", "reb_pct"], as_of=CUTOFF).as_dict()
    import json

    assert json.dumps(via_api, sort_keys=True, default=str) == json.dumps(direct, sort_keys=True, default=str)


def test_bad_requests_are_422_and_unknown_players_404(db, league, monkeypatch):
    client = make_client(db, monkeypatch, enabled=True)
    pid = league["player"].id
    assert client.get(f"/api/v1/players/{pid}/intelligence", params={"quantities": "swag"}).status_code == 422
    assert client.get(f"/api/v1/players/{pid}/intelligence", params={"kinds": "guessed"}).status_code == 422
    assert client.get(f"/api/v1/players/{pid}/intelligence", params={"as_of": "yesterday"}).status_code == 422
    assert client.get("/api/v1/players/not-a-player/intelligence").status_code == 404


def test_the_catalog_is_served_and_names_what_it_declines(db, league, monkeypatch):
    client = make_client(db, monkeypatch, enabled=True)
    c = client.get("/api/v1/player-intelligence/catalog").json()
    assert "shooting" in c["dimensions"] and "defensive_contribution" in c["unavailable"]


def test_existing_player_routes_are_unaffected_by_the_new_router(db, league, monkeypatch):
    client = make_client(db, monkeypatch, enabled=True)
    pid = league["player"].id
    assert client.get(f"/api/v1/players/{pid}").status_code == 200
    assert client.get(f"/api/v1/players/{pid}/stats").status_code in (200, 404, 409)
