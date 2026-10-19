"""R9.2 — the TEI band is served with the fact that it is not an 80% interval.

R9.1 measured the ``tei_low`` / ``tei_high`` band's out-of-time coverage at 60.8% (development) and 57.2%
(holdout) against a nominal 80%. The smallest safe correction is to say so wherever the band is served,
leaving the TEI formula and every existing field exactly as they were."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.analytics.impact import TEI_BAND_CALIBRATION
from app.db.base import get_db
from app.services.impact_reference import impact_reference

R91_RESULTS = Path(__file__).resolve().parents[3] / "docs" / "r9-1" / "results"


@pytest.fixture()
def client(db, cap_params):
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_the_stated_coverage_is_what_the_r91_artifacts_measured():
    """The constants are not folklore: they must match the committed backtest results."""
    dev = json.loads((R91_RESULTS / "development-impact_proxy" / "metrics.json").read_text())["overall"]["incumbent_tei"]["native_interval"]
    hold = json.loads((R91_RESULTS / "holdout-impact_proxy" / "metrics.json").read_text())["overall"]["incumbent_tei"]["native_interval"]
    assert TEI_BAND_CALIBRATION["measured_coverage"]["development"] == pytest.approx(dev["coverage"], abs=0.0005)
    assert TEI_BAND_CALIBRATION["measured_coverage"]["holdout"] == pytest.approx(hold["coverage"], abs=0.0005)
    assert TEI_BAND_CALIBRATION["nominal_level"] == 0.8
    assert TEI_BAND_CALIBRATION["status"] == "uncalibrated"


def test_the_player_detail_serves_the_band_with_its_calibration_and_keeps_the_old_field(db, client):
    from app.ingestion.demo_seed import seed_demo

    seed_demo(db, seasons=("2025-26",))
    teams = client.get("/api/v1/teams").json()
    scored = None
    for team in teams[:6]:
        for entry in client.get(f"/api/v1/teams/{team['id']}/roster").json()["roster"]:
            detail = client.get(f"/api/v1/players/{entry['player_id']}").json()
            if detail["impact"].get("tei_range_10_90"):
                scored = detail
                break
        if scored:
            break
    assert scored is not None, "the demo league should score at least one player"
    impact = scored["impact"]
    assert len(impact["tei_range_10_90"]) == 2  # the existing key is unchanged
    cal = impact["tei_range_calibration"]
    assert cal["status"] == "uncalibrated" and cal["nominal_level"] == 0.8
    assert cal["measured_coverage"]["holdout"] < 0.65  # visibly short of 0.8
    assert "not odds" in cal["reading"]


def test_the_league_reference_no_longer_describes_the_band_as_a_plain_10_90_range(db):
    from app.ingestion.demo_seed import seed_demo

    seed_demo(db, seasons=("2025-26",))
    ref = impact_reference(db)
    text = " ".join(ref["limitations"])
    assert "uncalibrated" in text and "not odds" in text
