"""POST /scenarios rejects an unknown focal team before anything is persisted.

`create_scenario` committed the scenario with whatever `focal_team_id` it was given and
then serialised it, which reads `focal_team.abbreviation`. An unknown team therefore
failed with a server error *after* the commit, and the stored row made `GET /scenarios`
fail for every later request.

These tests reuse the file-backed SQLite harness of `test_trade_create_atomicity` and
observe the outcome through a separate engine. All records are synthetic fixtures in a
database under pytest's `tmp_path`; it is never an application database.
"""

import uuid

from sqlalchemy import create_engine, select

from app.db.models import Scenario, ScenarioWeight
from tests.integration.test_trade_create_atomicity import make_world  # noqa: F401


def _scenario_rows(world) -> tuple[set[str], set[str]]:
    observer = create_engine(world.url)
    try:
        with observer.connect() as conn:
            return (
                set(conn.execute(select(Scenario.id)).scalars()),
                set(conn.execute(select(ScenarioWeight.id)).scalars()),
            )
    finally:
        observer.dispose()


def test_an_unknown_focal_team_is_rejected_and_nothing_is_saved(make_world):  # noqa: F811
    world = make_world()

    response = world.client.post(
        "/api/v1/scenarios", json={"name": "ghost team", "focal_team_id": str(uuid.uuid4())}
    )

    assert response.status_code == 404, response.text
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert "team " in error["message"]
    assert _scenario_rows(world) == (set(), set())
    # The failed request must not poison later reads.
    assert world.client.get("/api/v1/scenarios").status_code == 200


def test_a_valid_scenario_is_created_and_listed(make_world):  # noqa: F811
    world = make_world()

    response = world.client.post(
        "/api/v1/scenarios", json={"name": "real team", "focal_team_id": world.ids["team_a"]}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["focal_team"]["abbreviation"] == "AAA"
    scenario_ids, weight_ids = _scenario_rows(world)
    assert scenario_ids == {body["id"]}
    assert len(weight_ids) == len(body["weights"])
    listed = world.client.get("/api/v1/scenarios")
    assert [s["id"] for s in listed.json()] == [body["id"]]
