"""Store, as candidates, the models the validation registry recommends for a cutoff.

This is a thin loop over R9.1's ``train_candidate`` — the only write path, with its protected-state
fingerprint and isolation checks — not a second write path. For each quantity it trains the method
recommended for the *projected* output and, if different, the one recommended for the *inferred*
output, then records how each fared by pointing at the committed experiment artifacts.

Nothing is promoted: every stored model is ``status = 'candidate'`` and ``is_active = False``.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.config import BACKEND_DIR
from app.modeling.pairs import FeatureConfig
from app.modeling.registry import train_candidate

from .validation import TIER_WITHHELD, load_registry

DEFAULT_EVIDENCE = BACKEND_DIR.parent / "docs" / "r9-2" / "results"


def _evidence_dir(root: Path, quantity: str) -> Path | None:
    for stage in ("holdout", "development"):
        candidate = root / f"{stage}-{quantity}"
        if (candidate / "decisions.json").exists() and (candidate / "config.json").exists():
            return candidate
    return None


def materialise_recommended(db: Session, origin: str, evidence_root: Path | None = None) -> list[dict]:
    registry = load_registry()
    root = evidence_root or DEFAULT_EVIDENCE
    done: list[dict] = []
    for quantity in sorted(registry.raw.get("quantities", {})):
        methods: list[str] = []
        for kind in ("projected", "inferred"):
            f = registry.facts(quantity, kind)
            if f.method and f.tier != TIER_WITHHELD and f.method not in methods:
                methods.append(f.method)
        for method in methods:
            record = train_candidate(
                db, method, quantity, origin, features=FeatureConfig(), experiment_dir=_evidence_dir(root, quantity)
            )
            done.append({"quantity": quantity, "method": method, "version": record.version, "estimates": record.estimates_written, "reused_existing": record.reused_existing})
    return done
