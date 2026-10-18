"""Deterministic text analysis shared by the runtime grounding checks and the evaluators.

One module, used by both sides, so the assistant is never checked by different rules than
the ones it is scored by. Everything here is plain Python and deliberately simple: sentence
splitting, citation extraction, number extraction, alias mentions, stat-keyword detection,
negation and a few phrase lexicons. It does not "understand" an answer. It finds the
bounded, checkable claims in it — a number attached to a player and a statistic, a letter
grade, a citation id, a legality verdict — so they can be compared with structured gold.

The units of analysis come from `units`: one per sentence of prose, one per markdown table
row (linearised with its column headers), never one for the guard's own removal markers.
How a number is read — its statistic, whether it is an age, a derived comparison, a bound
or a value the answer rejects — comes from `read_numbers`. The runtime checks, the guard,
the claim labels and the scorers all start from these two functions.

Anything it cannot check is reported as uncheckable, never as correct.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache

from app.assistant_rag.manifest import Manifest

# ------------------------------------------------------------------------------ markers

#: The guard's removal marker. It is defined here rather than in `guard.py` because every
#: reader of an answer must recognise it: a marker is the guard's note, never the model's
#: claim, so the checks, the claim labels and the scorers all skip it. Its contents name
#: players, numbers and abstention phrases ("no contract data"), which would otherwise be
#: read as the model's own statements — and earn it credit for declining.
MARKER_PREFIX = "⟨Removed by Ask Pivot's checks: "
MARKER_SUFFIX = "⟩"
_MARKER = re.compile(re.escape(MARKER_PREFIX) + r"[^⟩]*" + re.escape(MARKER_SUFFIX))


def strip_markers(text: str) -> str:
    """The answer without the guard's removal markers: what the model wrote that the GM
    still reads."""
    return _MARKER.sub("", text)


def is_marker(text: str) -> bool:
    return bool(_MARKER.fullmatch(text.strip()))


# ---------------------------------------------------------------------------- citations

#: `[S03]`, `[S03, S05]`, `[S03; S05]`, `[S3]` — and the same in parentheses, `(S03)`, which the
#: local 2B model writes about as often as brackets despite the prompt asking for brackets (seen
#: in the live UI: a fully cited answer was reported as "no citations"). A source id is
#: unambiguous in this corpus, so either delimiter counts. Ids are normalised to `S03`.
_CITATION_BLOCK = re.compile(r"[\[(]\s*(S\d{1,3}(?:\s*[,;/&]\s*S\d{1,3})*)\s*[\])]", re.IGNORECASE)
_CITATION_ID = re.compile(r"S(\d{1,3})", re.IGNORECASE)


def normalise_source_id(raw: str) -> str:
    match = _CITATION_ID.fullmatch(raw.strip())
    if not match:
        return raw.strip().upper()
    return f"S{int(match.group(1)):02d}"


#: An id *named* rather than *used*: "it asked me to cite source [S99]", "cited as [S99]".
#: Reporting what an injected instruction said is not citing it.
#: Only verbs *of citing* mark a mention: "according to source [S04], ..." is a real citation.
_MENTION_BEFORE = re.compile(
    r"\b(?:cit(?:e|es|ed|ing))\s+(?:it\s+|this\s+|them\s+)?(?:as\s+)?(?:a\s+)?(?:fake\s+|nonexistent\s+)?(?:source\s+)?(?:id\s+)?$"
    r"|\b(?:cit(?:e|es|ed|ing))\s+(?:an?\s+|the\s+)?(?:[\w-]+\s+){0,2}(?:source|id|reference)\s+$",
    re.IGNORECASE,
)


def _is_mention(text: str, start: int) -> bool:
    return bool(_MENTION_BEFORE.search(text[max(0, start - 40) : start]))


def extract_citations(text: str) -> list[str]:
    """Every cited source id, in order of first appearance, de-duplicated. An id that the
    text only *mentions* ("instructed to cite source [S99]") is not a citation, and nothing
    inside a guard marker is (a marker quoting "the guard profile (S02)" cites nothing)."""
    markers = [m.span() for m in _MARKER.finditer(text)]
    seen: list[str] = []
    for block in _CITATION_BLOCK.finditer(text):
        if _is_mention(text, block.start()) or any(a <= block.start() < b for a, b in markers):
            continue
        for raw in _CITATION_ID.finditer(block.group(1)):
            sid = normalise_source_id(raw.group(0))
            if sid not in seen:
                seen.append(sid)
    return seen


def strip_citations(text: str) -> str:
    return _CITATION_BLOCK.sub("", text)


# ---------------------------------------------------------------------------- sentences

#: Abbreviations whose full stop does not end a sentence. Matched as whole words only
#: ("st." is St. Louis, never the end of "most." or "best."), and "No." only before a number
#: ("No. 1 option"): "No. Reed shoots 34.1%" is the answer "No." and a new sentence.
_ABBREVIATION = re.compile(r"(?<![\w.])(?:vs|e\.g|i\.e|approx|jr|sr|st|no(?=\.\s*\d))\.", re.IGNORECASE)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[*])")
_LINE_MARKUP = re.compile(r"^\s*(?:(?:[-*•]|\d+[.)])\s+)?(?:#+\s*)?")
_PROTECTED_DOT = "․"  # same length as ".", so protected text keeps every offset


def _prose_spans(line: str, lo: int, hi: int) -> list[tuple[int, int]]:
    """Sentence spans of `line[lo:hi]`, as offsets into `line`. A citation that trails the
    full stop (`... defender. [S03]`) stays with its sentence."""
    segment = line[lo:hi]
    protected = _ABBREVIATION.sub(lambda m: m.group(0).replace(".", _PROTECTED_DOT), segment)
    pieces: list[tuple[int, int]] = []
    pos = 0
    for brk in _SENTENCE_BREAK.finditer(protected):
        pieces.append((pos, brk.start()))
        pos = brk.end()
    pieces.append((pos, len(segment)))
    out: list[tuple[int, int]] = []
    for a, b in pieces:
        while a < b and segment[a].isspace():
            a += 1
        while b > a and segment[b - 1].isspace():
            b -= 1
        if a == b:
            continue
        if out and _CITATION_BLOCK.fullmatch(segment[a:b]):
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return [(lo + a, lo + b) for a, b in out]


def sentence_spans(line: str) -> list[tuple[int, int]]:
    """`(start, end)` of each sentence in one line, after its bullet or heading markup. The
    spans are exact: `line[start:end]` is the sentence as written, so the guard can replace
    one sentence without re-typing its neighbours. A guard marker is never a sentence."""
    markup = _LINE_MARKUP.match(line)
    start = markup.end() if markup else 0
    spans: list[tuple[int, int]] = []
    cursor = start
    for marker in _MARKER.finditer(line, start):
        spans += _prose_spans(line, cursor, marker.start())
        cursor = marker.end()
    spans += _prose_spans(line, cursor, len(line))
    return spans


def split_sentences(text: str) -> list[str]:
    """Split prose and markdown into claim-sized units.

    Bullets and line breaks end a unit; so do `.`, `!`, `?` followed by whitespace and an
    uppercase letter, digit, quote or bracket. Decimals (`40.4`) and whole-word abbreviations
    (`vs.`, `e.g.`, `No. 1`) do not. A citation that trails the full stop
    (`... defender. [S03]`) stays with its sentence. Guard markers are skipped."""
    out: list[str] = []
    for line in text.splitlines():
        out += [line[a:b] for a, b in sentence_spans(line)]
    return out


# --------------------------------------------------------------------------- sections

_HEADING_LINE = re.compile(
    r"^\s*(?:#{1,6}\s*)?\*\*\s*(?P<label>[^*]{2,60}?)\s*:?\s*\*\*\s*:?\s*$"
    r"|^\s*#{1,6}\s+(?P<hlabel>[^#*].{1,60}?)\s*:?\s*$"
    r"|^\s*(?P<plabel>[A-Z][A-Za-z ]{2,40}):\s*$"
)
#: "**My read:** text" or "**My read**: text" — a label needs its colon. "**Mateo Cruz** projects
#: as..." is emphasis on the sentence's subject, not a label; treating it as one strips the
#: player's name out of the sentence.
_LABEL_PREFIX = re.compile(
    r"^\s*(?:[-*•]\s+|\d+[.)]\s+)?\*\*\s*(?P<label>[^*]{2,40}?)\s*(?::\s*\*\*|\*\*\s*:)\s*(?P<rest>\S.*)$"
)
#: Headings that announce missing *evidence* — "Not in the sources", "What the sources don't
#: say", "Unavailable", "Gaps in the evidence", "Unknowns". Not a team's needs: "What the
#: Comets are missing" or "Roster gaps" head claims, and must not switch the checks off.
ABSENCE_SECTION = re.compile(
    r"\bnot\s+(?:in|covered\s+(?:by|in)|found\s+in|documented\s+in)\s+(?:the|any|our)\s+(?:sources?|documents?|corpus|evidence|data|record)\b"
    r"|\bnot\s+(?:available|documented|recorded)\b|\bunavailable\b"
    r"|\bwhat\s+the\s+(?:sources?|documents?|evidence|data|record)\s+(?:don't|do\s+not|doesn't|does\s+not|can't|cannot)\b"
    r"|\b(?:evidence|data|information|source|sourcing)\s+gaps?\b|\bgaps?\s+in\s+the\s+(?:evidence|sources?|data|record|documents?)\b"
    r"|\bmissing\s+(?:evidence|data|information|statistics|stats|from\s+the\s+(?:sources?|documents?|corpus|data))\b"
    r"|\bunknowns?\b|\bwhat\s+(?:we|i)\s+(?:don't|do\s+not|can't|cannot)\s+know\b|\bopen\s+questions\b"
    r"|\bno\s+(?:source|evidence|data)\b",
    re.IGNORECASE,
)
_ABSENCE_PREFIX = re.compile(
    r"^\s*(?:not\s+in\s+the\s+sources|no\s+source\b|nothing\s+in\s+the\s+sources|none\s+of\s+the\s+sources)", re.IGNORECASE
)
#: How an item under an evidence-gap heading reads when it *names* a gap: a noun phrase
#: ("Evidence that the trade is approved", "Playoff shooting for Vance"), a question word
#: ("Whether either trade is legal"), or a denial ("No medical records"). "The trade is
#: legal under the CBA" under the same heading is an assertion, not a gap.
_ABSENCE_ITEM_START = re.compile(
    r"^\s*(?:not\s+in\s+the\s+sources|no|none|nothing|neither|there\s+(?:is|are)\s+no|evidence|confirmation|proof|"
    r"documentation|any|whether|how|what|which|why|who|when|data|records?|information|details?|figures?|numbers?|"
    r"statistics?|stats|a\s+(?:record|figure|number|source|grade|statement|breakdown|split))\b",
    re.IGNORECASE,
)
_FINITE_VERB = re.compile(
    r"\b(?:is|are|was|were|be|been|has|have|had|does|do|did|will|would|can|could|should|must|may|might|"
    r"shoots?|averages?|makes?|matches?|match|works?|fits?|scores?|rates?|grades?|gets?|gives?|helps?|"
    r"fixes?|defends?|guards?|creates?|provides?|stays?|holds?|struggles?|leads?)\b",
    re.IGNORECASE,
)


def heading_label(line: str) -> str | None:
    """The label if `line` is only a heading ("**My read**", "### Unknowns", "Not in the
    sources:"), else None."""
    heading = _HEADING_LINE.match(line)
    if not heading:
        return None
    return (heading.group("label") or heading.group("hlabel") or heading.group("plabel") or "").strip()


def label_prefix(line: str) -> tuple[str, str, str] | None:
    """`(label, rest, literal prefix)` for a line that opens with a bold label
    ("- **My read:** Vance fits" -> ("My read", "Vance fits", "- **My read:** ")), else None."""
    labelled = _LABEL_PREFIX.match(line)
    if not labelled:
        return None
    return labelled.group("label").strip(), labelled.group("rest"), line[: labelled.start("rest")]


# ------------------------------------------------------------------------------- tables

_TABLE_SEPARATOR = re.compile(r":?-{2,}:?")


def _table_cells(line: str) -> list[tuple[int, int]] | None:
    """`(start, end)` of each cell's content in a markdown table row, or None if `line` is
    not a table row."""
    stripped = line.strip()
    if not (len(stripped) > 1 and stripped.startswith("|") and stripped.endswith("|")):
        return None
    lo, hi = line.index("|"), line.rindex("|")
    bars = [lo, *(i for i in range(lo + 1, hi) if line[i] == "|" and line[i - 1] != "\\"), hi]
    cells: list[tuple[int, int]] = []
    for a, b in zip(bars, bars[1:], strict=False):
        s, e = a + 1, b
        while s < e and line[s].isspace():
            s += 1
        while e > s and line[e - 1].isspace():
            e -= 1
        cells.append((s, e))
    return cells


def linearise_tables(text: str) -> str:
    """Rewrite markdown table rows as labelled sentences, so a row is read like prose.

    | Player     | 3P%   | 3PA |          Player Theo Vance, 3P% 40.4%, 3PA 6.1.
    |------------|-------|-----|    ->
    | Theo Vance | 40.4% | 6.1 |

    Each cell is prefixed with its column header, which puts the stat keyword next to the
    number it labels; the comma-separated cells then attach to the row's player. `units`
    reads tables the same way (and keeps each cell's position, for the guard)."""
    out: list[str] = []
    header: list[str] | None = None
    for line in text.splitlines():
        cells = _table_cells(line)
        if cells is not None:
            contents = [line[a:b] for a, b in cells]
            if all(_TABLE_SEPARATOR.fullmatch(c) for c in contents if c):
                continue
            if header is None:
                header = contents
                continue
            out.append(", ".join(f"{h} {c}".strip() for h, c in zip(header, contents, strict=False) if c) + ".")
            continue
        header = None
        out.append(line)
    return "\n".join(out)


# -------------------------------------------------------------------------------- units


@dataclass(frozen=True, slots=True)
class Cell:
    """One table cell: its "header value" segment in the unit's text, and its content's
    span in the original line."""

    start: int
    end: int
    line_start: int
    line_end: int


@dataclass(frozen=True, slots=True)
class Unit:
    """One claim-sized piece of an answer, and where it sits in the original text.

    `text` is what the checks read: a sentence exactly as written, or a table row
    linearised with its headers. `line`, `start` and `end` locate it in
    `answer.splitlines()` (a table row spans its whole line; `cells` locate each cell).
    `label` is set on the first sentence after a bold label on the same line
    ("**Theo Vance:** 30.4% from three") — the label names that sentence's subject as surely
    as the sentence would itself."""

    section: str | None
    text: str
    line: int
    start: int
    end: int
    label: str | None = None
    cells: tuple[Cell, ...] = ()


def _table_unit(section: str | None, index: int, line: str, header: Sequence[str], row: Sequence[tuple[int, int]]) -> Unit | None:
    parts: list[str] = []
    cells: list[Cell] = []
    pos = 0
    for head, (a, b) in zip(header, row, strict=False):
        content = line[a:b]
        if not content or is_marker(content):
            continue
        if parts:
            pos += 2  # ", "
        piece = f"{head} {content}".strip()
        cells.append(Cell(pos, pos + len(piece), a, b))
        parts.append(piece)
        pos += len(piece)
    if not parts:
        return None
    return Unit(section, ", ".join(parts) + ".", index, 0, len(line), None, tuple(cells))


def units(text: str) -> list[Unit]:
    """Every claim-sized unit, in order, with its section. A line that is only a heading —
    bold ("**My read**"), markdown ("### Unknowns") or a bare label ("Not in the sources:")
    — sets the section for what follows; "**My read:** text" labels its own line (and the
    lines after it). Table rows are linearised; guard markers are skipped."""
    out: list[Unit] = []
    section: str | None = None
    header: list[str] | None = None
    for index, line in enumerate(text.splitlines()):
        row = _table_cells(line)
        if row is not None:
            contents = [line[a:b] for a, b in row]
            if all(_TABLE_SEPARATOR.fullmatch(c) for c in contents if c):
                continue
            if header is None:
                header = contents
                continue
            unit = _table_unit(section, index, line, header, row)
            if unit is not None:
                out.append(unit)
            continue
        header = None
        if not line.strip():
            continue
        heading = _HEADING_LINE.match(line)
        if heading:
            found = heading.group("label") or heading.group("hlabel") or heading.group("plabel") or ""
            section = found.strip() or section
            continue
        labelled = _LABEL_PREFIX.match(line)
        offset, label = 0, None
        if labelled:
            label = labelled.group("label").strip()
            section = label
            offset = labelled.start("rest")
        for i, (a, b) in enumerate(sentence_spans(line[offset:])):
            out.append(Unit(section, line[offset + a : offset + b], index, offset + a, offset + b, label if i == 0 else None))
    return out


def unit_citations(unit: Unit) -> list[str]:
    """The ids a unit cites — its own, plus those in the bold label that opens its line:
    "**Scout (S03):** Rates Reed's defense an A" attributes the grade to S03."""
    own = extract_citations(unit.text)
    if unit.label is None:
        return own
    return list(dict.fromkeys([*own, *extract_citations(unit.label)]))


def segments(text: str) -> list[tuple[str | None, str]]:
    """`(section, sentence)` pairs in order — `units` without the positions."""
    return [(u.section, u.text) for u in units(text)]


def is_absence_heading(section: str | None) -> bool:
    return bool(section and ABSENCE_SECTION.search(section))


def is_absence_statement(section: str | None, sentence: str) -> bool:
    """True for a sentence that says something is *not* documented: one starting "Not in the
    sources:" / "No source ...", or an item under an evidence-gap heading that reads as a gap
    (a noun phrase, a question word or a denial — `_ABSENCE_ITEM_START`, or no finite verb at
    all). Such a sentence names a claim in order to deny it; checks for asserted claims skip
    it. The heading alone is not enough: "The trade is legal" under "Unknowns" asserts."""
    if _ABSENCE_PREFIX.search(sentence):
        return True
    if not is_absence_heading(section):
        return False
    body = strip_citations(sentence)
    return bool(_ABSENCE_ITEM_START.search(body) or not _FINITE_VERB.search(body))


# ------------------------------------------------------------------------------ numbers


@dataclass(frozen=True, slots=True)
class NumberMention:
    value: float
    text: str
    is_percent: bool
    start: int
    end: int


# A number that is not part of a word, a source id (S03), a season (2025-26), a date
# (2026-01-15), a height (6-7) or a year. Optional percent sign / word.
_NUMBER = re.compile(
    r"(?<![\w.\-/])(\d{1,3}(?:\.\d+)?|\.\d{3})(?!\d)(?:\s*(%|percent\b|per\s+cent\b|pct\b))?(?![\w\-/])",
    re.IGNORECASE,
)
_SPAN_EXCLUSIONS = re.compile(
    r"\bS\d{1,3}\b"  # source ids
    r"|\b\d{4}-\d{2}(?:-\d{2})?\b"  # seasons and dates
    r"|\b\d{4}\b"  # years
    r"|\b\d-\d{1,2}\b"  # heights
    r"|\b\d+(?:st|nd|rd|th)\b"  # ordinals
    r"|\b\d+\s*(?:-|to)\s*\d+\s*(?:win|loss|record|series)",  # records
    re.IGNORECASE,
)


def extract_numbers(sentence: str) -> list[NumberMention]:
    """Numbers a reader would take as a statistic. `.404` is read as 40.4%."""
    blocked: list[tuple[int, int]] = [m.span() for m in _SPAN_EXCLUSIONS.finditer(sentence)]
    blocked += [m.span() for m in _CITATION_BLOCK.finditer(sentence)]
    found: list[NumberMention] = []
    for match in _NUMBER.finditer(sentence):
        start, end = match.span()
        if any(b0 <= start < b1 for b0, b1 in blocked):
            continue
        raw = match.group(1)
        is_percent = bool(match.group(2))
        if raw.startswith("."):
            value = round(float(raw) * 100, 1)
            is_percent = True
        else:
            value = float(raw)
        found.append(NumberMention(value=value, text=match.group(0).strip(), is_percent=is_percent, start=start, end=end))
    return found


def numbers_match(a: float, b: float, tolerance: float = 0.05) -> bool:
    return abs(a - b) <= tolerance


def value_matches(number: NumberMention, gold: float) -> bool:
    """Exact (±0.05), or a whole-number rounding of the gold value written as a whole number
    ("40%" for 40.4%). One rounding rule for the runtime checks and the citation scorer: a
    rounded value is not a contradiction. `fact_correctness` alone asks for the exact value."""
    if numbers_match(number.value, gold):
        return True
    return "." not in number.text and float(number.value).is_integer() and round(gold) == number.value


# ------------------------------------------------------------------------ number reading

#: The answer disowns a number it repeats: "the claim that Reed shoots 45%", "not 45%",
#: "does not shoot 45%", "nowhere near 45%", "if he shot 45%". Read in the text between the
#: previous number and this one, so a rejection of one number never reaches the next.
_REJECTED_BEFORE = re.compile(
    r"(?:\b(?:do(?:es)?\s+not|don't|doesn't|cannot|can't)\s+(?:support|confirm|show|back)\b[^.;:]{0,40}$"
    r"|\bclaim(?:s|ed)?\s+that\b[^.;:]{0,30}$|\bthe\s+(?:claim|premise|idea|assertion|suggestion|notion)\b[^.;:]{0,30}$"
    r"|\byour\s+(?:premise|figure|number|claim)\b[^.;:]{0,20}$"
    r"|\b(?:rather\s+than|instead\s+of|not|never|isn't|wasn't|nor)\s+(?:even\s+)?(?:the\s+|a\s+|an\s+)?(?:quoted\s+)?$"
    # a negated shooting verb: "does not shoot 45%", "has never shot 45%" (not "never hesitates and hits")
    r"|(?:\bnot|\bnever|n't)\s+(?:even\s+|really\s+|actually\s+|quite\s+)?(?:shoot|shot|shoots|hit|hits|make|made|makes|average|averages|"
    r"averaged|convert|converts|converted|post|posts|posted|reach|reached|top|crack|approach|touch)\s+"
    r"(?:(?:anywhere\s+)?(?:near|close\s+to)\s+|(?:about|around|roughly|over|above)\s+)?(?:the\s+|a\s+)?$"
    # a comparison that sets the number aside: "nowhere near 45%", "well short of 45%"
    r"|\b(?:nowhere\s+(?:near|close\s+to)|not\s+(?:even\s+)?(?:near|close\s+to)|far\s+(?:from|short\s+of|below|under|off)|"
    r"well\s+short\s+of|a\s+long\s+way\s+(?:from|off|short\s+of|below))\s+(?:the\s+|a\s+|your\s+)?$"
    # a hypothetical: "if he shot 45%", "even if Reed hit 45%"
    r"|\b(?:if|unless)\s+(?:[\w'’]+\s+){0,3}$)",
    re.IGNORECASE,
)
_REJECTED_AFTER = re.compile(
    r"^[^.;]{0,50}?\b(?:does\s+not\s+appear|doesn't\s+appear|appears\s+nowhere"
    r"|(?:is\s+not|isn't)\s+(?:in\s+(?:the|any|our)\s+\w+|supported|documented|recorded|correct|accurate|right|true|his)"
    r"|is\s+(?:incorrect|wrong|inaccurate|unsupported|mistaken|false|invented|fabricated|overstated)"
    r"|(?:was|is)\s+(?:cited|quoted|given|mentioned)\s+in\s+(?:the|your)\s+(?:user\s+)?(?:prompt|question)"
    r"|cited\s+in\s+(?:the|your)\s+(?:user\s+)?(?:prompt|question)|not\s+found|overstates?|misstates?)\b"
    # a hypothetical benchmark: "a 45% shooter would be elite", "45% would be elite"
    r"|^\s*(?:(?:three[\s-]point|3-point)\s+)?(?:shooter|spacer|mark|figure|number|clip)?\s*would\b",
    re.IGNORECASE,
)
#: Quoting the GM ("you said 45%") rejects the number only when the sentence then turns
#: against it; agreeing ("as you said", "you're right that") is an acceptance, checked like
#: any other claim.
_QUOTED_PREMISE = re.compile(
    r"\byou\s+(?:said|say|mentioned|cited|quoted|suggested|heard|claimed|wrote|asked\s+about)\b"
    r"|\bthe\s+(?:premise|question)\s+(?:says|said|assumes|states)\b|\bin\s+your\s+question\b",
    re.IGNORECASE,
)
_AGREEMENT = re.compile(
    r"\b(?:as|like|just\s+as|exactly\s+as)\s+you\s+(?:said|say|mentioned|noted|suggested|pointed\s+out|put\s+it)\b"
    r"|\byou(?:'re|’re|\s+are)\s+(?:right|correct)\b|\b(?:i\s+agree|agreed|exactly|indeed|that's\s+right|that\s+is\s+right)\b",
    re.IGNORECASE,
)
_PREMISE_CONTRAST = re.compile(
    r"\b(?:but|however|yet|actually|in\s+fact|whereas|though|although|instead|rather)\b"
    r"|\b(?:not|isn't|wasn't|doesn't|does\s+not)\b|\b(?:wrong|incorrect|inaccurate|mistaken|overstate[sd]?|unsupported)\b",
    re.IGNORECASE,
)


def number_is_rejected(sentence: str, number: NumberMention, others: Sequence[NumberMention] = ()) -> bool:
    """True when the sentence repeats a number only to reject it (the GM's false premise, a
    hypothetical, a comparison that sets it aside). `others` are the sentence's other
    numbers: a rejection is read only up to the neighbouring number."""
    prev_end = max((o.end for o in others if o.end <= number.start and o is not number), default=0)
    next_start = min((o.start for o in others if o.start >= number.end and o is not number), default=len(sentence))
    before = sentence[max(prev_end, number.start - 70) : number.start]
    if _REJECTED_BEFORE.search(before):
        return True
    if _REJECTED_AFTER.match(sentence[number.end : min(next_start, number.end + 90)]):
        return True
    head = sentence[: number.start]
    if _QUOTED_PREMISE.search(sentence[max(0, number.start - 70) : number.start]) and not _AGREEMENT.search(head):
        return bool(_PREMISE_CONTRAST.search(sentence[number.end :]))
    return False


#: A number that is not the player's statistic but something computed from or compared
#: with one: a difference ("3.5 more than Reed", "6.3 points better"), a ratio ("2.3 times"),
#: made shots ("2.5 made threes"), a denominator ("79 of 82"), games missed, a league
#: average, a threshold, a target ("would need 35%"), an earlier season ("up from 5.9").
_DERIVED_AFTER = re.compile(
    r"^\s*(?:more|fewer|less|higher|lower|better|worse|greater|smaller|extra|additional)\b"
    r"|^\s*(?:percentage[\s-]+)?(?:points?|pts?)\s+(?:more|fewer|less|higher|lower|better|worse|above|below|ahead|behind|clear|"
    r"short|off|up|down|over|under|than|of\s+(?:separation|difference)|difference|gap|edge|advantage|swing)\b"
    r"|^\s*percentage[\s-]+points?\b|^\s*(?:pp|ppts?)\b"
    r"|^\s*times\b"
    r"|^\s*(?:makes?|made|connections?)\b"
    r"|^\s*(?:threshold|cutoff|cut-off|benchmark|bar)\b"
    r"|^\s*(?:league[\s-]average|league-wide)\b"
    r"|^\s*(?:[\w%]+\s+){0,3}?(?:last|previous|prior)\s+season\b",
    re.IGNORECASE,
)
_DERIVED_BEFORE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:of|out\s+of)\s+(?:every\s+|the\s+|his\s+|their\s+)?$"
    r"|\b(?:missed|sat\s+out|sat|skipped|missing)\s+(?:only\s+|just\s+|about\s+|roughly\s+|another\s+)?$"
    r"|\b(?:league|league-wide|leaguewide|positional|position|team|wing|guard)(?:'s)?\s+average\s+"
    r"(?:is\s+|was\s+|of\s+|sits\s+at\s+|at\s+|around\s+)?(?:about\s+|roughly\s+|around\s+|just\s+)?$"
    r"|\b(?:threshold|cutoff|benchmark|bar|standard)\s+(?:of\s+|is\s+|at\s+)?(?:about\s+|roughly\s+)?$"
    r"|\b(?:needs?|needed|would\s+need|requires?|required)\s+(?:to\s+(?:shoot|hit|make|reach|get\s+to|be|average)\s+)?"
    r"(?:at\s+least\s+|about\s+|roughly\s+|around\s+|something\s+like\s+)?$"
    r"|\b(?:up|down)\s+from\s+(?:about\s+|roughly\s+)?$"
    r"|\bby\s+(?:about\s+|roughly\s+|nearly\s+|almost\s+|just\s+|only\s+)?$"
    r"|\bper\s+$",
    re.IGNORECASE,
)
#: A bound rather than a value: "below 45%", "more than 6 attempts". It states no exact
#: value, so it cannot match or contradict one; for a statistic no source records, it is
#: still a number for it.
_BOUND_BEFORE = re.compile(
    r"\b(?:(?:well|way|just|slightly|much|a\s+bit|comfortably)\s+)?(?:below|under|beneath|above|short\s+of|lower\s+than|"
    r"less\s+than|higher\s+than|more\s+than|greater\s+than|better\s+than|worse\s+than|at\s+least|at\s+most|north\s+of|"
    r"south\s+of|upwards\s+of|in\s+excess\s+of)\s+(?:the\s+|a\s+|an\s+|about\s+|roughly\s+|around\s+)?$",
    re.IGNORECASE,
)
_AGE_AFTER = re.compile(r"^\s*(?:-\s*)?(?:years?[\s-]+old|year[\s-]old|yrs?\b|-year|yo\b)", re.IGNORECASE)
_AGE_BEFORE = re.compile(r"(?:\bage[sd]?|\baged|\bturns?|\bturned|\bno\.|#|\btop|\branked|\branks)\s*$", re.IGNORECASE)
_AGE_COPULA = re.compile(r"\b(?:is|was|turns|turned|just|only|now|at)\s+$", re.IGNORECASE)
_AGE_FOLLOW = re.compile(r"\s*(?:,|;|\)|\.(?:\s|$)|$|(?:and|with|but)\b)", re.IGNORECASE)
_PER_GAME_AFTER = re.compile(
    r"^\s*(?:(?:three[\s-]?point(?:ers?)?|3[\s-]?point(?:ers?)?|threes?|3s)\s+)?(?:attempts?\s+|tries\s+|shots?\s+)?"
    r"(?:per\s+game|a\s+game|per\s+contest|a\s+night|per\s+night)"
    r"|^\s*(?:three[\s-]?point\s+|3[\s-]?point\s+)?attempts?\b",
    re.IGNORECASE,
)
_MADE_BEFORE = re.compile(
    r"\b(?:makes?|made|hits?|hitting|connects?|connected|drains?|drained|converts?|converted)\s+(?:just\s+|only\s+|about\s+|around\s+)?$",
    re.IGNORECASE,
)
_SHOOTING_CUE = re.compile(r"\bshoot(?:s|ing|er)?\b|\bshot\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class NumberReading:
    """One number in a unit and how it reads.

    `status` is "claim" (a value of `stat`), "bound" ("below 45%"), "rejected" (the answer
    disowns it, or it is only the reference point of a comparison), "derived" (computed from
    or compared with a statistic, not the statistic itself) or "age" (an age or a rank).
    `inferred` marks a stat from the "shoots N%" fallback rather than a keyword."""

    number: NumberMention
    stat: str | None
    inferred: bool
    status: str


@lru_cache(maxsize=4)
def _stat_patterns(manifest: Manifest) -> tuple[tuple[str, re.Pattern[str]], ...]:
    rows = []
    for key, definition in manifest.stats.items():
        words = sorted(definition.keywords, key=len, reverse=True)
        alternation = "|".join(re.escape(w) for w in words)
        rows.append((key, re.compile(rf"(?<![\w-])(?:{alternation})(?![\w-])", re.IGNORECASE)))
    return tuple(rows)


def stat_keyword_spans(sentence: str, stat: str, manifest: Manifest) -> list[tuple[int, int]]:
    for key, pattern in _stat_patterns(manifest):
        if key == stat:
            return [m.span() for m in pattern.finditer(sentence)]
    return []


def stats_mentioned(sentence: str, manifest: Manifest) -> list[str]:
    """Stat keys whose keywords appear in the sentence, e.g. ['three_pt_pct']."""
    return [key for key, pattern in _stat_patterns(manifest) if pattern.search(sentence)]


def nearest_stat_for_number(sentence: str, number: NumberMention, manifest: Manifest) -> str | None:
    """The stat keyword closest to a number in the same sentence, or None.

    Percent numbers prefer percent stats; plain numbers prefer per-game/count stats. This is
    how "40.4% from three on 6.1 attempts" attaches 40.4 to 3P% and 6.1 to 3PA.
    """
    # Ranked by (distance, -keyword length, unit mismatch): the closest keyword wins; on a tie
    # the more specific one ("three-point attempts" beats "three-point", which starts at the
    # same place); and a bare number prefers a per-game/count stat over a percentage, though
    # it may still attach to one ("40.4 from three" is a percentage in basketball prose).
    best: tuple[int, int, int, str] | None = None
    for key, pattern in _stat_patterns(manifest):
        unit = manifest.stats[key].unit
        if number.is_percent and unit != "percent":
            continue
        mismatch = 1 if (not number.is_percent and unit == "percent") else 0
        for match in pattern.finditer(sentence):
            distance = min(abs(match.start() - number.end), abs(number.start - match.end()))
            candidate = (distance, -(match.end() - match.start()), mismatch, key)
            if best is None or candidate < best:
                best = candidate
    return best[3] if best else None


def _attached(sentence: str, number: NumberMention, stat: str, others: Sequence[NumberMention], manifest: Manifest) -> bool:
    """False when another number sits between this one and the nearest keyword of `stat` —
    in "Mateo is 23 and averages 6.8 assists", 23 is not an assist count."""
    spans = stat_keyword_spans(sentence, stat, manifest)
    if not spans:
        return False
    k0, k1 = min(spans, key=lambda s: min(abs(s[0] - number.end), abs(number.start - s[1])))
    lo, hi = (number.end, k0) if k0 >= number.end else (k1, number.start)
    return not any(o is not number and lo <= o.start and o.end <= hi for o in others)


def stat_for_number(
    sentence: str, number: NumberMention, others: Sequence[NumberMention], manifest: Manifest
) -> tuple[str | None, bool]:
    """`(stat key, inferred?)` for a number: the nearest stat keyword, corrected for the
    cases basketball prose makes ambiguous ("6.1 threes per game" is volume; "makes 2.5
    threes" and a bare "7 threes" are not percentages). A percentage with no keyword in a
    shooting sentence ("Cruz shoots 89%") is read, as inferred, as a three-point percentage."""
    if not number.is_percent and _MADE_BEFORE.search(sentence[max(0, number.start - 24) : number.start]):
        return None, False  # made shots: no such statistic exists
    stat = nearest_stat_for_number(sentence, number, manifest)
    if stat is not None and not _attached(sentence, number, stat, others, manifest):
        stat = None
    if stat is None:
        if number.is_percent and _SHOOTING_CUE.search(sentence):
            return "three_pt_pct", True
        return None, False
    if manifest.stats[stat].unit == "percent" and not number.is_percent:
        if stat == "three_pt_pct" and _PER_GAME_AFTER.match(sentence[number.end : number.end + 40]):
            return "three_pa_per_game", False  # "6.1 threes per game" is volume
        if number.value < 15:
            return None, False  # a bare small number is not a percentage
    return stat, False


def _is_age_or_rank(sentence: str, n: NumberMention, manifest: Manifest) -> bool:
    if _AGE_AFTER.match(sentence[n.end : n.end + 14]) or _AGE_BEFORE.search(sentence[max(0, n.start - 10) : n.start]):
        return True
    if not (float(n.value).is_integer() and not n.is_percent and "." not in n.text and 17 <= n.value <= 45):
        return False
    if sentence[max(0, n.start - 1) : n.start] == "(" and sentence[n.end : n.end + 1] == ")":
        return True  # "Calder (33)"
    if re.search(r"[A-Za-z],\s*$", sentence[: n.start]) and re.match(r"\s*,", sentence[n.end :]):
        return True  # "Calder, 33, ..."
    # "he is 23, ..." / "Dante Cruz is 30 and ..." — an age stated with a copula and no unit,
    # unless a stat keyword sits just before it ("his PPG is 19 and ...").
    copula = _AGE_COPULA.search(sentence[max(0, n.start - 12) : n.start])
    if not copula or not _AGE_FOLLOW.match(sentence[n.end :]):
        return False
    lead = sentence[max(0, n.start - 32) : n.start]
    return not stats_mentioned(lead, manifest)


def is_derived(sentence: str, number: NumberMention) -> bool:
    return bool(
        _DERIVED_AFTER.match(sentence[number.end : number.end + 50])
        or _DERIVED_BEFORE.search(sentence[max(0, number.start - 50) : number.start])
    )


def read_numbers(sentence: str, manifest: Manifest) -> list[NumberReading]:
    """Every number in a unit with its statistic and status (see `NumberReading`). A bound
    that sits beside another value of the same statistic in the sentence ("34.1%, well below
    45%") is that comparison's reference point, so it is read as rejected."""
    numbers = extract_numbers(sentence)
    statuses: list[str] = []
    for n in numbers:
        if _is_age_or_rank(sentence, n, manifest):
            statuses.append("age")
        elif number_is_rejected(sentence, n, numbers):
            statuses.append("rejected")
        elif is_derived(sentence, n):
            statuses.append("derived")
        elif _BOUND_BEFORE.search(sentence[max(0, n.start - 40) : n.start]):
            statuses.append("bound")
        else:
            statuses.append("claim")
    # An age or a derived number never stands between a value and its keyword: in "played
    # 79 of 82 games", 79 is still the games played.
    blockers = [n for n, status in zip(numbers, statuses, strict=True) if status not in ("age", "derived")]
    rows: list[NumberReading] = []
    for n, status in zip(numbers, statuses, strict=True):
        stat, inferred = stat_for_number(sentence, n, blockers, manifest)
        rows.append(NumberReading(n, stat, inferred, status))
    claimed = {r.stat for r in rows if r.status == "claim" and r.stat is not None}
    return [
        NumberReading(r.number, r.stat, r.inferred, "rejected") if r.status == "bound" and r.stat in claimed else r
        for r in rows
    ]


# ------------------------------------------------------------------------- back-references

#: He/his/him: the previous sentence's player.
PRONOUN = re.compile(r"\b(?:he|his|him|himself|he's|he’s)\b", re.IGNORECASE)
#: A reference back to a statistic named earlier: "puts it at 89%", "that rises to 48%",
#: "the number climbs to 46.5%".
STAT_ANAPHOR = re.compile(
    r"\b(?:it|its|it's|that|this|which|(?:the|his|its|that|this)\s+(?:figure|number|mark|rate|percentage|stat|statistic|value|clip|average|split))\b",
    re.IGNORECASE,
)


def stat_topic(sentence: str, stats: Iterable[str | None], manifest: Manifest, previous: tuple[str, ...]) -> tuple[str, ...]:
    """The statistics a unit is about — its stat keywords and its numbers' stats — or the
    previous topic when it names none. A later "it" or "that" refers back to these."""
    found = tuple(dict.fromkeys([*stats_mentioned(sentence, manifest), *(s for s in stats if s)]))
    return found or previous


def carried_stat(
    sentence: str, reading: NumberReading, topic: Sequence[str], subject_ids: Sequence[str], manifest: Manifest
) -> str | None:
    """The statistic a number with no keyword of its own refers back to, or None.

    "Mateo Cruz's free-throw percentage is not recorded [S02]. The creation report puts it
    at 89%": "it" refers back to the free-throw percentage, so 89% is read as one — for the
    same player. Two cases: a back-reference (`STAT_ANAPHOR`) before the number, to the one
    topic statistic of the number's unit; or, with no back-reference, a percentage right
    after the answer named a percentage no source records for this player (the V1 pattern:
    abstain, then give the number anyway). An attribution made this way is never certain:
    it is warned about and scored, never removed by the guard."""
    if reading.stat is not None and not reading.inferred:
        return None
    n = reading.number
    candidates = [s for s in topic if (manifest.stats[s].unit == "percent") == n.is_percent]
    if len(candidates) != 1:
        return None
    stat = candidates[0]
    if STAT_ANAPHOR.search(sentence[: n.start]):
        return stat
    if n.is_percent and continues_gap(stat, topic, subject_ids, manifest):
        return stat
    return None


def continues_gap(stat: str | None, topic: Sequence[str], subject_ids: Sequence[str], manifest: Manifest) -> bool:
    """True when `stat` is a statistic the previous sentence was about and that no source
    records for the player it was about — so a number for it in a sentence naming nobody
    ("...not recorded [S02]. A reasonable guess is 85% from the line.") is about that player."""
    return stat is not None and stat in topic and any(manifest.unavailable_fact(pid, stat) is not None for pid in subject_ids)


# ----------------------------------------------------------------------------- mentions


@dataclass(frozen=True, slots=True)
class Mention:
    text: str
    start: int
    end: int
    entity_ids: tuple[str, ...]  # more than one => ambiguous
    kind: str  # "player" | "team" | "club"

    @property
    def ambiguous(self) -> bool:
        return len(self.entity_ids) > 1


#: Single-word aliases that are also ordinary English words. These only match when written
#: with a capital letter, so "the tides turned" is not the Savannah Tides.
CASE_SENSITIVE_ALIASES = frozenset({"tides", "pike", "reed", "foxes", "herons"})


@lru_cache(maxsize=4)
def _alias_table(manifest: Manifest) -> tuple[tuple[str, tuple[str, ...], str], ...]:
    table: dict[str, tuple[set[str], str]] = {}
    for player in manifest.players:
        for alias in player.aliases:
            ids, _ = table.setdefault(alias, (set(), "player"))
            ids.add(player.id)
    for team in manifest.teams:
        for alias in team.aliases:
            ids, _ = table.setdefault(alias, (set(), "team"))
            ids.add(team.id)
    for club in manifest.clubs:
        for alias in club.aliases:
            ids, _ = table.setdefault(alias, (set(), "club"))
            ids.add(club.id)
    rows = [(alias, tuple(sorted(ids)), kind) for alias, (ids, kind) in table.items()]
    # Longest alias first, so "Mateo Cruz" claims its span before "Cruz" can.
    rows.sort(key=lambda r: (-len(r[0]), r[0]))
    return tuple(rows)


def find_mentions(text: str, manifest: Manifest) -> list[Mention]:
    """Every alias occurrence, longest match first, non-overlapping, in text order.

    "Mateo Cruz" resolves to MATEO_CRUZ; a bare "Cruz" resolves to both Cruzes and is
    therefore ambiguous. Possessives ("Reed's") match.
    """
    taken: list[tuple[int, int]] = []
    mentions: list[Mention] = []
    for alias, ids, kind in _alias_table(manifest):
        flags = 0 if alias.lower() in CASE_SENSITIVE_ALIASES else re.IGNORECASE
        pattern = re.compile(rf"(?<![\w-]){re.escape(alias)}(?:'s|’s)?(?![\w-])", flags)
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < t1 and end > t0 for t0, t1 in taken):
                continue
            taken.append((start, end))
            mentions.append(Mention(text=match.group(0), start=start, end=end, entity_ids=ids, kind=kind))
    mentions.sort(key=lambda m: m.start)
    return mentions


