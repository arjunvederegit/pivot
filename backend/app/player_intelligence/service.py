"""``PlayerIntelligenceService`` — the one place player intelligence is retrieved from.

    service.get(player_id, quantities, as_of, kinds, model_version, include_experimental)

Every consumer (an API route today; Player Explorer, Team Outlook, Trade Evaluator, Strategy Lab and
Ask Pivot in R9.3) gets the same typed ``PlayerIntelligenceRecord`` for the same question, so no tool
recomputes a statistic and no two tools can disagree about one.

What the service does, and does not do:

* **Read-only.** It never trains, writes or publishes. Inferred and projected values are read from the
  candidate store (``candidate_estimates``) that ``train-candidate`` fills under R9.1's isolation
  guarantees; a cutoff with no stored model is reported as unavailable, never computed on the fly.
* **Measured by default.** Measured values need only data integrity. Inferred and projected values are
  returned only when the caller asks for those kinds, and a model that is still a *candidate* is
  returned only with ``include_experimental=True`` — and then labelled as one (``model.status``,
  ``model.evaluation_status``) on every record.
* **Refusals are part of the answer.** Anything not returned comes back as an ``Unavailable`` with
  the reason (no attempts, not in the scored population, not validated, dimension not supported), so
  a consumer can show "Pivot cannot see this" rather than an empty cell.
* **As-of.** ``as_of`` is an information cutoff (a season label). Nothing after it is read: measured
  rows are filtered to ``<= as_of`` and stored models are chosen by the cutoff they were trained at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, defer

from app.canonical.seasons import InvalidSeasonError, Season
from app.core.errors import DomainError, NotFoundError
from app.db.models import (
    CandidateEstimate,
    ModelVersion,
    Player,
    PlayerArchetype,
    PlayerSeasonStats,
)
from app.modeling.contract import (
    EvaluationStatus,
    Interval,
    ModelRef,
    OutputKind,
    Period,
    PlayerIntelligenceRecord,
    Provenance,
    Reliability,
    Sample,
    SourceRef,
)
from app.modeling.predictors import PREDICTORS
from app.modeling.registry import ABILITY_SUFFIX, CANDIDATE_MODEL_PREFIX
from app.modeling.targets import get_target

from .measured import SeasonRow, Unavailable, load_season_rows, measured_for
from .quantities import DIMENSIONS, QUANTITIES, UNAVAILABLE_DIMENSIONS
from .validation import (
    TIER_WITHHELD,
    KindFacts,
    ValidationRegistry,
    load_registry,
    status_from_stored,
)

ALL_KINDS = (OutputKind.MEASURED, OutputKind.INFERRED, OutputKind.PROJECTED)
#: Inferred ability is withheld below this reliability. Chosen from `results/selection_check.json` (development folds):
#: for players who took any attempt the next season, the model over-predicted by +1.2 to +3.5 points in every reliability bin
#: below 0.30 for three-point and free-throw accuracy and by < 0.6 points in every bin above it. Volume carries information
#: about ability (coaches let good shooters shoot) that a model treating attempts only as precision cannot use, so a
#: low-evidence estimate is mostly the league curve — and the curve is too high for low-volume players.
INFERRED_MIN_RELIABILITY = 0.30


class InvalidRequestError(DomainError):
    status_code = 422
    code = "invalid_request"


@dataclass(frozen=True)
class IntelligenceResponse:
    player_id: str
    as_of: str | None
    requested: dict[str, Any]
    records: list[PlayerIntelligenceRecord]
    unavailable: list[Unavailable]
    context: dict[str, Any]
    notices: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "as_of": self.as_of,
            "requested": self.requested,
            "context": self.context,
            "records": [r.model_dump(mode="json") for r in self.records],
            "reliability_statements": {
                f"{r.quantity}:{r.output_kind.value}": r.reliability_statement() for r in self.records
            },
            "unavailable": [u.as_dict() for u in self.unavailable],
            "notices": self.notices,
        }


def serving_decision(model_status: str, evaluation: EvaluationStatus, include_experimental: bool) -> tuple[bool, str]:
    """May a stored model's output be returned, and on what terms? One rule, applied everywhere.

    * ``active`` + ``holdout_confirmed``: returned as validated (nothing qualifies in R9.2: promotion is manual).
    * ``candidate``: returned only on request, always labelled.
    * ``rejected`` / ``retired``: never.
    * an evaluation that ``failed``: never, even on request — an estimate whose method was shown not to
      meet the pre-registered bar is not served to anyone as intelligence.
    """
    if model_status in ("rejected", "retired"):
        return False, f"the model is {model_status}"
    if evaluation is EvaluationStatus.FAILED:
        return False, "the model failed its pre-registered evaluation"
    if model_status == "active" and evaluation is EvaluationStatus.HOLDOUT_CONFIRMED:
        return True, "validated"
    if model_status == "candidate":
        if include_experimental:
            return True, "experimental"
        return False, "an experimental (candidate) model output; pass include_experimental=true to see it, labelled as such"
    return False, f"model status {model_status!r} with evaluation {evaluation.value!r} is not servable"


class PlayerIntelligenceService:
    def __init__(self, db: Session, registry: ValidationRegistry | None = None) -> None:
        self.db = db
        self.registry = registry or load_registry()
        #: Set only inside ``get_many``: a batch resolves each model version once. A single ``get`` never caches, so
        #: a model stored (or rejected) a moment ago is always seen.
        self._cache: dict[tuple, Any] | None = None

    # ------------------------------------------------------------------ catalog

    def catalog(self) -> dict[str, Any]:
        """What the engine offers, per dimension and quantity, and what it declines to offer."""
        out: dict[str, Any] = {"validation_registry": self.registry.version, "dimensions": {}, "unavailable": {}}
        for key, dim in DIMENSIONS.items():
            quantities = []
            for qid in dim["quantities"]:
                spec = QUANTITIES[qid]
                kinds: dict[str, dict[str, Any]] = {}
                for kind in spec.kinds:
                    if kind is OutputKind.MEASURED:
                        kinds[kind.value] = {"tier": "measured"}
                    else:
                        f = self.registry.facts(qid, kind.value)
                        kinds[kind.value] = {"tier": f.tier, "method": f.method, "evaluation_status": f.evaluation_status.value, "reason": f.reason}
                for k in (OutputKind.INFERRED, OutputKind.PROJECTED):
                    if k not in spec.kinds:
                        kinds[k.value] = {"tier": "not_offered", "reason": spec.not_offered.get(k.value, "not offered")}
                quantities.append({"id": qid, "label": spec.label, "unit": spec.unit, "definition": spec.definition, "source": spec.source, "kinds": kinds})
            out["dimensions"][key] = {"label": dim["label"], "status": dim["status"], "quantities": quantities}
        out["unavailable"] = UNAVAILABLE_DIMENSIONS
        return out

    # ------------------------------------------------------------------ retrieval

    def latest_season(self) -> str | None:
        seasons = self.db.scalars(
            select(PlayerSeasonStats.season).where(PlayerSeasonStats.season_type == "regular").distinct()
        ).all()
        parsed = []
        for s in seasons:
            try:
                parsed.append((Season.parse(s).start_year, s))
            except InvalidSeasonError:
                continue
        return max(parsed)[1] if parsed else None

    def get(
        self,
        player_id: str,
        quantities: list[str] | None = None,
        as_of: str | None = None,
        kinds: list[str] | tuple[str, ...] | None = None,
        model_version: str | None = None,
        include_experimental: bool = False,
    ) -> IntelligenceResponse:
        player = self.db.get(Player, player_id)
        if player is None:
            raise NotFoundError(f"no player with id {player_id!r}")
        wanted_kinds = self._kinds(kinds)
        if as_of is not None:
            try:
                Season.parse(as_of)
            except InvalidSeasonError as exc:
                raise InvalidRequestError(f"as_of must be a season like '2024-25': {exc}") from exc
        cutoff = as_of or self.latest_season()
        names = list(quantities) if quantities else [q for q, spec in QUANTITIES.items() if OutputKind.MEASURED in spec.kinds]

        records: list[PlayerIntelligenceRecord] = []
        unavailable: list[Unavailable] = []
        real: list[str] = []
        for q in dict.fromkeys(names):
            if q in QUANTITIES:
                real.append(q)
            elif q in UNAVAILABLE_DIMENSIONS:
                info = UNAVAILABLE_DIMENSIONS[q]
                unavailable.append(Unavailable(q, "*", info["reason"], info["requirement"]))
            else:
                raise InvalidRequestError(f"unknown quantity {q!r}; known: {sorted(QUANTITIES)} and {sorted(UNAVAILABLE_DIMENSIONS)}")

        rows: dict[str, SeasonRow] = load_season_rows(self.db, player_id, cutoff) if cutoff else {}
        if OutputKind.MEASURED in wanted_kinds and cutoff:
            recs, missing = measured_for(player_id, real, rows, cutoff)
            records += recs
            unavailable += missing
        for kind in (OutputKind.INFERRED, OutputKind.PROJECTED):
            if kind not in wanted_kinds:
                continue
            for q in real:
                self._model_record(player_id, q, kind, cutoff, rows, model_version, include_experimental, records, unavailable)

        notices = []
        if include_experimental and any(r.output_kind is not OutputKind.MEASURED for r in records):
            notices.append("inferred and projected values below come from candidate models: they are experimental, not validated or promoted")
        return IntelligenceResponse(
            player_id=player_id,
            as_of=cutoff,
            requested={"quantities": names, "kinds": [k.value for k in wanted_kinds], "model_version": model_version, "include_experimental": include_experimental},
            records=records,
            unavailable=unavailable,
            context=self._context(player, cutoff, rows),
            notices=notices,
        )

    def get_many(
        self,
        player_ids: list[str],
        quantities: list[str] | None = None,
        as_of: str | None = None,
        kinds: list[str] | tuple[str, ...] | None = None,
        model_version: str | None = None,
        include_experimental: bool = False,
    ) -> dict[str, IntelligenceResponse]:
        """The same question for several players (a roster, a trade's assets): one answer per player.

        Deliberately a loop over ``get``: every consumer, one player or fifteen, goes through the same code, so
        a roster view and a player view can never disagree about a number. ``as_of`` is resolved once so the
        whole batch shares one information cutoff."""
        wanted = list(dict.fromkeys(player_ids))
        known = set(self.db.scalars(select(Player.id).where(Player.id.in_(wanted))).all()) if wanted else set()
        unknown = [p for p in wanted if p not in known]
        if unknown:
            raise NotFoundError(f"no such player id(s): {unknown}; nothing was computed")
        cutoff = as_of or self.latest_season()
        self._cache = {}  # versions and ability presence are resolved once per (quantity, cutoff) for the whole batch
        try:
            return {pid: self.get(pid, quantities, cutoff, kinds, model_version, include_experimental) for pid in wanted}
        finally:
            self._cache = None

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _kinds(kinds: list[str] | tuple[str, ...] | None) -> tuple[OutputKind, ...]:
        if not kinds:
            return (OutputKind.MEASURED,)
        out = []
        for k in kinds:
            try:
                out.append(OutputKind(str(k)))
            except ValueError as exc:
                raise InvalidRequestError(f"unknown output kind {k!r}; use measured, inferred or projected") from exc
        return tuple(dict.fromkeys(out))

    def _context(self, player: Player, cutoff: str | None, rows: dict[str, SeasonRow]) -> dict[str, Any]:
        ctx: dict[str, Any] = {"player": {"id": player.id, "name": player.full_name}}
        if cutoff and cutoff in rows:
            row = rows[cutoff]
            age = row.base.get("AGE")
            ctx["season"] = {
                "season": cutoff, "season_type": "regular", "team_id": row.team_id, "age": age,
                "team_count": row.team_count, "games": row.games, "minutes": row.total_minutes,
            }
            arche = self.db.scalar(select(PlayerArchetype).where(PlayerArchetype.player_id == player.id, PlayerArchetype.season == cutoff))
            if arche is not None:
                ctx["role_descriptor"] = {
                    "label": arche.label, "role_id": arche.role_id, "season": cutoff,
                    "note": "a rule-based descriptor of how the player was used, not a measure of ability",
                }
        return ctx

    def _versions(self, quantity: str) -> list[ModelVersion]:
        key = ("versions", quantity)
        if self._cache is not None and key in self._cache:
            return self._cache[key]  # type: ignore[no-any-return]
        rows = list(
            self.db.scalars(
                select(ModelVersion).where(ModelVersion.model_name == f"{CANDIDATE_MODEL_PREFIX}{quantity}").options(defer(ModelVersion.lineage))
            ).all()
        )
        if self._cache is not None:
            self._cache[key] = rows
        return rows

    def _has_ability(self, version_id: str, quantity: str, cutoff: str) -> bool:
        key = ("ability", version_id, cutoff)
        if self._cache is not None and key in self._cache:
            return bool(self._cache[key])
        found = bool(
            self.db.scalar(
                select(
                    exists().where(
                        CandidateEstimate.model_version_id == version_id, CandidateEstimate.season == cutoff,
                        CandidateEstimate.target == f"{quantity}{ABILITY_SUFFIX}",
                    )
                )
            )
        )
        if self._cache is not None:
            self._cache[key] = found
        return found

    def _resolve_version(
        self, quantity: str, kind: OutputKind, cutoff: str, method: str | None, model_version: str | None
    ) -> tuple[ModelVersion | None, str | None]:
        """The stored model that answers this question, or the reason there is none."""
        versions = self._versions(quantity)
        if model_version is not None:
            named = next((v for v in versions if v.version == model_version), None)
            if named is None:
                return None, f"no stored model version {model_version!r} exists for {quantity}"
            trained_at = (named.config or {}).get("origin")
            if trained_at != cutoff:
                return None, f"model version {model_version!r} was trained at cutoff {trained_at}, not {cutoff}"
            if kind is OutputKind.INFERRED and not self._has_ability(named.id, quantity, cutoff):
                return None, f"model version {model_version!r} stores no ability estimate"
            return named, None
        fits = [
            v for v in versions
            if (v.config or {}).get("origin") == cutoff and (v.config or {}).get("spec") == method and v.status in ("candidate", "active")
        ]
        if kind is OutputKind.INFERRED:
            fits = [v for v in fits if self._has_ability(v.id, quantity, cutoff)]
        if not fits:
            return None, (
                f"no stored {method or 'model'} output for cutoff {cutoff}: it has not been materialised "
                "(a read never trains a model); run `make materialise-skills` for this cutoff on a copy of the database"
            )
        return max(fits, key=lambda v: v.trained_at or datetime.min), None

    def _registry_applies(self, version: ModelVersion, facts: KindFacts, quantity: str) -> bool:
        """May the registry's evidence (evaluation status, reference, calibration) be attached to this stored model?

        Only if it IS the model the experiments evaluated: same method, evaluated against one of the registry's source
        runs, trained under the configuration the experiment used, on the data the experiment saw, and not stale. A
        stored model that merely shares a method name — trained on other data, or never evaluated — gets its own
        stored status, not borrowed evidence."""
        vm = version.validation_metrics or {}
        raw = vm.get("evaluation_status")
        if isinstance(raw, str) and raw.startswith("stale:"):
            return False
        return bool(
            facts.method == (version.config or {}).get("spec")
            and vm.get("experiment") in self.registry.run_ids(quantity)
            and vm.get("config_matches") is True
            and vm.get("experiment_data_fingerprint") == self.registry.data_fingerprint(quantity)
        )

    def _model_record(
        self, player_id: str, quantity: str, kind: OutputKind, cutoff: str | None, rows: dict[str, SeasonRow],
        model_version: str | None, include_experimental: bool,
        records: list[PlayerIntelligenceRecord], unavailable: list[Unavailable],
    ) -> None:
        spec = QUANTITIES[quantity]
        k = kind.value
        if kind not in spec.kinds:
            unavailable.append(Unavailable(quantity, k, spec.not_offered.get(k, "this output kind is not offered for this quantity")))
            return
        if cutoff is None:
            unavailable.append(Unavailable(quantity, k, "no statistics are ingested"))
            return
        facts = self.registry.facts(quantity, k)
        if facts.tier == TIER_WITHHELD:
            # Withheld means nobody — naming a model version does not change what the evidence supports.
            unavailable.append(Unavailable(quantity, k, f"withheld: {facts.reason}", season=cutoff))
            return
        version, missing = self._resolve_version(quantity, kind, cutoff, facts.method, model_version)
        if version is None:
            unavailable.append(Unavailable(quantity, k, missing or "no stored model", season=cutoff))
            return
        stored_raw = (version.validation_metrics or {}).get("evaluation_status")
        stored_eval = status_from_stored(stored_raw)
        applies = self._registry_applies(version, facts, quantity)
        evaluation = facts.evaluation_status if applies else stored_eval
        # A stored 'failed' (the candidate did not advance, or was not confirmed, as a FORECASTER) blocks its projections,
        # and a stale 'failed' stays failed. It does not block an ability estimate: that is governed by its own
        # pre-registered serving rule, already applied by the registry (non-inferiority of the model's forecasts and
        # calibration of its predictive interval).
        if stored_eval is EvaluationStatus.FAILED and kind is OutputKind.PROJECTED:
            evaluation = EvaluationStatus.FAILED
        allowed, why = serving_decision(version.status, evaluation, include_experimental)
        if not allowed:
            unavailable.append(Unavailable(quantity, k, why, season=cutoff))
            return
        target = quantity if kind is OutputKind.PROJECTED else f"{quantity}{ABILITY_SUFFIX}"
        est = self.db.scalar(
            select(CandidateEstimate).where(
                CandidateEstimate.model_version_id == version.id, CandidateEstimate.player_id == player_id,
                CandidateEstimate.season == cutoff, CandidateEstimate.target == target,
            )
        )
        if est is None:
            unavailable.append(Unavailable(
                quantity, k,
                "the player is not in this model's scored population at the cutoff "
                "(at least 200 minutes over the three seasons ending at the cutoff)",
                season=cutoff,
            ))
            return
        inputs = est.inputs or {}
        evidence = inputs.get("history_exposure", est.exposure) or 0.0
        observed = inputs.get("n_observed")
        if evidence <= 0 or (observed is not None and observed <= 0):
            # No attempts in any season the model could see: the stored value is the league curve, which is not an
            # estimate of *this* player. Returning it as one (even with reliability 0) would invent a rating from nothing.
            unavailable.append(Unavailable(
                quantity, k,
                f"no {get_target(quantity).exposure_unit} in any season the model could see, so there is nothing to infer from "
                "(the league curve is not an estimate of this player)",
                season=cutoff,
            ))
            return
        if kind is OutputKind.INFERRED and (inputs.get("reliability") or 0.0) < INFERRED_MIN_RELIABILITY:
            unavailable.append(Unavailable(
                quantity, k,
                f"reliability {(inputs.get('reliability') or 0.0):.2f} is below the serving floor of {INFERRED_MIN_RELIABILITY:.2f}: with this little "
                "evidence the estimate would be mostly the league curve, and that curve over-predicts low-volume players "
                "(docs/r9-2/shooting-intelligence-methodology.md §7). The measured value is still available",
                requirement="more attempts (the estimate becomes servable as evidence accumulates)",
                season=cutoff,
            ))
            return
        records.append(self._build_record(player_id, quantity, kind, version, est, facts, evaluation, rows.get(cutoff), applies))

    def _build_record(
        self, player_id: str, quantity: str, kind: OutputKind, version: ModelVersion, est: CandidateEstimate,
        facts: KindFacts, evaluation: EvaluationStatus, row: SeasonRow | None, registry_applies: bool,
    ) -> PlayerIntelligenceRecord:
        spec = QUANTITIES[quantity]
        cutoff = est.season
        cfg = version.config or {}
        method = cfg.get("spec", "?")
        predictor = PREDICTORS.get(method)
        target = get_target(quantity)
        model = ModelRef(
            name=version.model_name, version=version.version, status=version.status,  # type: ignore[arg-type]
            evaluation_status=evaluation,
            evaluation_ref=facts.evaluation_ref if registry_applies else (version.validation_metrics or {}).get("experiment"),
            methodology=predictor.description if predictor else method,
            feature_definitions=[f"{c}: {target.description}" if c == "x_t" else c for c in (predictor.inputs if predictor else ())],
        )
        lineage = version.lineage or {}
        provenance = Provenance(
            sources=[SourceRef(provider="nba_api", dataset="leaguedashplayerstats/base+advanced")],
            data_snapshot_id=version.data_snapshot_id, code_commit=version.code_commit,
            reproducibility={
                "seed": cfg.get("seed"), "training_window": version.training_period, "config": cfg,
                "fitted": (lineage.get("fit") or {}).get("fitted"), "interval": (lineage.get("fit") or {}).get("interval"),
                "training_counts": lineage.get("counts"),
            },
        )
        inputs = est.inputs or {}
        if kind is OutputKind.INFERRED:  # the evidence the posterior rests on: every season the filter used
            attempts, unit = est.exposure, f"{target.exposure_unit} across the seasons the estimate used"
        else:  # the three-season window that decides who is scored and sets the interval's width
            attempts, unit = est.exposure, f"{target.exposure_unit} over the three seasons ending at the cutoff"
        sample = Sample(
            attempts=attempts, unit=unit,
            games=row.games if row else None, minutes=row.total_minutes if row else None,
            seasons_used=int(inputs["n_observed"]) if inputs.get("n_observed") is not None else None,
        )
        lo_bound, hi_bound = spec.bounds if spec.bounds else (-float("inf"), float("inf"))
        evidence_note = (
            []
            if registry_applies
            else [
                "the validation registry's evidence is NOT attached: this stored model was not trained against the committed "
                "experiments (different data, configuration or never evaluated), so only its own stored evaluation is shown"
            ]
        )
        shared_assumptions = [
            spec.definition,
            "the model sees only seasons up to the cutoff; every parameter was estimated on information available then",
        ]
        if kind is OutputKind.INFERRED:
            low, high = est.interval_low, est.interval_high
            assert low is not None and high is not None and est.interval_level is not None
            low, high = max(low, lo_bound), min(high, hi_bound)
            rel = inputs.get("reliability")
            w = inputs.get("weight_on_player_record")
            n_eff = inputs.get("effective_attempts")
            interpretation = (
                f"{rel:.0%} of the spread in true ability between players is resolved by this player's record: the estimate is "
                f"{w:.0%} his own record and {1 - w:.0%} the league curve for his age, equivalent to about {n_eff:.0f} attempts of current-season-quality evidence"
                if rel is not None and w is not None and n_eff is not None else "reliability could not be derived"
            )
            return PlayerIntelligenceRecord(
                player_id=player_id, quantity=quantity, unit=spec.unit, output_kind=OutputKind.INFERRED, value=est.estimate,
                period=Period(season=cutoff, as_of_season=cutoff),
                interval=Interval(
                    low=low, high=high, nominal_level=est.interval_level, kind="credible",
                    method="a Gaussian state-space model (empirical Bayes)",
                    interpretation=f"a range for the player's underlying {spec.label.lower()} at the end of {cutoff}, not for a future observed value",
                ),
                sample=sample, model=model, provenance=provenance,
                reliability=Reliability(
                    reliability=float(rel if rel is not None else 0.0), effective_attempts=n_eff,
                    weight_on_player_record=float(w if w is not None else 0.0), prior_mean=float(inputs.get("curve", est.estimate)),
                    interpretation=interpretation,
                ),
                assumptions=shared_assumptions + [
                    "ability is the level the player's statistic fluctuates around; it can drift between seasons (persistence is estimated)",
                    "attempts are treated as independent trials scaled by a fitted dispersion; shot-quality and context variation is not modelled",
                ],
                limitations=[
                    "ability is latent and was never observed: this is an estimate from box-score evidence, not a measurement",
                    "no role or opponent adjustment beyond the age curve (and, for models that use them, the role covariates listed in the methodology)",
                    "attempts are treated only as precision: that volume itself is information about ability (coaches let good shooters shoot) "
                    "is not used, so for players who shoot rarely the estimate leans on a league curve that is too high",
                    "the league curve the estimate is pulled toward was fitted on players who later qualified for evaluation "
                    f"(at least {int(cfg.get('features', {}).get('min_target_minutes', 500))} minutes"
                    + (f" and {int(target.min_exposure)} {target.exposure_unit}" if target.min_exposure else "")
                    + " in a following season); a player far outside that group is pulled toward a rotation-level average, "
                    "so a low-reliability estimate should be read as 'little is known', not as a measurement",
                    *(facts.limitations if registry_applies else []),
                    *evidence_note,
                ],
            )
        low, high = est.interval_low, est.interval_high
        if low is not None and high is not None:
            low, high = max(low, lo_bound), min(high, hi_bound)
        attr = facts.numbers.get("attrition", {}) if registry_applies else {}
        share = attr.get("share_evaluated_of_scored")
        conditioning = (
            f"the player plays at least {int(cfg.get('features', {}).get('min_target_minutes', 500))} minutes"
            + (f" and takes at least {int(target.min_exposure)} {target.exposure_unit}" if target.min_exposure else "")
            + " in the target season"
        )
        interval = None
        if low is not None and high is not None and est.interval_level is not None:
            interval = Interval(
                low=low, high=high, nominal_level=est.interval_level, kind="prediction",
                method="residual-calibrated: leave-one-transition-out forecast errors, spread modelled by evidence (a + b/attempts), empirical quantile",
                calibration=facts.calibration if registry_applies else None,
                interpretation=f"a range for the player's observed {spec.label.lower()} next season, given {conditioning}",
            )
        return PlayerIntelligenceRecord(
            player_id=player_id, quantity=quantity, unit=spec.unit, output_kind=OutputKind.PROJECTED, value=est.estimate,
            period=Period(season=cutoff, as_of_season=cutoff),
            target_period=Period(season=est.target_season),
            interval=interval, sample=sample, model=model, provenance=provenance,
            conditioning=conditioning,
            assumptions=shared_assumptions + ["next season's observed rate = the player's ability then, plus sampling noise that depends on the attempts he takes"],
            limitations=[
                "a forecast conditional on playing a rotation role; it is not a forecast of whether he will play or of his total contribution"
                + (f" (only {share:.0%} of players scored at a cutoff met this condition the following season)" if share else ""),
                *(facts.limitations if registry_applies else []),
                *evidence_note,
            ],
        )
