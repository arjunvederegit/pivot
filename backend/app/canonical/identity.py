"""Who is this player, and which team is that? Pivot's one answer, with its doubt on the surface.

Identity in Pivot has three layers, and this module owns all of them:

1. **Canonical keys.** ``players.nba_player_id`` / ``teams.nba_team_id`` are the NBA.com
   identifiers and the join keys for every NBA.com-derived row. Relationships inside the
   database use the UUID primary keys, so a provider's id never leaks into a foreign key.
2. **Provider crosswalk.** Any *other* provider's ids map to Pivot's through
   ``provider_entity_ids`` (``ProviderIdMap``). BALLDONTLIE player 237 is not NBA.com's 2544
   and its payload never says who 237 is; a link is made once, by evidence, and recorded.
3. **Name resolution** (``PlayerIdentityIndex``) for sources that give only a name:
   a contract snapshot, a transaction page. Tiered, descending confidence, and **never a
   silent guess** — an ambiguity is returned as an ambiguity.

The tiers and the measurements behind them (38 duplicated lowercase names in the 5,121-player
table; 26 names with diacritics, inconsistently kept) are preserved from the R2a contract-import
work this was lifted from; ``app.ingestion.identity`` still re-exports every name it used to
define.

A fourth, smaller piece lives here because it was copy-pasted into three modules: team code
aliases. Basketball-Reference prints ``BRK``/``CHO``/``PHO``, the logo folder uses ``phl``/``uth``;
``canonical_team_code`` is the single place those are reconciled.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.canonical.seasons import Season
from app.db.models import Player, ProviderEntityId, RosterEntry, Team, utcnow

# --------------------------------------------------------------------------- name tools

SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}


def unaccent(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)
    )


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", value.lower()).strip()


def strip_suffix(value: str) -> str:
    parts = value.split()
    while len(parts) > 2 and parts[-1].lower().strip(".") in {s.strip(".") for s in SUFFIXES}:
        parts = parts[:-1]
    return " ".join(parts)


# -------------------------------------------------------------------- team code aliases

#: Spellings that are not NBA.com's three-letter code, mapped to it, per source. The
#: NBA.com abbreviation is canonical because ``teams.abbreviation`` stores it.
TEAM_CODE_ALIASES_BY_SOURCE: dict[str, dict[str, str]] = {
    # Basketball-Reference (contracts snapshot, transaction pages).
    "bbref": {"BRK": "BKN", "CHO": "CHA", "PHO": "PHX"},
    # The user's logo files are named with the lowercase abbreviation, with two exceptions.
    "asset_filename": {"PHL": "PHI", "UTH": "UTA"},
}

#: Every alias in one map, for callers that do not know which source a code came from. Safe
#: to merge: no alias key collides with a real NBA.com abbreviation.
TEAM_CODE_ALIASES: dict[str, str] = {
    alias: canonical
    for table in TEAM_CODE_ALIASES_BY_SOURCE.values()
    for alias, canonical in table.items()
}


def canonical_team_code(code: str, source: str | None = None) -> str:
    """The NBA.com abbreviation for ``code``. An unknown code is returned upper-cased as-is."""
    code = code.strip().upper()
    table = TEAM_CODE_ALIASES_BY_SOURCE[source] if source else TEAM_CODE_ALIASES
    return table.get(code, code)


@dataclass(frozen=True)
class FranchiseEra:
    """One name a franchise carried, for the seasons it carried it.

    ``franchise`` is the abbreviation the franchise uses today, which is what
    ``teams.abbreviation`` holds. Season bounds are *start years* (2008 = 2008-09). Only
    renames and relocations since 1995 are recorded; this is a curated reference, not a
    claim to be a complete history of the league.
    """

    franchise: str
    name: str
    abbreviation: str
    first_season: int
    last_season: int | None  # None = still the current identity


FRANCHISE_ERAS: tuple[FranchiseEra, ...] = (
    FranchiseEra("BKN", "New Jersey Nets", "NJN", 1977, 2011),
    FranchiseEra("BKN", "Brooklyn Nets", "BKN", 2012, None),
    FranchiseEra("OKC", "Seattle SuperSonics", "SEA", 1967, 2007),
    FranchiseEra("OKC", "Oklahoma City Thunder", "OKC", 2008, None),
    FranchiseEra("NOP", "New Orleans Hornets", "NOH", 2002, 2012),
    FranchiseEra("NOP", "New Orleans Pelicans", "NOP", 2013, None),
    FranchiseEra("CHA", "Charlotte Bobcats", "CHA", 2004, 2013),
    FranchiseEra("CHA", "Charlotte Hornets", "CHA", 2014, None),
    FranchiseEra("MEM", "Vancouver Grizzlies", "VAN", 1995, 2000),
    FranchiseEra("MEM", "Memphis Grizzlies", "MEM", 2001, None),
    FranchiseEra("WAS", "Washington Bullets", "WSB", 1974, 1996),
    FranchiseEra("WAS", "Washington Wizards", "WAS", 1997, None),
)


def franchise_era(franchise: str, season: Season) -> FranchiseEra | None:
    """The identity a franchise carried in ``season``, where one is recorded here.

    ``None`` means "no rename is recorded for this franchise", i.e. the current name applies
    — not that the franchise did not exist.
    """
    for era in FRANCHISE_ERAS:
        if (
            era.franchise == franchise.upper()
            and era.first_season <= season.start_year
            and (era.last_season is None or season.start_year <= era.last_season)
        ):
            return era
    return None


def franchise_for_historical_code(code: str, season: Season | None = None) -> str | None:
    """The current franchise abbreviation for a historical code such as ``NJN`` or ``SEA``."""
    code = code.upper()
    for era in FRANCHISE_ERAS:
        if era.abbreviation != code:
            continue
        if season is None or (
            era.first_season <= season.start_year
            and (era.last_season is None or season.start_year <= era.last_season)
        ):
            return era.franchise
    return None


# ------------------------------------------------------------------- player resolution


class MatchStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


@dataclass
class Resolution:
    player: Player | None
    #: provider_id | nba_player_id | exact_name | unaccented | suffix_insensitive, optionally
    #: suffixed ``+roster`` / ``+bio``; or ``ambiguous`` / ``conflicting_bio`` / ``none``.
    method: str
    ambiguous_candidates: list[Player] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.player is not None

    @property
    def status(self) -> MatchStatus:
        if self.player is not None:
            return MatchStatus.RESOLVED
        return MatchStatus.AMBIGUOUS if self.method == "ambiguous" else MatchStatus.UNRESOLVED


#: Providers list the same player's height up to two inches apart (shoes, rounding), so only
#: a larger gap counts as contradicting evidence.
HEIGHT_CONFLICT_INCHES = 3


@dataclass(frozen=True)
class PlayerHints:
    """Biographical facts from the *source* record, used only to separate namesakes.

    A hint can exclude a candidate (a recorded fact contradicts it) or confirm one (draft
    year or birth date agrees). Height is conflict-only (too many players share one) and
    only a gap of ``HEIGHT_CONFLICT_INCHES`` or more counts.
    """

    draft_year: int | None = None
    draft_number: int | None = None
    birth_date: date | None = None
    height_inches: int | None = None

    def judge(self, candidate: Player) -> tuple[bool, int]:
        """(conflicts, strong_agreements) of ``candidate`` against these hints."""
        agree = 0
        if self.draft_year is not None and candidate.draft_year is not None:
            if self.draft_year != candidate.draft_year:
                return True, 0
            agree += 1
            if (
                self.draft_number is not None
                and candidate.draft_number is not None
                and self.draft_number != candidate.draft_number
            ):
                return True, 0
        if self.birth_date is not None and candidate.birth_date is not None:
            if self.birth_date != candidate.birth_date:
                return True, 0
            agree += 1
        if (
            self.height_inches is not None
            and candidate.height_inches is not None
            and abs(self.height_inches - candidate.height_inches) >= HEIGHT_CONFLICT_INCHES
        ):
            return True, 0
        return False, agree


class PlayerIdentityIndex:
    """Tiered lookup built once per import.

    Tiers are tried in descending confidence and **never** fall through silently: the
    method that produced a match is recorded on every resolution, and an ambiguity is
    returned as an ambiguity rather than resolved by picking.
    """

    def __init__(
        self, db: Session, season: str, preferred_player_ids: set[str] | None = None
    ):
        """`preferred_player_ids` overrides the tie-break population.

        The default is the current roster, which is right for a contract snapshot: it is
        about players who are on a team now. It is wrong for a *historical* transaction —
        a 2016-17 trade is not about whoever holds the name today — so the transaction
        importer passes the players who actually recorded a season in the trade's own
        season instead. Passing an empty set disables the tie-break entirely, which
        leaves a duplicated name reported as ambiguous rather than resolved to the wrong
        era's player.
        """
        self._db = db
        self._players = list(db.scalars(select(Player)).all())
        self._rostered: set[str] = (
            preferred_player_ids
            if preferred_player_ids is not None
            else {
                entry.player_id
                for entry in db.scalars(
                    select(RosterEntry).where(
                        RosterEntry.season == season, RosterEntry.is_current
                    )
                ).all()
            }
        )
        self._by_id: dict[str, Player] = {p.id: p for p in self._players}
        self._by_nba_id: dict[int, Player] = {
            p.nba_player_id: p for p in self._players if p.nba_player_id is not None
        }
        self._by_exact: dict[str, list[Player]] = defaultdict(list)
        self._by_unaccented: dict[str, list[Player]] = defaultdict(list)
        self._by_stripped: dict[str, list[Player]] = defaultdict(list)
        for player in self._players:
            name = player.full_name or ""
            self._by_exact[_normalize(name)].append(player)
            self._by_unaccented[_normalize(unaccent(name))].append(player)
            self._by_stripped[_normalize(unaccent(strip_suffix(name)))].append(player)
        self._provider_links: dict[tuple[str, str], str] | None = None

    @property
    def rostered_player_ids(self) -> set[str]:
        return self._rostered

    def _linked(self, provider: str, provider_id: str) -> Player | None:
        if self._provider_links is None:
            self._provider_links = {
                (row.provider, row.provider_id): row.internal_id
                for row in self._db.scalars(
                    select(ProviderEntityId).where(ProviderEntityId.entity_type == "player")
                ).all()
            }
        internal = self._provider_links.get((provider, str(provider_id)))
        return self._by_id.get(internal) if internal else None

    def _disambiguate(
        self,
        candidates: list[Player],
        method: str,
        hints: PlayerHints | None,
        prefer_rostered: bool,
    ) -> Resolution:
        if len(candidates) == 1 and hints is None:
            return Resolution(candidates[0], method)

        if hints is not None:
            judged = [(c, *hints.judge(c)) for c in candidates]
            survivors = [(c, agree) for c, conflict, agree in judged if not conflict]
            if not survivors:
                # Every namesake contradicts the source's own biography: the person the
                # source means is not in this database. Report that, do not pick.
                return Resolution(None, "conflicting_bio", ambiguous_candidates=candidates)
            confirmed = [c for c, agree in survivors if agree > 0]
            if len(confirmed) == 1:
                return Resolution(confirmed[0], f"{method}+bio")
            if len(survivors) == 1 and len(candidates) == 1:
                # One name match, nothing contradicts it, nothing confirms it either.
                return Resolution(survivors[0][0], method)
            candidates = [c for c, _ in survivors]

        if len(candidates) == 1:
            return Resolution(candidates[0], method)
        if prefer_rostered:
            # A currently-rostered player is the one a contract snapshot is about; a
            # historical namesake is not. This resolves `Brandon Williams` deterministically.
            on_roster = [p for p in candidates if p.id in self._rostered]
            if len(on_roster) == 1:
                return Resolution(on_roster[0], f"{method}+roster")
        return Resolution(None, "ambiguous", ambiguous_candidates=candidates)

    def resolve(
        self,
        *,
        nba_player_id: int | None = None,
        name: str = "",
        provider: str | None = None,
        provider_id: str | int | None = None,
        hints: PlayerHints | None = None,
        prefer_rostered: bool = True,
    ) -> Resolution:
        if provider is not None and provider_id is not None:
            linked = self._linked(provider, str(provider_id))
            if linked is not None:
                return Resolution(linked, "provider_id")
        if nba_player_id is not None:
            player = self._by_nba_id.get(nba_player_id)
            if player is not None:
                return Resolution(player, "nba_player_id")
        for index, method in (
            (self._by_exact, "exact_name"),
            (self._by_unaccented, "unaccented"),
            (self._by_stripped, "suffix_insensitive"),
        ):
            key = {
                "exact_name": _normalize(name),
                "unaccented": _normalize(unaccent(name)),
                "suffix_insensitive": _normalize(unaccent(strip_suffix(name))),
            }[method]
            candidates = index.get(key, [])
            if candidates:
                # An ambiguity at a high-confidence tier is reported rather than
                # retried at a looser one, which would only widen the candidate set.
                return self._disambiguate(candidates, method, hints, prefer_rostered)
        return Resolution(None, "none")


# ------------------------------------------------------------------- provider crosswalk


@dataclass(frozen=True)
class LinkOutcome:
    status: str  # created | unchanged | conflict | replaced
    link: ProviderEntityId


class ProviderIdMap:
    """Read/write access to ``provider_entity_ids``. Idempotent; never overwrites silently."""

    def __init__(self, db: Session):
        self._db = db

    def lookup(self, entity_type: str, provider: str, provider_id: str | int) -> str | None:
        """The internal id (UUID) a provider id is linked to, or ``None``."""
        row = self._db.scalar(
            select(ProviderEntityId).where(
                ProviderEntityId.entity_type == entity_type,
                ProviderEntityId.provider == provider,
                ProviderEntityId.provider_id == str(provider_id),
            )
        )
        return row.internal_id if row else None

    def links_for(self, entity_type: str, internal_id: str) -> list[ProviderEntityId]:
        return list(
            self._db.scalars(
                select(ProviderEntityId).where(
                    ProviderEntityId.entity_type == entity_type,
                    ProviderEntityId.internal_id == internal_id,
                )
            ).all()
        )

    def link(
        self,
        entity_type: str,
        provider: str,
        provider_id: str | int,
        internal_id: str,
        *,
        method: str,
        confidence: str = "high",
        evidence: dict[str, Any] | None = None,
        replace: bool = False,
    ) -> LinkOutcome:
        """Record ``provider:provider_id -> internal_id``.

        Re-linking to the same entity is a no-op that refreshes the evidence. Linking an id
        that already points elsewhere is a **conflict**: nothing is overwritten (unless the
        caller passes ``replace=True`` — a deliberate, manual act) and a data-quality finding
        is filed, because one provider id naming two people means one of the links is wrong
        and Pivot cannot tell which.
        """
        if entity_type not in ("player", "team"):
            raise ValueError(f"unknown entity_type {entity_type!r}")
        existing = self._db.scalar(
            select(ProviderEntityId).where(
                ProviderEntityId.entity_type == entity_type,
                ProviderEntityId.provider == provider,
                ProviderEntityId.provider_id == str(provider_id),
            )
        )
        now = utcnow()
        if existing is None:
            row = ProviderEntityId(
                entity_type=entity_type,
                provider=provider,
                provider_id=str(provider_id),
                internal_id=internal_id,
                match_method=method,
                confidence=confidence,
                evidence=evidence or {},
                observed_at=now,
            )
            self._db.add(row)
            self._db.flush()
            return LinkOutcome("created", row)
        if existing.internal_id == internal_id:
            existing.evidence = evidence or existing.evidence
            existing.observed_at = now
            return LinkOutcome("unchanged", existing)
        if replace:
            existing.internal_id = internal_id
            existing.match_method = method
            existing.confidence = confidence
            existing.evidence = evidence or {}
            existing.observed_at = now
            return LinkOutcome("replaced", existing)
        from app.ingestion.quality import upsert_issue

        upsert_issue(
            self._db,
            "provider_id_conflict",
            f"{provider} {entity_type} id {provider_id} is linked to {existing.internal_id} "
            f"but a new match points to {internal_id} ({method}); link left unchanged",
            severity="error",
            entity=f"{provider}:{entity_type}:{provider_id}",
        )
        return LinkOutcome("conflict", existing)


# ------------------------------------------------------------------------------- teams


class TeamIndex:
    """Teams by NBA.com id or abbreviation (alias-aware), loaded once."""

    def __init__(self, db: Session):
        teams = list(db.scalars(select(Team)).all())
        self._by_abbr = {t.abbreviation.upper(): t for t in teams}
        self._by_nba_id = {t.nba_team_id: t for t in teams}
        self._by_name = {t.full_name.lower(): t for t in teams}

    def by_abbreviation(self, code: str, source: str | None = None) -> Team | None:
        return self._by_abbr.get(canonical_team_code(code, source))

    def by_nba_id(self, nba_team_id: int) -> Team | None:
        return self._by_nba_id.get(nba_team_id)

    def by_full_name(self, name: str) -> Team | None:
        return self._by_name.get(name.lower())


__all__ = [
    "FRANCHISE_ERAS",
    "SUFFIXES",
    "TEAM_CODE_ALIASES",
    "TEAM_CODE_ALIASES_BY_SOURCE",
    "FranchiseEra",
    "LinkOutcome",
    "MatchStatus",
    "PlayerHints",
    "PlayerIdentityIndex",
    "ProviderIdMap",
    "Resolution",
    "TeamIndex",
    "canonical_team_code",
    "franchise_era",
    "franchise_for_historical_code",
    "strip_suffix",
    "unaccent",
]