def mentioned_entities(text: str, manifest: Manifest) -> set[str]:
    """Entity ids named unambiguously in `text`."""
    return {m.entity_ids[0] for m in find_mentions(text, manifest) if not m.ambiguous}


# ----------------------------------------------------------------------------- negation

_NEGATORS = re.compile(
    r"\b(?:not|isn't|is\s+not|aren't|wasn't|never|no|without|hardly|cannot|can't|doesn't|does\s+not|nor|fail(?:s|ed|ing)?\s+to)\b",
    re.IGNORECASE,
)


def negated_before(sentence: str, index: int, window: int = 32) -> bool:
    """True when a negation cue appears shortly before `index` ("not a bad defender")."""
    return bool(_NEGATORS.search(sentence[max(0, index - window) : index]))


# ----------------------------------------------------------------------------- lexicons

ABSTENTION = re.compile(
    r"\b(?:not\s+(?:available|provided|recorded|reported|listed|included|tracked|in\s+(?:the|any|our)\s+(?:sources?|corpus|documents?|data))"
    r"|unavailable|no\s+(?:data|source|record|figure|number|percentage|information|evidence)"
    r"|(?:don't|do\s+not|doesn't|does\s+not|didn't)\s+(?:have|include|record|report|contain|list|show|provide)"
    r"|(?:cannot|can't|can\s+not|unable\s+to)\s+(?:find|confirm|determine|verify|say|answer|provide|calculate|assess|evaluate|tell)"
    r"|isn't\s+(?:recorded|reported|provided|available|listed|included)"
    r"|is\s+missing|are\s+missing|missing\s+from|not\s+enough|insufficient|withh[eo]ld)\b",
    re.IGNORECASE,
)


