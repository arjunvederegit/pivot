"""Post-generation grounding checks — what can be verified about an answer, deterministically.

A RAG answer arrives with citations like `[S04]`. It is tempting to read a citation as
"verified". It is not: a citation is a *pointer* — "this sentence came from passage S04" —
and the model can attach a valid pointer to a false sentence. The V1 evaluator made exactly
that mistake and gave full marks to "Cruz shoots 89% free throws [S01]", a statistic no
source contains. So this module checks what plain code *can* check, and labels the rest as
unchecked:

* **citation validity** — every cited id must be one of the passages retrieved for this
  answer. `[S99]` (nonexistent) and `[S12]` (real, but not retrieved) are both invalid;
* **numbers** — each number is read by `claims.read_numbers` (its statistic; whether it is
  an age, a derived comparison such as "3.5 more than Reed", a bound such as "below 45%", or
  a value the answer rejects such as "nowhere near 45%"), attached to a player (the nearest
  name in the sentence, a bold label on its line, or the previous sentence's subject for
  he/his/him) and compared with the fact manifest: match, contradiction, a statistic the
  manifest records as *unavailable*, or — when no player can be identified — unverified. A
  number with no keyword that refers back ("the creation report puts *it* at 89%") takes the
  previous sentence's statistic and subject (`claims.carried_stat`). Markdown tables are read
  row by row with their column headers, exactly as the scorers read them;
* **policy echoes** — legality verdicts, numeric fit scores, injection canaries and
  citations of untrusted (promotional / anonymous) sources;
* **direction checks** — "Niko Reed is a bad defender" when every documented defensive
  grade for him is positive: a `claim_contradicts_assessment` warning (and a limitation line);
* **misleading citations** — a correct number cited to a passage that does not contain it
  ("Vance shoots 40.4% [S03]", when S03 is Reed's report): `citation_not_supporting`. A
  valid id on a true sentence can still point at the wrong evidence.

`analyze_answer` exposes the per-sentence results (citations, each number's verdict, the
section heading it sits under) so `epistemics.py` can label each sentence as an observed
fact, an analyst's opinion, an inference or unavailable information from the same analysis.

`attribution_status` never claims more than this: the best status an answer can earn is
`citations_present_not_fact_verified`.

The text analysis lives in `claims.py`, shared with the evaluators, so the runtime check and
the benchmark score an answer by the same rules. Pure Python: no LangChain.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.assistant_rag import claims
from app.assistant_rag.manifest import Fact, Manifest, UnavailableFact
from app.assistant_rag.policy import relevant_unavailable_facts
from app.assistant_rag.types import (
    EntityResolution,
    GroundingReport,
    GroundingWarning,
    PolicyDecision,
    RetrievalResult,
)

FICTIONAL_NOTE = (
    "All sources are fictional (synthetic_demo); citations show where a claim came from, "
    "not that it is true."
)

_DEFENSE_DIMENSIONS = ("point_of_attack_defense", "wing_defense", "team_defense")


def _quote(sentence: str, limit: int = 140) -> str:
    text = " ".join(sentence.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _fmt(value: float, unit: str) -> str:
    if unit == "count":
        return str(int(value)) if float(value).is_integer() else f"{value:g}"
    text = f"{value:.1f}"
    return f"{text}%" if unit == "percent" else text


def _ids(source_ids: Iterable[str]) -> str:
    return ", ".join(source_ids)


# ---------------------------------------------------------------------------- numbers


_value_matches = claims.value_matches  # one rounding rule, shared with the scorers


class _Subject:
    """Who a sentence's numbers are about: one id, or several for an ambiguous mention."""

    def __init__(self, mention_text: str, ids: tuple[str, ...]) -> None:
        self.text = mention_text
        self.ids = ids

    @property
    def ambiguous(self) -> bool:
        return len(self.ids) > 1


def _disambiguate(ids: tuple[str, ...], context_ids: set[str]) -> tuple[str, ...]:
    """A bare "Cruz" means Mateo when the question (or the answer elsewhere) named him."""
    if len(ids) <= 1:
        return ids
    narrowed = tuple(i for i in ids if i in context_ids)
    return narrowed if len(narrowed) == 1 else ids


