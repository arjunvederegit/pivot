"""R9.2 — the TEI band is served with the fact that it is not an 80% interval.

R9.1 measured the ``tei_low`` / ``tei_high`` band's out-of-time coverage at 60.8% (development) and 57.2%
(holdout) against a nominal 80%. The smallest safe correction is to say so wherever the band is served,
leaving the TEI formula and every existing field exactly as they were."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.analytics.impact import TEI_BAND_CALIBRATION
from app.db.base import get_db
from app.db.models import ModelVersion, PlayerImpactEstimate
from tests.conftest import make_player, make_team

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


@pytest.fixture()
def scored_player(db):
    team = make_team(db, 1, "AAA")
    active = ModelVersion(
        model_name="player_impact", version="active-v", algorithm="weighted z-score index (test fixture)",
        training_period="2023-24..2025-26", trained_at=datetime(2026, 8, 25, tzinfo=UTC), is_active=True,
    )
    db.add(active)
    db.flush()
    player = make_player(db, 100, "Fixture Scored", team)
    db.add(PlayerImpactEstimate(player_id=player.id, season="2025-26", model_version_id=active.id, tei=1.5, tei_low=0.5, tei_high=2.5))
    db.commit()
    return player


def test_the_player_detail_serves_the_band_with_its_calibration_and_keeps_the_old_field(client, scored_player):
    impact = client.get(f"/api/v1/players/{scored_player.id}").json()["impact"]
    assert impact["tei_range_10_90"] == [0.5, 2.5]  # the existing key and values are unchanged
    cal = impact["tei_range_calibration"]
    assert cal["status"] == "uncalibrated" and cal["nominal_level"] == 0.8
    assert cal["measured_coverage"]["holdout"] < 0.65  # visibly short of 0.8
    assert "not as odds" in cal["reading"]


def test_the_league_reference_no_longer_describes_the_band_as_a_plain_10_90_range(client, scored_player):
    body = client.get("/api/v1/players/impact-distribution").json()
    text = " ".join(body["limitations"])
    assert "uncalibrated" in text and "not odds" in text
    assert "57" in text and "61" in text