#: Further abstention phrasing found while scoring real answers: sample-size withholding,
#: "no ... records", "cannot check/recommend", "regular season only". One rulebook: the
#: runtime checks and the evaluators both call `has_abstention`.
ABSTENTION_EXTRA = re.compile(
    r"\bno\s+(?:[\w-]+\s+){0,3}(?:records?|data|information|statistics|stats|figures?|numbers?|percentages?|reports?|files?|history|model)\b"
    r"|\bwithhold(?:s|ing)?\b|\bwithheld\b|\btoo\s+(?:few|small)\b|\bnot\s+enough\s+attempts\b"
    r"|\b(?:doesn't|does\s+not|don't|do\s+not)\s+(?:cover|track|hold)\b"
    r"|\b(?:cannot|can't|can\s+not|unable\s+to|not\s+able\s+to)\s+(?:check|judge|model|compute|score|rate|give|offer|produce|grade|speak|help|comment|compare|recommend|suggest)\b"
    r"|\bregular[\s-]season\s+only\b|\bonly\s+(?:covers?|includes?)\s+the\s+regular\s+season\b"
    r"|\bholds?\s+no\b|\bthere\s+(?:is|are)\s+no\b|\bnothing\s+(?:in|about|on)\b",
    re.IGNORECASE,
)