def _attribute(
    number: claims.NumberMention,
    subjects: Sequence[tuple[int, int, _Subject]],
    carried: _Subject | None,
    sentence: str = "",
    label: _Subject | None = None,
) -> tuple[_Subject | None, str]:
    """Who a number is about, and how sure: `(subject, how)` with `how` one of "named" (the
    nearest preceding player name, or the bold label that opens the sentence's own line:
    "**Theo Vance:** 30.4% from three"), "carried" (a pronoun before the number refers back:
    "His 40.4% outperforms Holloway's 29.8%" is about the previous subject, not Holloway),
    "following" (the nearest following name) or "none"."""
    before = [s for s in subjects if s[1] <= number.start]
    if before:
        return max(before, key=lambda s: s[1])[2], "named"
    if label is not None:
        return label, "named"
    if carried is not None and claims.PRONOUN.search(sentence[: number.start]):
        return carried, "carried"
    after = [s for s in subjects if s[0] >= number.end]
    if after:
        return min(after, key=lambda s: s[0])[2], "following"
    return (carried, "carried") if carried is not None else (None, "none")


def _names(ids: Iterable[str], manifest: Manifest) -> str:
    names = []
    for pid in ids:
        player = manifest.player(pid)
        names.append(player.name if player else pid)
    return " or ".join(names)


def _check_number(
    sentence: str,
    number: claims.NumberMention,
    stat: str,
    subject: _Subject | None,
    sentence_ids: set[str],
    manifest: Manifest,
) -> GroundingWarning | None:
    definition = manifest.stats[stat]
    shown = number.text
    if subject is None:
        return GroundingWarning(
            "unverified_number",
            f"'{shown}' is attached to {definition.label} in \"{_quote(sentence)}\", but no player "
            "could be identified, so it was not checked against the fact manifest.",
        )
    primary = subject.ids
    # A match for the attributed player, or for another player named in the same sentence
    # ("40.4% for Vance and 34.1% for Reed"), is not a contradiction.
    matching = [
        pid
        for pid in dict.fromkeys((*primary, *sorted(sentence_ids)))
        if (fact := manifest.fact(pid, stat)) is not None and _value_matches(number, fact.value)
    ]
    if matching:
        if subject.ambiguous and set(matching) <= set(primary):
            missing = [g for pid in primary if (g := manifest.unavailable_fact(pid, stat)) is not None]
            gap_note = " " + " ".join(g.reason for g in missing) if missing else ""
            return GroundingWarning(
                "unverified_number",
                f"'{subject.text}' could be {_names(primary, manifest)}; '{shown}' matches "
                f"{_possessive(_names(matching, manifest))} {definition.label}, but the answer does not say "
                f"which player it means.{gap_note}",
                tuple(dict.fromkeys(s for pid in matching if (f := manifest.fact(pid, stat)) for s in f.source_ids)),
            )
        return None
    if definition.unit == "percent" and not number.is_percent:
        # "0.1 threes per game": a per-game number whose keyword reads as a percentage.
        for pid in primary:
            if any(
                manifest.stats[f.stat].unit != "percent" and _value_matches(number, f.value)
                for f in manifest.facts_for(pid)
            ):
                return None
    gaps: list[UnavailableFact] = [
        g for pid in primary if (g := manifest.unavailable_fact(pid, stat)) is not None
    ]
    if gaps:
        missing_for = _names(dict.fromkeys(g.entity_id for g in gaps), manifest)
        message = (
            f"The answer gives '{shown}' as {_possessive(missing_for)} {definition.label}, a "
            "statistic no source records. " + " ".join(g.reason for g in gaps)
        )
        sources = [s for g in gaps for s in g.source_ids]
        if subject.ambiguous:
            others = [f for pid in primary if (f := manifest.fact(pid, stat)) is not None]
            other_note = "".join(
                f" If it means {_names([f.entity_id], manifest)}, the manifest value is "
                f"{_fmt(f.value, definition.unit)} ({_ids(f.source_ids)}), not '{shown}'."
                for f in others
            )
            sources += [s for f in others for s in f.source_ids]
            message = f"'{subject.text}' could be {_names(primary, manifest)}. {message}{other_note}"
        return GroundingWarning("number_for_unavailable_stat", message, tuple(dict.fromkeys(sources)))
    facts: list[Fact] = [f for pid in primary if (f := manifest.fact(pid, stat)) is not None]
    if facts:
        gold = "; ".join(
            f"{_possessive(_names([f.entity_id], manifest))} {definition.label} is {_fmt(f.value, definition.unit)} "
            f"in the fact manifest ({_ids(f.source_ids)})"
            for f in facts
        )
        prefix = f"'{subject.text}' could be {_names(primary, manifest)}. " if subject.ambiguous else ""
        return GroundingWarning(
            "number_contradicts_manifest",
            f"{prefix}{gold}; the answer says '{shown}'.",
            tuple(dict.fromkeys(s for f in facts for s in f.source_ids)),
        )
    return GroundingWarning(
        "unverified_number",
        f"'{shown}' for {_possessive(_names(primary, manifest))} {definition.label} could not be checked: the "
        "fact manifest has no value for it.",
    )


