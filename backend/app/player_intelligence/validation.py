"""What the committed validation artifacts say about each served quantity.

The service never decides on its own whether a model has been validated: it reads this registry, which
``scripts/r9_2_build_registry.py`` builds *from the experiment artifacts* (decisions, metrics, the data
fingerprint) and which is committed beside the code, so a change in what is served is a visible,
reviewable diff that names the experiments behind it.

A missing registry, or a quantity with no entry, means *unevaluated* — never "assumed fine".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.modeling.contract import Calibration, EvaluationStatus

REGISTRY_PATH = Path(__file__).with_name("validation_registry.json")

#: Serving tiers, strongest first. Nothing in R9.2 reaches ``validated``: that needs a human-approved
#: promotion (docs/r9-2/model-safety-and-promotion.md).
TIER_VALIDATED = "validated"
TIER_BENCHMARKED = "benchmarked"
TIER_EXPERIMENTAL = "experimental"
TIER_WITHHELD = "withheld"


@dataclass(frozen=True)
class KindFacts:
    """Evidence for one (quantity, output kind)."""

    quantity: str
    kind: str
    method: str | None
    tier: str
    evaluation_status: EvaluationStatus
    evaluation_ref: str | None
    reason: str
    #: Out-of-time calibration of the forecast interval, when one was measured.
    calibration: Calibration | None = None
    numbers: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationRegistry:
    raw: dict

    @property
    def version(self) -> str:
        return str(self.raw.get("version", "none"))

    def facts(self, quantity: str, kind: str) -> KindFacts:
        entry = (self.raw.get("quantities", {}).get(quantity, {}) or {}).get(kind)
        if not entry:
            return KindFacts(
                quantity, kind, None, TIER_WITHHELD, EvaluationStatus.UNEVALUATED, None,
                "no validation record exists for this quantity and output kind",
            )
        cal = entry.get("calibration")
        return KindFacts(
            quantity=quantity, kind=kind, method=entry.get("method"), tier=entry["tier"],
            evaluation_status=EvaluationStatus(entry["evaluation_status"]),
            evaluation_ref=entry.get("evaluation_ref"), reason=entry.get("reason", ""),
            calibration=Calibration(**cal) if cal else None,
            numbers=entry.get("numbers", {}), limitations=list(entry.get("limitations", [])),
        )

    def experiment_config(self, quantity: str) -> dict | None:
        """The configuration (features, seed, interval level) the registry's experiments were run under."""
        return ((self.raw.get("generated_from", {}) or {}).get(quantity, {}) or {}).get("config")

    def applies_to(self, quantity: str, method: str | None, version_config: dict | None, validation_metrics: dict | None) -> bool:
        """May this registry's evidence (evaluation status, reference, calibration) be attached to a stored model?

        Only if it IS the model the experiments evaluated. Checked from the stored model's OWN configuration against the
        configuration the registry recorded, and from the evaluation record the (controlled) training path wrote: same
        method; evaluated against one of the registry's source runs, on the registry's data fingerprint; trained under
        the experiments' features / seed / interval level; and not stale. A model that merely shares a method name — trained
        on other data, under another configuration, or never evaluated — gets its own stored status, not borrowed evidence."""
        cfg, vm = version_config or {}, validation_metrics or {}
        raw = vm.get("evaluation_status")
        if isinstance(raw, str) and raw.strip().lower().startswith("stale:"):
            return False
        recorded = self.experiment_config(quantity)
        if not method or cfg.get("spec") != method or not recorded:
            return False
        return bool(
            vm.get("experiment") in self.run_ids(quantity)
            and vm.get("config_matches") is True
            and vm.get("experiment_data_fingerprint") == self.data_fingerprint(quantity)
            and cfg.get("features") == recorded.get("features")
            and cfg.get("seed") == recorded.get("seed")
            and cfg.get("interval_level") == recorded.get("interval_level")
        )

    def run_ids(self, quantity: str) -> list[str]:
        """The experiment runs (development, holdout) whose artifacts this registry's evidence for ``quantity`` came from."""
        src = (self.raw.get("generated_from", {}) or {}).get(quantity, {}) or {}
        return [v for v in (src.get("development"), src.get("holdout")) if v]

    def data_fingerprint(self, quantity: str) -> str | None:
        return ((self.raw.get("generated_from", {}) or {}).get(quantity, {}) or {}).get("data_fingerprint")

    def served_methods(self, quantity: str, kind: str) -> list[str]:
        f = self.facts(quantity, kind)
        return [f.method] if f.method and f.tier != TIER_WITHHELD else []


@lru_cache(maxsize=1)
def load_registry(path: str | None = None) -> ValidationRegistry:
    target = Path(path) if path else REGISTRY_PATH
    if not target.exists():
        return ValidationRegistry({"version": "none", "quantities": {}})
    return ValidationRegistry(json.loads(target.read_text()))


_KNOWN_UNEVALUATED = frozenset({"unevaluated", "not_evaluated_as_candidate", "not_applicable"})


def status_from_stored(stored: str | None) -> EvaluationStatus:
    """Map a candidate's stored ``validation_metrics.evaluation_status`` onto the contract enum.

    **Fails closed:** a status this function does not recognise is ``FAILED`` (refused), never ``UNEVALUATED`` (servable
    on request) — so a typo, a different casing or a value written by a future version cannot lift a model out of a refusal."""
    if stored is None or (isinstance(stored, str) and not stored.strip()):
        return EvaluationStatus.UNEVALUATED
    if not isinstance(stored, str):
        return EvaluationStatus.FAILED
    stored = stored.strip().lower()
    if stored.startswith("stale:"):
        # The data changed since the experiment ran, so its evidence no longer describes this model. That can only
        # LOWER a status: a stale 'passed' is unevaluated, but a stale 'failed' is still failed (staleness must never
        # lift a model out of a refusal).
        inner = status_from_stored(stored[len("stale:"):])
        return EvaluationStatus.FAILED if inner is EvaluationStatus.FAILED else EvaluationStatus.UNEVALUATED
    if stored in _KNOWN_UNEVALUATED:
        return EvaluationStatus.UNEVALUATED
    return {
        "development_only": EvaluationStatus.DEVELOPMENT_ONLY,
        "holdout_confirmed": EvaluationStatus.HOLDOUT_CONFIRMED,
        "benchmarked": EvaluationStatus.BENCHMARKED,
        "failed": EvaluationStatus.FAILED,
        "failed_development_rule": EvaluationStatus.FAILED,
        "holdout_not_confirmed": EvaluationStatus.FAILED,
    }.get(stored, EvaluationStatus.FAILED)
