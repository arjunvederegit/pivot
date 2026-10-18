"""The evidence grid — documented observations per GM priority, straight from the manifest.

In roster-fit mode a GM wants to see candidates side by side against their priorities. The
obvious design is a weighted "fit score" per player; this sandbox deliberately does not have
one (there is no fit model, and a number would look more precise than the evidence is). The
grid shows what the documents actually say instead:

    rows        candidate players — those the question named or the conversation carried,
                otherwise those the retrieved passages are about — never players already on
                the selected team, at most five, in retrieval order;
    columns     the GM's priorities at must-preserve and high, then medium (at most five);
    each cell   the most recent analyst grade for that player and priority, as written in its
                source, with the source id and date, the conflict id when reports disagree,
                and a short manifest stat line where one exists ("3P 40.4% on 6.1 3PA").

Nothing is weighted, summed or ranked, and a blank cell says "No documented observation"
rather than inventing a neutral grade. The grid is deterministic: the same question returns
the same grid whatever the model writes.

Pure Python: no LangChain.
"""

from __future__ import annotations

from app.assistant_rag.manifest import Assessment, Manifest
from app.assistant_rag.models import EvidenceGridOut, GridCellOut, GridRowOut
from app.assistant_rag.policy import priority_order
from app.assistant_rag.types import EntityResolution, QueryContext, RetrievalResult

GRID_LABEL = "Documented observations — not a fit score"
MAX_ROWS = 5
MAX_DIMENSIONS = 5
NO_OBSERVATION = "No documented observation"
_GRID_LEVELS = ("must_preserve", "high", "medium")


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def grid_dimensions(priorities: dict[str, str], manifest: Manifest) -> list[str]:
    ordered = [k for k in priority_order(priorities, manifest) if priorities[k] in _GRID_LEVELS]
    return ordered[:MAX_DIMENSIONS]


def stat_line(player_id: str, dimension: str, manifest: Manifest) -> str | None:
    """The manifest statistic that belongs next to a grade, or why it is missing."""
    if dimension == "spacing":
        pct = manifest.fact(player_id, "three_pt_pct")
        attempts = manifest.fact(player_id, "three_pa_per_game")
        if pct is not None:
            line = f"3P {pct.value:.1f}%"
            return f"{line} on {attempts.value:.1f} 3PA" if attempts is not None else line
        gap = manifest.unavailable_fact(player_id, "three_pt_pct")
        return gap.reason if gap is not None else None
    simple = {"rebounding": ("rebounds_per_game", "RPG"), "rim_protection": ("blocks_per_game", "BPG")}
    if dimension in simple:
        stat, label = simple[dimension]
        fact = manifest.fact(player_id, stat)
        return f"{fact.value:.1f} {label}" if fact is not None else None
    if dimension == "availability":
        fact = manifest.fact(player_id, "games_played")
        return f"{_fmt(fact.value)} GP" if fact is not None else None
    if dimension == "youth_upside":
        player = manifest.player(player_id)
        return f"Age {player.age}" if player is not None else None
    return None


def latest_assessment(player_id: str, dimension: str, manifest: Manifest) -> Assessment | None:
    """The most recent grade by its source's report date (ties: manifest order)."""
    best: tuple[str, Assessment] | None = None
    for assessment in manifest.assessments_for(player_id, dimension):
        meta = manifest.document(assessment.source_id)
        date = meta.report_date if meta is not None else ""
        if best is None or date > best[0]:
            best = (date, assessment)
    return best[1] if best else None


def _conflict_id(player_id: str, dimension: str, manifest: Manifest) -> str | None:
    return next(
        (c.id for c in manifest.conflicts if player_id in c.entity_ids and c.dimension == dimension),
        None,
    )


def _candidate_rows(
    entities: EntityResolution, retrieval: RetrievalResult, team_id: str | None, manifest: Manifest
) -> list[str]:
    def eligible(pid: str) -> bool:
        player = manifest.player(pid)
        return player is not None and player.current_club_id != team_id

    named = [
        e.id
        for e in entities.resolved
        if e.kind == "player" and e.how in ("named", "carried_from_history") and eligible(e.id)
    ]
    if named:
        return list(dict.fromkeys(named))[:MAX_ROWS]
    from_sources: list[str] = []
    for item in sorted(retrieval.selected, key=lambda c: c.rank):
        for eid in item.chunk.meta.entity_ids:
            if eligible(eid) and eid not in from_sources:
                from_sources.append(eid)
    return from_sources[:MAX_ROWS]


def build_evidence_grid(
    ctx: QueryContext, entities: EntityResolution, retrieval: RetrievalResult, manifest: Manifest
) -> EvidenceGridOut | None:
    """The grid for a roster-fit answer, or None (research mode, or nothing to show)."""
    if ctx.mode != "roster_fit":
        return None
    team = manifest.team(ctx.team_id) if ctx.team_id else None
    priorities = dict(ctx.priorities) or (dict(team.default_priorities) if team else {})
    dimensions = grid_dimensions(priorities, manifest)
    rows_ids = _candidate_rows(entities, retrieval, team.id if team else None, manifest)
    if not dimensions or not rows_ids:
        return None
    rows: list[GridRowOut] = []
    for pid in rows_ids:
        player = manifest.player(pid)
        if player is None:  # pragma: no cover - _candidate_rows only returns known players
            continue
        cells = []
        for dimension in dimensions:
            assessment = latest_assessment(pid, dimension, manifest)
            meta = manifest.document(assessment.source_id) if assessment else None
            cells.append(
                GridCellOut(
                    dimension=dimension,  # type: ignore[arg-type]
                    grade=assessment.grade if assessment else None,
                    summary=assessment.summary if assessment else NO_OBSERVATION,
                    source_id=assessment.source_id if assessment else None,
                    report_date=meta.report_date if meta else None,
                    stat_line=stat_line(pid, dimension, manifest),
                    conflict_id=_conflict_id(pid, dimension, manifest),
                )
            )
        rows.append(
            GridRowOut(
                player_id=pid,
                player_name=player.name,
                current_club=manifest.club_name(player.current_club_id),
                cells=cells,
            )
        )
    origin = "the GM's priorities" if ctx.priorities else "the team's default priorities"
    excluded = f" Players already on the {team.name} are left out." if team else ""
    note = (
        "Non-official and unranked. Each cell is the most recent analyst grade written in a "
        f"source for that player and priority, shown as written; columns follow {origin} "
        "(must-preserve and high first, then medium). Nothing is weighted, summed or ranked, and "
        "a blank cell means no document assesses it. Conflict ids mark reports that disagree — "
        f"read both.{excluded}"
    )
    return EvidenceGridOut(label=GRID_LABEL, dimensions=dimensions, rows=rows, note=note)  # type: ignore[arg-type]
