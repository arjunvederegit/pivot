"""Live multi-turn GM conversations, scored turn by turn.

The benchmark tests single turns, with any history written by hand. A real conversation is
different: each answer becomes the next turn's history, so an early mistake (a wrong number,
a recommendation the GM then revises) is carried forward. This runner plays the scripted
conversations in `conversations.json` through the same `AssistantService` the API uses:

1. turn 1 is asked with no history;
2. turn *n* is asked with every earlier question and the assistant's **own** earlier answers
   as history (unless the conversation is marked `independent_turns`, for side-by-side
   comparisons such as "the same question for two different teams");
3. every turn is a benchmark case (same schema, same deterministic scorers), so a turn's
   expectations — abstain on legality, keep the numbers right, do not recommend our own
   players — are checked exactly as in the benchmark;
4. each listed `pair` is scored with `objective_sensitivity`: did the stated trade-offs move
   when the GM's priorities (or the team) changed?

Output, per run: `conversations.json` (every request, response and score) and
`transcript.md` — the conversation as a GM would read it, with the checks beside each turn.
Fake mode exercises the harness without Ollama and is not a quality signal.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.assistant_rag.evaluation import scorers
from app.assistant_rag.evaluation.dataset import BenchmarkCase, HistoryMessage, parse_case
from app.assistant_rag.evaluation.runner import (
    DEFAULT_OUTPUT_DIR,
    ENVIRONMENT_ERROR,
    CaseRecord,
    ServiceAnswerer,
    Variant,
    answer_case,
    new_run_id,
    parse_variants,
)
from app.assistant_rag.manifest import Manifest, load_manifest
from app.config import Settings, get_settings

CONVERSATIONS_PATH = Path(__file__).resolve().parent / "conversations.json"


@dataclass(frozen=True)
class Conversation:
    id: str
    description: str
    turns: tuple[BenchmarkCase, ...]
    pairs: tuple[tuple[str, str], ...]
    independent_turns: bool


def load_conversations(path: Path | None = None, manifest: Manifest | None = None) -> list[Conversation]:
    """Parse and validate every scripted conversation (each turn against the manifest)."""
    m = manifest or load_manifest()
    raw = json.loads((path or CONVERSATIONS_PATH).read_text(encoding="utf-8"))
    out: list[Conversation] = []
    seen: set[str] = set()
    for conv in raw["conversations"]:
        turns = tuple(parse_case(t, m) for t in conv["turns"])
        ids = {t.id for t in turns}
        if ids & seen:
            raise ValueError(f"conversation {conv['id']}: turn ids reused across conversations: {ids & seen}")
        seen |= ids
        pairs = tuple((a, b) for a, b in conv.get("pairs", []))
        for a, b in pairs:
            if a not in ids or b not in ids:
                raise ValueError(f"conversation {conv['id']}: pair ({a}, {b}) names a turn it does not have")
        out.append(Conversation(conv["id"], conv["description"], turns, pairs, bool(conv.get("independent_turns"))))
    return out


@dataclass
class ConversationRecord:
    conversation: Conversation
    variant: Variant
    turns: list[CaseRecord]
    pair_scores: list[scorers.ScoreResult]

    def failed(self) -> list[tuple[str, scorers.ScoreResult]]:
        rows = [(r.case.id, s) for r in self.turns for s in r.scores if s.passed is False]
        rows += [(s.case_id or "pair", s) for s in self.pair_scores if s.passed is False]
        return rows


def _with_history(turn: BenchmarkCase, previous: Sequence[CaseRecord]) -> BenchmarkCase:
    history: list[HistoryMessage] = []
    for record in previous:
        history.append(HistoryMessage("user", record.case.question))
        if record.response is not None:
            history.append(HistoryMessage("assistant", str(record.response.get("answer") or "")))
    return replace(turn, history=tuple(history))


async def play(conversation: Conversation, answerer: Any, variant: Variant, *, manifest: Manifest) -> ConversationRecord:
    """Play one conversation turn by turn, carrying the assistant's own answers forward."""
    records: list[CaseRecord] = []
    for turn in conversation.turns:
        case = turn if conversation.independent_turns else _with_history(turn, records)
        record = await answer_case(answerer, case, variant, k=None)
        record.scores = scorers.score_case(record.case, record.output(), manifest) if record.scored else []
        raw = record.raw_output() if record.scored else None
        record.raw_scores = scorers.score_case(record.case, raw, manifest) if raw is not None else list(record.scores)
        records.append(record)
    by_id = {r.case.id: r for r in records}
    pair_scores = []
    for a, b in conversation.pairs:
        ra, rb = by_id[a], by_id[b]
        if ra.response is None or rb.response is None:
            continue
        pair_scores.append(scorers.score_pair(ra.case, ra.output(), rb.case, rb.output(), manifest))
    return ConversationRecord(conversation, variant, records, pair_scores)


# ----------------------------------------------------------------------------- report


