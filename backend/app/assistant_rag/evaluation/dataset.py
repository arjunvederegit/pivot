"""The version-controlled benchmark: what Ask Pivot is expected to do, case by case.

An evaluation *dataset* (LangSmith calls each row an "example") pairs an input with what a
good output must satisfy. Here one row is a `BenchmarkCase`: the exact `AskRequest` a GM
would send (question, mode, team, priorities, history) plus `expect`, a set of machine-
checkable expectations, and `rubric`, prose for a human reviewer covering what no
deterministic check can (tone, usefulness, whether the trade-off was explained well).

Two design rules keep the benchmark honest:

1. **Gold values live in the manifest, never here.** A case says "the answer must state
   Theo Vance's three-point percentage" as `{"entity_id": "THEO_VANCE", "stat":
   "three_pt_pct"}`; the scorer looks up 40.4 in `fixtures/manifest.json`. A benchmark that
   copied the number would drift the day the manifest changed, and nobody would notice.
2. **Validate everything on load, and fail loudly.** An unknown key (`"facs"`), an entity
   that does not exist, a stat with no gold value, a polarity that disagrees with the
   analysts' latest grade, a one-sided pair — each raises `BenchmarkError`. A typo in an
   evaluation file must never silently turn a check off.

File schema (`benchmark.json`)::

    {
      "benchmark_id": str, "version": str, "description": str,
      "cases": [{
        "id": str,                      # ^[a-z0-9_]+$, unique
        "category": str,                # one of CATEGORIES
        "description": str,
        "mode": "research" | "roster_fit",
        "team_id": str | null,          # optional, default null
        "priorities": {dimension: level},           # optional, default {}
        "previous_priorities": {dimension: level} | null,   # optional, default null
        "constraints": [str],           # optional, default []
        "history": [{"role": "user" | "assistant", "content": str}],   # optional
        "question": str,
        "expect": {...},                # see below; at least one key
        "rubric": str,                  # guidance for human review of subjective quality
        "severity": "blocking" | "major" | "minor",
        "pair_with": str | null         # optional; the partner case of a two-case pair
      }]
    }

`expect` keys (all optional, at least one required):

    retrieval            {"required_sources": [sid], "any_of": [[sid, ...]], "k": 6}
    facts                [{"entity_id", "stat"}]            gold value from manifest.facts
    must_abstain         [{"entity_id", "stat"}]            must be a manifest unavailable fact
                         or [{"topic": TOPIC, "terms": [str]}]  terms required for
                         real_nba_entity / out_of_corpus (names the off-corpus subject)
    answer_kinds         [AnswerKind]                       allowed `answer_kind` values
    polarity             [{"entity_id", "dimension", "expected": "positive" | "negative"}]
                         must agree with the most recent manifest assessment
    conflict             {"conflict_id"}                    a manifest conflict
    no_legality_verdict  true
    no_fit_score         true
    injection            true                               canaries come from the manifest
    must_mention_entities [entity id]
    must_not_recommend   [player id]                        e.g. the selected team's own players
    continuity           {"must_reference_any": [keyword | entity id]}
    priority_sensitivity {"emphasis_keywords": [str], "revision_acknowledgement": bool,
                          "deprioritized": [dimension]}     the revised side of a pair
    clarification        {"candidates": [player id, ...]}   a clarifying question is expected
    no_clarification     true                               the name must be resolved instead

Pure Python: no LangChain, no I/O beyond reading one JSON file.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args

from app.assistant_rag.manifest import Assessment, Manifest, load_manifest
from app.assistant_rag.models import AnswerKind

BENCHMARK_PATH = Path(__file__).resolve().parent / "benchmark.json"

#: Every case belongs to exactly one of these, and a test proves each one is represented.
CATEGORIES: tuple[str, ...] = (
    "fact_recall",
    "abstention",
    "ambiguity",
    "polarity",
    "conflict",
    "team_fit",
    "goal_revision",
    "counterevidence",
    "legality",
    "fit_score",
    "injection",
    "selective_evidence",
    "real_nba",
    "out_of_scope",
    "false_premise",
    "method",
    "continuity",
    "comparison",
)
SEVERITIES: tuple[str, ...] = ("blocking", "major", "minor")
MODES: tuple[str, ...] = ("research", "roster_fit")
ABSTAIN_TOPICS: tuple[str, ...] = (
    "cba_legality",
    "fit_score",
    "medical",
    "playoff_statistics",
    "real_nba_entity",
    "out_of_corpus",
)
#: Topics whose subject is not in the manifest, so the case must name it in `terms`.
TOPICS_NEEDING_TERMS = frozenset({"real_nba_entity", "out_of_corpus"})
ANSWER_KINDS: tuple[str, ...] = get_args(AnswerKind)
POLARITIES: tuple[str, ...] = ("positive", "negative")

_TOP_KEYS = frozenset({"benchmark_id", "version", "description", "cases"})
_CASE_REQUIRED = frozenset({"id", "category", "description", "mode", "question", "expect", "rubric", "severity"})
_CASE_OPTIONAL = frozenset({"team_id", "priorities", "previous_priorities", "constraints", "history", "pair_with"})
_EXPECT_KEYS = frozenset(
    {
        "retrieval",
        "facts",
        "must_abstain",
        "answer_kinds",
        "polarity",
        "conflict",
        "no_legality_verdict",
        "no_fit_score",
        "injection",
        "must_mention_entities",
        "must_not_recommend",
        "continuity",
        "priority_sensitivity",
        "clarification",
        "no_clarification",
    }
)
_CASE_ID = re.compile(r"^[a-z0-9_]+$")
_ENTITY_ID = re.compile(r"^[A-Z][A-Z0-9_]*$")


class BenchmarkError(ValueError):
    """The benchmark file is malformed or disagrees with the manifest. Raised at load."""


# ------------------------------------------------------------------------- expectations


@dataclass(frozen=True, slots=True)
class RetrievalExpect:
    required_sources: tuple[str, ...] = ()
    #: Each inner group is satisfied when ANY of its sources is retrieved; a group counts once.
    any_of: tuple[tuple[str, ...], ...] = ()
    k: int = 6


@dataclass(frozen=True, slots=True)
class FactRef:
    entity_id: str
    stat: str


@dataclass(frozen=True, slots=True)
class AbstainExpect:
    """Either a stat the corpus deliberately lacks (`entity_id` + `stat`) or a whole topic."""

    entity_id: str | None = None
    stat: str | None = None
    topic: str | None = None
    terms: tuple[str, ...] = ()

    @property
    def is_stat(self) -> bool:
        return self.topic is None


@dataclass(frozen=True, slots=True)
class PolarityExpect:
    entity_id: str
    dimension: str
    expected: str  # "positive" | "negative"


@dataclass(frozen=True, slots=True)
class PrioritySensitivity:
    emphasis_keywords: tuple[str, ...]
    revision_acknowledgement: bool = False
    #: Dimensions the GM just demoted; their trade-off sentences should change.
    deprioritized: tuple[str, ...] = ()


@dataclass(frozen=True)
class Expectations:
    retrieval: RetrievalExpect | None = None
    facts: tuple[FactRef, ...] = ()
    must_abstain: tuple[AbstainExpect, ...] = ()
    answer_kinds: tuple[str, ...] = ()
    polarity: tuple[PolarityExpect, ...] = ()
    conflict_id: str | None = None
    no_legality_verdict: bool = False
    no_fit_score: bool = False
    injection: bool = False
    must_mention_entities: tuple[str, ...] = ()
    must_not_recommend: tuple[str, ...] = ()
    continuity_any: tuple[str, ...] = ()
    priority_sensitivity: PrioritySensitivity | None = None
    clarification_candidates: tuple[str, ...] = ()
    no_clarification: bool = False


@dataclass(frozen=True, slots=True)
class HistoryMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    category: str
    description: str
    mode: str
    team_id: str | None
    priorities: Mapping[str, str]
    previous_priorities: Mapping[str, str] | None
    constraints: tuple[str, ...]
    history: tuple[HistoryMessage, ...]
    question: str
    expect: Expectations
    rubric: str
    severity: str
    pair_with: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def request_payload(self) -> dict[str, Any]:
        """The case as an `AskRequest` body — exactly what the runner sends to the pipeline."""
        return {
            "question": self.question,
            "mode": self.mode,
            "team_id": self.team_id,
            "priorities": dict(self.priorities),
            "previous_priorities": dict(self.previous_priorities) if self.previous_priorities is not None else None,
            "constraints": list(self.constraints),
            "history": [{"role": m.role, "content": m.content} for m in self.history],
        }


@dataclass(frozen=True)
class Benchmark:
    id: str
    version: str
    description: str
    cases: tuple[BenchmarkCase, ...]
    #: sha256 of the canonical JSON (sorted keys, no whitespace). Recorded with every run so a
    #: score can always be traced to the exact benchmark that produced it.
    content_hash: str
    #: The manifest version the expectations were validated against.
    corpus_version: str

    def case(self, case_id: str) -> BenchmarkCase:
        for case in self.cases:
            if case.id == case_id:
                return case
        raise KeyError(case_id)

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(sorted({c.category for c in self.cases}))

    def pairs(self) -> list[tuple[BenchmarkCase, BenchmarkCase]]:
        """`(initial, revised)` for every pair; the revised side declares `priority_sensitivity`."""
        out: list[tuple[BenchmarkCase, BenchmarkCase]] = []
        for case in self.cases:
            if case.pair_with and case.expect.priority_sensitivity is not None:
                out.append((self.case(case.pair_with), case))
        return out


# ----------------------------------------------------------------------------- parsing


def canonical_json(raw: Any) -> str:
    return json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(raw: Any) -> str:
    return hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest()


def _keys(where: str, obj: Any, allowed: frozenset[str], required: frozenset[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise BenchmarkError(f"{where}: expected an object, got {type(obj).__name__}")
    unknown = set(obj) - allowed
    if unknown:
        raise BenchmarkError(f"{where}: unknown key(s) {sorted(unknown)}")
    missing = required - set(obj)
    if missing:
        raise BenchmarkError(f"{where}: missing key(s) {sorted(missing)}")
    return obj


def _str(where: str, value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise BenchmarkError(f"{where}: expected a non-empty string")
    return value


def _str_list(where: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise BenchmarkError(f"{where}: expected a list of strings")
    return tuple(_str(f"{where}[{i}]", v) for i, v in enumerate(value))


def _bool(where: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise BenchmarkError(f"{where}: expected true or false")
    return value


class _Checker:
    """Cross-references against the manifest, with error messages that name the case."""

    def __init__(self, manifest: Manifest) -> None:
        self.m = manifest
        self.players = {p.id for p in manifest.players}
        self.teams = {t.id for t in manifest.teams}
        self.dimensions = {d.key for d in manifest.priority_dimensions}
        self.levels = set(manifest.priority_levels)

    def player(self, where: str, value: Any) -> str:
        pid = _str(where, value)
        if pid not in self.players:
            raise BenchmarkError(f"{where}: unknown player id {pid!r}")
        return pid

    def entity(self, where: str, value: Any) -> str:
        eid = _str(where, value)
        if eid not in self.players and eid not in self.teams:
            raise BenchmarkError(f"{where}: unknown player or team id {eid!r}")
        return eid

    def stat(self, where: str, value: Any) -> str:
        key = _str(where, value)
        if key not in self.m.stats:
            raise BenchmarkError(f"{where}: unknown stat {key!r}")
        return key

    def dimension(self, where: str, value: Any) -> str:
        key = _str(where, value)
        if key not in self.dimensions:
            raise BenchmarkError(f"{where}: unknown priority dimension {key!r}")
        return key

    def source(self, where: str, value: Any) -> str:
        sid = _str(where, value)
        if sid not in self.m.source_ids:
            raise BenchmarkError(f"{where}: unknown source id {sid!r}")
        return sid

    def priorities(self, where: str, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            raise BenchmarkError(f"{where}: expected an object of dimension -> level")
        out: dict[str, str] = {}
        for key, level in value.items():
            self.dimension(f"{where}.{key}", key)
            if level not in self.levels:
                raise BenchmarkError(f"{where}.{key}: unknown priority level {level!r}")
            out[key] = level
        return out


def _parse_retrieval(where: str, raw: Any, ck: _Checker) -> RetrievalExpect:
    obj = _keys(where, raw, frozenset({"required_sources", "any_of", "k"}))
    required = tuple(ck.source(f"{where}.required_sources[{i}]", s) for i, s in enumerate(obj.get("required_sources", [])))
    groups_raw = obj.get("any_of", [])
    if not isinstance(groups_raw, list):
        raise BenchmarkError(f"{where}.any_of: expected a list of lists")
    groups = []
    for i, group in enumerate(groups_raw):
        if not isinstance(group, list) or not group:
            raise BenchmarkError(f"{where}.any_of[{i}]: expected a non-empty list of source ids")
        groups.append(tuple(ck.source(f"{where}.any_of[{i}][{j}]", s) for j, s in enumerate(group)))
    if not required and not groups:
        raise BenchmarkError(f"{where}: needs required_sources or any_of")
    k = obj.get("k", 6)
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= 12:
        raise BenchmarkError(f"{where}.k: expected an integer between 1 and 12")
    return RetrievalExpect(required_sources=required, any_of=tuple(groups), k=k)


def _list(where: str, value: Any) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise BenchmarkError(f"{where}: expected a non-empty list")
    return value


def _parse_expect(where: str, raw: Any, ck: _Checker) -> Expectations:
    obj = _keys(where, raw, _EXPECT_KEYS)
    if not obj:
        raise BenchmarkError(f"{where}: a case must expect something")
    m = ck.m
    kwargs: dict[str, Any] = {}

    if "retrieval" in obj:
        kwargs["retrieval"] = _parse_retrieval(f"{where}.retrieval", obj["retrieval"], ck)

    if "facts" in obj:
        facts = []
        for i, item in enumerate(_list(f"{where}.facts", obj["facts"])):
            w = f"{where}.facts[{i}]"
            fobj = _keys(w, item, frozenset({"entity_id", "stat"}), frozenset({"entity_id", "stat"}))
            ref = FactRef(ck.player(f"{w}.entity_id", fobj["entity_id"]), ck.stat(f"{w}.stat", fobj["stat"]))
            if m.fact(ref.entity_id, ref.stat) is None:
                raise BenchmarkError(f"{w}: the manifest has no gold value for {ref.entity_id}.{ref.stat}")
            facts.append(ref)
        kwargs["facts"] = tuple(facts)

    if "must_abstain" in obj:
        items = []
        for i, item in enumerate(_list(f"{where}.must_abstain", obj["must_abstain"])):
            w = f"{where}.must_abstain[{i}]"
            aobj = _keys(w, item, frozenset({"entity_id", "stat", "topic", "terms"}))
            if "topic" in aobj:
                if "entity_id" in aobj or "stat" in aobj:
                    raise BenchmarkError(f"{w}: use either topic or entity_id+stat, not both")
                topic = _str(f"{w}.topic", aobj["topic"])
                if topic not in ABSTAIN_TOPICS:
                    raise BenchmarkError(f"{w}.topic: unknown topic {topic!r}")
                terms = _str_list(f"{w}.terms", aobj.get("terms", []))
                if topic in TOPICS_NEEDING_TERMS and not terms:
                    raise BenchmarkError(f"{w}: topic {topic} needs terms naming the off-corpus subject")
                items.append(AbstainExpect(topic=topic, terms=terms))
            else:
                if "terms" in aobj or "entity_id" not in aobj or "stat" not in aobj:
                    raise BenchmarkError(f"{w}: a stat abstention needs exactly entity_id and stat")
                pid = ck.player(f"{w}.entity_id", aobj["entity_id"])
                stat = ck.stat(f"{w}.stat", aobj["stat"])
                if m.fact(pid, stat) is not None:
                    raise BenchmarkError(f"{w}: {pid}.{stat} IS in the manifest; abstaining would be wrong")
                if m.unavailable_fact(pid, stat) is None:
                    raise BenchmarkError(f"{w}: {pid}.{stat} is not a documented unavailable fact")
                items.append(AbstainExpect(entity_id=pid, stat=stat))
        kwargs["must_abstain"] = tuple(items)

    if "answer_kinds" in obj:
        kinds = _str_list(f"{where}.answer_kinds", _list(f"{where}.answer_kinds", obj["answer_kinds"]))
        for kind in kinds:
            if kind not in ANSWER_KINDS:
                raise BenchmarkError(f"{where}.answer_kinds: unknown answer kind {kind!r}")
        kwargs["answer_kinds"] = kinds

    if "polarity" in obj:
        items_p = []
        for i, item in enumerate(_list(f"{where}.polarity", obj["polarity"])):
            w = f"{where}.polarity[{i}]"
            keys = frozenset({"entity_id", "dimension", "expected"})
            pobj = _keys(w, item, keys, keys)
            pid = ck.player(f"{w}.entity_id", pobj["entity_id"])
            dim = ck.dimension(f"{w}.dimension", pobj["dimension"])
            expected = pobj["expected"]
            if expected not in POLARITIES:
                raise BenchmarkError(f"{w}.expected: must be 'positive' or 'negative'")
            latest = latest_assessment(m, pid, dim)
            if latest is None:
                raise BenchmarkError(f"{w}: the manifest has no assessment of {pid} on {dim}")
            if latest.polarity != expected:
                raise BenchmarkError(
                    f"{w}: expected {expected} but the latest assessment ({latest.source_id}) is {latest.polarity}"
                )
            items_p.append(PolarityExpect(pid, dim, expected))
        kwargs["polarity"] = tuple(items_p)

    if "conflict" in obj:
        cobj = _keys(f"{where}.conflict", obj["conflict"], frozenset({"conflict_id"}), frozenset({"conflict_id"}))
        cid = _str(f"{where}.conflict.conflict_id", cobj["conflict_id"])
        if cid not in {c.id for c in m.conflicts}:
            raise BenchmarkError(f"{where}.conflict: unknown conflict id {cid!r}")
        kwargs["conflict_id"] = cid

    for flag in ("no_legality_verdict", "no_fit_score", "injection", "no_clarification"):
        if flag in obj:
            if not _bool(f"{where}.{flag}", obj[flag]):
                raise BenchmarkError(f"{where}.{flag}: omit the key instead of setting it false")
            kwargs[flag] = True

    if "must_mention_entities" in obj:
        kwargs["must_mention_entities"] = tuple(
            ck.entity(f"{where}.must_mention_entities[{i}]", e)
            for i, e in enumerate(_list(f"{where}.must_mention_entities", obj["must_mention_entities"]))
        )
    if "must_not_recommend" in obj:
        kwargs["must_not_recommend"] = tuple(
            ck.player(f"{where}.must_not_recommend[{i}]", e)
            for i, e in enumerate(_list(f"{where}.must_not_recommend", obj["must_not_recommend"]))
        )

    if "continuity" in obj:
        w = f"{where}.continuity"
        cont = _keys(w, obj["continuity"], frozenset({"must_reference_any"}), frozenset({"must_reference_any"}))
        refs = _str_list(f"{w}.must_reference_any", _list(f"{w}.must_reference_any", cont["must_reference_any"]))
        for token in refs:
            if _ENTITY_ID.match(token):
                ck.entity(f"{w}.must_reference_any", token)
        kwargs["continuity_any"] = refs

    if "priority_sensitivity" in obj:
        w = f"{where}.priority_sensitivity"
        ps = _keys(
            w,
            obj["priority_sensitivity"],
            frozenset({"emphasis_keywords", "revision_acknowledgement", "deprioritized"}),
            frozenset({"emphasis_keywords"}),
        )
        kwargs["priority_sensitivity"] = PrioritySensitivity(
            emphasis_keywords=_str_list(f"{w}.emphasis_keywords", _list(f"{w}.emphasis_keywords", ps["emphasis_keywords"])),
            revision_acknowledgement=_bool(f"{w}.revision_acknowledgement", ps.get("revision_acknowledgement", False)),
            deprioritized=tuple(
                ck.dimension(f"{w}.deprioritized[{i}]", d) for i, d in enumerate(ps.get("deprioritized", []))
            ),
        )

    if "clarification" in obj:
        w = f"{where}.clarification"
        cl = _keys(w, obj["clarification"], frozenset({"candidates"}), frozenset({"candidates"}))
        candidates = tuple(ck.player(f"{w}.candidates[{i}]", c) for i, c in enumerate(_list(f"{w}.candidates", cl["candidates"])))
        if len(set(candidates)) < 2:
            raise BenchmarkError(f"{w}: a clarification needs at least two distinct candidates")
        kwargs["clarification_candidates"] = candidates
    if kwargs.get("no_clarification") and kwargs.get("clarification_candidates"):
        raise BenchmarkError(f"{where}: clarification and no_clarification are mutually exclusive")

    return Expectations(**kwargs)


def report_date(m: Manifest, source_id: str) -> str:
    doc = m.document(source_id)
    return doc.report_date if doc else ""


def latest_assessment(m: Manifest, entity_id: str, dimension: str) -> Assessment | None:
    """The most recent assessment by report date — the one a current evaluation should follow
    (Calder's 2026 C+ supersedes his 2023 A-). Polarity gold is always this assessment's."""
    rows = m.assessments_for(entity_id, dimension)
    if not rows:
        return None
    return max(rows, key=lambda a: report_date(m, a.source_id))


def parse_case(raw: Any, manifest: Manifest) -> BenchmarkCase:
    obj = _keys("case", raw, _CASE_REQUIRED | _CASE_OPTIONAL, _CASE_REQUIRED)
    cid = _str("case.id", obj["id"])
    if not _CASE_ID.match(cid):
        raise BenchmarkError(f"case id {cid!r} must match {_CASE_ID.pattern}")
    where = f"case {cid}"
    ck = _Checker(manifest)

    category = _str(f"{where}.category", obj["category"])
    if category not in CATEGORIES:
        raise BenchmarkError(f"{where}: unknown category {category!r}")
    mode = _str(f"{where}.mode", obj["mode"])
    if mode not in MODES:
        raise BenchmarkError(f"{where}: unknown mode {mode!r}")
    severity = _str(f"{where}.severity", obj["severity"])
    if severity not in SEVERITIES:
        raise BenchmarkError(f"{where}: unknown severity {severity!r}")

    team_id = obj.get("team_id")
    if team_id is not None and team_id not in ck.teams:
        raise BenchmarkError(f"{where}: unknown team id {team_id!r}")

    previous = obj.get("previous_priorities")
    history_raw = obj.get("history", [])
    if not isinstance(history_raw, list):
        raise BenchmarkError(f"{where}.history: expected a list")
    history = []
    for i, turn in enumerate(history_raw):
        w = f"{where}.history[{i}]"
        tobj = _keys(w, turn, frozenset({"role", "content"}), frozenset({"role", "content"}))
        if tobj["role"] not in ("user", "assistant"):
            raise BenchmarkError(f"{w}.role: must be 'user' or 'assistant'")
        history.append(HistoryMessage(tobj["role"], _str(f"{w}.content", tobj["content"])))

    pair_with = obj.get("pair_with")
    if pair_with is not None:
        _str(f"{where}.pair_with", pair_with)
        if pair_with == cid:
            raise BenchmarkError(f"{where}: a case cannot pair with itself")

    return BenchmarkCase(
        id=cid,
        category=category,
        description=_str(f"{where}.description", obj["description"]),
        mode=mode,
        team_id=team_id,
        priorities=ck.priorities(f"{where}.priorities", obj.get("priorities", {})),
        previous_priorities=ck.priorities(f"{where}.previous_priorities", previous) if previous is not None else None,
        constraints=_str_list(f"{where}.constraints", obj.get("constraints", [])),
        history=tuple(history),
        question=_str(f"{where}.question", obj["question"]),
        expect=_parse_expect(f"{where}.expect", obj["expect"], ck),
        rubric=_str(f"{where}.rubric", obj["rubric"]),
        severity=severity,
        pair_with=pair_with,
        raw=obj,
    )


def parse_benchmark(raw: Any, manifest: Manifest) -> Benchmark:
    """Validate a parsed benchmark document against `manifest` and build a `Benchmark`."""
    top = _keys("benchmark", raw, _TOP_KEYS, _TOP_KEYS)
    cases_raw = top["cases"]
    if not isinstance(cases_raw, list) or not cases_raw:
        raise BenchmarkError("benchmark.cases: expected a non-empty list")
    cases = tuple(parse_case(c, manifest) for c in cases_raw)

    by_id: dict[str, BenchmarkCase] = {}
    for case in cases:
        if case.id in by_id:
            raise BenchmarkError(f"duplicate case id {case.id!r}")
        by_id[case.id] = case
    for case in cases:
        if case.pair_with is None:
            if case.expect.priority_sensitivity is not None:
                raise BenchmarkError(f"case {case.id}: priority_sensitivity needs a pair_with partner")
            continue
        partner = by_id.get(case.pair_with)
        if partner is None:
            raise BenchmarkError(f"case {case.id}: pair_with points to unknown case {case.pair_with!r}")
        if partner.pair_with != case.id:
            raise BenchmarkError(f"case {case.id}: pair with {partner.id} is not symmetric")
        sides = [c for c in (case, partner) if c.expect.priority_sensitivity is not None]
        if len(sides) != 1:
            raise BenchmarkError(f"pair {case.id}/{partner.id}: exactly one side must declare priority_sensitivity")
        if sides[0].previous_priorities is None:
            raise BenchmarkError(f"case {sides[0].id}: the revised side of a pair needs previous_priorities")

    return Benchmark(
        id=_str("benchmark.benchmark_id", top["benchmark_id"]),
        version=_str("benchmark.version", top["version"]),
        description=_str("benchmark.description", top["description"]),
        cases=cases,
        content_hash=content_hash(raw),
        corpus_version=manifest.corpus_version,
    )


def load_benchmark(path: Path | None = None, manifest: Manifest | None = None) -> Benchmark:
    """Read, validate and hash the benchmark (default: the version-controlled `benchmark.json`)."""
    target = path or BENCHMARK_PATH
    with target.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    return parse_benchmark(raw, manifest or load_manifest())
