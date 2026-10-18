"""The context catalog the UI renders, and the priority diff the prompt explains.

`build_context_catalog` turns the manifest into `ContextOut`: the fictional teams a GM can
select (with their documented default priorities and constraints), the players, the
priority dimensions and levels, and a set of starter questions. The starters are chosen to
teach the *kind* of question the sandbox is good at — trade-offs between priorities,
head-to-head comparisons against a team's needs, pre-mortems ("what would make us regret
this?"), reading conflicting reports — plus one legality question that demonstrates a
deterministic abstention.

`priority_changes` is how "the GM changed their mind" becomes explicit. A follow-up that
raises spacing from medium to must-preserve should change the recommendation, and the
prompt tells the model which priorities moved so it can say what changed and why.

Pure Python: no LangChain.
"""

from __future__ import annotations

from app.assistant_rag.manifest import Manifest
from app.assistant_rag.models import (
    ContextOut,
    PlayerOut,
    PriorityChangeOut,
    PriorityDimensionOut,
    StarterOut,
    TeamOut,
)

MODES: tuple[str, ...] = ("research", "roster_fit")

#: (mode, team_id, prompt, intent). Every player and team here is fictional.
STARTERS: tuple[tuple[str, str | None, str, str], ...] = (
    (
        "roster_fit",
        "COMETS",
        "We need better point-of-attack defense without losing the spacing our closing lineup "
        "depends on. Who fits, and what would we give up?",
        "Trade-off: add one priority without breaking a must-preserve one.",
    ),
    (
        "roster_fit",
        "COMETS",
        "Compare Niko Reed and Theo Vance for our closing lineup.",
        "Head-to-head comparison against the team's stated priorities.",
    ),
    (
        "roster_fit",
        "COMETS",
        "What would make us regret acquiring Bastian Holloway?",
        "Pre-mortem: name the evidence that would make the move wrong.",
    ),
    (
        "roster_fit",
        "SUNDOGS",
        "We need shooting volume and secondary creation, but our team defense has to stay intact. "
        "Who should we look at?",
        "Two needs plus a must-preserve identity: weigh what each option costs.",
    ),
    (
        "roster_fit",
        "TIDES",
        "How do we add size and rim protection without slowing down our pace?",
        "A need that conflicts with the team's identity: look for the least-bad compromise.",
    ),
    (
        "roster_fit",
        "RIVERHAWKS",
        "We want a young lead creator for the rebuild. Is Mateo Cruz the right target, and what "
        "are the risks?",
        "Timeline fit: does the player's age and profile match a rebuild?",
    ),
    (
        "research",
        None,
        "How should I read the conflicting reports on Theo Vance's point-of-attack defense?",
        "Conflicting evidence: dates, sample sizes and what each report actually observed.",
    ),
    (
        "research",
        None,
        "Is Dorian Pike's 41.2% three-point shooting real spacing?",
        "A good-looking number in context: volume, and a promotional source to be wary of.",
    ),
    (
        "research",
        None,
        "Has Wes Calder's wing defense held up since his 2023 evaluation?",
        "Stale evidence: an old grade against a recent re-evaluation.",
    ),
    (
        "research",
        None,
        "What did the playoff film study show about how defenses played Niko Reed?",
        "Qualitative playoff notes, with no playoff statistics to lean on.",
    ),
    (
        "roster_fit",
        "COMETS",
        "Is a Theo Vance trade legal under the CBA, and do the salaries match?",
        "Abstention demo: the sandbox has no CBA engine or contract data, and says so.",
    ),
)


def build_context_catalog(manifest: Manifest) -> ContextOut:
    teams = [
        TeamOut(
            id=t.id,
            name=t.name,
            abbreviation=t.abbreviation,
            timeline=t.timeline,
            identity=t.identity,
            default_priorities=dict(t.default_priorities),
            default_constraints=list(t.default_constraints),
            strategy_source_ids=list(t.strategy_source_ids),
        )
        for t in manifest.teams
    ]
    players = [
        PlayerOut(
            id=p.id,
            name=p.name,
            position=p.position,
            age=p.age,
            current_club=manifest.club_name(p.current_club_id),
            archetype=p.archetype,
        )
        for p in manifest.players
    ]
    dimensions = [
        PriorityDimensionOut(key=d.key, label=d.label, description=d.description)  # type: ignore[arg-type]
        for d in manifest.priority_dimensions
    ]
    team_ids = {t.id for t in manifest.teams}
    starters = [
        StarterOut(mode=mode, team_id=team_id, prompt=prompt, intent=intent)  # type: ignore[arg-type]
        for mode, team_id, prompt, intent in STARTERS
        if team_id is None or team_id in team_ids
    ]
    return ContextOut(
        data_mode="synthetic_demo",
        label=manifest.label,
        disclaimer=manifest.disclaimer,
        corpus_version=manifest.corpus_version,
        league=manifest.league_name,
        season=manifest.season,
        modes=list(MODES),  # type: ignore[arg-type]
        priority_levels=list(manifest.priority_levels),  # type: ignore[arg-type]
        priority_dimensions=dimensions,
        teams=teams,
        players=players,
        starters=starters,
    )


def priority_changes(
    previous: dict[str, str] | None, current: dict[str, str], manifest: Manifest
) -> list[PriorityChangeOut]:
    """What moved between the previous turn's priorities and this turn's, in the manifest's
    dimension order. `previous=None` means there was no previous turn to compare with, so
    nothing "changed"; an empty dict means the GM previously had no priorities."""
    if previous is None:
        return []
    changes: list[PriorityChangeOut] = []
    for dimension in manifest.priority_dimensions:
        before = previous.get(dimension.key)
        after = current.get(dimension.key)
        if before == after:
            continue
        changes.append(
            PriorityChangeOut(
                key=dimension.key,  # type: ignore[arg-type]
                label=dimension.label,
                before=before,  # type: ignore[arg-type]
                after=after,  # type: ignore[arg-type]
            )
        )
    return changes


def describe_change(change: PriorityChangeOut) -> str:
    """'Spacing: medium -> must preserve' / 'Rebounding: added at high' / '...: removed'."""

    def level(value: str | None) -> str:
        return (value or "").replace("_", " ")

    if change.before is None:
        return f"{change.label}: added at {level(change.after)}"
    if change.after is None:
        return f"{change.label}: removed (was {level(change.before)})"
    return f"{change.label}: {level(change.before)} -> {level(change.after)}"