def _defense_direction_note(
    sentence: str, subject_ids: Sequence[str], manifest: Manifest
) -> str | None:
    found = _defense_direction(sentence, subject_ids, manifest)
    return found[0] if found else None


def _defense_direction(
    sentence: str, subject_ids: Sequence[str], manifest: Manifest
) -> tuple[str, tuple[str, ...]] | None:
    """A defensive-quality claim whose direction disagrees with *every* documented grade:
    `(note, the grades' source ids)`."""
    stripped = claims.strip_citations(sentence)
    for pattern, claimed, opposite in (
        (claims.NEGATIVE_DEFENSE, "weak", "positive"),
        (claims.POSITIVE_DEFENSE, "strong", "negative"),
    ):
        match = next(
            (
                m
                for m in pattern.finditer(stripped)
                if not claims.negated_before(stripped, m.start()) and not claims.is_hypothetical(stripped, m.start())
            ),
            None,
        )
        if match is None:
            continue
        for pid in subject_ids:
            player = manifest.player(pid)
            grades = [a for d in _DEFENSE_DIMENSIONS for a in manifest.assessments_for(pid, d)]
            if player is None or not grades or any(a.polarity != opposite for a in grades):
                continue
            detail = ", ".join(
                f"{_dimension_label(a.dimension, manifest).lower()} {a.grade} ({a.source_id})"
                for a in grades
            )
            note = (
                f"Check against the source: the answer calls {_possessive(player.name)} defense "
                f"{claimed}, but every documented defensive grade for him is {opposite} — {detail}."
            )
            return note, tuple(dict.fromkeys(a.source_id for a in grades))
    return None


def _dimension_label(key: str, manifest: Manifest) -> str:
    dimension = manifest.dimension(key)
    return dimension.label if dimension else key.replace("_", " ")


def _possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


# --------------------------------------------------------------------------- analysis

@dataclass(frozen=True, slots=True)
class NumberVerdict:
    """One number in an answer, attached to a statistic and a player, and what the fact
    manifest says about it."""

    text: str
    stat: str
    subject_ids: tuple[str, ...]
    verdict: str  # "match" | "contradicts" | "unavailable" | "unverified"
    fact_source_ids: tuple[str, ...]  # where the manifest says this value is documented
    #: How the player was identified: "named" in this sentence before the number, or by the
    #: bold label opening its line (certain), or "carried"/"following"/"none" (inferred). The
    #: guard only acts on "named".
    attribution: str = "named"
    #: False when the *statistic* is inferred — "shoots 89%" with no keyword, or "it" referring
    #: back to an earlier sentence — or the number is only a bound ("below 80%"). The guard
    #: only acts on certain readings; the warning stands either way.
    certain: bool = True
    start: int = -1  # where the number sits in the unit's text
    value: float | None = None


