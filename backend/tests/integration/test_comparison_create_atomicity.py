"""POST /comparisons is validated before it is persisted.

`create_comparison` used to commit the `ComparisonSet` and only then call `get_comparison`,
which evaluates every trade and can reject the request (for example, cap parameters that
are no longer loaded). A rejected request therefore left a durable comparison behind. It
also never checked `scenario_id`, so an unknown scenario was stored and then silently
replaced by the default "custom" weights when the comparison was read.

These tests reuse the file-backed SQLite harness of `test_trade_create_atomicity`: the
outcome is observed through a separate engine, so a commit anywhere in the request is
visible and a rolled-back one is not. All records are synthetic test fixtures in a
database under pytest's `tmp_path`; it is never an application database.
"""

import uuid

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from app.db.models import ComparisonSet, LeagueCapParameters
from tests.integration.test_trade_create_atomicity import (  # noqa: F401
    _payload,
    make_world,
)


def _comparison_ids(world) -> set[str]:
    observer = create_engine(world.url)
    try:
        with observer.connect() as conn:
            return set(conn.execute(select(ComparisonSet.id)).scalars())
    finally:
        observer.dispose()


def _two_saved_trades(world) -> list[str]:
    ids = world.ids
    first = world.client.post("/api/v1/trades", json=_payload(ids, name="swap one"))
    assert first.status_code == 201, first.text
    second = world.client.post(
        "/api/v1/trades",
        json=_payload(
            ids,
            name="swap two",
            moves=[
                {
                    "player_id": ids["a2"],
                    "from_team_id": ids["team_a"],
                    "to_team_id": ids["team_b"],
                },
                {
                    "player_id": ids["b1"],
                    "from_team_id": ids["team_b"],
                    "to_team_id": ids["team_a"],
                },
            ],
        ),
    )
    assert second.status_code == 201, second.text
    return [first.json()["id"], second.json()["id"]]


def test_a_valid_comparison_is_created_and_persisted(make_world):  # noqa: F811
    world = make_world()
    trade_ids = _two_saved_trades(world)

    response = world.client.post(
        "/api/v1/comparisons", json={"name": "two swaps", "trade_ids": trade_ids}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "two swaps"
    assert {row["trade_id"] for row in body["alternatives"]} == set(trade_ids)
    assert _comparison_ids(world) == {body["id"]}
    # The stored comparison reads back identically.
    again = world.client.get(f"/api/v1/comparisons/{body['id']}")
    assert again.status_code == 200
    assert again.json() == body


def test_an_unknown_scenario_is_rejected_and_nothing_is_saved(make_world):  # noqa: F811
    world = make_world()
    trade_ids = _two_saved_trades(world)

    response = world.client.post(
        "/api/v1/comparisons",
        json={"name": "bad scenario", "trade_ids": trade_ids, "scenario_id": str(uuid.uuid4())},
    )

    assert response.status_code == 404, response.text
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert "scenario " in error["message"]
    assert _comparison_ids(world) == set()


def test_an_unknown_trade_is_rejected_and_nothing_is_saved(make_world):  # noqa: F811
    world = make_world()
    trade_ids = _two_saved_trades(world)

    response = world.client.post(
        "/api/v1/comparisons",
        json={"name": "bad trade", "trade_ids": [trade_ids[0], str(uuid.uuid4())]},
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"
    assert _comparison_ids(world) == set()


def test_a_comparison_whose_evaluation_is_rejected_is_not_saved(make_world):  # noqa: F811
    """The cap parameters the saved trades need are gone: the comparison cannot be built."""
    world = make_world()
    trade_ids = _two_saved_trades(world)
    observer = create_engine(world.url)
    try:
        with Session(observer) as session:
            session.execute(delete(LeagueCapParameters))
            session.commit()
    finally:
        observer.dispose()

    response = world.client.post(
        "/api/v1/comparisons", json={"name": "no cap data", "trade_ids": trade_ids}
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "data_unavailable"
    assert _comparison_ids(world) == set()
