"""R9.3A — a reviewed, reversible way to activate provider data on another database.

The preview database holds, beside everything the owner's development database already has, the *additive* data
R8.3 – R9.3A produced: BALLDONTLIE identity links, statistic snapshots, contracts and contract years (all five
seasons), injuries, the sync runs that wrote them, and R9.2's stored candidate models and estimates. This module
copies exactly that — and nothing else — into a target database, with the safeguards an activation needs:

* **Dry run by default.** ``plan`` reports, per table, how many rows would be added, which already exist, which
  conflict, and which would break a foreign key. ``apply`` is a separate, explicit call.
* **Additive and idempotent.** Rows are inserted by primary key; an existing row is never overwritten (a row that
  exists with different content is reported as a *conflict* and stops the activation). Re-running changes nothing.
* **Allow-listed.** Only the tables listed in ``ADDITIVE_TABLES`` are touched. Players, teams, rosters, TEI
  estimates and model versions of the serving models, saved trades and every other table are read for a
  before/after fingerprint and must come out **byte-identical**; if one differs, the transaction is rolled back.
* **Identity-checked.** Every player and team a copied row refers to must already exist in the target under the
  same canonical id; a row that would dangle is refused.
* **Revision-checked.** Source and target must be at the same Alembic revision.
* **Backed up first.** ``apply`` writes a consistent SQLite backup of the target (the sqlite3 backup API), verifies
  that it opens, passes ``integrity_check`` and has the same row counts, and checks free disk space, *before* the
  first insert. It returns the restore command. Nothing proceeds without it.

SQLite only (the development database). A PostgreSQL deployment activates through the documented migration and
refresh path instead; this tool refuses a non-SQLite URL rather than guessing.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, select, text
from sqlalchemy.engine import Engine

from app.db.models import Base

#: Allow-list, in dependency order (parents before children). Anything not here is never written.
ADDITIVE_TABLES: tuple[str, ...] = (
    "data_sync_runs",
    "provider_entity_ids",
    "contracts",
    "contract_years",
    "injuries",
    "provider_stat_snapshots",
    "model_versions",
    "candidate_estimates",
)

#: Per-table row filter on the SOURCE: what counts as additive for tables that also hold non-additive rows.
SOURCE_FILTER: dict[str, str] = {
    # Only the sync runs the provider jobs wrote; the owner's own run history is never copied over.
    "data_sync_runs": "job_name IN ('sync_provider_identities','sync_contracts','sync_injuries','sync_provider_stats')",
    # Only CANDIDATE models. An active or retired model of the target is never replaced and none is imported.
    "model_versions": "status = 'candidate' AND is_active = 0",
}

#: Which foreign keys to verify against the target before inserting: (table, column) -> parent table.
REFERENCES: dict[tuple[str, str], str] = {
    ("provider_entity_ids", "internal_id"): "*",  # a player OR a team (entity_type decides)
    ("contracts", "player_id"): "players",
    ("injuries", "player_id"): "players",
    ("provider_stat_snapshots", "internal_id"): "*",
    ("candidate_estimates", "player_id"): "players",
}


class ActivationRefused(RuntimeError):
    """The activation cannot proceed safely; nothing was written."""


@dataclass
class TablePlan:
    table: str
    source_rows: int = 0
    to_add: int = 0
    already_present: int = 0
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    dangling: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "source_rows": self.source_rows,
            "to_add": self.to_add,
            "already_present": self.already_present,
            "conflicts": len(self.conflicts),
            "conflict_examples": self.conflicts[:3],
            "dangling": len(self.dangling),
            "dangling_examples": self.dangling[:3],
        }


def _sqlite_path(url: str) -> Path:
    if not url.startswith("sqlite:///"):
        raise ActivationRefused("only SQLite databases are supported by this tool (got a different URL scheme)")
    raw = url[len("sqlite:///"):]
    if raw in ("", ":memory:"):
        raise ActivationRefused("an in-memory database cannot be activated or backed up")
    return Path(raw)


def _engine(path: Path, *, read_only: bool) -> Engine:
    uri = f"sqlite:///file:{path}?mode={'ro' if read_only else 'rw'}&uri=true"
    return create_engine(uri)


def _revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        try:
            return conn.execute(text("select version_num from alembic_version")).scalar()
        except Exception:  # noqa: BLE001 — a database without the table has no revision
            return None


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def _fingerprint(conn: Any, skip: tuple[str, ...]) -> dict[str, tuple[int, str]]:
    """Order-independent row hash of every table not in ``skip`` — the proof that nothing else moved.

    Takes a connection, so it can be read inside the activation's own transaction and rolled back on a mismatch.
    """
    out: dict[str, tuple[int, str]] = {}
    names = [r[0] for r in conn.execute(text("select name from sqlite_master where type='table' and name not like 'sqlite_%'"))]
    for name in sorted(names):
        if name in skip:
            continue
        acc, n = 0, 0
        for row in conn.execute(text(f'select * from "{name}"')):
            acc ^= int.from_bytes(hashlib.sha256(repr(tuple(row)).encode()).digest()[:16], "big")
            n += 1
        out[name] = (n, format(acc, "032x"))
    return out


def _ids(engine: Engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(text(f'select id from "{table}"'))}


def _source_rows(source: Engine, table: str) -> list[dict[str, Any]]:
    """Typed rows (JSON and datetimes decoded), so they round-trip through ``Table.insert`` unchanged."""
    stmt = select(_table(table))
    clause = SOURCE_FILTER.get(table)
    if clause:
        stmt = stmt.where(text(clause))
    with source.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(stmt)]


def plan(source_path: Path, target_url: str) -> dict[str, Any]:
    """Dry run: what activating ``source_path`` into the target would do. Writes nothing."""
    target_path = _sqlite_path(target_url)
    if source_path.resolve() == target_path.resolve():
        raise ActivationRefused("the source and the target are the same file")
    for p, label in ((source_path, "source"), (target_path, "target")):
        if not p.exists():
            raise ActivationRefused(f"the {label} database {p} does not exist")
    source, target = _engine(source_path, read_only=True), _engine(target_path, read_only=True)
    src_rev, tgt_rev = _revision(source), _revision(target)
    if src_rev != tgt_rev:
        raise ActivationRefused(
            f"schema revisions differ (source {src_rev}, target {tgt_rev}): migrate the target first "
            "(`make migrate` on a COPY, then on the target) — nothing was written"
        )
    players, teams = _ids(target, "players"), _ids(target, "teams")
    plans: list[TablePlan] = []
    for name in ADDITIVE_TABLES:
        tp = TablePlan(name)
        rows = _source_rows(source, name)
        tp.source_rows = len(rows)
        existing = _existing_rows(target, name, [r["id"] for r in rows])
        for row in rows:
            current = existing.get(row["id"])
            if current is not None:
                if _same(row, current):
                    tp.already_present += 1
                else:
                    tp.conflicts.append({"id": row["id"], "differs_in": _diff(row, current)})
                continue
            problem = _dangling(name, row, players, teams)
            if problem:
                tp.dangling.append({"id": row["id"], "problem": problem})
            else:
                tp.to_add += 1
        plans.append(tp)
    blocked = any(p.conflicts or p.dangling for p in plans)
    return {
        "mode": "dry-run",
        "source": str(source_path),
        "target": str(target_path),
        "revision": src_rev,
        "tables": [p.as_dict() for p in plans],
        "total_to_add": sum(p.to_add for p in plans),
        "blocked": blocked,
        "blocked_reason": "conflicting or dangling rows — resolve them before applying" if blocked else None,
    }


def _existing_rows(db: Engine | Any, table: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Rows of ``table`` already present in the target, by id.

    ``db`` may be an Engine (a dry run) or the open Connection of the activation's own transaction: SQLite allows one
    writer, so a second connection opened while the transaction holds the write lock would fail with "database is
    locked" (found by rehearsing against a copy of the real database).
    """
    out: dict[str, dict[str, Any]] = {}

    def read(conn: Any) -> None:
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            if not chunk:
                continue
            stmt = select(_table(table)).where(_table(table).c.id.in_(chunk))
            for r in conn.execute(stmt):
                out[r._mapping["id"]] = dict(r._mapping)

    if isinstance(db, Engine):
        with db.connect() as conn:
            read(conn)
    else:
        read(db)
    return out