@dataclass(frozen=True)
class SentenceAnalysis:
    text: str
    section: str | None  # the bold/markdown heading the sentence sits under, if any
    citations: tuple[str, ...]  # every id cited in the sentence, valid or not
    numbers: tuple[NumberVerdict, ...]
    warnings: tuple[GroundingWarning, ...]
    direction: tuple[str, tuple[str, ...]] | None  # (note, grade source ids)
    subject_ids: tuple[str, ...]
    #: The player a label or heading names ("**Theo Vance:** ...") — the subject of a clause
    #: that names nobody itself.
    referent_ids: tuple[str, ...] = ()
    #: Where the unit sits in the answer (line, span, table cells) — what the guard replaces.
    unit: claims.Unit | None = None


_VERDICT_BY_CODE = {
    "number_contradicts_manifest": "contradicts",
    "number_for_unavailable_stat": "unavailable",
    "unverified_number": "unverified",
}


def _matching_sources(
    number: claims.NumberMention, stat: str, ids: Iterable[str], manifest: Manifest
) -> tuple[str, ...]:
    sources: list[str] = []
    for pid in dict.fromkeys(ids):
        fact = manifest.fact(pid, stat)
        if fact is not None and _value_matches(number, fact.value):
            sources += fact.source_ids
            continue
        if manifest.stats[stat].unit == "percent" and not number.is_percent:
            # "0.1 threes per game": the per-game fact the number actually matches
            for other in manifest.facts_for(pid):
                if manifest.stats[other.stat].unit != "percent" and _value_matches(number, other.value):
                    sources += other.source_ids
    return tuple(dict.fromkeys(sources))


def analyze_answer(answer: str, entities: EntityResolution, manifest: Manifest) -> list[SentenceAnalysis]:
    """Every unit of an answer (`claims.units`: sentences and table rows, never the guard's
    markers) with its citations, its numbers' verdicts against the fact manifest, and any
    defensive-direction contradiction. Pure; shared by the runtime warnings below, the
    guard and `epistemics.classify_answer`."""
    # Context for disambiguating a bare surname: players the question resolved, plus
    # players the answer itself names unambiguously.
    context_ids = set(entities.player_ids) | {
        eid for eid in claims.mentioned_entities(claims.strip_markers(answer), manifest) if manifest.player(eid)
    }
    carried: _Subject | None = None
    topic: tuple[str, ...] = ()  # the statistics the last stat-bearing unit was about
    analyses: list[SentenceAnalysis] = []
    for unit in claims.units(answer):
        section, sentence = unit.section, unit.text
        found = claims.find_mentions(sentence, manifest)
        mentions = [m for m in found if m.kind == "player"]
        subjects = [
            (m.start, m.end, _Subject(re.sub(r"(?:'s|’s)$", "", m.text), _disambiguate(m.entity_ids, context_ids)))
            for m in mentions
        ]
        # The subject a pronoun or a bare fragment refers to: a section label that names one
        # player ("**Niko Reed:** ...his 3P%") wins over the previous sentence's subject.
        heading = [m for m in claims.find_mentions(section or "", manifest) if m.kind == "player" and not m.ambiguous]
        heading_subject = _Subject(heading[0].text, heading[0].entity_ids) if len(heading) == 1 else None
        # ...and the label on the sentence's own line names it outright.
        label_subject = heading_subject if unit.label is not None else None
        referent = heading_subject or carried
        pronoun_subject = referent if (claims.PRONOUN.search(sentence) or (heading_subject and not subjects)) else None
        # A sentence that names nobody can still refer back ("The creation report puts it at
        # 89%"): its numbers may take the referent, and the referent's statistic.
        back_subject = referent if not mentions and not any(m.kind != "player" for m in found) else None
        sentence_ids = {pid for _, _, s in subjects for pid in s.ids}
        verdicts: list[NumberVerdict] = []
        warnings: list[GroundingWarning] = []
        for reading in claims.read_numbers(sentence, manifest):
            if reading.status not in ("claim", "bound"):
                continue  # an age, a rejected premise ("nowhere near 45%"), a derived comparison ("3.5 more than")
            number = reading.number
            stat, certain = reading.stat, reading.status == "claim" and not reading.inferred
            subject, how = _attribute(number, subjects, pronoun_subject, sentence, label_subject)
            if stat is None or reading.inferred:
                fallback = subject if subject is not None else back_subject
                back = claims.carried_stat(sentence, reading, topic, fallback.ids if fallback else (), manifest)
                if back is not None:
                    stat, certain = back, False
                    if subject is None and fallback is not None:
                        subject, how = fallback, "carried"
            elif subject is None and back_subject is not None and claims.continues_gap(stat, topic, back_subject.ids, manifest):
                subject, how, certain = back_subject, "carried", False
            if stat is None:
                continue
            if reading.status == "bound" and not (
                subject is not None and any(manifest.unavailable_fact(pid, stat) for pid in subject.ids)
            ):
                continue  # "below 45%" states no value to compare; it matters only for a missing statistic
            candidates = set(sentence_ids) | (set(pronoun_subject.ids) if pronoun_subject is not None else set())
            warning = _check_number(sentence, number, stat, subject, candidates, manifest)
            ids = subject.ids if subject is not None else ()
            if warning is None:
                verdict, sources = "match", _matching_sources(number, stat, (*ids, *sorted(candidates)), manifest)
            else:
                warnings.append(warning)
                verdict, sources = _VERDICT_BY_CODE.get(warning.code, "unverified"), warning.source_ids
            verdicts.append(
                NumberVerdict(number.text, stat, tuple(ids), verdict, tuple(sources), how, certain, number.start, number.value)
            )
        topic = claims.stat_topic(sentence, [v.stat for v in verdicts], manifest, topic)
        direction_ids = [pid for _, _, s in subjects if not s.ambiguous for pid in s.ids]
        if not direction_ids and pronoun_subject is not None and not pronoun_subject.ambiguous:
            direction_ids = list(pronoun_subject.ids)
        analyses.append(
            SentenceAnalysis(
                text=sentence,
                section=section,
                citations=tuple(claims.unit_citations(unit)),
                numbers=tuple(verdicts),
                warnings=tuple(warnings),
                direction=_defense_direction(sentence, direction_ids, manifest),
                subject_ids=tuple(dict.fromkeys(pid for _, _, s in subjects for pid in s.ids)),
                referent_ids=tuple(heading_subject.ids) if heading_subject is not None else (),
                unit=unit,
            )
        )
        if subjects:
            carried = subjects[0][2]
    return analyses


