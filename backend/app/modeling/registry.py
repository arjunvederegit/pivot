"""Candidate-only training.

``train_candidate`` is the *only* write path R9.1 adds, and it writes exactly two kinds of row:
one ``model_versions`` row with ``status = 'candidate'`` and ``is_active = False``, and the
candidate's rows in ``candidate_estimates``. It never updates or deletes anything else.

That promise is enforced, not hoped for: the function fingerprints **every table, including every
existing candidate** (all of ``model_versions`` and ``candidate_estimates`` except the rows it is
creating) immediately before it writes, fingerprints them again before it commits, and rolls back and
raises ``ProtectedStateViolation`` if a single byte differs. The expensive computation happens
*before* the first fingerprint, so the window in which an unrelated writer can cause a (safe, rolled
back) false alarm is the few milliseconds of the write itself. The legacy ``make train`` deletes the previous version's impact estimates and
retires every active model; this path cannot, and a regression test fails if that ever changes.

Training and promotion are separate. Nothing here sets ``is_active``; ``promotion_blockers`` lists
what a future, audited promotion step would have to establish.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Base, CandidateEstimate, ModelVersion, PlayerImpactEstimate, Standing

from .intervals import fit_interval, loto_residuals
from .pairs import FeatureConfig, PairCache
from .panel import PlayerSeasonPanel, season_key, season_label
from .predictors import PREDICTORS, FitContext, NotApplicable
from .provenance import DataSnapshot, config_hash, environment
from .targets import get_target

CANDIDATE_MODEL_PREFIX = "player_forecast_"
#: Estimates must be finite before they are stored: PostgreSQL would accept a NaN silently.
MIN_TRAINING_ROWS = 40


class ProtectedStateViolation(RuntimeError):
    """A candidate operation changed state it does not own. It has been rolled back."""


class CandidateError(ValueError):
    """The request cannot be honoured safely."""


def protected_fingerprint(db: Session, exclude_model_id: str | None = None) -> dict[str, dict]:
    """Row count and content hash of every table.

    ``exclude_model_id`` leaves out the one candidate being created (its ``model_versions`` row and
    its ``candidate_estimates``) so that a *new* candidate is not mistaken for tampering — while every
    OTHER candidate, and everything else, is protected."""
    result: dict[str, dict] = {}
    for table in Base.metadata.sorted_tables:
        query = select(*table.c)
        if exclude_model_id is not None:
            if table.name == "model_versions":
                query = query.where(table.c.id != exclude_model_id)
            elif table.name == "candidate_estimates":
                query = query.where(table.c.model_version_id != exclude_model_id)
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


_WRITE_VERBS = frozenset({"INSERT", "UPDATE", "DELETE", "REPLACE", "DROP", "ALTER", "CREATE", "TRUNCATE"})


@contextmanager
def _read_only_computation(db: Session) -> Iterator[None]:
    """Make the session's connection refuse any write while the candidate is being computed.

    Fingerprinting the protected tables around the minutes-long computation would raise a false
    alarm whenever an unrelated process legitimately writes meanwhile. The computation is read-only
    by construction, so that is enforced directly instead: a statement that would modify anything,
    issued on this session's connection, raises before it executes. Other connections (another
    process, another thread) are unaffected."""
    connection = db.connection()

    def guard(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        head = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
        if head in _WRITE_VERBS:
            raise ProtectedStateViolation(f"a write was attempted during read-only computation: {statement.strip()[:160]}")

    event.listen(connection, "before_cursor_execute", guard)
    try:
        yield
    finally:
        event.remove(connection, "before_cursor_execute", guard)


@dataclass
class ForwardFit:
    """A model fitted on every transition observable at ``origin``, with what is needed to use it."""

    fitted: object  # modeling.predictors.Fitted
    predictor: object
    interval: object  # modeling.intervals.IntervalModel
    scoring: pd.DataFrame  # the cutoff's feature rows (one per scored player)
    pred: np.ndarray
    lo: np.ndarray
    hi: np.ndarray
    params: dict
    counts: dict


def forward_fit(
    panel: PlayerSeasonPanel, spec: str, target_name: str, origin: str, features: FeatureConfig,
    seed: int, level: float,
) -> ForwardFit:
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
    if len(train) < MIN_TRAINING_ROWS:
        raise CandidateError(f"{len(train)} training rows at {origin}; at least {MIN_TRAINING_ROWS} are needed")
    ctx = FitContext(target=target, seed=seed)
    try:
        fitted = predictor.fit(train, ctx)
    except NotApplicable as exc:
        raise CandidateError(f"{spec} cannot run on this panel: {exc}") from exc
    scoring = cache.features(origin, target).reset_index(drop=True)
    pred = fitted.predict(scoring)
    if not np.isfinite(pred).all():
        raise CandidateError(f"{spec} produced non-finite estimates for {target_name} at {origin}; nothing was stored")
    try:
        interval = fit_interval(loto_residuals(predictor, train, ctx), level)
    except ValueError as exc:
        raise CandidateError(f"{spec}: cannot calibrate an interval ({exc}); nothing was stored") from exc
    lo, hi = interval.apply(pred, scoring["window_exposure"].to_numpy(dtype=float))
    if not (np.isfinite(lo).all() and np.isfinite(hi).all()):
        raise CandidateError(f"{spec} produced non-finite intervals for {target_name} at {origin}; nothing was stored")
    return ForwardFit(
        fitted, predictor, interval, scoring, pred, lo, hi,
        {"fitted": fitted.params, "interval": interval.params()},
        {"training_pairs": int(len(train)), "training_origins": origins, "scored": int(len(scoring))},
    )


def _forward_estimates(
    panel: PlayerSeasonPanel, spec: str, target_name: str, origin: str, features: FeatureConfig,
    seed: int, level: float,
) -> tuple[pd.DataFrame, dict, dict]:
    ff = forward_fit(panel, spec, target_name, origin, features, seed, level)
    out = ff.scoring.assign(pred=ff.pred, lo=ff.lo, hi=ff.hi)
    ability = ability_table(ff.fitted, ff.scoring, level)
    if ability is not None:
        # R9.2: a model with a posterior of ability carries it as extra columns on the same frame.
        out = out.assign(**{c: ability[c].to_numpy() for c in ABILITY_COLUMNS})
    return out, ff.params, ff.counts


#: Suffix of the ``target`` under which a model's posterior of ability (``inferred``) is stored.
ABILITY_SUFFIX = "@ability"
ABILITY_COLUMNS = (
    "ability", "ability_sd", "ability_lo", "ability_hi", "curve", "reliability", "weight_on_player_record",
    "effective_attempts", "n_observed", "history_exposure",
)


def ability_table(fitted: object, scoring: pd.DataFrame, level: float) -> pd.DataFrame | None:
    """Per-player posterior of ability at the cutoff, for models that have one (the state-space family).

    ``weight_on_player_record`` is the model's actual weight on the player's own observations (the response of the
    posterior mean to a unit shift in all of them) — not the reliability, which equals it only when the cutoff
    season itself is observed. ``effective_attempts`` uses the player's *own* per-attempt variance so that a
    one-season player's evidence is worth about the attempts he took. ``history_exposure`` is the attempts across
    every season the filter used (up to ten), which is what the estimate actually rests on."""
    if not hasattr(fitted, "posterior"):
        return None
    post = fitted.posterior(scoring)  # type: ignore[attr-defined]
    p = fitted.p  # type: ignore[attr-defined]
    extras = fitted.ability_extras(scoring)  # type: ignore[attr-defined]
    from app.player_intelligence import rate_models as rm

    lo, hi = post.credible_interval("now", level)
    rel = rm.reliability(post.var_now, p.tau2)
    out = pd.DataFrame(
        {
            "player_id": scoring["player_id"].to_numpy(),
            "ability": post.level_now, "ability_sd": np.sqrt(post.var_now), "ability_lo": lo, "ability_hi": hi,
            "curve": post.curve_now, "reliability": rel, "weight_on_player_record": np.clip(extras["weight"], 0.0, 1.0),
            "effective_attempts": rm.effective_attempts(post.var_now, p.tau2, p.phi, extras["unit_variance"]),
            "n_observed": post.n_observed.astype(float),
            "history_exposure": extras["history_exposure"],
            "window_exposure": scoring["window_exposure"].to_numpy(dtype=float),
        }
    )
    if not np.isfinite(out.drop(columns=["player_id"]).to_numpy(dtype=float)).all():
        raise CandidateError("the ability posterior is not finite for every player; nothing was stored")
    return out


#: Models and targets that read integer shot counts, which only the R9.2 skill panel carries.
SKILL_PANEL_MODELS = frozenset({"state_space", "state_space_role", "beta_binomial"})
SKILL_PANEL_TARGETS = frozenset({"fg3_pct", "ft_pct"})


def panel_for(db: Session, spec: str, target: str, skill_panel: bool = False) -> PlayerSeasonPanel:
    """The panel a (model, target) pair needs: R9.1's, or R9.2's with shot counts added.

    ``skill_panel=True`` forces R9.2's: R9.2 experiments ran on it, so a baseline served beside them must be
    trained on the same data (same fingerprint), or its stored evaluation would read as stale."""
    if skill_panel or spec in SKILL_PANEL_MODELS or target in SKILL_PANEL_TARGETS:
        from app.player_intelligence.panel import build_skill_panel

        return build_skill_panel(db)
    return PlayerSeasonPanel.from_db(db)


def evaluation_from_experiment(experiment_dir: Path, spec: str, target: str, data_fingerprint: str) -> dict:
    """What an experiment's artifacts actually say about ``spec`` — never more than they say.

    ``data_fingerprint`` is the fingerprint of the full panel as it stands now.

    The status is read from the decision files, not assumed: a candidate that did not advance is
    ``failed_development_rule``, not ``development_only``; a baseline was never a candidate; a
    development run made on different data than the candidate was trained on is ``stale``."""
    try:
        decisions = json.loads((experiment_dir / "decisions.json").read_text())
        config = json.loads((experiment_dir / "config.json").read_text())
        recorded = json.loads((experiment_dir / "data_snapshot.json").read_text())
    except (OSError, ValueError) as exc:
        raise CandidateError(f"cannot read the experiment artifacts in {experiment_dir}: {exc}") from exc
    if decisions.get("target") != target:
        raise CandidateError(f"{experiment_dir.name} evaluated {decisions.get('target')!r}, not {target!r}")
    stage = config.get("stage")
    entry = (decisions.get("candidates") or {}).get(spec)
    if entry is None:
        status = "not_evaluated_as_candidate"
    elif stage == "holdout":
        status = "holdout_confirmed" if entry.get("confirmed") else "holdout_not_confirmed"
    else:
        status = "development_only" if entry.get("advance") else "failed_development_rule"
    if recorded.get("fingerprint") != data_fingerprint:
        status = f"stale:{status}"
    # R9.2: a committed aggregate copy of a run keeps the run's real id in ``run_id.txt``.
    run_id = experiment_dir / "run_id.txt"
    experiment_id = run_id.read_text().strip() if run_id.exists() else experiment_dir.name
    return {
        "evaluation_status": status,
        "experiment": experiment_id,
        "stage": stage,
        "family": decisions.get("family"),
        "decision": entry,
        "experiment_data_fingerprint": recorded.get("fingerprint"),
        "current_data_fingerprint": data_fingerprint,
    }


def _config_matches(experiment_dir: Path, config: dict) -> bool:
    """Did the experiment evaluate the same configuration this candidate is trained under?"""
    try:
        recorded = json.loads((experiment_dir / "config.json").read_text())["backtest"]
    except (OSError, ValueError, KeyError):
        return False
    return (
        recorded.get("features") == config["features"]
        and recorded.get("seed") == config["seed"]
        and recorded.get("interval_level") == config["interval_level"]
    )


#: Evaluation fields that assert WHERE a model's evidence came from. Only `evaluation_from_experiment` may set them.
PROVENANCE_KEYS = ("experiment", "config_matches", "experiment_data_fingerprint", "current_data_fingerprint")
ARTIFACT_SCHEMA = "r9.2-3"
#: Part of every candidate's configuration, hence of its version id. Bump it whenever what a candidate STORES changes
#: (a column's meaning, a new input key): a store written by older code then gets a new version instead of being
#: "reused" under an id that no longer describes the rows.

_NUMERIC_COLUMNS = ("estimate", "interval_low", "interval_high", "interval_level", "exposure")
_TEXT_COLUMNS = ("output_kind", "season", "target_season")  # what the row means and which period it describes


def _estimate_rows(
    model_id: str, scoring: pd.DataFrame, ability: pd.DataFrame | None, origin: str, target: str, target_season: str, level: float
) -> list[CandidateEstimate]:
    """Every row a candidate stores — the one place they are built, for writing AND for the reuse comparison, so the two
    can never describe different things."""
    rows = [
        CandidateEstimate(
            model_version_id=model_id,
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
            inputs={"x_t": _clean(r.x_t), "age": _clean(r.age), "window_minutes": _clean(r.window_minutes)}
            | {k: _clean(getattr(r, k)) for k in ("history_exposure", "n_observed") if hasattr(r, k)},
        )
        for r in scoring.itertuples(index=False)
    ]
    if ability is not None:
        # R9.2: a model that carries a posterior of ability stores it as `inferred`, beside (never instead of) its forecast.
        rows += [
            CandidateEstimate(
                model_version_id=model_id,
                player_id=str(r.player_id),
                season=origin,
                target_season=origin,
                target=f"{target}{ABILITY_SUFFIX}",
                output_kind="inferred",
                estimate=float(r.ability),
                interval_low=float(r.ability_lo),
                interval_high=float(r.ability_hi),
                interval_level=level,
                exposure=float(r.history_exposure),
                inputs={
                    k: _clean(getattr(r, k))
                    for k in ("ability_sd", "curve", "reliability", "weight_on_player_record", "effective_attempts", "n_observed", "window_exposure")
                } | {"interval_kind": "credible"},
            )
            for r in ability.itertuples(index=False)
        ]
    return rows


def _row_differences(stored: list[CandidateEstimate], expected: list[CandidateEstimate]) -> list[str]:
    """Why a stored candidate's rows are not what retraining now produces (empty = identical, to 1e-9)."""
    by_key = {(e.player_id, e.target): e for e in stored}
    want = {(e.player_id, e.target): e for e in expected}
    problems: list[str] = []
    if by_key.keys() != want.keys():
        problems.append(f"the set of stored rows differs ({len(by_key)} stored, {len(want)} expected)")
        return problems
    for key, new in want.items():
        old = by_key[key]
        for col in _NUMERIC_COLUMNS:
            a, b = getattr(old, col), getattr(new, col)
            if (a is None) != (b is None) or (a is not None and abs(float(a) - float(b)) > 1e-9):
                problems.append(f"{key[1]} {col} differs for a player ({a} stored, {b} now)")
        for col in _TEXT_COLUMNS:
            a, b = getattr(old, col), getattr(new, col)
            if str(getattr(a, "value", a)) != str(getattr(b, "value", b)):
                problems.append(f"{key[1]} {col} differs for a player ({a} stored, {b} now)")
        old_inputs, new_inputs = old.inputs or {}, new.inputs or {}
        if old_inputs.keys() != new_inputs.keys():
            problems.append(f"{key[1]} inputs keys differ ({sorted(set(old_inputs) ^ set(new_inputs))})")
        else:
            for k, v in new_inputs.items():
                o = old_inputs[k]
                same = (o is None and v is None) or (isinstance(v, (int, float)) and isinstance(o, (int, float)) and abs(float(o) - float(v)) <= 1e-9) or o == v
                if not same:
                    problems.append(f"{key[1]} input {k!r} differs for a player ({o} stored, {v} now)")
        if len(problems) >= 5:
            break
    return problems


