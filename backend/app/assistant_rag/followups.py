"""Suggested follow-up questions — prompts that push the GM's *reasoning*, not just more facts.

A research assistant that only answers is a search box. The follow-ups are where Ask Pivot
models the habits of a good front office: challenge the recommendation ("what would make
this wrong?"), stress-test the priorities ("what if spacing mattered more than
point-of-attack defense?"), compare the two strongest options head to head, inspect how much
of the answer rests on a single report, and decide what to scout next.

They are deterministic and built from this turn's context — the GM's actual top two
priorities, the players the retrieved passages are about, the conflicts on the table — so
they are specific without a second model call. For a clarification they are the
disambiguated versions of the question; for a refusal, in-scope versions of it.

Pure Python: no LangChain.
"""

from __future__ import annotations

import re

from app.assistant_rag.manifest import Manifest
from app.assistant_rag.policy import (
    ROUTE_CLARIFY,
    ROUTE_REFUSE,
    in_scope_alternatives,
    top_priority_keys,
)
from app.assistant_rag.types import EntityResolution, PolicyDecision, QueryContext, RetrievalResult

MIN_FOLLOW_UPS = 3
MAX_FOLLOW_UPS = 5
_FILLERS = (
    "Which of these claims rests on a single report?",
    "What should we scout next to test this read?",
    "What can this research sandbox not calculate, and why?",
    "Which players are documented as strong point-of-attack defenders?",
)


def _label(key: str, manifest: Manifest) -> str:
    dimension = manifest.dimension(key)
    return (dimension.label if dimension else key.replace("_", " ")).lower()


def _possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _finish(items: list[str]) -> list[str]:
    out = list(dict.fromkeys(i for i in items if i))
    for filler in _FILLERS:
        if len(out) >= MIN_FOLLOW_UPS:
            break
        if filler not in out:
            out.append(filler)
    return out[:MAX_FOLLOW_UPS]


def _retrieved_players(
    retrieval: RetrievalResult, manifest: Manifest, exclude_team: str | None
) -> list[str]:
    players: list[str] = []
    for item in sorted(retrieval.selected, key=lambda c: c.rank):
        for eid in item.chunk.meta.entity_ids:
            player = manifest.player(eid)
            if player is None or eid in players:
                continue
            if exclude_team is not None and player.current_club_id == exclude_team:
                continue
            players.append(eid)
    return players


def _clarify_follow_ups(ctx: QueryContext, entities: EntityResolution, manifest: Manifest) -> list[str]:
    out: list[str] = []
    ambiguity = entities.ambiguous[0]
    bare = re.sub(r"(?:'s|’s)$", "", ambiguity.mention.strip())
    pattern = re.compile(rf"(?<![\w-]){re.escape(bare)}(?![\w-])", re.IGNORECASE)
    names = []
    for cid in ambiguity.candidate_ids[:3]:
        player = manifest.player(cid)
        name = player.name if player else manifest.club_name(cid)
        names.append(name)
        rewritten = pattern.sub(name, ctx.question.strip(), count=1)
        out.append(rewritten if rewritten != ctx.question.strip() else f"Tell me about {name}.")
    if len(names) >= 2:
        out.append(f"Compare {names[0]} and {names[1]} on the documented evidence.")
    return out


def suggest_follow_ups(
    ctx: QueryContext,
    entities: EntityResolution,
    decision: PolicyDecision,
    retrieval: RetrievalResult,
    manifest: Manifest,
) -> list[str]:
    """Three to five follow-up prompts for this turn."""
    if decision.route == ROUTE_CLARIFY and entities.ambiguous:
        return _finish(_clarify_follow_ups(ctx, entities, manifest))
    if decision.route == ROUTE_REFUSE:
        return _finish(in_scope_alternatives(ctx, entities, manifest, limit=MAX_FOLLOW_UPS))

    team = manifest.team(ctx.team_id) if ctx.team_id else None
    roster_fit = ctx.mode == "roster_fit"
    exclude = team.id if (team is not None and roster_fit) else None
    top = top_priority_keys(ctx, manifest, limit=2)
    named = [
        pid
        for pid in entities.player_ids
        if (p := manifest.player(pid)) is not None and (exclude is None or p.current_club_id != exclude)
    ]
    retrieved = _retrieved_players(retrieval, manifest, exclude)
    candidates = list(dict.fromkeys([*named, *retrieved]))

    out: list[str] = []
    out.append(
        "What evidence would make this recommendation wrong?"
        if roster_fit
        else "What evidence would contradict this read?"
    )
    if len(top) == 2:
        out.append(
            f"What if {_label(top[1], manifest)} mattered more than {_label(top[0], manifest)}?"
        )
    if len(candidates) >= 2:
        first, second = (manifest.player(pid) for pid in candidates[:2])
        if first is not None and second is not None:
            focus = f" on {_label(top[0], manifest)}" if top else ""
            out.append(f"Compare {first.name} and {second.name}{focus}.")
    # Inspect uncertainty: a known conflict on the table is the sharpest version of it.
    uncertainty = "Which of these claims rests on a single report?"
    for conflict in manifest.conflicts_for(set(retrieval.source_ids)):
        subject = next((manifest.player(e) for e in conflict.entity_ids if manifest.player(e)), None)
        if subject is not None:
            uncertainty = (
                f"How should we weigh the conflicting reports on {_possessive(subject.name)} "
                f"{_label(conflict.dimension, manifest)}?"
            )
            break
    out.append(uncertainty)
    focus_player = manifest.player(candidates[0]) if candidates else None
    if focus_player is not None:
        out.append(f"What should we scout next to settle the open questions on {focus_player.name}?")
    else:
        out.append("What should we scout next to test this read?")
    return _finish(out)