# ------------------------------------------------------------------------------- check


def _factual_looking(sentence: str, manifest: Manifest) -> bool:
    if sentence.rstrip().endswith("?") or claims.has_abstention(sentence):
        return False
    return bool(
        claims.find_mentions(sentence, manifest)
        or claims.extract_numbers(sentence)
        or claims.stats_mentioned(sentence, manifest)
    )


def _attribution_note(status: str, invalid: Sequence[str]) -> str:
    if status == "not_applicable_no_model_answer":
        return (
            "No language-model answer to check: this reply was written by deterministic rules. "
            "Any citation in it points to the sandbox's own documentation."
        )
    if status == "no_citations":
        return (
            "The answer cites no sources, so none of its claims can be traced to a retrieved "
            "passage. Treat every statement in it as unsupported."
        )
    pointer = (
        "A valid citation is a pointer to a passage that was retrieved for this answer, not proof "
        "that the sentence citing it is true. Numbers next to a named player and statistic were "
        "compared with the fact manifest; everything else is unverified."
    )
    if status == "citations_include_invalid_ids":
        verb = "was" if len(invalid) == 1 else "were"
        return (
            f"The answer cites {_ids(invalid)}, which {verb} not among the passages retrieved for "
            f"it — those citations point to nothing the model was shown. {pointer}"
        )
    return pointer