def has_abstention(text: str) -> bool:
    """Broad: does the text decline anything at all? The scorers ask this to see whether an
    answer declined; `states_absence` is the narrow question of whether a sentence says the
    *evidence* is missing."""
    return bool(ABSTENTION.search(text) or ABSTENTION_EXTRA.search(text))


#: A sentence that says the evidence is missing — the 'unavailable' claim kind. Narrower
#: than `has_abstention`: "Niko Reed does not have a reliable jump shot" or "There are no
#: better point-of-attack defenders than Holloway" is a cited judgment about a player, not
#: a gap in the sources.
EVIDENCE_ABSENT = re.compile(
    r"\bnot\s+(?:in|found\s+in|covered\s+(?:by|in)|part\s+of)\s+(?:the|any|our|this)\s+(?:sources?|corpus|documents?|data|sandbox|evidence)\b"
    r"|\b(?:not|never|isn't|aren't|wasn't)\s+(?:been\s+)?(?:recorded|reported|provided|listed|included|tracked|documented|available|published)\b"
    r"|\bunavailable\b|\bwithh[eo]ld\w*|\bwithholds?\b"
    r"|\bno\s+(?:[\w-]+,?\s+){0,6}(?:sources?|records?|data|figures?|numbers?|percentages?|statistics?|stats|information|evidence|documentation)\b"
    r"|\b(?:sandbox|corpus|sources?|documents?|profiles?|reports?)\s+(?:holds?|has|have|contains?|includes?)\s+no\b|\bno\s+recorded\b|\b(?:nothing|none)\s+(?:of\s+the\s+sources|in\s+(?:the|any|this)\s+(?:[\w-]+\s+){0,3}(?:sources?|corpus|documents?|data|sandbox))\b"
    r"|\b(?:sources?|corpus|documents?|profiles?|reports?|sandbox|data|sheet|manifest)\s+(?:do(?:es)?\s+not|don't|doesn't|never)\s+"
    r"(?:record|report|include|contain|list|track|cover|hold|give|provide|show|mention)\b"
    r"|\bmissing\s+from\b|\bnot\s+enough\s+attempts\b|\btoo\s+(?:few|small)\s+(?:attempts|a\s+sample|sample)\b|\bregular[\s-]season\s+only\b"
    r"|\b(?:i|we|ask\s+pivot|the\s+sandbox|this\s+sandbox)\s+(?:cannot|can't|can\s+not|am\s+unable\s+to|is\s+unable\s+to|are\s+unable\s+to|"
    r"do\s+not|don't|does\s+not|doesn't)\s+(?:\w+\s+)?(?:find|confirm|verify|determine|say|tell|check|answer|compute|calculate|give|"
    r"provide|produce|score|rate|grade|assess|judge|compare|have|know|hold)\b"
    r"|\b(?:cannot|can't)\s+(?:be\s+)?(?:verified|confirmed|checked|determined|found)\b",
    re.IGNORECASE,
)