_IGNORED = {"created_at", "updated_at"}


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return all(_norm(a.get(k)) == _norm(b.get(k)) for k in set(a) | set(b) if k not in _IGNORED)


def _diff(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    return sorted(k for k in set(a) | set(b) if k not in _IGNORED and _norm(a.get(k)) != _norm(b.get(k)))


def _norm(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat()
    return value


def _dangling(table: str, row: dict[str, Any], players: set[str], teams: set[str]) -> str | None:
    for (t, column), parent in REFERENCES.items():
        if t != table:
            continue
        value = row.get(column)
        if parent == "players" and value not in players:
            return f"{column} {value} is not a player in the target"
        if parent == "*":
            kind = row.get("entity_type")
            pool = players if kind == "player" else teams if kind == "team" else set()
            if value not in pool:
                return f"{column} {value} is not a {kind} in the target"
    return None


# --------------------------------------------------------------------------------------- backup


def verified_backup(target_path: Path, backup_dir: Path) -> dict[str, Any]:
    """A consistent copy of the target, proven readable, before anything is written."""
    size = target_path.stat().st_size
    backup_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(backup_dir).free
    if free < size * 2 + 50_000_000:
        raise ActivationRefused(
            f"not enough free disk space for a backup: {free / 1e6:.0f} MB free, {size * 2 / 1e6:.0f} MB needed"
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"{target_path.stem}.pre-r93a-{stamp}.db"
    src = sqlite3.connect(f"file:{target_path}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)  # the SQLite online-backup API: consistent even if another process holds the file
    finally:
        dst.close()
        src.close()
    check = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
    try:
        integrity = check.execute("pragma integrity_check").fetchone()[0]
        counts_backup = _row_counts(check)
    finally:
        check.close()
    orig = sqlite3.connect(f"file:{target_path}?mode=ro", uri=True)
    try:
        counts_target = _row_counts(orig)
    finally:
        orig.close()
    if integrity != "ok" or counts_backup != counts_target:
        dest.unlink(missing_ok=True)
        raise ActivationRefused("the backup did not verify (integrity or row counts differ); nothing was written")
    return {
        "path": str(dest),
        "bytes": dest.stat().st_size,
        "integrity_check": integrity,
        "tables": len(counts_backup),
        "rows": sum(counts_backup.values()),
        "restore": f"cp '{dest}' '{target_path}'  # with every server using the target stopped",
    }


def _row_counts(con: sqlite3.Connection) -> dict[str, int]:
    names = [r[0] for r in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
    return {n: con.execute(f'select count(*) from "{n}"').fetchone()[0] for n in sorted(names)}


# ---------------------------------------------------------------------------------------- apply


def apply(source_path: Path, target_url: str, backup_dir: Path) -> dict[str, Any]:
    """Activate for real: plan, refuse on any conflict/dangling row, back up, insert, verify, or roll back."""
    report = plan(source_path, target_url)
    if report["blocked"]:
        raise ActivationRefused(report["blocked_reason"] or "blocked")
    target_path = _sqlite_path(target_url)
    backup = verified_backup(target_path, backup_dir)
    source = _engine(source_path, read_only=True)
    target = _engine(target_path, read_only=False)
    players, teams = _ids(target, "players"), _ids(target, "teams")
    written: dict[str, int] = {}
    with target.begin() as conn:
        before = _fingerprint(conn, ADDITIVE_TABLES)
        for name in ADDITIVE_TABLES:
            rows = _source_rows(source, name)
            existing = _existing_rows(conn, name, [r["id"] for r in rows])
            fresh = [r for r in rows if r["id"] not in existing and not _dangling(name, r, players, teams)]
            if fresh:
                conn.execute(_table(name).insert(), fresh)
            written[name] = len(fresh)
        # Candidate estimates must point at a model version that exists in the target (just inserted or already there).
        orphans = conn.execute(
            text(
                "select count(*) from candidate_estimates ce left join model_versions mv "
                "on mv.id = ce.model_version_id where mv.id is null"
            )
        ).scalar()
        fk = conn.execute(text("pragma foreign_key_check")).fetchall()
        if orphans or fk:
            raise ActivationRefused(f"foreign-key check failed after insert ({orphans} orphan estimates, {len(fk)} violations): rolled back")
        after = _fingerprint(conn, ADDITIVE_TABLES)
        if after != before:  # raising inside ``begin()`` rolls the whole activation back
            changed = sorted(t for t in before if before.get(t) != after.get(t))
            raise ActivationRefused(f"tables outside the allow-list changed ({changed}): rolled back")
    return {
        "mode": "applied",
        "backup": backup,
        "written": written,
        "total_written": sum(written.values()),
        "untouched_tables_identical": True,
        "untouched_tables_checked": len(after),
        "rollback": backup["restore"],
    }
