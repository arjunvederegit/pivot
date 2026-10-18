"""R8.1 API compatibility check: dump a fixed set of responses, then compare two dumps.

    # in the code you want to measure, against a COPY of a real database:
    DATABASE_URL=sqlite:///copy.db PYTHONPATH=. python scripts/r8_1_api_snapshot.py dump out.json
    python scripts/r8_1_api_snapshot.py compare before.json after.json

``compare`` demands that everything in *before* is still in *after* with the same value, ignoring
the keys named in ``ADDITIVE`` (metadata that is allowed to grow or change meaning on purpose).
Anything else that differs is a regression. Read-only against the database.
"""

from __future__ import annotations

import json
import sys

#: Keys whose content is provenance metadata, deliberately extended in R8 / R8.1.
ADDITIVE = {"data_status"}


def dump(path: str) -> int:
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    import app.main as main_module
    from app.db.base import SessionLocal
    from app.db.models import RosterEntry, Team
    from app.main import app

    main_module.RATE_LIMIT_PER_MINUTE = 10**9
    out: dict[str, object] = {}
    with TestClient(app) as client, SessionLocal() as db:
        teams = list(db.scalars(select(Team).order_by(Team.abbreviation)).all())
        urls = ["/api/v1/teams", "/api/v1/players?limit=50", "/api/v1/players/impact-distribution"]
        for t in teams:
            base = f"/api/v1/teams/{t.id}"
            urls += [base, f"{base}/roster", f"{base}/needs", f"{base}/payroll",
                     f"{base}/season-stats", f"{base}/cap-outlook"]
        pids = [pid for (pid,) in db.execute(
            select(RosterEntry.player_id).where(RosterEntry.is_current).order_by(RosterEntry.player_id).limit(120))]
        for pid in pids:
            urls += [f"/api/v1/players/{pid}", f"/api/v1/players/{pid}/stats"]
        for url in urls:
            r = client.get(url)
            out[url] = {"status": r.status_code, "body": r.json() if r.status_code == 200 else None}
    with open(path, "w") as handle:
        json.dump(out, handle, sort_keys=True, default=str)
    print(f"{len(out)} responses -> {path}")
    return 0


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in ADDITIVE}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _missing_or_changed(before, after, path=""):
    if isinstance(before, dict):
        if not isinstance(after, dict):
            yield path, "type changed"
            return
        for k, v in before.items():
            if k not in after:
                yield f"{path}/{k}", "removed"
            else:
                yield from _missing_or_changed(v, after[k], f"{path}/{k}")
    elif isinstance(before, list):
        if not isinstance(after, list) or len(before) != len(after):
            yield path, "list length/type changed"
            return
        for i, (b, a) in enumerate(zip(before, after, strict=True)):
            yield from _missing_or_changed(b, a, f"{path}[{i}]")
    elif before != after:
        yield path, f"{before!r} -> {after!r}"


def compare(before_path: str, after_path: str) -> int:
    with open(before_path) as f1, open(after_path) as f2:
        before, after = json.load(f1), json.load(f2)
    problems = []
    for url, b in before.items():
        a = after.get(url)
        if a is None:
            problems.append((url, "missing", ""))
            continue
        if a["status"] != b["status"]:
            problems.append((url, "status", f"{b['status']} -> {a['status']}"))
            continue
        for where, why in _missing_or_changed(_strip(b["body"]), _strip(a["body"])):
            problems.append((url, where, why))
    non_200 = sum(1 for b in before.values() if b["status"] != 200)
    print(json.dumps({"responses": len(before), "non_200_in_baseline": non_200,
                      "differences": len(problems), "examples": problems[:10]}, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "dump":
        sys.exit(dump(sys.argv[2]))
    if len(sys.argv) == 4 and sys.argv[1] == "compare":
        sys.exit(compare(sys.argv[2], sys.argv[3]))
    print(__doc__)
    sys.exit(2)
