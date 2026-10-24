"""Where a result came from: data fingerprint, environment, feature manifest.

A model version is only reproducible if the *inputs* can be identified, not merely named. The
fingerprint here is a hash of the exact rows a run read, so two runs with the same fingerprint read
the same numbers and two runs with different ones did not — whatever the database file is called.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

import numpy as np
import pandas as pd

from app.config import BACKEND_DIR

#: Rounded before hashing so a harmless float round trip through JSON does not change identity.
_HASH_DECIMALS = 9


def frame_fingerprint(df: pd.DataFrame, columns: list[str] | None = None) -> str:
    """SHA-256 over the canonical content of a frame (sorted rows, sorted columns, rounded floats)."""
    columns = sorted(columns if columns is not None else df.columns)
    ordered = df[columns].sort_values(
        [c for c in ("season", "player_id") if c in columns] or columns, kind="mergesort"
    )
    digest = hashlib.sha256()
    digest.update(json.dumps(columns).encode())
    for row in ordered.itertuples(index=False, name=None):
        digest.update(
            json.dumps(
                [
                    round(float(v), _HASH_DECIMALS)
                    if isinstance(v, (float, np.floating)) and not np.isnan(v)
                    else (None if _is_missing(v) else str(v) if not isinstance(v, (int, float)) else v)
                    for v in row
                ],
                separators=(",", ":"),
            ).encode()
        )
    return digest.hexdigest()


def _is_missing(value: object) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class DataSnapshot:
    """An explicit identifier for the data a run was allowed to read."""

    snapshot_id: str
    fingerprint: str
    seasons: tuple[str, ...]
    rows: int
    columns: tuple[str, ...]
    retrieved_from: str | None
    retrieved_to: str | None

    @classmethod
    def of_panel(
        cls, frame: pd.DataFrame, retrieved: tuple[str | None, str | None] = (None, None)
    ) -> DataSnapshot:
        cols = [c for c in frame.columns if c not in ("full_name",)]
        fingerprint = frame_fingerprint(frame, cols)
        return cls(
            snapshot_id=f"ds-{fingerprint[:16]}",
            fingerprint=fingerprint,
            seasons=tuple(sorted(frame["season"].unique())),
            rows=int(len(frame)),
            columns=tuple(sorted(cols)),
            retrieved_from=retrieved[0],
            retrieved_to=retrieved[1],
        )

    def as_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "fingerprint": self.fingerprint,
            "seasons": list(self.seasons),
            "rows": self.rows,
            "columns": list(self.columns),
            "source_retrieved_from": self.retrieved_from,
            "source_retrieved_to": self.retrieved_to,
        }


def code_state() -> dict:
    """Commit and whether the working tree had uncommitted changes when the run happened."""

    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args], capture_output=True, text=True, cwd=BACKEND_DIR, timeout=10
            )
            return out.stdout.strip() if out.returncode == 0 else None
        except Exception:
            return None

    status = git("status", "--porcelain", "--", ".")
    return {
        "commit": git("rev-parse", "--short", "HEAD"),
        "dirty": bool(status) if status is not None else None,
    }


def environment() -> dict:
    versions: dict[str, str | None] = {}
    for package in ("numpy", "pandas", "scipy", "scikit-learn", "sqlalchemy"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": versions,
        "code": code_state(),
    }


def config_hash(config: dict) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    source: str
    unit: str
    as_of_rule: str
    transformation: str
    leakage_class: str = "none"


def feature_manifest(window_seasons: int, decay: float) -> list[dict]:
    """Every feature a candidate may read, with its source, unit and as-of rule."""
    z = "z-score within the season, minutes-weighted, that season's own league distribution"
    rule = "the origin season's row only"
    rows = [
        FeatureDefinition("x_t", "target measure in the origin season", "target unit", rule, "none"),
        FeatureDefinition("x_lag1", "target measure, season before the origin", "target unit",
                          "the season before the origin; missing for a player without it", "none"),
        FeatureDefinition("x_lag2", "target measure, two seasons before the origin", "target unit",
                          "two seasons before the origin; missing if absent", "none"),
        FeatureDefinition("x_bar", "exposure-weighted, recency-weighted mean of the target measure",
                          "target unit", f"last {window_seasons} seasons ending at the origin, decay {decay}",
                          "sum(decay^k * exposure_k * x_k) / sum(decay^k * exposure_k)"),
        FeatureDefinition("exposure_t", "target exposure in the origin season", "minutes or attempts", rule, "none"),
        FeatureDefinition("window_exposure", "total exposure over the window", "minutes or attempts",
                          f"last {window_seasons} seasons ending at the origin", "sum"),
        FeatureDefinition("age", "NBA.com base table AGE in the origin season", "years", rule, "none"),
        FeatureDefinition("tei_window", "incumbent TEI computed by the production code path",
                          "TEI index points", f"last {window_seasons} seasons ending at the origin; "
                          "z-scored against the origin season's moments", "production code, unchanged"),
    ]
    for col, label in (
        ("pts_per75", "points per 75 possessions"), ("TS_PCT", "true shooting"),
        ("AST_PCT", "assist share"), ("TM_TOV_PCT", "turnover share"), ("USG_PCT", "usage"),
        ("OREB_PCT", "offensive rebound share"), ("DREB_PCT", "defensive rebound share"),
        ("stl_per_min", "steals per minute"), ("blk_per_min", "blocks per minute"),
        ("PIE", "player impact estimate (a component of the impact_proxy TARGET in the next season)"),
        ("NET_RATING", "on-court net rating (the other component of the impact_proxy target)"),
        ("fg3a_rate", "three-point attempts per FGA"), ("fta_rate", "free throws attempted per FGA"),
        ("MIN", "minutes per game"),
    ):
        rows.append(FeatureDefinition(f"z_{col}", f"NBA.com {label}", "z", rule, z))
    return [r.__dict__ for r in rows]


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
