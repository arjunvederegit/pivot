"""What would have to be true before a player-intelligence model could be promoted — stated as code, never executed.

R9.2 promotes nothing. This module exists so the gates are explicit and testable, and so that a future promotion
step has one place to extend. ``promotion_readiness`` only *reads*; ``eligible`` is ``False`` for every model in
R9.2 because the last gate, an explicit human approval record, has no implementation yet.

The reversible design (documented in ``docs/r9-2/model-safety-and-promotion.md``) is append-only: promotion would add
a record that names a candidate version and the evidence behind it, never mutate the candidate or any earlier
record; rollback adds a later record that supersedes it. The serving rule in ``service.serving_decision`` already
refuses to return a ``candidate`` as validated, so a promotion that did not change the model's status would change nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ModelVersion
from app.modeling.contract import EvaluationStatus

from .validation import TIER_WITHHELD, ValidationRegistry, load_registry, status_from_stored


@dataclass(frozen=True)
class PromotionReadiness:
    version: str
    eligible: bool
    blockers: list[str] = field(default_factory=list)
    satisfied: list[str] = field(default_factory=list)


def promotion_readiness(db: Session, version: str, registry: ValidationRegistry | None = None) -> PromotionReadiness:
    registry = registry or load_registry()
    model = db.scalar(select(ModelVersion).where(ModelVersion.version == version))
    if model is None or model.status != "candidate":
        return PromotionReadiness(version, False, [f"{version!r} is not a candidate model"])
    blockers: list[str] = []
    satisfied: list[str] = []
    quantity = (model.target or "").split("@")[0]
    spec = (model.config or {}).get("spec")
    kinds = [k for k in ("projected", "inferred") if registry.facts(quantity, k).method == spec]
    stored = status_from_stored((model.validation_metrics or {}).get("evaluation_status"))
    if not kinds:
        blockers.append(f"the validation registry does not recommend {spec!r} for {quantity!r}")
    for k in kinds:
        f = registry.facts(quantity, k)
        if f.tier == TIER_WITHHELD:
            blockers.append(f"the {k} output is withheld: {f.reason}")
        elif f.evaluation_status is not EvaluationStatus.HOLDOUT_CONFIRMED or k == "inferred":
            blockers.append(
                f"the {k} output is {f.evaluation_status.value}, not holdout-confirmed"
                + (" (ability is latent and is never 'confirmed')" if k == "inferred" else "")
            )
        else:
            satisfied.append(f"the {k} output was confirmed on a holdout that had not been read before")
    if stored is EvaluationStatus.FAILED:
        blockers.append("the stored evaluation of this candidate failed its pre-registered rule")
    blockers += [
        "the existing validation batteries (comparables, acquisition, adversarial) have not been re-run with this model's outputs in the path",
        "a reproducibility re-run from a clean environment must match the stored estimates",
        "no human approval record exists: promotion is a reviewed decision and has no automated path in R9.2",
    ]
    return PromotionReadiness(version, False, blockers, satisfied)
