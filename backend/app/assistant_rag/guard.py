"""The answer guard — block what the checks can prove is wrong, and say so.

`grounding.py` *reports* problems; this module *acts* on the few it can establish with
certainty. A warning beside an answer still lets the GM read "the trade is approved under
the CBA". So after the checks run, every sentence (or table cell) that

* states a trade-legality verdict              (no CBA engine or contract data exists here),
* states a numeric fit or impact score         (no model produces one),
* repeats an injection canary phrase           (it followed a planted instruction),
* cites a source id that does not exist        ([S99]: the injection's fake source),
* gives a number that contradicts the fact sheet, or a number for a statistic that no
  source records, for a player the sentence names (the manifest is the canonical value;
  a number tied to a player only by a pronoun or by "it", or whose statistic had to be
  inferred, is warned about, never deleted, because the attribution itself could be wrong)

is removed from the answer the GM sees and replaced by a visible marker naming the reason —
for a wrong number, with the documented value and where it is documented. Nothing is
silently rewritten: the model's raw text is kept in `diagnostics.raw_answer`, the warnings
still describe it, and evaluations score both, so the guard can never make a weak model
look strong. Only the flagged sentence is replaced, by its exact span: every other line,
and every other sentence on the same line, is returned exactly as the model wrote it. In a
markdown table only the offending cell is replaced, so the table keeps its shape.

What the guard never touches: statements of absence ("Not in the sources: proof the trade is
approved"), numbers the answer repeats only to reject ("the 45% figure does not appear in
any source", "nowhere near 45%"), and numbers that are not the player's statistic at all —
a difference, a ratio, a league average, a threshold, games missed, an age ("3.5 more than
Reed", "79 of 82 games") — the same readings the checks use (`claims.is_absence_statement`,
`claims.read_numbers`). Nor a wrong number in a sentence that also states the documented
value for that player and statistic: the sentence is more likely comparing than asserting.
Opinions, emphasis and reasoning are never removed: whether a recommendation is *wise* is
for the GM, not for a regex. Pure Python.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.assistant_rag import claims
from app.assistant_rag.claims import MARKER_PREFIX, MARKER_SUFFIX
from app.assistant_rag.grounding import NumberVerdict, SentenceAnalysis, analyze_answer
from app.assistant_rag.manifest import Manifest
from app.assistant_rag.types import EntityResolution

__all__ = ["MARKER_PREFIX", "MARKER_SUFFIX", "GuardResult", "Removal", "guard_answer"]


@dataclass(frozen=True, slots=True)
class Removal:
    code: str  # "legality_verdict" | "fit_score" | "injection_canary" | "nonexistent_source" | "wrong_number"
    reason: str
    sentence: str


@dataclass(frozen=True)
class GuardResult:
    text: str
    removals: tuple[Removal, ...]

    @property
    def applied(self) -> bool:
        return bool(self.removals)

    @property
    def removed_everything(self) -> bool:
        """True when the guard removed every claim the model made: what is left is markers
        (and perhaps headings), so the reply is no longer an answer from the sources."""
        return self.applied and not claims.units(self.text)


def _fmt(value: float, unit: str) -> str:
    if unit == "count":
        return str(int(value)) if float(value).is_integer() else f"{value:g}"
    text = f"{value:.1f}"
    return f"{text}%" if unit == "percent" else text


def _player_name(player_id: str, manifest: Manifest) -> str:
    player = manifest.player(player_id)
    return player.name if player is not None else player_id


def _removable(number: NumberVerdict, analysis: SentenceAnalysis, manifest: Manifest) -> bool:
    if number.verdict not in ("contradicts", "unavailable"):
        return False
    if number.attribution != "named" or not number.certain:
        # Precision first: a number tied to a player only by a pronoun, by position or by
        # "it", or whose statistic had to be inferred, may be misread by *us*. It keeps its
        # warning; it is never deleted.
        return False
    # "Reed shoots 34.1% from three — a 45% shooter would be elite": a sentence that also
    # states the documented value is comparing, not asserting the other number.
    if any(
        other is not number and other.verdict == "match" and other.stat == number.stat and set(other.subject_ids) & set(number.subject_ids)
        for other in analysis.numbers
    ):
        return False
    # "Shooting volume low (2.6 ppg)": the player's documented value under another label is a
    # mislabel the checker cannot tell from an invention — warned about, not deleted.
    return not (
        number.verdict == "contradicts"
        and number.value is not None
        and any(
            f.stat != number.stat and claims.numbers_match(number.value, f.value)
            for pid in number.subject_ids
            for f in manifest.facts_for(pid)
        )
    )


def _number_reason(number: NumberVerdict, manifest: Manifest) -> str:
    definition = manifest.stats[number.stat]
    names = [p.name for pid in number.subject_ids if (p := manifest.player(pid)) is not None]
    stat = f"the {definition.label} of {' or '.join(names)}" if names else f"a player's {definition.label}"
    if number.verdict == "unavailable":
        gaps = [g for pid in number.subject_ids if (g := manifest.unavailable_fact(pid, number.stat)) is not None]
        detail = gaps[0].reason if gaps else "no source records it"
        # plain ids, never "(S02)": a marker must not read as a citation
        detail = re.sub(r"\((S\d{1,3}(?:\s*,\s*S\d{1,3})*)\)", r"\1", detail)
        return f"it gave {stat} as {number.text}, a statistic no source records ({detail.rstrip('.')})"
    documented = "; ".join(
        # plain ids, never "[S04]": a marker must not read as a citation
        f"{_player_name(f.entity_id, manifest)} {_fmt(f.value, definition.unit)} (documented in {', '.join(f.source_ids)})"
        for pid in number.subject_ids
        if (f := manifest.fact(pid, number.stat)) is not None
    )
    return f"it gave {stat} as {number.text}; the fact sheet has {documented}"


def _reasons(analysis: SentenceAnalysis, manifest: Manifest) -> list[tuple[str, str, int]]:
    """`(code, reason, position in the unit's text)` for each thing in this unit that must
    not reach the GM. A sentence gets at most one; a table row one per offending cell."""
    sentence = analysis.text
    found: list[tuple[str, str, int]] = []
    # A statement of absence names a verdict or a score in order to deny it ("Not in the
    # sources: proof the trade is CBA-approved"), so the phrase checks skip it. A *number* or
    # a *fake source id* is a claim wherever it sits — a confused model does put real (and
    # wrong) statistics under a "Not in the sources" heading — so those checks never skip.
    if not claims.is_absence_statement(analysis.section, sentence):
        # sentence-level predicates: the text is already segmented, and re-segmenting a lone
        # "**9/10**" would read it as a bold heading and miss it
        verdict = claims.legality_verdict_match(sentence)
        if verdict is not None:
            found.append(("legality_verdict", "it stated a trade-legality verdict; the sandbox has no contract data or CBA engine", verdict.start()))
        elif claims.is_fit_score(sentence):
            found.append(("fit_score", "it gave a numeric fit or impact score, which nothing in the sandbox can compute", _first_digit(sentence)))
        else:
            lowered = sentence.lower()
            for canary in manifest.injection_canaries:
                if not re.fullmatch(r"S\d{1,3}", canary) and canary.lower() in lowered:
                    found.append(("injection_canary", "it repeated a phrase planted by an instruction inside an untrusted source", lowered.index(canary.lower())))
                    break
    # the unit's own citations: a fake id in a bold label stays visible with the label
    fake = [sid for sid in claims.extract_citations(sentence) if manifest.document(sid) is None]
    if fake:
        found.append(("nonexistent_source", f"it cited {', '.join(fake)}, a source that does not exist in the corpus", _citation_at(sentence, fake[0])))
    for number in analysis.numbers:
        if _removable(number, analysis, manifest):
            found.append(("wrong_number", _number_reason(number, manifest), number.start))
    if analysis.unit is None or not analysis.unit.cells:
        return found[:1]
    return found


def _first_digit(sentence: str) -> int:
    """Position of the first digit outside a citation — where a score sits in a table row."""
    blocks = [m.span() for m in re.finditer(r"[\[(]\s*S\d{1,3}[^\])]*[\])]", sentence)]
    return next((i for i, ch in enumerate(sentence) if ch.isdigit() and not any(a <= i < b for a, b in blocks)), 0)


def _citation_at(sentence: str, sid: str) -> int:
    match = re.search(rf"S0*{int(sid[1:])}\b", sentence, re.IGNORECASE)
    return match.start() if match else 0


def _marker(reason: str, *, in_table: bool = False) -> str:
    text = f"{MARKER_PREFIX}{reason.rstrip('.')}.{MARKER_SUFFIX}"
    return text.replace("|", "/") if in_table else text


def guard_answer(answer: str, entities: EntityResolution, manifest: Manifest) -> GuardResult:
    """Return the answer with every provably unsupported sentence (or table cell) replaced
    by a marker, and nothing else changed."""
    lines = answer.splitlines()
    edits: dict[int, dict[tuple[int, int], str]] = {}
    removals: list[Removal] = []
    for analysis in analyze_answer(answer, entities, manifest):
        unit = analysis.unit
        if unit is None:
            continue
        for code, reason, position in _reasons(analysis, manifest):
            line_edits = edits.setdefault(unit.line, {})
            if unit.cells:
                cell = next((c for c in unit.cells if c.start <= position < c.end), unit.cells[-1])
                span = (cell.line_start, cell.line_end)
                if span in line_edits:
                    continue  # one marker per cell
                line_edits[span] = _marker(reason, in_table=True)
                removals.append(Removal(code, reason, lines[unit.line].strip()))
            else:
                line_edits[(unit.start, unit.end)] = _marker(reason)
                removals.append(Removal(code, reason, analysis.text))
    if not removals:
        return GuardResult(answer, ())
    for index, line_edits in edits.items():
        line = lines[index]
        for (start, end), marker in sorted(line_edits.items(), reverse=True):
            line = line[:start] + marker + line[end:]
        lines[index] = line
    return GuardResult("\n".join(lines), tuple(removals))