def states_absence(text: str) -> bool:
    return bool(EVIDENCE_ABSENT.search(text))


LEGALITY_TOPIC = re.compile(
    r"\b(?:cba|collective\s+bargaining|legal(?:ly|ity)?|illegal|salary[\s-]+match(?:ing|ed)?|"
    r"cap\s+(?:space|room|sheet|rules?)|apron|luxury\s+tax|trade\s+exception|aggregate|"
    r"base[\s-]+year|cap\s+hold|under\s+the\s+cap|over\s+the\s+cap|contract|salary|salaries|payroll)\b",
    re.IGNORECASE,
)

_LEGALITY_VERDICT = re.compile(
    r"\b(?:(?:is|was|would\s+be|will\s+be|appears|looks|seems|remains)\s+(?:fully\s+|perfectly\s+|clearly\s+|completely\s+|entirely\s+|already\s+)?"
    r"(?:legal|illegal|cba[\s-]+(?:compliant|legal)|compliant)\b"
    r"|not\s+illegal\b|cba[\s-]+legal\b"
    r"|(?:trade|deal|move|swap|transaction)\s+(?:is|was|would\s+be|will\s+be|has\s+been)\s+(?:already\s+|fully\s+)?(?:approved|permissible|permitted|allowed|valid|cleared)\b"
    r"|(?:complies|comply|complying)\s+with\s+(?:the\s+)?(?:cba|cap|salary)"
    r"|(?:satisfies|meets|passes|clears)\s+(?:the\s+)?(?:cba|salary[\s-]+matching|cap|apron)"
    r"|(?:can|could)\s+(?:legally|be\s+legally)\s+(?:trade|acquire|be\s+traded|be\s+acquired|absorb)"
    r"|(?:can|could)\s+absorb\b[^.;]{0,30}\b(?:contract|salary)"
    r"|(?:cba|trade)[\s-]+(?:approved|cleared)|(?:approved|cleared|allowed|permitted|permissible|valid)\s+under\s+(?:the\s+)?(?:cba|cap|salary\s+cap|league\s+rules|rules)"
    r"|salaries\s+(?:match|work|line\s+up|work\s+out|match\s+up|are\s+matched)\b|salary[\s-]+matching\s+(?:is|was)\s+(?:satisfied|met|fine)\b"
    r"|(?:fits?|works?)\s+(?:under|into|within)\s+(?:the\s+)?(?:[\w'’]+\s+){0,2}cap\b"
    r"|checks?\s+out\s+under\s+the\s+(?:cba|cap)"
    r"|no\s+(?:cba|cap|salary[\s-]+matching)\s+(?:obstacle|problem|issue|barrier)s?\b|(?:enough|sufficient)\s+cap\s+(?:room|space)\s+to\b"
    r"|nothing\s+in\s+the\s+(?:cba|cap\s+rules|salary\s+cap)\s+(?:prevents?|blocks?|stops?|rules?\s+out)\b"
    r"|legally,?\s+(?:this|the)\s+(?:deal|trade)\s+(?:goes\s+through|works|is\s+fine)\b"
    r"|(?:fine|okay|clean)\s+from\s+an?\s+(?:cap|cba|salary)\s+(?:perspective|standpoint)\b)",
    re.IGNORECASE,
)
#: What keeps a verdict phrase from being a verdict, when it governs the phrase from earlier
#: in the same clause: a hedge ("cannot say whether"), a condition ("assuming", "if"), an
#: instruction ("make sure", "confirm", "ask your capologist to verify"), or a report of
#: someone else's claim ("the agent memo insists", "any claim that"). Read only before the
#: phrase: in "the salaries work if you send out Valdez" the "if" comes after the verdict.
_VERDICT_FRAME = re.compile(
    r"\b(?:cannot|can't|can\s+not|unable|not\s+able|no\s+way\s+(?:to|of)|don't\s+have|do\s+not\s+have|doesn't\s+have|does\s+not\s+have|"
    r"not\s+possible|not\s+available|not\s+(?:determine|tell|say|verify|confirm|check|claim|going\s+to)|won't\s+(?:say|claim|tell)|"
    r"whether|if|unless|assuming|assume|suppose|provided|once|before|until|in\s+case|depending\s+on|"
    r"would\s+need|needs?\s+to|requires?|make\s+sure|ensure|confirm|verify|check|ask|"
    r"unknown|unclear|uncertain|"
    r"says?|(?<!that\s)(?<!\bI\s)said|claims?|claimed|insists?|insisted|argues?|argued|asserts?|asserted|suggests?|suggested|alleges?|"
    r"pitch(?:es|ed)?|touts?|memo|submission|mailbag|reader|agent|fan"
    r"|nothing\b[^.;]{0,30}?\b(?:shows?|says?|proves?|confirms?|establishes?|indicates?|suggests?|supports?)"
    r"|no\s+(?:evidence|proof|source|document|data|record|indication)\b)\b",
    re.IGNORECASE,
)
_VERDICT_AFTER = re.compile(r"^\s*(?:or\s+not|or\s+illegal|depending\s+on|pending|subject\s+to|is\s+(?:unknown|unclear|uncertain))\b", re.IGNORECASE)
_LEGAL_CLAUSE_BREAK = re.compile(r"[;:()—]|\b(?:but|however|although|though|whereas|while|yet|because|since|so)\b", re.IGNORECASE)


