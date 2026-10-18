"""Four kinds of statement — and which one each sentence of an answer is.

A GM reading a research answer needs to know, sentence by sentence, what *kind* of thing is
being said, because each deserves a different level of trust:

  observed fact     "Theo Vance shoots 40.4% from three on 6.1 attempts [S04]." A recorded
                    statistic. Checked here against the fact manifest.
  analyst opinion   "Dana Whitfield grades Reed's point-of-attack defense A [S03]." A named
                    person's dated judgment — a scout, analyst, coach or front office. Real
                    evidence, but an opinion: two scouts can disagree (S05 vs S06).
  inference         "My read: Vance is the safer fit if spacing must be preserved." The
                    assistant's own conclusion for *this* GM's priorities. Nobody documented it.
  unavailable       "Mateo Cruz's free-throw percentage is not in the sources." A gap.

(plus `untrusted_claim` — something only an agent pitch or a public submission says — and
`question`, a question back to the GM.)

The grounded prompt asks the model to write in exactly these terms. A 2B model will not
always comply, so the labels the UI shows are assigned *here*, deterministically, from:

1. the sentence's own signals — a statement that the *evidence* is missing (narrow:
   "not recorded", "no source", "the profile does not list" — not a judgment such as "Reed
   does not have a reliable jump shot"), a question mark, a "My read:" prefix, a number that
   the grounding analysis matched against the manifest;
2. the label or heading it sits under, when that is the model declaring the kind: a
   "**My read**" heading or a "**My read:**" label marks the assistant's own conclusion
   (inference) even when it cites the evidence it drew on; an item under a "**Not in the
   sources**" heading that names a gap is unavailable. A documented statistic still wins —
   a model can put the wrong sentence under the right heading;
3. what it cites — each source's `evidence_class`, which comes from its document type in
   the manifest (a stat profile is observed; a scouting report is opinion; an agent pitch is
   an untrusted claim), never from the model.

Uncited reasoning is classed as inference — the honest reading of a sentence nobody's
document supports. The guard's removal markers are not claims and are never labelled.
Pure Python; reuses `grounding.analyze_answer` so a sentence is analysed once for both the
warnings and these labels.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.assistant_rag import claims
from app.assistant_rag.grounding import SentenceAnalysis, analyze_answer
from app.assistant_rag.manifest import DocumentMeta, Manifest
from app.assistant_rag.types import EntityResolution

EVIDENCE_CLASS_BY_DOC_TYPE: dict[str, str] = {
    "stat_profile": "observed_statistic",
    "scouting_report": "analyst_opinion",
    "analytics_note": "analyst_opinion",
    "coaching_note": "analyst_opinion",
    "team_strategy_memo": "team_context",
    "method_guide": "method_guidance",
    "agent_pitch": "untrusted_claim",
    "fan_submission": "untrusted_claim",
}

CLAIM_KINDS = ("observed_fact", "analyst_opinion", "inference", "unavailable", "untrusted_claim", "question")

_INLINE_INFERENCE = re.compile(r"^\s*(?:my\s+read|inference|interpretation|my\s+view)\s*[:—-]", re.IGNORECASE)
#: A heading or bold label that declares the assistant's own conclusion.
_INFERENCE_SECTION = re.compile(
    r"^\s*(?:my\s+(?:read|view|take|inference|interpretation|recommendation)|inferences?|interpretation)\b", re.IGNORECASE
)
_INLINE_UNAVAILABLE = re.compile(r"^\s*not\s+in\s+the\s+sources\s*[:—-]", re.IGNORECASE)
#: Too short to be a claim ("Yes.", "In short:").
_MIN_WORDS = 4


def evidence_class(meta: DocumentMeta) -> str:
    """The kind of evidence a whole source is. Trust overrides type: an untrusted document is
    a claim whatever it looks like."""
    if meta.trust == "untrusted":
        return "untrusted_claim"
    return EVIDENCE_CLASS_BY_DOC_TYPE.get(meta.doc_type, "analyst_opinion")


@dataclass(frozen=True, slots=True)
class ClassifiedClaim:
    text: str
    kind: str  # one of CLAIM_KINDS
    section: str | None
    source_ids: tuple[str, ...]
    check: str  # "matches_manifest" | "contradicts_manifest" | "unverified" | "not_numeric"
    note: str | None


def _check(analysis: SentenceAnalysis) -> tuple[str, str | None]:
    verdicts = [n.verdict for n in analysis.numbers]
    if not verdicts:
        return "not_numeric", None
    if any(v in ("contradicts", "unavailable") for v in verdicts):
        return "contradicts_manifest", "A number here does not match the fact manifest (see Checks)."
    if all(v == "match" for v in verdicts):
        return "matches_manifest", None
    return "unverified", "A number here could not be attributed to one player and checked."


def _classify(analysis: SentenceAnalysis, sources: Mapping[str, DocumentMeta]) -> ClassifiedClaim | None:
    text = analysis.text.strip()
    body = claims.strip_citations(text).strip()
    if len(body.split()) < _MIN_WORDS and not analysis.citations:
        return None
    check, note = _check(analysis)
    cited = tuple(dict.fromkeys(analysis.citations))
    classes = {evidence_class(sources[sid]) for sid in cited if sid in sources}
    section = analysis.section

    def claim(kind: str, extra: str | None = None) -> ClassifiedClaim:
        return ClassifiedClaim(text, kind, section, cited, check, extra or note)

    # 1. The sentence's own signals win over everything else: a gap in the evidence (stated
    # by the sentence, or named as an item under a "Not in the sources" heading), a question.
    absent = _INLINE_UNAVAILABLE.search(body) or claims.states_absence(body) or claims.is_absence_statement(section, body)
    if absent and check != "matches_manifest":
        return claim("unavailable")
    if body.endswith("?"):
        return claim("question")
    # 2. "My read:" on the sentence, or a "My read" heading or label over it: the model's own
    # conclusion, whatever evidence it cites — unless it states a documented statistic.
    if _INLINE_INFERENCE.search(body) or (section and _INFERENCE_SECTION.search(section)):
        if cited and check == "matches_manifest":
            return claim("observed_fact", "Labelled as inference, but it states a documented statistic.")
        return claim("inference")
    # 3. A statistic the manifest confirms is an observed fact, whoever it is cited to.
    if check == "matches_manifest":
        return claim("observed_fact", None if cited else "A documented statistic, stated without a citation.")
    # 4. Otherwise the citations decide.
    if cited:
        if classes and classes <= {"untrusted_claim"}:
            return claim("untrusted_claim")
        if classes and classes <= {"observed_statistic"}:
            return claim("observed_fact")
        if not classes:
            return claim("inference", "Cites only ids that were not retrieved, so nothing documents it.")
        return claim("analyst_opinion")
    # 5. Uncited: the assistant's reasoning.
    if check != "not_numeric":
        return claim("inference", "States a number without citing a source.")
    return claim("inference")


def classify_answer(
    answer: str,
    *,
    entities: EntityResolution,
    manifest: Manifest,
    sources: Mapping[str, DocumentMeta],
    analyses: Sequence[SentenceAnalysis] | None = None,
) -> list[ClassifiedClaim]:
    """Label every claim-sized sentence. `sources` are the documents the model was shown
    (citations to anything else carry no evidence class). On a guarded answer the guard's
    markers are skipped — `claims.units` never yields one — so a removal note is neither a
    claim nor glued to the sentence beside it."""
    rows = analyses if analyses is not None else analyze_answer(answer, entities, manifest)
    out: list[ClassifiedClaim] = []
    for analysis in rows:
        if claims.MARKER_PREFIX in analysis.text:
            continue
        classified = _classify(analysis, sources)
        if classified is not None:
            out.append(classified)
    return out


def summarize(claimed: Sequence[ClassifiedClaim]) -> dict[str, int]:
    counts = Counter(c.kind for c in claimed)
    return {kind: counts.get(kind, 0) for kind in CLAIM_KINDS}