def train_candidate(
    db: Session,
    spec: str,
    target: str,
    origin: str,
    *,
    seed: int = 20260719,
    level: float = 0.8,
    features: FeatureConfig | None = None,
    experiment_dir: Path | None = None,
    evaluation: dict | None = None,
    skill_panel: bool = False,
) -> CandidateRecord:
    """Train a candidate and persist it — and nothing else — under ``status = 'candidate'``.

    ``experiment_dir`` (a development or holdout run) is read to record, truthfully, how the model
    fared. ``evaluation`` is accepted for callers that already hold that record — but the provenance
    fields (which experiment, whether the configuration matched, the data fingerprints) are only ever
    set from ``experiment_dir``; a caller-supplied dict cannot assert them."""
    features = features or FeatureConfig()
    if evaluation is not None:
        # A caller can neither assert where evidence came from nor claim a status: it may only record that a model
        # was NOT confirmed (a failure) or was never evaluated. Positive evidence comes only from `experiment_dir`.
        evaluation = {k: v for k, v in evaluation.items() if k not in PROVENANCE_KEYS}
        if evaluation.get("evaluation_status") not in ("failed", "unevaluated"):
            evaluation["evaluation_status"] = "unevaluated"
    if spec not in PREDICTORS:
        raise CandidateError(f"unknown model {spec!r}; registered: {sorted(PREDICTORS)}")
    if db.new or db.deleted or any(db.is_modified(o) for o in db.dirty):
        raise CandidateError(
            "the session holds uncommitted changes; commit or roll them back first (a protected-state "
            "violation rolls the whole session back, which would discard them)"
        )

    # 1. Everything that is expensive or can fail happens BEFORE any write — under a read-only guard.
    try:
        with _read_only_computation(db):
            panel = panel_for(db, spec, target, skill_panel=skill_panel)
            if origin not in panel.seasons:
                raise CandidateError(f"no statistics for {origin}; ingested seasons: {panel.seasons}")
            scoring, params, counts = _forward_estimates(panel, spec, target, origin, features, seed, level)
            ability = scoring if "ability" in scoring.columns else None
            snapshot = DataSnapshot.of_panel(panel.as_of(origin).frame)
            target_season = season_label(season_key(origin) + 1)
            if experiment_dir is not None:
                # Staleness is judged against the data as it is NOW (the whole panel the experiment saw),
                # not against the possibly earlier cutoff this candidate was trained at.
                evaluation = evaluation_from_experiment(
                    experiment_dir, spec, target, DataSnapshot.of_panel(panel.frame).fingerprint
                )
    except BaseException:
        db.rollback()
        raise

    config = {
        "spec": spec, "target": target, "origin": origin, "target_season": target_season,
        "seed": seed, "interval_level": level, "features": features.as_dict(),
        "model_spec": PREDICTORS[spec].spec(), "artifact_schema": ARTIFACT_SCHEMA,
    }
    if experiment_dir is not None and evaluation is not None:
        # Whether the candidate was trained under the configuration the experiment evaluated (window, minute floors,
        # seed, interval level). Registry evidence is only attached to a stored model that matches it.
        matches = _config_matches(experiment_dir, config)
        evaluation = {**evaluation, "config_matches": matches}
        if not matches:
            # The experiment evaluated a different configuration: its verdict says nothing about THIS model. A failure
            # still stands (stale/failed can only lower a status); a confirmation does not carry over.
            if evaluation.get("evaluation_status") in ("holdout_confirmed", "development_only"):
                evaluation["evaluation_status"] = "unevaluated"
            evaluation["evaluation_note"] = "the experiment evaluated a different configuration than this model was trained under"
    version = f"cand-{spec}-{target}-{config_hash({**config, 'data': snapshot.fingerprint})[:12]}"
    model_name = f"{CANDIDATE_MODEL_PREFIX}{target}"
    estimates = {str(r.player_id): float(r.pred) for r in scoring.itertuples(index=False)}

    existing = db.scalar(
        select(ModelVersion).where(ModelVersion.model_name == model_name, ModelVersion.version == version)
    )
    if existing is not None:
        return _reuse(db, existing, scoring, ability, evaluation if experiment_dir is not None else None, version, model_name, spec, target, origin, target_season, snapshot, level)

    active = db.scalars(
        select(ModelVersion).where(ModelVersion.model_name == "player_impact", ModelVersion.is_active)
    ).first()
    env = environment()

    # 2. Write, with the guard tightly around the write.
    before = protected_fingerprint(db)
    try:
        model = ModelVersion(
            model_name=model_name,
            version=version,
            algorithm=PREDICTORS[spec].description[:200],
            training_period=f"{panel.seasons[0]}..{origin}",
            feature_list=list(PREDICTORS[spec].inputs),
            target=target,
            validation_metrics=evaluation or {"evaluation_status": "unevaluated"},
            artifact_path=None,
            trained_at=datetime.now(UTC),
            code_commit=env["code"]["commit"],
            data_snapshot_id=snapshot.snapshot_id,
            is_active=False,
            status="candidate",
            config=config,
            lineage={
                "data_snapshot": snapshot.as_dict(),
                "environment": env,
                "seed": seed,
                "fit": params,
                "counts": counts,
                "compared_against_active_version": active.version if active else None,
                "promotion": "not promoted; see promotion_blockers()",
            },
        )
        db.add(model)
        db.flush()
        db.add_all(_estimate_rows(model.id, scoring, ability, origin, target, target_season, level))
        db.flush()
        _assert_isolated(db, model)
        changed = diff_fingerprints(before, protected_fingerprint(db, exclude_model_id=model.id))
        if changed:
            raise ProtectedStateViolation("candidate training changed protected state: " + "; ".join(changed))
        db.commit()
    except IntegrityError:
        # Another process created the same version between our check and our write (the unique constraint caught it).
        # Nothing of ours was kept: reuse the winner's candidate, after comparing every stored column.
        db.rollback()
        winner = db.scalar(select(ModelVersion).where(ModelVersion.model_name == model_name, ModelVersion.version == version))
        if winner is None:
            raise
        return _reuse(db, winner, scoring, ability, evaluation if experiment_dir is not None else None, version, model_name, spec, target, origin, target_season, snapshot, level)
    except BaseException:
        db.rollback()
        raise
    return CandidateRecord(model.id, version, model_name, spec, target, origin, target_season, len(estimates), False, snapshot.snapshot_id)