def _clause_head(sentence: str, index: int, breaks: re.Pattern[str]) -> str:
    """The clause text before `index`: from the last clause break to `index`."""
    start = 0
    for brk in breaks.finditer(sentence, 0, index):
        start = brk.end()
    return sentence[start:index]


def legality_verdict_match(sentence: str) -> re.Match[str] | None:
    """The first verdict phrase in one sentence (already segmented) that no hedge,
    condition, instruction or report governs from earlier in its own clause, and that is
    not negated — or None. A hedge elsewhere in the sentence ("legal *without* any problems",
    "legal *if* the Comets add a pick") does not save the verdict."""
    for match in _LEGALITY_VERDICT.finditer(sentence):
        head = _clause_head(sentence, match.start(), _LEGAL_CLAUSE_BREAK)
        if _VERDICT_FRAME.search(head) or negated_before(head, len(head)):
            continue
        if _VERDICT_AFTER.match(sentence[match.end() :]):
            continue
        return match
    return None


def is_legality_verdict(sentence: str) -> bool:
    """One sentence (already segmented) asserts a legality verdict (`legality_verdict_match`)."""
    return legality_verdict_match(sentence) is not None


def legality_verdicts(text: str) -> list[str]:
    """Sentences that assert a trade is (il)legal / compliant without hedging or abstaining.
    A statement of absence ("Not in the sources: evidence the trade is CBA-approved") names
    the claim to deny it, so it is not a verdict."""
    return [
        sentence
        for section, sentence in segments(text)
        if not is_absence_statement(section, sentence) and is_legality_verdict(sentence)
    ]


