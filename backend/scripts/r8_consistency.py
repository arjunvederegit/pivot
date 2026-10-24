"""R8 cross-tool consistency check against a real database. Read-only.

    cd backend
    DATABASE_URL=sqlite:///./tradelab.db .venv/bin/python -m scripts.r8_consistency

For every team, asks the three consumers in the Team Overview -> Player Evaluation ->
Strategy Lab workflow the same question — who is on this roster, and what did they do — and
reports any disagreement:

* Team Overview   GET /teams/{id}/roster and /teams/{id}/season-stats
* Player Eval     GET /players?team_id=, GET /players/{id}, GET /players/{id}/stats
* Strategy Lab    EvaluationService roster cards (the trade evaluator's own query)
* Cap Lab         GET /teams/{id}/payroll

It also reports the facts the new transparency fields expose on real data: the age of each
dataset, rostered players with no statistics row (listed, not zero) and duplicated names.

Nothing is written. Exit code 0: no disagreement. 1: at least one. The database must already
be at the current migration (`make migrate`); the script says so rather than guessing.
"""

from __future__ import annotations

import json
import sys
from collections import Counter

from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select

import app.main as main_module
from app.config import get_settings
from app.db.base import SessionLocal, engine
from app.db.models import Player, Team
from app.main import app
from app.services.evaluation import EvaluationService


def main() -> int:
    columns = {c["name"] for c in inspect(engine).get_columns("player_season_stats")}
    if "season_type" not in columns:
        print(json.dumps({"error": "database predates R8; run `make migrate` first"}, indent=2))
        return 2

    # This script is a local, read-only audit issuing a few thousand requests; the demo-scale
    # per-IP limiter exists for the public server, not for it.
    main_module.RATE_LIMIT_PER_MINUTE = 10**9
    settings = get_settings()
    disagreements: list[dict] = []
    checked = Counter()

    def expect(label: str, left: object, right: object, **context: object) -> None:
        checked[label] += 1
        if left != right:
            disagreements.append({"check": label, "left": left, "right": right, **context})

    with TestClient(app) as client, SessionLocal() as db:
        teams = list(db.scalars(select(Team).order_by(Team.abbreviation)).all())
        service = EvaluationService(db)
        grouped = service._all_rosters()  # the trade evaluator's all-rosters query
        for team in teams:
            roster = client.get(f"/api/v1/teams/{team.id}/roster").json()
            overview = {p["player_id"] for p in roster["roster"]}
            directory = client.get(
                "/api/v1/players",
                params={"team_id": team.id, "limit": 200, "active_only": False},
            ).json()
            lab = {c.player_id for c in grouped.get(team.id, [])}
            payroll = {p["player_id"] for p in client.get(f"/api/v1/teams/{team.id}/payroll").json().get("players", [])}
            expect("overview == directory", overview, {p["id"] for p in directory["players"]}, team=team.abbreviation)
            expect("overview == strategy lab", overview, lab, team=team.abbreviation)
            if payroll:
                expect("overview == payroll", overview, payroll, team=team.abbreviation)

            season_stats = client.get(f"/api/v1/teams/{team.id}/season-stats").json()
            expect(
                "stats + missing == roster",
                set(season_stats["stats"]) | set(season_stats["missing_player_ids"]),
                overview,
                team=team.abbreviation,
            )
            for player_id in sorted(overview):
                page = client.get(f"/api/v1/players/{player_id}").json()
                expect(
                    "player page team == overview team",
                    (page["player"]["current_team"] or {}).get("id"),
                    team.id,
                    player=page["player"]["full_name"],
                )
                expect("membership_status", page["membership_status"], "member", player=page["player"]["full_name"])
                if player_id in season_stats["stats"]:
                    history = client.get(f"/api/v1/players/{player_id}/stats").json()
                    current = next(
                        (s for s in history["seasons"] if s["season"] == settings.current_season),
                        None,
                    )
                    row = season_stats["stats"][player_id]
                    expect(
                        "team page stats == player page stats",
                        {k: (current or {}).get("base", {}).get(k) for k in ("GP", "PTS", "MIN")},
                        {k: row[k] for k in ("GP", "PTS", "MIN")},
                        player=page["player"]["full_name"],
                    )

        freshness = client.get("/api/v1/data/freshness").json()
        duplicate_names = db.execute(
            select(func.lower(Player.full_name), func.count(Player.id))
            .group_by(func.lower(Player.full_name))
            .having(func.count(Player.id) > 1)
        ).all()

    report = {
        "database": str(engine.url).split("///")[-1],
        "teams": len(teams),
        "checks_run": sum(checked.values()),
        "checks_by_type": dict(checked),
        "disagreements": disagreements,
        "datasets": {
            name: {
                "freshness": meta["freshness"],
                "age_days": None if meta["age_seconds"] is None else round(meta["age_seconds"] / 86400, 1),
                "present": meta["present_count"],
                "expected": meta["expected_count"],
                "completeness": meta["completeness"],
            }
            for name, meta in freshness["datasets"].items()
        },
        "duplicate_player_names": len(duplicate_names),
    }
    print(json.dumps(report, indent=2, default=str))
    return 1 if disagreements else 0


if __name__ == "__main__":
    sys.exit(main())