def _verdict(s: scorers.ScoreResult) -> str:
    return {True: "PASS", False: "FAIL", None: "n/a"}[s.passed]


def render_transcript(results: Sequence[ConversationRecord], *, mode: str) -> str:
    lines = [
        "# Ask Pivot — multi-turn conversation run",
        "",
        "> FICTIONAL DATA / SANDBOX. Every team, player and report is invented.",
        "",
        f"Mode: **{mode}**" + (" — fake model: a harness check, NOT a quality signal." if mode == "fake" else ""),
        "",
    ]
    for result in results:
        conv = result.conversation
        failed = result.failed()
        lines += [f"## {conv.id} ({result.variant.label})", "", conv.description, "",
                  f"Checks failed: **{len(failed)}**" + ("" if failed else " — every applicable check passed."), ""]
        for i, record in enumerate(result.turns, start=1):
            case = record.case
            lines += [f"### Turn {i} — `{case.id}`", "", f"**GM:** {case.question}", ""]
            if case.previous_priorities is not None:
                lines += [f"*Priorities changed:* {dict(case.previous_priorities)} → {dict(case.priorities)}", ""]
            if record.response is None:
                lines += [f"**Error:** {(record.error or {}).get('code')} — {(record.error or {}).get('message')}", ""]
                continue
            r = record.response
            summary = r.get("claim_summary") or {}
            kinds = ", ".join(f"{k} {v}" for k, v in summary.items() if v)
            lines += [
                f"**Ask Pivot** ({r.get('answer_kind')}, {record.latency_ms / 1000:.1f} s, "
                f"cited {', '.join(r.get('cited_source_ids') or []) or 'nothing'}):",
                "",
                *[f"> {line}" if line else ">" for line in str(r.get("answer") or "").splitlines()],
                "",
                f"*Kinds of statement:* {kinds or '—'}",
                f"*Runtime warnings:* {', '.join(w['code'] for w in r.get('warnings') or []) or 'none'}",
                f"*Answer guard removed:* {', '.join(record.guard_codes()) or 'nothing'}"
                + (
                    f" — failed checks on the model's raw text: {sum(1 for x in record.raw_scores if x.passed is False)}, "
                    f"after the guard: {sum(1 for x in record.scores if x.passed is False)}"
                    if record.guard_codes() else ""
                ),
                "",
                "| check | verdict | detail |",
                "|---|---|---|",
            ]
            for s in record.scores:
                if s.key in ("latency_ms",) or s.passed is None:
                    continue
                lines.append(f"| {s.key} | {_verdict(s)} | {s.detail.replace('|', '/')[:220]} |")
            lines.append("")
        for s in result.pair_scores:
            lines += [f"**Pair `{s.case_id}` — objective sensitivity: {_verdict(s)}.** {s.detail}", ""]
    return "\n".join(lines)


@dataclass(frozen=True)
class ConversationRunResult:
    directory: Path
    results: list[ConversationRecord]
    exit_code: int


def run_conversations(
    *,
    mode: str = "live",
    variants: str | Sequence[Any] = "grounded:hybrid",
    conversation_ids: Sequence[str] | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    settings: Settings | None = None,
    manifest: Manifest | None = None,
    path: Path | None = None,
) -> ConversationRunResult:
    """Play every (or the named) conversation once per variant and write the transcript.
    Exit code: 0 ok, 1 a blocking check failed (live mode), 2 an environment error."""
    m = manifest or load_manifest()
    conversations = load_conversations(path, m)
    if conversation_ids:
        unknown = set(conversation_ids) - {c.id for c in conversations}
        if unknown:
            raise ValueError(f"unknown conversation id(s): {sorted(unknown)}")
        conversations = [c for c in conversations if c.id in set(conversation_ids)]
    answerer = ServiceAnswerer(settings or get_settings(), mode=mode)

    async def run_all() -> list[ConversationRecord]:
        out = []
        for variant in parse_variants(variants):
            for conv in conversations:
                out.append(await play(conv, answerer, variant, manifest=m))
        return out

    # One event loop for the whole run: the Ollama clients bind to the loop they first ran on.
    results = asyncio.run(run_all())
    directory = output_dir / f"{new_run_id()}-conversations-{mode}"
    directory.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "conversation": r.conversation.id,
            "variant": r.variant.label,
            "turns": [t.to_json() for t in r.turns],
            "pairs": [s.as_dict() for s in r.pair_scores],
        }
        for r in results
    ]
    (directory / "conversations.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (directory / "transcript.md").write_text(render_transcript(results, mode=mode), encoding="utf-8")
    environment = any(t.status == ENVIRONMENT_ERROR for r in results for t in r.turns)
    blocking = any(s.severity == "blocking" for r in results for _, s in r.failed())
    exit_code = 2 if environment else (1 if blocking and mode == "live" else 0)
    return ConversationRunResult(directory, results, exit_code)