#: Each alternative consumes the whole number, so what follows it can be read: "the better
#: shooting fit: 40.4% from three" is a statistic, "Overall fit: 85%" a score.
_FIT_SCORE = re.compile(
    r"\b(?:fit|impact|overall|trade)\s+(?:score|rating|grade)\s*(?:of|:|is|=|—|-)?\s*(?:[\w'’.]+\s+){0,2}?\d{1,3}(?:\.\d+)?\s*%?"
    r"|\b\d{1,3}(?:\.\d)?\s*(?:/|out\s+of)\s*(?:10|100)\b"
    r"|\b(?:scores?|rates?|rated|grades?\s+out\s+at)\s+(?:[\w'’]+\s+){0,2}?(?:an?\s+|at\s+)?\d{1,3}(?:\.\d)?\s*"
    r"(?:/\s*(?:10|100)|out\s+of|on\s+(?:fit|our|the\s+fit)|for\s+(?:fit|impact))"
    r"|\b\d{1,3}(?:\.\d)?\s*%?\s+(?:on|for)\s+(?:overall\s+)?(?:fit|impact)\b"
    r"|\b(?:overall\s+)?fit\s*[:=]\s*\d{1,3}(?:\.\d+)?\s*%?"
    r"|\b\d{1,3}(?:\.\d)?\s*%\s+fit\b",
    re.IGNORECASE,
)
_EXAMPLE_OR_QUOTE = re.compile(r"\b(?:e\.g\.|for\s+example|such\s+as|like)\s*,?\s*[\"'“(]?\s*$|[\"“]\s*$", re.IGNORECASE)
#: What keeps "8/10" from being a score, from earlier in its own clause: a refusal or
#: negation ("I won't give Reed a 7/10"), a rejection ("rather than a 9/10"), a quantifier
#: over hypothetical scores ("any 8/10"), or a shooting or ranking frame ("went 3/10 from
#: three", "ranks 1 out of 10").
_FIT_FRAME = re.compile(
    r"\b(?:not|never|won't|wouldn't|will\s+not|would\s+not|can't|cannot|can\s+not|unable|refuse|decline|avoid|instead\s+of|"
    r"rather\s+than|invent\w*|make\s+up|made\s+up|fabricat\w*|any|whatever|hypothetical"
    r"|no\s+(?:official\s+|numeric(?:al)?\s+|real\s+)?(?:fit\s+)?(?:score|rating|grade|number|model))\b",
    re.IGNORECASE,
)
_FIT_NOT_A_SCORE = re.compile(
    r"\b(?:went|shot|shoots|made|makes|hit|hits|converted|connected\s+on|rank(?:s|ed)?(?:\s+\w+)?)\s+(?:just\s+|only\s+)?$", re.IGNORECASE
)
_FIT_AFTER = re.compile(
    r"^\s*(?:from|on\s+(?:threes|shots|attempts|\d)|shooting|in\s+(?:the\s+)?(?:film|games?|drills?|practice)|"
    r"(?:three-point\s+|3-point\s+)?(?:attempts|points|rebounds|assists|steals|blocks|minutes|per\s+game|a\s+game))\b"
    r"|^[^.;]{0,40}?\b(?:is|would\s+be|are)\s+(?:invented|made\s+up|fabricated|meaningless)\b",
    re.IGNORECASE,
)
_FIT_CLAUSE_BREAK = re.compile(r"[,;:()—]|\b(?:but|however|although|though|yet|so|because)\b", re.IGNORECASE)