def _assert_isolated(db: Session, model: ModelVersion) -> None:
    """Post-write invariants the fingerprint cannot express: the new row must be unreachable by serving."""
    if model.is_active or model.status != "candidate" or not model.model_name.startswith(CANDIDATE_MODEL_PREFIX):
        raise ProtectedStateViolation(
            f"the new model is not isolated (is_active={model.is_active}, status={model.status}, name={model.model_name})"
        )
    leaked = db.scalar(
        select(PlayerImpactEstimate.id).where(PlayerImpactEstimate.model_version_id == model.id).limit(1)
    )
    if leaked is not None:
        raise ProtectedStateViolation("a serving estimate references the candidate model")


def _reuse(
    db: Session, existing: ModelVersion, scoring: pd.DataFrame, ability: pd.DataFrame | None, evaluation: dict | None,
    version: str, model_name: str, spec: str, target: str, origin: str, target_season: str,
    snapshot: DataSnapshot, level: float,
) -> CandidateRecord:
    """The same specification on the same data is the same candidate — but only if it still produces
    the same numbers. If it does not (the code or a library changed), say so rather than return
    stale rows under an unchanged id. Nothing is deleted or rewritten except, when asked, this
    candidate's own evaluation record — and that only after EVERY stored column (estimate, interval,
    exposure, every input) has been compared with what retraining produces now."""
    stored = list(db.scalars(select(CandidateEstimate).where(CandidateEstimate.model_version_id == existing.id)).all())
    problems = _row_differences(stored, _estimate_rows(existing.id, scoring, ability, origin, target, target_season, level))
    if problems:
        raise CandidateError(
            f"{version} exists but retraining now gives different estimates: {'; '.join(problems)}. The code or environment "
            "changed since it was trained. Nothing was modified; change the seed or configuration (or the artifact "
            "schema) to create a new candidate, or investigate the difference."
        )
    if evaluation is not None and existing.validation_metrics != evaluation:
        before = protected_fingerprint(db, exclude_model_id=existing.id)
        try:
            existing.validation_metrics = evaluation
            db.flush()
            changed = diff_fingerprints(before, protected_fingerprint(db, exclude_model_id=existing.id))
            if changed:
                raise ProtectedStateViolation("recording an evaluation changed protected state: " + "; ".join(changed))
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return CandidateRecord(existing.id, version, model_name, spec, target, origin, target_season, len([e for e in stored if e.target == target]), True, snapshot.snapshot_id)


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
