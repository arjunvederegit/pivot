"""Linking BALLDONTLIE's ids to Pivot's players and teams.

BALLDONTLIE's player and team ids are its own. Its player payload carries **no NBA.com id**,
so the link has to be made from a name plus whatever biography both sides agree on. That is
the case the canonical identity layer exists for, and this module only feeds it:

* A team is linked by NBA.com abbreviation (alias-aware), and the full name must agree.
* A player is linked by the tiered name resolution, with draft year/number, height as
  *hints* that can exclude a namesake or confirm one. An unresolvable or ambiguous player is
  reported, never linked. Nothing is fuzzy-matched.

Links are idempotent and recorded with their method and evidence. Re-running never changes
an existing link; a disagreement with one is filed as a conflict.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.canonical.identity import (
    PlayerHints,
    PlayerIdentityIndex,
    ProviderIdMap,
    TeamIndex,
)
from app.canonical.records import PlayerRecord, TeamRecord

PROVIDER = "balldontlie"


@dataclass
class CrosswalkReport:
    entity_type: str
    total: int = 0
    created: int = 0
    unchanged: int = 0
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    methods: Counter[str] = field(default_factory=Counter)

    @property
    def linked(self) -> int:
        return self.created + self.unchanged

    @property
    def link_rate(self) -> float:
        return round(self.linked / self.total, 4) if self.total else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "total": self.total,
            "linked": self.linked,
            "created": self.created,
            "unchanged": self.unchanged,
            "ambiguous": len(self.ambiguous),
            "unresolved": len(self.unresolved),
            "conflicts": len(self.conflicts),
            "link_rate": self.link_rate,
            "methods": dict(self.methods),
        }


def crosswalk_teams(db: Session, records: list[TeamRecord]) -> CrosswalkReport:
    index = TeamIndex(db)
    ids = ProviderIdMap(db)
    report = CrosswalkReport("team", total=len(records))
    for record in records:
        team = index.by_abbreviation(record.abbreviation)
        if team is None:
            report.unresolved.append(
                {"provider_id": record.provider_id, "abbreviation": record.abbreviation}
            )
            continue
        if team.full_name.lower() != record.full_name.lower():
            # Same abbreviation, different name: do not link on the code alone.
            report.ambiguous.append(
                {
                    "provider_id": record.provider_id,
                    "abbreviation": record.abbreviation,
                    "provider_name": record.full_name,
                    "pivot_name": team.full_name,
                }
            )
            continue
        _record(
            report,
            ids.link(
                "team",
                PROVIDER,
                record.provider_id,
                team.id,
                method="abbreviation+full_name",
                evidence={"abbreviation": record.abbreviation, "full_name": record.full_name},
            ),
            record.provider_id,
            "abbreviation+full_name",
        )
    db.flush()
    return report


def crosswalk_players(
    db: Session, records: list[PlayerRecord], *, season: str
) -> CrosswalkReport:
    index = PlayerIdentityIndex(db, season)
    ids = ProviderIdMap(db)
    report = CrosswalkReport("player", total=len(records))
    for record in records:
        hints = PlayerHints(
            draft_year=record.draft_year,
            draft_number=record.draft_number,
            height_inches=record.height_inches,
        )
        resolution = index.resolve(
            name=record.full_name,
            provider=PROVIDER,
            provider_id=record.provider_id,
            hints=hints,
            # A player the provider lists on a team is a current player, so a currently
            # rostered namesake is the likelier meaning. A historical record gets no such
            # presumption: it stays ambiguous unless biography separates the namesakes.
            prefer_rostered=record.team_provider_id is not None,
        )
        if resolution.player is None:
            bucket = report.ambiguous if resolution.method == "ambiguous" else report.unresolved
            bucket.append(
                {
                    "provider_id": record.provider_id,
                    "name": record.full_name,
                    "draft_year": record.draft_year,
                    "reason": resolution.method,
                    "candidates": [
                        {
                            "nba_player_id": c.nba_player_id,
                            "draft_year": c.draft_year,
                            "birth_date": c.birth_date.isoformat() if c.birth_date else None,
                        }
                        for c in resolution.ambiguous_candidates
                    ],
                }
            )
            continue
        if resolution.method == "provider_id":
            report.unchanged += 1
            report.methods["provider_id"] += 1
            continue
        confidence = (
            "high" if resolution.method in ("exact_name", "exact_name+bio", "unaccented+bio") else "medium"
        )
        outcome = ids.link(
            "player",
            PROVIDER,
            record.provider_id,
            resolution.player.id,
            method=resolution.method,
            confidence=confidence,
            evidence={
                "name": record.full_name,
                "draft_year": record.draft_year,
                "draft_number": record.draft_number,
                "height_inches": record.height_inches,
                "nba_player_id": resolution.player.nba_player_id,
            },
        )
        _record(report, outcome, record.provider_id, resolution.method)
    db.flush()
    return report


def _record(report: CrosswalkReport, outcome: Any, provider_id: str, method: str) -> None:
    if outcome.status == "created":
        report.created += 1
        report.methods[method] += 1
    elif outcome.status in ("unchanged", "replaced"):
        report.unchanged += 1
    else:
        report.conflicts.append({"provider_id": provider_id, "method": method})