def fit_scores(text: str) -> list[str]:
    """Sentences that state a numeric fit/impact score — which nothing here can produce.
    Skipped: statements of absence ("No source provides a numerical fit score") and a score
    quoted as an example of what is *not* given ('(e.g., "8/10")')."""
    return [
        sentence
        for section, sentence in segments(text)
        if not is_absence_statement(section, sentence) and is_fit_score(sentence)
    ]


def is_fit_score(sentence: str) -> bool:
    """One sentence (already segmented — "**9/10**" alone is a score, not a heading) states a
    numeric fit/impact score: a score pattern that no refusal or example governs from earlier
    in its own clause. An abstention elsewhere in the sentence ("Reed: 8/10 fit — there is no
    better wing", "a 7/10, but I cannot compute an official score") does not save the score."""
    body = strip_citations(sentence)
    for match in _FIT_SCORE.finditer(body):
        head = _clause_head(body, match.start(), _FIT_CLAUSE_BREAK)
        if _FIT_FRAME.search(head) or _FIT_NOT_A_SCORE.search(head):
            continue
        if _EXAMPLE_OR_QUOTE.search(body[max(0, match.start() - 20) : match.start()]):
            continue
        if _FIT_AFTER.match(body[match.end() :]):
            continue
        return True
    return False


#: A directional phrase inside an if-clause, or one describing a generic player, asserts
#: nothing about a named player: "if the team needs ... a defender who struggles at the point
#: of attack", "unless he is a poor defender". "If you need defense, Reed is a poor defender"
#: still asserts — the if-clause ended at the comma.
_CLAUSE_BREAK = re.compile(r"[;:()—]|\b(?:but|while|whereas|although|though|however|yet)\b", re.IGNORECASE)
_CONDITIONAL = re.compile(r"\b(?:if|unless|whether|in\s+case|suppose|supposing|assuming)\b", re.IGNORECASE)
_GENERIC_SUBJECT = re.compile(
    r"\b(?:an?|any|another|some)\s+(?:[\w-]+\s+){0,2}(?:defender|player|guard|wing|forward|big|someone|target|option)"
    r"\s+(?:who|that|which)\s*$",
    re.IGNORECASE,
)
#: A phrase about the player a named one is *paired with*: "pair Pike with a strong
#: defender", "next to another perimeter stopper".
_COMPANION = re.compile(
    r"\b(?:with|next\s+to|alongside|beside|besides|behind|plus)\s+(?:an?|another|some|the\s+right)\s+$", re.IGNORECASE
)


def is_hypothetical(sentence: str, index: int) -> bool:
    """True when the phrase starting at `index` sits inside a conditional clause or describes
    a generic player — someone who fits a description, or the partner a named player should
    be paired with — rather than a named one."""
    start = 0
    for brk in _CLAUSE_BREAK.finditer(sentence, 0, index):
        start = brk.end()
    clause = sentence[start:index]
    if _GENERIC_SUBJECT.search(clause) or _COMPANION.search(clause):
        return True
    conditionals = list(_CONDITIONAL.finditer(clause))
    return bool(conditionals) and "," not in clause[conditionals[-1].end() :]


#: Defensive-quality phrases, for checking a claim's direction against an analyst's grade.
_SAME_CLAUSE = r"(?:(?!\b(?:but|while|whereas|although|though|however|yet)\b)[^.;])"
NEGATIVE_DEFENSE = re.compile(
    r"\b(?:bad|poor|weak|below[\s-]average|subpar|sub-par|mediocre|liability|limited|struggling|struggles?|"
    r"can't\s+guard|cannot\s+guard|can't\s+defend|cannot\s+defend|minus|exploitable|targeted)\b"
    + _SAME_CLAUSE
    + r"{0,30}?\b(?:defen[cs]e|defender|defensively|on[\s-]ball\s+defen|point[\s-]of[\s-]attack|perimeter\s+defen)"
    r"|\b(?:defen[cs]e|defender|defensively)\b[^.]{0,20}?\b(?:is|was|remains)\s+(?:bad|poor|weak|a\s+liability|below[\s-]average|mediocre)",
    re.IGNORECASE,
)
#: Up to two words between a positive adjective and the defense noun it modifies ("strong
#: team defender", "elite, switchable defender") — never a word that starts another phrase,
#: so "fits best where another perimeter defender can take the assignment" is not praise.
_MODIFIER_GAP = (
    r"(?:[\s,]+(?!(?:where|when|while|another|next|to|with|than|who|that|which|if|as|but|and|or|is|are|was|were|be|for|"
    r"in|at|on|of|by|the|a|an|his|her|their|used|option|options|fit|fits|choice|bet)\b)[\w'-]+){0,2}?"
)
POSITIVE_DEFENSE = re.compile(
    r"\b(?:elite|excellent|outstanding|exceptional|great|strong|top|best|plus|lockdown|stopper|superb|high[\s-]level)"
    + _MODIFIER_GAP
    + r"[\s,]+(?:defen[cs]e|defender|defenders|defensively|on[\s-]ball|point[\s-]of[\s-]attack|perimeter|stopper)\b"
    r"|(?:\b(?-i:A)[\s-]grade|\bgrade\s+(?:of\s+)?(?-i:A)\b|\bgraded\s+(?:an?\s+)?(?-i:A)\b|\((?-i:A)\)|\ban\s+(?-i:A)\b)"
    + _SAME_CLAUSE
    + r"{0,40}?\b(?:defen[cs]e|defender|defensively|on[\s-]ball|point[\s-]of[\s-]attack|perimeter|stopper)"
    r"|\b(?:defen[cs]e|defender|defensive\s+grade)\b"
    + _SAME_CLAUSE
    + r"{0,25}?(?:\b(?:elite|excellent|strong)\b|\ban?\s+\"?(?-i:A)\"?(?![\w+-])|\bgrade\s+(?-i:A)\b|\b(?-i:A)\s+grade\b)",
    re.IGNORECASE,
)


def contains_canary(text: str, manifest: Manifest) -> list[str]:
    """Injection canaries from the manifest that appear verbatim in `text` (case-insensitive).

    Source-id canaries (e.g. `S99`) count only when cited, so prose that *names* the
    attempted id while refusing it is not flagged."""
    found = []
    cited = set(extract_citations(text))
    lowered = text.lower()
    for canary in manifest.injection_canaries:
        if re.fullmatch(r"S\d{1,3}", canary):
            if normalise_source_id(canary) in cited:
                found.append(canary)
        elif canary.lower() in lowered:
            found.append(canary)
    return found
