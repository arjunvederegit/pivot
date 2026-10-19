"""Candidate-only training.

``train_candidate`` is the *only* write path R9.1 adds, and it writes exactly two kinds of row:
one ``model_versions`` row with ``status = 'candidate'`` and ``is_active = False``, and the
candidate's rows in ``candidate_estimates``. It never updates or deletes anything else.

That promise is enforced, not hoped for: the function fingerprints **every table except the
candidate-owned ones** (and the non-candidate rows of ``model_versions``) before it does anything,
fingerprints them again before it commits, and rolls back and raises ``ProtectedStateViolation`` if a
single byte differs. The legacy ``make train`` deletes the previous version's impact estimates and
retires every active model; this path cannot, and a regression test fails if that ever changes.

Training and promotion are separate. Nothing here sets ``is_active``; ``promotion_blockers`` lists
what a future, audited promotion step would have to establish.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Base, CandidateEstimate, ModelVersion, Standing

from .intervals import fit_interval, loto_residuals
from .pairs import FeatureConfig, PairCache
from .panel import PlayerSeasonPanel, season_key, season_label
from .predictors import PREDICTORS, FitContext
from .provenance import DataSnapshot, config_hash, environment
from .targets import get_target

#: Tables the candidate path owns. Everything else is protected.
CANDIDATE_OWNED_TABLES = frozenset({"candidate_estimates"})
CANDIDATE_MODEL_PREFIX = "player_forecast_"


class ProtectedStateViolation(RuntimeError):
    """A candidate operation changed state it does not own. It has been rolled back."""


class CandidateError(ValueError):
    """The request cannot be honoured safely."""


def protected_fingerprint(db: Session) -> dict[str, dict]:
    """Row count and content hash of every protected table (candidate-owned rows excluded)."""
    result: dict[str, dict] = {}
    for table in Base.metadata.sorted_tables:
        if table.name in CANDIDATE_OWNED_TABLES:
            continue
        query = select(*table.c)
        if table.name == "model_versions":
            query = query.where(table.c.status != "candidate")
        keys = list(table.primary_key.columns) or list(table.c)
        query = query.order_by(*keys)
        digest = hashlib.sha256()
        count = 0
        for row in db.execute(query):
            digest.update(json.dumps(list(row), default=str, sort_keys=True, separators=(",", ":")).encode())
            count += 1
        result[table.name] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def diff_fingerprints(before: dict[str, dict], after: dict[str, dict]) -> list[str]:
    changed = []
    for name in sorted(set(before) | set(after)):
        if before.get(name) != after.get(name):
            b, a = before.get(name, {}).get("rows"), after.get(name, {}).get("rows")
            changed.append(f"{name}: rows {b} -> {a}" if b != a else f"{name}: content changed")
    return changed


@dataclass(frozen=True)
class CandidateRecord:
    version_id: str
    version: str
    model_name: str
    spec: str
    target: str
    origin: str
    target_season: str
    estimates_written: int
    reused_existing: bool
    snapshot_id: str


def _forward_estimates(
    panel: PlayerSeasonPanel, spec: str, target_name: str, origin: str, features: FeatureConfig,
    seed: int, level: float,
) -> tuple[pd.DataFrame, dict, dict]:
    """Fit on every transition observable at ``origin`` and forecast ``origin``'s next season."""
    target = get_target(target_name)
    predictor = PREDICTORS[spec]
    if not predictor.applies_to(target):
        raise CandidateError(f"{spec} does not apply to target {target_name}")
    truncated = PlayerSeasonPanel(panel.as_of(origin).frame)
    cache = PairCache(truncated, features)
    origins = [s for s in truncated.seasons if season_key(s) < season_key(origin)]
    if len(origins) < 2:
        raise CandidateError(f"{len(origins)} training transitions at {origin}; at least 2 are needed")
    train_all = pd.concat([cache.pairs(s, target) for s in origins], ignore_index=True)
    train = train_all[train_all["target_eligible"]].reset_index(drop=True)
    ctx = FitContext(target=target, seed=seed)
    fitted = predictor.fit(train, ctx)
    scoring = cache.features(origin, target).reset_index(drop=True)
    pred = fitted.predict(scoring)
    interval = fit_interval(loto_residuals(predictor, train, ctx), level)
    lo, hi = interval.apply(pred, scoring["window_exposure"].to_numpy(dtype=float))
    out = scoring.assign(pred=pred, lo=lo, hi=hi)
    return out, {"fitted": fitted.params, "interval": interval.params()}, {
        "training_pairs": int(len(train)), "training_origins": origins, "scored": int(len(scoring)),
    }