def check_answer(
    answer: str,
    retrieval: RetrievalResult,
    entities: EntityResolution,
    decision: PolicyDecision,
    manifest: Manifest,
    *,
    model_called: bool,
) -> GroundingReport:
    """Check one answer against what was retrieved and against the fact manifest.

    `retrieval` must describe the passages the model was actually shown (if the prompt
    trimmed some, pass a result restricted to the survivors). `cited_source_ids` lists only
    valid citations; invalid ones are in `invalid_citations`. On a guarded answer, the guard's
    markers are not checked: they are its notes, not the model's claims (a marker quoting
    "the guard profile (S02)" must not make an uncited answer look cited)."""
    answer = claims.strip_markers(answer)
    retrieved = set(retrieval.source_ids)
    cited = claims.extract_citations(answer)
    valid = tuple(s for s in cited if s in retrieved)
    invalid = tuple(s for s in cited if s not in retrieved)
    warnings: list[GroundingWarning] = []
    notes: list[str] = []

    for sid in invalid:
        if manifest.document(sid) is None:
            message = f"[{sid}] is cited, but no source with that id exists in the corpus."
        else:
            message = f"[{sid}] is a real corpus document, but it was not retrieved for this answer, so the model never saw it."
        warnings.append(GroundingWarning("invalid_citation", message, (sid,)))

    if model_called:
        warnings += _model_answer_warnings(answer, retrieval, entities, manifest, valid, cited, notes)

    if not model_called:
        status = "not_applicable_no_model_answer"
    elif not cited:
        status = "no_citations"
    elif invalid:
        status = "citations_include_invalid_ids"
    else:
        status = "citations_present_not_fact_verified"

    limitations = _limitations(answer, retrieval, entities, decision, manifest, notes)
    return GroundingReport(
        cited_source_ids=valid,
        invalid_citations=invalid,
        attribution_status=status,
        attribution_note=_attribution_note(status, invalid),
        warnings=tuple(dict.fromkeys(warnings)),
        limitations=limitations,
    )


#: Document types written about specific players. A team memo or method guide can support a
#: sentence about any player (it states the need, not the player), so it is never flagged.
_PLAYER_SCOPED_TYPES = frozenset({"scouting_report", "stat_profile", "analytics_note", "agent_pitch", "fan_submission"})


def _off_subject_citations(
    analysis: SentenceAnalysis, retrieved: set[str], manifest: Manifest
) -> list[GroundingWarning]:
    """A clause about one player that cites only reports written about *other* players
    ("**Theo Vance:** ... struggles at the point of attack [S09]", when S09 is Marchetti's
    report). The claim may even be true; the citation still points at the wrong evidence."""
    warnings: list[GroundingWarning] = []
    for clause in analysis.text.split(";"):
        cited = [c for c in dict.fromkeys(claims.extract_citations(clause)) if c in retrieved]
        if not cited:
            continue
        mentions = [m for m in claims.find_mentions(claims.strip_citations(clause), manifest) if m.kind == "player"]
        if any(m.ambiguous for m in mentions):
            continue
        ids = {pid for m in mentions for pid in m.entity_ids}
        if not ids:
            ids = set(analysis.referent_ids) or set(analysis.subject_ids)
        if len(ids) != 1:
            continue
        (pid,) = ids
        docs = [manifest.document(sid) for sid in cited]
        if any(d is None or d.doc_type not in _PLAYER_SCOPED_TYPES or not d.entity_ids or pid in d.entity_ids for d in docs):
            continue
        player = manifest.player(pid)
        if player is None:
            continue
        about = sorted({eid for d in docs if d is not None for eid in d.entity_ids})
        verb = "is a source" if len(cited) == 1 else "are sources"
        warnings.append(
            GroundingWarning(
                "citation_not_supporting",
                f"\"{_quote(clause.strip())}\" is about {player.name}, but {_ids(cited)} {verb} about "
                f"{_names(about, manifest)}, not him. A valid citation can still point at another player's evidence.",
                tuple(cited),
            )
        )
    return warnings


