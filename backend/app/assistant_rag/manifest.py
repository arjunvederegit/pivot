"""The authoritative fictional league, loaded and validated.

`fixtures/manifest.json` is the single source of truth for every *bounded* fact the
assistant may state: who the players are, the exact synthetic statistics, which statistics
are deliberately missing, the analysts' letter grades and which reports contradict each
other. The documents are prose written to agree with it, and a test proves they do.

Why a manifest at all, when the documents already say these things? Because the language
model must never be the canonical calculator. When it writes "Theo Vance shoots 40.4%", the
grounding check and the evaluator compare that number to `facts` here — structured gold —
rather than searching the prose for a substring. That is the fix for the V1 evaluator,
which awarded full marks to a fabricated free-throw percentage.

Pure Python: no LangChain, no pydantic, no I/O beyond reading one JSON file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"
DOCUMENTS_DIR = FIXTURES_DIR / "documents"

VALID_TRUST = frozenset({"internal", "untrusted"})
VALID_POLARITY = frozenset({"positive", "mixed", "negative"})


class ManifestError(ValueError):
    """The manifest is internally inconsistent. Raised at load, never worked around."""


@dataclass(frozen=True, slots=True)
class Team:
    id: str
    name: str
    city: str
    nickname: str
    abbreviation: str
    aliases: tuple[str, ...]
    timeline: str
    identity: str
    default_priorities: dict[str, str]
    default_constraints: tuple[str, ...]
    strategy_source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Club:
    """A fictional club that players belong to but a GM cannot select."""

    id: str
    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Player:
    id: str
    name: str
    first_name: str
    last_name: str
    aliases: tuple[str, ...]
    position: str
    age: int
    height: str
    current_club_id: str
    archetype: str
    summary: str


@dataclass(frozen=True, slots=True)
class StatDefinition:
    key: str
    label: str
    unit: str  # "percent" | "per_game" | "count"
    keywords: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Fact:
    entity_id: str
    stat: str
    value: float
    source_ids: tuple[str, ...]

    @property
    def id(self) -> str:
        return f"{self.entity_id}.{self.stat}"


@dataclass(frozen=True, slots=True)
class UnavailableFact:
    entity_id: str
    stat: str
    reason: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UnavailableTopic:
    key: str
    label: str
    reason: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Assessment:
    entity_id: str
    dimension: str
    grade: str
    polarity: str
    summary: str
    source_id: str


@dataclass(frozen=True, slots=True)
class Conflict:
    id: str
    entity_ids: tuple[str, ...]
    dimension: str
    source_ids: tuple[str, ...]
    summary: str
    guidance: str


@dataclass(frozen=True, slots=True)
class PriorityDimension:
    key: str
    label: str
    description: str
    keywords: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DocumentMeta:
    source_id: str
    version: str
    file: str
    title: str
    doc_type: str
    author: str
    author_role: str
    report_date: str
    season: str
    entity_ids: tuple[str, ...]
    team_ids: tuple[str, ...]
    topics: tuple[str, ...]
    trust: str
    sha256: str | None

    @property
    def versioned_id(self) -> str:
        """`S03@v2` — the id a citation resolves to. Bump `version` whenever text changes."""
        return f"{self.source_id}@{self.version}"


@dataclass(frozen=True, eq=False)  # identity-hashed: usable as an lru_cache key
class Manifest:
    corpus_id: str
    corpus_version: str
    data_mode: str
    label: str
    disclaimer: str
    league_name: str
    season: str
    teams: tuple[Team, ...]
    clubs: tuple[Club, ...]
    players: tuple[Player, ...]
    priority_dimensions: tuple[PriorityDimension, ...]
    priority_levels: tuple[str, ...]
    stats: dict[str, StatDefinition]
    facts: tuple[Fact, ...]
    unavailable_facts: tuple[UnavailableFact, ...]
    unavailable_topics: tuple[UnavailableTopic, ...]
    assessments: tuple[Assessment, ...]
    conflicts: tuple[Conflict, ...]
    injection_canaries: tuple[str, ...]
    documents: tuple[DocumentMeta, ...]
    _index: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    # ----------------------------------------------------------------- lookups

    def team(self, team_id: str) -> Team | None:
        return self._index["teams"].get(team_id)

    def club_name(self, club_id: str) -> str:
        team = self.team(club_id)
        if team is not None:
            return team.name
        club = self._index["clubs"].get(club_id)
        return club.name if club is not None else club_id

    def player(self, player_id: str) -> Player | None:
        return self._index["players"].get(player_id)

    def document(self, source_id: str) -> DocumentMeta | None:
        return self._index["documents"].get(source_id)

    def dimension(self, key: str) -> PriorityDimension | None:
        return self._index["dimensions"].get(key)

    def facts_for(self, entity_id: str) -> list[Fact]:
        return [f for f in self.facts if f.entity_id == entity_id]

    def fact(self, entity_id: str, stat: str) -> Fact | None:
        return self._index["facts"].get((entity_id, stat))

    def unavailable_fact(self, entity_id: str, stat: str) -> UnavailableFact | None:
        return self._index["unavailable"].get((entity_id, stat))

    def assessments_for(self, entity_id: str, dimension: str | None = None) -> list[Assessment]:
        return [
            a
            for a in self.assessments
            if a.entity_id == entity_id and (dimension is None or a.dimension == dimension)
        ]

    def conflicts_for(self, source_ids: set[str]) -> list[Conflict]:
        """Conflicts whose every source is in `source_ids` — both sides are on the table."""
        return [c for c in self.conflicts if set(c.source_ids) <= source_ids]

    @property
    def source_ids(self) -> frozenset[str]:
        return frozenset(self._index["documents"])

    @property
    def selectable_teams(self) -> tuple[Team, ...]:
        return self.teams


# ------------------------------------------------------------------------------- loading


def _tuple(value: Any) -> tuple[str, ...]:
    return tuple(str(v) for v in (value or ()))


def parse_manifest(raw: dict[str, Any]) -> Manifest:
    """Build and validate a `Manifest`. Every cross-reference is checked; the first broken
    one raises `ManifestError` naming it."""
    teams = tuple(
        Team(
            id=t["id"],
            name=t["name"],
            city=t["city"],
            nickname=t["nickname"],
            abbreviation=t["abbreviation"],
            aliases=_tuple(t["aliases"]),
            timeline=t["timeline"],
            identity=t["identity"],
            default_priorities=dict(t["default_priorities"]),
            default_constraints=_tuple(t["default_constraints"]),
            strategy_source_ids=_tuple(t["strategy_source_ids"]),
        )
        for t in raw["teams"]
    )
    clubs = tuple(
        Club(id=c["id"], name=c["name"], aliases=_tuple(c["aliases"]))
        for c in raw.get("other_clubs", [])
    )
    players = tuple(
        Player(
            id=p["id"],
            name=p["name"],
            first_name=p["first_name"],
            last_name=p["last_name"],
            aliases=_tuple(p["aliases"]),
            position=p["position"],
            age=int(p["age"]),
            height=p["height"],
            current_club_id=p["current_club_id"],
            archetype=p["archetype"],
            summary=p["summary"],
        )
        for p in raw["players"]
    )
    dimensions = tuple(
        PriorityDimension(
            key=d["key"], label=d["label"], description=d["description"], keywords=_tuple(d["keywords"])
        )
        for d in raw["priority_dimensions"]
    )
    stats = {
        key: StatDefinition(key=key, label=s["label"], unit=s["unit"], keywords=_tuple(s["keywords"]))
        for key, s in raw["stat_definitions"].items()
    }
    facts = tuple(
        Fact(
            entity_id=f["entity_id"],
            stat=f["stat"],
            value=float(f["value"]),
            source_ids=_tuple(f["source_ids"]),
        )
        for f in raw["facts"]
    )
    unavailable = tuple(
        UnavailableFact(
            entity_id=u["entity_id"], stat=u["stat"], reason=u["reason"], source_ids=_tuple(u["source_ids"])
        )
        for u in raw.get("unavailable_facts", [])
    )
    topics = tuple(
        UnavailableTopic(key=u["key"], label=u["label"], reason=u["reason"], source_ids=_tuple(u["source_ids"]))
        for u in raw.get("unavailable_topics", [])
    )
    assessments = tuple(
        Assessment(
            entity_id=a["entity_id"],
            dimension=a["dimension"],
            grade=a["grade"],
            polarity=a["polarity"],
            summary=a["summary"],
            source_id=a["source_id"],
        )
        for a in raw.get("assessments", [])
    )
    conflicts = tuple(
        Conflict(
            id=c["id"],
            entity_ids=_tuple(c["entity_ids"]),
            dimension=c["dimension"],
            source_ids=_tuple(c["source_ids"]),
            summary=c["summary"],
            guidance=c["guidance"],
        )
        for c in raw.get("conflicts", [])
    )
    documents = tuple(
        DocumentMeta(
            source_id=d["source_id"],
            version=d["version"],
            file=d["file"],
            title=d["title"],
            doc_type=d["doc_type"],
            author=d["author"],
            author_role=d["author_role"],
            report_date=d["report_date"],
            season=d["season"],
            entity_ids=_tuple(d["entity_ids"]),
            team_ids=_tuple(d["team_ids"]),
            topics=_tuple(d["topics"]),
            trust=d["trust"],
            sha256=d.get("sha256"),
        )
        for d in raw["documents"]
    )

    manifest = Manifest(
        corpus_id=raw["corpus_id"],
        corpus_version=raw["corpus_version"],
        data_mode=raw["data_mode"],
        label=raw["label"],
        disclaimer=raw["disclaimer"],
        league_name=raw["league"]["name"],
        season=raw["league"]["season"],
        teams=teams,
        clubs=clubs,
        players=players,
        priority_dimensions=dimensions,
        priority_levels=_tuple(raw["priority_levels"]),
        stats=stats,
        facts=facts,
        unavailable_facts=unavailable,
        unavailable_topics=topics,
        assessments=assessments,
        conflicts=conflicts,
        injection_canaries=_tuple(raw.get("injection_canaries", [])),
        documents=documents,
    )
    _build_index(manifest)
    _validate(manifest)
    return manifest


def _unique(kind: str, ids: list[str]) -> None:
    seen: set[str] = set()
    for value in ids:
        if value in seen:
            raise ManifestError(f"duplicate {kind} id {value!r}")
        seen.add(value)


def _build_index(m: Manifest) -> None:
    _unique("team", [t.id for t in m.teams] + [c.id for c in m.clubs])
    _unique("player", [p.id for p in m.players])
    _unique("document", [d.source_id for d in m.documents])
    _unique("dimension", [d.key for d in m.priority_dimensions])
    fact_keys = [f"{f.entity_id}.{f.stat}" for f in m.facts]
    _unique("fact", fact_keys)
    m._index.update(
        teams={t.id: t for t in m.teams},
        clubs={c.id: c for c in m.clubs},
        players={p.id: p for p in m.players},
        documents={d.source_id: d for d in m.documents},
        dimensions={d.key: d for d in m.priority_dimensions},
        facts={(f.entity_id, f.stat): f for f in m.facts},
        unavailable={(u.entity_id, u.stat): u for u in m.unavailable_facts},
    )


def _validate(m: Manifest) -> None:
    if m.data_mode != "synthetic_demo":
        raise ManifestError("the assistant corpus must be data_mode 'synthetic_demo'")
    entity_ids = set(m._index["players"]) | set(m._index["teams"]) | set(m._index["clubs"])
    sources = set(m._index["documents"])
    dimensions = set(m._index["dimensions"])
    levels = set(m.priority_levels)

    def need_source(owner: str, source_id: str) -> None:
        if source_id not in sources:
            raise ManifestError(f"{owner} cites unknown source {source_id!r}")

    for team in m.teams:
        for key, level in team.default_priorities.items():
            if key not in dimensions or level not in levels:
                raise ManifestError(f"team {team.id} has an invalid priority {key}={level}")
        for sid in team.strategy_source_ids:
            need_source(f"team {team.id}", sid)
    for player in m.players:
        if player.current_club_id not in entity_ids:
            raise ManifestError(f"player {player.id} has unknown club {player.current_club_id!r}")
    for fact in m.facts:
        if fact.entity_id not in m._index["players"]:
            raise ManifestError(f"fact {fact.id} is about an unknown player")
        if fact.stat not in m.stats:
            raise ManifestError(f"fact {fact.id} uses an undefined stat")
        if not fact.source_ids:
            raise ManifestError(f"fact {fact.id} has no source")
        for sid in fact.source_ids:
            need_source(f"fact {fact.id}", sid)
            if m._index["documents"][sid].trust != "internal":
                raise ManifestError(f"fact {fact.id} is sourced to untrusted document {sid}")
    for gap in m.unavailable_facts:
        if (gap.entity_id, gap.stat) in m._index["facts"]:
            raise ManifestError(f"{gap.entity_id}.{gap.stat} is both a fact and unavailable")
        for sid in gap.source_ids:
            need_source(f"unavailable fact {gap.entity_id}.{gap.stat}", sid)
    for assessment in m.assessments:
        if assessment.entity_id not in m._index["players"]:
            raise ManifestError(f"assessment for unknown player {assessment.entity_id}")
        if assessment.dimension not in dimensions:
            raise ManifestError(f"assessment uses unknown dimension {assessment.dimension}")
        if assessment.polarity not in VALID_POLARITY:
            raise ManifestError(f"assessment polarity {assessment.polarity!r} is invalid")
        need_source(f"assessment {assessment.entity_id}.{assessment.dimension}", assessment.source_id)
    for conflict in m.conflicts:
        if len(conflict.source_ids) < 2:
            raise ManifestError(f"conflict {conflict.id} needs at least two sources")
        for sid in conflict.source_ids:
            need_source(f"conflict {conflict.id}", sid)
    for doc in m.documents:
        if doc.trust not in VALID_TRUST:
            raise ManifestError(f"document {doc.source_id} has invalid trust {doc.trust!r}")
        for eid in doc.entity_ids + doc.team_ids:
            if eid not in entity_ids:
                raise ManifestError(f"document {doc.source_id} references unknown entity {eid!r}")


@lru_cache(maxsize=1)
def load_manifest(path: str | None = None) -> Manifest:
    """The process-wide manifest. Cached: the file is versioned with the code."""
    target = Path(path) if path else MANIFEST_PATH
    with target.open(encoding="utf-8") as handle:
        return parse_manifest(json.load(handle))