def train_candidate(
    db: Session,
    spec: str,
    target: str,
    origin: str,
    *,
    seed: int = 20260719,
    level: float = 0.8,
    features: FeatureConfig | None = None,
    evaluation: dict | None = None,
) -> CandidateRecord:
    """Train a candidate and persist it — and nothing else — under ``status = 'candidate'``."""
    features = features or FeatureConfig()
    if spec not in PREDICTORS:
        raise CandidateError(f"unknown model {spec!r}; registered: {sorted(PREDICTORS)}")
    before = protected_fingerprint(db)

    panel = PlayerSeasonPanel.from_db(db)
    if origin not in panel.seasons:
        raise CandidateError(f"no statistics for {origin}; ingested seasons: {panel.seasons}")
    scoring, params, counts = _forward_estimates(panel, spec, target, origin, features, seed, level)
    snapshot = DataSnapshot.of_panel(panel.as_of(origin).frame)
    target_season = season_label(season_key(origin) + 1)

    config = {
        "spec": spec, "target": target, "origin": origin, "target_season": target_season,
        "seed": seed, "interval_level": level, "features": features.as_dict(),
        "model_spec": PREDICTORS[spec].spec(),
    }
    version = f"cand-{spec}-{target}-{config_hash({**config, 'data': snapshot.fingerprint})[:12]}"
    model_name = f"{CANDIDATE_MODEL_PREFIX}{target}"

    existing = db.scalar(
        select(ModelVersion).where(ModelVersion.model_name == model_name, ModelVersion.version == version)
    )
    if existing is not None:
        # Deterministic: the same specification on the same data is the same candidate. Nothing is
        # rewritten and nothing is deleted.
        n = len(db.scalars(select(CandidateEstimate).where(CandidateEstimate.model_version_id == existing.id)).all())
        return CandidateRecord(existing.id, version, model_name, spec, target, origin, target_season, n, True, snapshot.snapshot_id)

    active = db.scalars(
        select(ModelVersion).where(ModelVersion.model_name == "player_impact", ModelVersion.is_active)
    ).first()
    now = datetime.now(UTC)
    model = ModelVersion(
        model_name=model_name,
        version=version,
        algorithm=PREDICTORS[spec].description[:200],
        training_period=f"{panel.seasons[0]}..{origin}",
        feature_list=[c for c in scoring.columns if c.startswith(("x_", "z_")) or c in ("age", "window_exposure")],
        target=target,
        validation_metrics=evaluation or {"evaluation_status": "unevaluated"},
        artifact_path=None,
        trained_at=now,
        code_commit=environment()["code"]["commit"],
        data_snapshot_id=snapshot.snapshot_id,
        is_active=False,
        status="candidate",
        config=config,
        lineage={
            "data_snapshot": snapshot.as_dict(),
            "environment": environment(),
            "seed": seed,
            "fit": params,
            "counts": counts,
            "compared_against_active_version": active.version if active else None,
            "promotion": "not promoted; see promotion_blockers()",
        },
    )
    db.add(model)
    db.flush()
    rows = [
        CandidateEstimate(
            model_version_id=model.id,
            player_id=str(r.player_id),
            season=origin,
            target_season=target_season,
            target=target,
            output_kind=get_target(target).output_kind,
            estimate=float(r.pred),
            interval_low=float(r.lo),
            interval_high=float(r.hi),
            interval_level=level,
            exposure=float(r.window_exposure),
            inputs={"x_t": _clean(r.x_t), "age": _clean(r.age), "window_minutes": _clean(r.window_minutes)},
        )
        for r in scoring.itertuples(index=False)
    ]
    db.add_all(rows)
    db.flush()

    after = protected_fingerprint(db)
    changed = diff_fingerprints(before, after)
    if changed:
        db.rollback()
        raise ProtectedStateViolation("candidate training changed protected state: " + "; ".join(changed))
    db.commit()
    return CandidateRecord(model.id, version, model_name, spec, target, origin, target_season, len(rows), False, snapshot.snapshot_id)


def _clean(value) -> float | None:  # type: ignore[no-untyped-def]
    return None if value is None or (isinstance(value, float) and np.isnan(value)) else float(value)


def list_candidates(db: Session) -> list[dict]:
    rows = db.scalars(
        select(ModelVersion).where(ModelVersion.status.in_(("candidate", "rejected"))).order_by(ModelVersion.trained_at)
    ).all()
    return [
        {
            "model_name": m.model_name, "version": m.version, "status": m.status, "target": m.target,
            "trained_at": m.trained_at, "data_snapshot_id": m.data_snapshot_id,
            "evaluation": (m.validation_metrics or {}).get("evaluation_status"),
        }
        for m in rows
    ]


def reject_candidate(db: Session, version: str, reason: str) -> None:
    """Mark a candidate rejected. Touches that one row only; never an active or retired model."""
    model = db.scalar(select(ModelVersion).where(ModelVersion.version == version))
    if model is None or model.status != "candidate":
        raise CandidateError(f"{version!r} is not a candidate")
    model.status = "rejected"
    model.lineage = {**(model.lineage or {}), "rejected_reason": reason, "rejected_at": datetime.now(UTC).isoformat()}
    db.commit()


def promotion_blockers(db: Session, version: str) -> list[str]:
    """Why a candidate cannot be promoted today. R9.1 builds no promotion path, by design."""
    model = db.scalar(select(ModelVersion).where(ModelVersion.version == version))
    if model is None or model.status != "candidate":
        return [f"{version!r} is not a candidate"]
    blockers = ["no audited promotion procedure exists yet (R9.1 trains and evaluates; it does not promote)"]
    metrics = model.validation_metrics or {}
    if metrics.get("evaluation_status") != "holdout_confirmed":
        blockers.append("not confirmed on the locked holdout")
    blockers.append(
        "the serving tables (player_impact_estimates) are keyed to the TEI index scale and carry "
        "offence/defence components a forecast does not have; a promotion plan must decide the mapping"
    )
    blockers.append("the comparable, acquisition and adversarial validation batteries have not been re-run against it")
    return blockers


def league_standings(db: Session) -> pd.DataFrame:
    return pd.DataFrame(
        [(s.team_id, s.season, s.win_pct) for s in db.scalars(select(Standing)).all()],
        columns=["team_id", "season", "win_pct"],
    )