def _model_answer_warnings(
    answer: str,
    retrieval: RetrievalResult,
    entities: EntityResolution,
    manifest: Manifest,
    valid: Sequence[str],
    cited: Sequence[str],
    notes: list[str],
) -> list[GroundingWarning]:
    warnings: list[GroundingWarning] = []
    sentences = claims.split_sentences(answer)

    if not cited and any(_factual_looking(s, manifest) for s in sentences):
        warnings.append(
            GroundingWarning(
                "uncited_answer",
                "The answer makes factual-looking statements but cites no source; none of them "
                "can be traced to a retrieved passage.",
            )
        )

    retrieved = set(retrieval.source_ids)
    for analysis in analyze_answer(answer, entities, manifest):
        warnings += analysis.warnings
        if analysis.direction is not None:
            note, grade_sources = analysis.direction
            notes.append(note)
            warnings.append(
                GroundingWarning(
                    "claim_contradicts_assessment",
                    f"\"{_quote(analysis.text)}\" — {note[len('Check against the source: '):]}",
                    grade_sources,
                )
            )
        cited_here = [c for c in analysis.citations if c in retrieved]
        before = len(warnings)
        for number in analysis.numbers:
            if number.verdict != "match" or not cited_here or not number.fact_source_ids:
                continue
            if not set(cited_here) & set(number.fact_source_ids):
                label = manifest.stats[number.stat].label
                who = _possessive(_names(number.subject_ids, manifest)) if number.subject_ids else "the"
                warnings.append(
                    GroundingWarning(
                        "citation_not_supporting",
                        f"'{number.text}' ({who} {label}) is correct, but it is cited to "
                        f"{_ids(cited_here)}, which does not contain it; the manifest documents it in "
                        f"{_ids(number.fact_source_ids)}. A valid citation can still point at the wrong evidence.",
                        tuple(dict.fromkeys([*cited_here, *number.fact_source_ids])),
                    )
                )
        if len(warnings) == before:  # a misplaced number already says the pointer is wrong
            warnings += _off_subject_citations(analysis, retrieved, manifest)

    for sentence in claims.legality_verdicts(answer):
        warnings.append(
            GroundingWarning(
                "unsupported_legality_claim",
                f"\"{_quote(sentence)}\" states a trade-legality verdict, which this sandbox cannot "
                "support: it has no CBA engine and no contract data.",
            )
        )
    for sentence in claims.fit_scores(answer):
        warnings.append(
            GroundingWarning(
                "invented_fit_score",
                f"\"{_quote(sentence)}\" gives a numeric fit or impact score; the sandbox has no "
                "model that produces one, so the number is invented.",
            )
        )
    for canary in claims.contains_canary(answer, manifest):
        planted = tuple(
            dict.fromkeys(
                item.chunk.source_id for item in retrieval.selected if canary.lower() in item.chunk.text.lower()
            )
        )
        warnings.append(
            GroundingWarning(
                "injection_marker_echoed",
                f"The answer repeats '{canary}', a marker planted in a source's injected "
                "instructions — the model may have followed text it should have ignored.",
                planted,
            )
        )
    for sid in valid:
        meta = manifest.document(sid)
        if meta is None:
            meta = next((i.chunk.meta for i in retrieval.selected if i.chunk.source_id == sid), None)
        if meta is not None and meta.trust == "untrusted":
            warnings.append(
                GroundingWarning(
                    "untrusted_source_cited",
                    f"{sid} is untrusted third-party material ({meta.doc_type.replace('_', ' ')}, "
                    f"{meta.author_role}); anything cited to it is a claim, not an established fact.",
                    (sid,),
                )
            )
    return warnings


def _limitations(
    answer: str,
    retrieval: RetrievalResult,
    entities: EntityResolution,
    decision: PolicyDecision,
    manifest: Manifest,
    notes: Sequence[str],
) -> tuple[str, ...]:
    lines: list[str] = []
    players = list(entities.player_ids)
    for ambiguity in entities.ambiguous:
        players += list(ambiguity.candidate_ids)
    players += [eid for eid in claims.mentioned_entities(answer, manifest) if manifest.player(eid)]
    for mention in claims.find_mentions(answer, manifest):
        if mention.ambiguous and mention.kind == "player":
            players += list(mention.entity_ids)
    text = f"{retrieval.query_text}\n{answer}"
    for gap in relevant_unavailable_facts(manifest, players, text):
        lines.append(gap.reason)
    for item in decision.unsupported:
        lines.append(item.message)
    if retrieval.low_evidence:
        top = "none" if retrieval.top_dense_score is None else f"{retrieval.top_dense_score:.2f}"
        lines.append(
            "Retrieval found little relevant evidence (top similarity "
            f"{top}, threshold {retrieval.min_evidence_threshold:.2f}); treat this answer as weakly supported."
        )
    for conflict in manifest.conflicts_for(set(retrieval.source_ids)):
        lines.append(f"Sources disagree: {conflict.summary} {conflict.guidance}")
    lines += list(notes)
    lines.append(FICTIONAL_NOTE)
    return tuple(dict.fromkeys(lines))
