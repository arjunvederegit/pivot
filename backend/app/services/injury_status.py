"""R9.3A — a player's injury status from the provider feed, dated honestly.

The BALLDONTLIE injury feed carries a status and an expected return date but **no report date**, so the only
date Pivot can attach is its own retrieval time. That decides the wording:

* a reading retrieved within ``FRESH_HOURS`` is "current as of" its retrieval time;
* an older reading is "last reported" with its retrieval time and is flagged stale — it is never presented as
  today's status;
* a closed row (the provider stopped listing the injury) says "no longer reported", which is not "healthy";
* no row says "none on file", which is not "healthy" either — injury absence is never a clean bill of health.

Read-only; no provider call.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.calendar import today_in_league
from app.db.models import DataSyncRun, Injury, Player

FRESH_HOURS = 24.0
PROVIDER = "balldontlie"
INJURY_JOB = "sync_injuries"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def injury_status(db: Session, player_id: str, *, now: datetime | None = None, today: date | None = None) -> dict[str, Any]:
    player = db.get(Player, player_id)
    if player is None:
        return {"available": False, "reason": "no such player"}
    moment = now or datetime.now(UTC)
    # A return date is a calendar day in the league's timezone: UTC midnight made "has the date passed" flip at 8 pm ET.
    day = today or today_in_league(moment)
    last_run = db.scalar(
        select(DataSyncRun)
        .where(DataSyncRun.job_name == INJURY_JOB, DataSyncRun.status == "succeeded")
        .order_by(DataSyncRun.finished_at.desc())
    )
    checked_at = _aware(last_run.finished_at) if last_run else None
    base = {
        "player_id": player_id,
        "provider": PROVIDER,
        "last_feed_check": checked_at.isoformat() if checked_at else None,
        "has_report_date": False,
        "note": "The provider reports no date for an injury, so the only date shown is when Pivot retrieved it. "
        "No injury on file is not the same as healthy.",
    }
    rows = db.scalars(select(Injury).where(Injury.player_id == player_id, Injury.source_provider == PROVIDER)).all()
    if not rows:
        return {**base, "available": False, "reason": "No injury is on file for this player in the last feed Pivot read."}
    row = sorted(rows, key=lambda r: _aware(r.source_retrieved_at) or datetime.min.replace(tzinfo=UTC))[-1]
    retrieved = _aware(row.source_retrieved_at)
    closed_at = _aware(row.valid_to)
    if closed_at is not None:
        return {
            **base,
            "available": False,
            "reason": "The provider stopped listing an injury for this player (closed "
            f"{closed_at.date().isoformat()}). That is not a statement that he is healthy.",
        }
    age_hours = round((moment - retrieved).total_seconds() / 3600, 1) if retrieved else None
    fresh = age_hours is not None and age_hours <= FRESH_HOURS
    return_passed = bool(row.return_date and row.return_date < day)
    return {
        **base,
        "available": True,
        "status": row.status,
        "description": row.description,
        "return_date": row.return_date.isoformat() if row.return_date else None,
        "return_date_passed": return_passed,
        "retrieved_at": retrieved.isoformat() if retrieved else None,
        "age_hours": age_hours,
        "freshness": "fresh" if fresh else "stale",
        "framing": "current as of retrieval" if fresh else "last reported — may no longer be current",
        "caveats": (
            (
                []
                if fresh
                else [
                    f"Retrieved {age_hours:.0f} hours ago; the status may have changed since."
                    if age_hours is not None
                    else "Pivot has no retrieval time for this reading, so its age is unknown; the status may have changed."
                ]
            )
            + (["The reported return date has passed; the status may be out of date."] if return_passed else [])
        ),
    }
