"""Deterministic routing rules — what Ask Pivot answers, clarifies or declines, decided
*before* any retrieval or model call.

Why rules, and why first? A language model asked "is this trade legal?" will happily write a
confident paragraph; a small local model will do so most of the time. Telling it "do not
answer legality questions" in the prompt helps, but a prompt is a request, not a guarantee.
So the requests this sandbox cannot serve are recognised here with plain regular
expressions, and the reply is written by code:

    route "clarify"  an ambiguous name ("Cruz") that nothing in the conversation resolves —
                     guessing would answer about the wrong player with full confidence;
    route "refuse"   the question is *only* about something the sandbox has no data for
                     (trade legality, salaries, a numeric fit score, a real NBA player, a
                     name that exists nowhere) — no model call, a deterministic
                     explanation instead;
    route "answer"   everything else. Mixed questions ("does Vance fit, and is the trade
                     legal?") are answered, with the unsupported part attached as an
                     `Unsupported` caveat that the prompt tells the model to decline and the
                     response shows the GM.

The rules are deliberately conservative and readable. They will miss paraphrases — that is
why the prompt repeats the same limits and `grounding.py` checks the answer afterwards for
legality verdicts and invented scores. Defence in depth, each layer simple enough to test.

Every "cannot" message is taken from the manifest's `unavailable_topics`, so the reason the
GM reads is the same reason the sandbox's own documentation (S31) gives.

Pure Python: no LangChain, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from app.assistant_rag import claims
from app.assistant_rag.manifest import Manifest, Player, UnavailableFact
from app.assistant_rag.types import EntityResolution, PolicyDecision, QueryContext, Unsupported

ROUTE_ANSWER = "answer"
ROUTE_CLARIFY = "clarify"
ROUTE_REFUSE = "refuse"

#: "What this research sandbox cannot calculate" — the document every deterministic refusal
#: cites. The pipeline must force-include it in the response's sources (see
#: `deterministic_citations`), because a citation only counts when its source was returned.
SANDBOX_LIMITS_SOURCE_ID = "S31"

#: `models.UnsupportedKind` -> the manifest's `unavailable_topics[].key`.
TOPIC_FOR_KIND: dict[str, str] = {
    "cba_legality": "cba_legality",
    "contracts_and_cap": "contracts_and_cap",
    "fit_score": "fit_scores",
    "medical": "medical",
    "playoff_statistics": "playoff_statistics",
}
#: Kinds whose deterministic explanation cites the sandbox-limits document.
_KINDS_CITING_LIMITS = frozenset({"cba_legality", "contracts_and_cap", "fit_score"})
_LEGALITY_KINDS = frozenset({"cba_legality", "contracts_and_cap"})

# ------------------------------------------------------------------------------ intent cues

#: Trade-rule intent: phrasing that asks for a verdict ("is it legal", "can we trade for",
#: "make the trade work"). `claims.LEGALITY_TOPIC` is the shared topic lexicon.
_CBA_CUES = re.compile(
    r"\b(?:cba|collective\s+bargaining|legal(?:ly|ity)?|illegal|salary[\s-]+match\w*|"
    r"match(?:ing)?\s+(?:the\s+)?salar(?:y|ies)|salaries\s+match|trade\s+exceptions?|aggregat\w+|"
    r"base[\s-]+year|(?:can|could|may)\s+(?:we|i|they|you)\s+(?:even\s+|actually\s+|still\s+)?trade\s+for|"
    r"trade\s+(?:rules?|restrictions?|machine)|"
    r"(?:allowed|permitted|able)\s+to\s+trade|make\s+(?:the|this|a|that)\s+trade\s+work|"
    r"make\s+(?:the\s+)?(?:money|salar(?:y|ies)|numbers)\s+work|salar(?:y|ies)\s+work|"
    r"second\s+apron)\b",
    re.IGNORECASE,
)
#: Trade intent without a verdict request ("should we trade for Vance?", "trade for a wing —
#: options?"). It earns the legality caveat, but on its own it is a research question, so it
#: never makes a question legality-only.
_TRADE_INTENT = re.compile(r"\btrade\s+for\b", re.IGNORECASE)
#: Money / cap intent. "How much" counts only when money follows it ("how much would he
#: cost?"); "how much does his defense matter?" is a research question.
_MONEY_CUES = re.compile(
    r"\b(?:contracts?|salary|salaries|payroll|cap\s+(?:space|room|sheet|holds?|rules?|number|hit)|"
    r"under\s+the\s+cap|over\s+the\s+cap|luxury\s+tax|tax\s+bill|aprons?|"
    # "afford Holloway" is about money; "can't afford to slow down" is an idiom.
    r"afford(?:able|ability|ed|s)?(?!\s+to\b)|"
    r"max(?:imum)?\s+(?:deal|contract|money)|extensions?|paid|pay\s+(?:him|them|for)|money|"
    r"how\s+much\s+(?:[\w'’]+\s+){0,5}?(?:costs?|costing|earns?|earning|paid|pay|owed?|dollars|millions?)|"
    r"fit\s+(?:\w+\s+){0,3}?(?:under|into|below)\s+(?:the\s+|our\s+)?(?:cap|apron|tax))\b",
    re.IGNORECASE,
)
#: A request for a number that no model in the sandbox produces.
_FIT_SCORE_REQUEST = re.compile(
    r"\b(?:(?:fit|impact|trade|overall|value)\s+(?:scores?|ratings?|numbers?)|"
    r"rate\s+(?:\w+\s+){0,4}?(?:out\s+of|from\s+(?:1|one|0|zero)\b|on\s+a\s+scale)|"
    r"score\s+(?:each|every|them|him|these|those|the\s+(?:players?|options?|candidates?|targets?))|"
    r"on\s+a\s+scale\s+of|out\s+of\s+(?:10|ten|100)\b|\d{1,3}\s*/\s*(?:10|100)\b|"
    r"numeric(?:al)?\s+(?:scores?|ratings?|grades?)|rank\s+(?:them|these|those)\s+by\s+(?:score|number))",
    re.IGNORECASE,
)
_MEDICAL = re.compile(
    r"\b(?:injur(?:y|ies|ed)|medical|medicals|surger(?:y|ies)|health|healthy|knee|ankle|"
    r"hamstring|achilles|concussions?|mri|rehab\w*|physicals?)\b",
    re.IGNORECASE,
)
_PLAYOFF = re.compile(r"\b(?:playoffs?|post[\s-]?season)\b", re.IGNORECASE)
#: With a playoff cue, these ask for playoff *numbers* ("how did he shoot in the playoffs?").
_STAT_WORDS = re.compile(
    r"(?:%|\b(?:stats?|statistics?|statistical|numbers|percentages?|pct|averag\w+|per\s+game|"
    r"splits?|efficiency|ppg|rpg|apg|true\s+shooting|shoot|shot|shooting|scor(?:e|ed|ing))\b)",
    re.IGNORECASE,
)
#: Words that mean the GM is asking for reasoning over evidence — the thing the sandbox is
#: for. A legality question that also contains one of these is a mixed question, answered
#: with a caveat; one without any is answered deterministically. `defen[cds]` covers
#: defense/defence/defensive(ly)/defend(s/ing)/defender(s); the skill words here and
#: `retrieval._DOMAIN_CUE` are kept in step by a test.
_RESEARCH_CUES = re.compile(
    r"\b(?:fits?|fitting|defen[cds]\w*|shoot\w*|shooter|pass(?:er|ers|ing)|handl\w*|scor(?:er|ers|ing)|"
    r"compar\w*|evidence|scout\w*|recommend\w*|"
    r"who|better|best|priorit\w*|risks?|risky|regret|should|worth|targets?|pursue|upgrade|"
    r"help|role|lineups?|spacing|rebound\w*|creat\w*|playmak\w*|grades?|reports?|film|"
    r"strengths?|weakness\w*|trade[\s-]?offs?|versus|vs|guard\w*|rim|size|upside|youth|"
    r"availability|conflict\w*|disagree\w*|why|explain)\b",
    re.IGNORECASE,
)


def _strip(pattern: re.Pattern[str], text: str) -> str:
    return pattern.sub(" ", text)


def _has_research_cues(question: str, manifest: Manifest) -> bool:
    """Research intent left once the unsupported phrases themselves are removed — so "fit
    score" does not count as asking about fit, nor "cap space" about spacing."""
    remainder = question
    for pattern in (_FIT_SCORE_REQUEST, _MONEY_CUES, _CBA_CUES, claims.LEGALITY_TOPIC):
        remainder = _strip(pattern, remainder)
    if _RESEARCH_CUES.search(remainder):
        return True
    lowered = remainder.lower()
    for dimension in manifest.priority_dimensions:
        for keyword in (dimension.label, *dimension.keywords):
            if re.search(rf"(?<![\w-]){re.escape(keyword.lower())}(?![\w-])", lowered):
                return True
    return False


# --------------------------------------------------------------------------- messages


def topic_reason(kind: str, manifest: Manifest) -> str | None:
    key = TOPIC_FOR_KIND.get(kind)
    if key is None:
        return None
    topic = next((t for t in manifest.unavailable_topics if t.key == key), None)
    return topic.reason if topic else None


def topic_label(kind: str, manifest: Manifest) -> str:
    key = TOPIC_FOR_KIND.get(kind)
    topic = next((t for t in manifest.unavailable_topics if t.key == key), None)
    if topic is not None:
        return topic.label
    return {"real_nba_entity": "Real NBA players and teams", "unknown_team": "Unknown team"}.get(
        kind, kind.replace("_", " ")
    )


_FALLBACK_REASONS = {
    "cba_legality": "The sandbox cannot check trade legality: it has no CBA engine and no contract data.",
    "contracts_and_cap": "The sandbox holds no contract, salary or cap data.",
    "fit_score": "The sandbox has no fit or impact model, only documented scouting observations.",
    "medical": "The sandbox holds no medical or injury records.",
    "playoff_statistics": "The sandbox's statistical profiles cover the regular season only.",
}


def _unsupported(kind: str, manifest: Manifest) -> Unsupported:
    return Unsupported(kind=kind, message=topic_reason(kind, manifest) or _FALLBACK_REASONS[kind])


def _join_names(names: Sequence[str], conjunction: str = "and") -> str:
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} {conjunction} {names[-1]}"


def _real_nba_message(names: Sequence[str], manifest: Manifest) -> str:
    quoted = _join_names([f"'{n}'" for n in names])
    verb = "is" if len(names) == 1 else "are"
    return (
        f"{quoted} {verb} not part of the fictional {manifest.league_name}. Ask Pivot only "
        "knows the invented players and teams in its sandbox corpus and will not describe real "
        "NBA players or teams from memory."
    )


def _unknown_name_message(names: Sequence[str], manifest: Manifest) -> str:
    quoted = _join_names([f"'{n}'" for n in names])
    verb = "is" if len(names) == 1 else "are"
    players = _join_names([p.name for p in manifest.players])
    teams = _join_names([t.name for t in manifest.teams])
    return (
        f"{quoted} {verb} not a player or team in the fictional {manifest.league_name}, so there "
        f"are no sources about {'it' if len(names) == 1 else 'them'}. The sandbox's players are "
        f"{players}; its teams are {teams}."
    )


def describe_candidate(entity_id: str, manifest: Manifest) -> str:
    """'Mateo Cruz (PG, Mesa Foxes)' — the disambiguating detail a GM needs."""
    player = manifest.player(entity_id)
    if player is not None:
        return f"{player.name} ({player.position}, {manifest.club_name(player.current_club_id)})"
    team = manifest.team(entity_id)
    if team is not None:
        return f"the {team.name} (team)"
    return manifest.club_name(entity_id)


def _bare_mention(mention: str) -> str:
    return re.sub(r"(?:'s|’s)$", "", mention.strip())


def clarification_text(entities: EntityResolution, manifest: Manifest) -> str:
    questions = []
    for ambiguity in entities.ambiguous:
        options = [describe_candidate(cid, manifest) for cid in ambiguity.candidate_ids]
        questions.append(
            f"Which {_bare_mention(ambiguity.mention)} do you mean — {_join_names(options, 'or')}?"
        )
    return " ".join(questions)


# ----------------------------------------------------------------------------- decide


def decide(ctx: QueryContext, entities: EntityResolution, manifest: Manifest) -> PolicyDecision:
    """Route one question. Order matters and is the whole policy:

    1. a question only about legality or money is refused — even with an ambiguous name,
       because which Cruz was meant changes nothing about the answer. Trade intent without
       a verdict request ("should we trade for Vance?") is never legality-only: it is
       answered, with the legality caveat;
    2. a question only about real NBA names (no fictional player or team named) is refused;
       so is one only about an invented name — but only when nothing fictional is in play
       (no player or team named or carried, no team selected in roster fit). Spotting an
       invented name is a heuristic, so beside anything real it is a caveat, never a refusal;
    3. an unresolved ambiguous name is clarified;
    4. a bare request for numeric scores, with nothing to research, is refused;
    5. everything else is answered, with any unsupported part attached as a caveat.
    """
    question = ctx.question
    unsupported: list[Unsupported] = []
    notes: list[str] = []

    if entities.unrecognized:
        unsupported.append(Unsupported("real_nba_entity", _real_nba_message(entities.unrecognized, manifest)))
    if entities.unknown_names:
        unsupported.append(Unsupported("unknown_entity", _unknown_name_message(entities.unknown_names, manifest)))
    if ctx.team_id and manifest.team(ctx.team_id) is None:
        unsupported.append(
            Unsupported("unknown_team", f"'{ctx.team_id}' is not a team in the fictional sandbox league.")
        )

    legality_kinds: list[str] = []
    if _CBA_CUES.search(question):
        legality_kinds.append("cba_legality")
    if _MONEY_CUES.search(question):
        legality_kinds.append("contracts_and_cap")
    if not legality_kinds and claims.LEGALITY_TOPIC.search(question):
        legality_kinds.append("cba_legality")
    unsupported.extend(_unsupported(kind, manifest) for kind in legality_kinds)
    if "cba_legality" not in legality_kinds and _TRADE_INTENT.search(question):
        unsupported.append(_unsupported("cba_legality", manifest))  # a caveat, not a refusal

    asks_fit_score = bool(_FIT_SCORE_REQUEST.search(question))
    if asks_fit_score:
        unsupported.append(_unsupported("fit_score", manifest))
    if _MEDICAL.search(question):
        unsupported.append(_unsupported("medical", manifest))
    if _PLAYOFF.search(question) and (
        _STAT_WORDS.search(question) or claims.stats_mentioned(question, manifest)
    ):
        unsupported.append(_unsupported("playoff_statistics", manifest))

    research = _has_research_cues(question, manifest)
    named_fictional = [e for e in entities.resolved if e.how == "named"]
    # Everything fictional the question is about: named, carried from earlier turns, or the
    # selected team in roster fit (in research mode the selection is only UI context).
    in_play = [e for e in entities.resolved if e.how != "selected_team" or ctx.mode == "roster_fit"]
    has_players = bool(entities.player_ids)
    caveats = tuple(unsupported)

    if legality_kinds and not research:
        notes.append("Legality/money cues with no research cues: answered deterministically.")
        return PolicyDecision(route=ROUTE_REFUSE, unsupported=caveats, notes=tuple(notes))
    if not entities.ambiguous and (
        (entities.unrecognized and not named_fictional) or (entities.unknown_names and not in_play)
    ):
        notes.append("Only names outside the fictional league were found: answered deterministically.")
        return PolicyDecision(route=ROUTE_REFUSE, unsupported=caveats, notes=tuple(notes))
    if entities.ambiguous:
        notes.append(
            "Ambiguous mention(s) "
            + ", ".join(repr(_bare_mention(a.mention)) for a in entities.ambiguous)
            + " not resolved by the conversation: asking instead of guessing."
        )
        return PolicyDecision(
            route=ROUTE_CLARIFY,
            unsupported=caveats,
            clarification=clarification_text(entities, manifest),
            notes=tuple(notes),
        )
    if asks_fit_score and not research and not has_players:
        notes.append("A numeric score was requested with nothing to research: answered deterministically.")
        return PolicyDecision(route=ROUTE_REFUSE, unsupported=caveats, notes=tuple(notes))
    if caveats:
        notes.append(
            "Answering with caveats: " + ", ".join(u.kind for u in caveats) + " cannot be served."
        )
    return PolicyDecision(route=ROUTE_ANSWER, unsupported=caveats, notes=tuple(notes))


def deterministic_citations(decision: PolicyDecision) -> tuple[str, ...]:
    """Source ids that `deterministic_answer(decision, ...)` cites.

    The pipeline treats a citation as valid only when its source was retrieved, so it must
    add these sources to the response itself (a refusal never runs retrieval)."""
    if decision.route == ROUTE_REFUSE and any(u.kind in _KINDS_CITING_LIMITS for u in decision.unsupported):
        return (SANDBOX_LIMITS_SOURCE_ID,)
    return ()


# ------------------------------------------------------------------ deterministic replies


def _possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def priority_order(priorities: dict[str, str], manifest: Manifest) -> list[str]:
    """Priority keys, most important first: by level, then by the manifest's dimension order."""
    level_rank = {level: i for i, level in enumerate(manifest.priority_levels)}
    dim_rank = {d.key: i for i, d in enumerate(manifest.priority_dimensions)}
    keys = [k for k in priorities if k in dim_rank]
    return sorted(keys, key=lambda k: (level_rank.get(priorities[k], 99), dim_rank[k]))


def effective_priorities(ctx: QueryContext, manifest: Manifest) -> dict[str, str]:
    """The GM's priorities, or the selected team's documented defaults when none were set."""
    if ctx.priorities:
        return dict(ctx.priorities)
    team = manifest.team(ctx.team_id) if ctx.team_id else None
    return dict(team.default_priorities) if team else {}


def top_priority_keys(ctx: QueryContext, manifest: Manifest, limit: int = 2) -> list[str]:
    priorities = effective_priorities(ctx, manifest)
    ordered = [k for k in priority_order(priorities, manifest) if priorities[k] != "low"]
    return ordered[:limit]


def _label(key: str, manifest: Manifest) -> str:
    dimension = manifest.dimension(key)
    return dimension.label if dimension else key.replace("_", " ")


def _players(ids: Iterable[str], manifest: Manifest) -> list[Player]:
    found = []
    for pid in ids:
        player = manifest.player(pid)
        if player is not None and player not in found:
            found.append(player)
    return found


def in_scope_alternatives(
    ctx: QueryContext, entities: EntityResolution, manifest: Manifest, limit: int = 3
) -> list[str]:
    """Questions the sandbox *can* research, built from what the GM already named.

    Phrased to avoid the money and legality words, so following one does not trip the same
    refusal again."""
    team = manifest.team(ctx.team_id) if ctx.team_id else None
    players = _players(entities.player_ids, manifest)
    if team is not None:
        players = [p for p in players if p.current_club_id != team.id] or players
    priorities = [_label(k, manifest).lower() for k in top_priority_keys(ctx, manifest)]
    out: list[str] = []
    if players:
        first = players[0]
        if team is not None:
            out.append(
                f"Leaving the trade mechanics aside, how would {first.name} fit the {_possessive(team.name)} priorities?"
            )
        else:
            out.append(f"What do the scouting reports say about {_possessive(first.name)} strengths and weaknesses?")
        if len(players) > 1:
            out.append(f"Compare {first.name} and {players[1].name} on the documented evidence.")
        out.append(f"What would make us regret acquiring {first.name}?")
    elif team is not None:
        focus = " and ".join(priorities) if priorities else "stated priorities"
        out.append(f"Which players best fit the {_possessive(team.name)} needs on {focus}?")
        out.append(f"What do the {_possessive(team.name)} strategy memos say we must not give up?")
    out.extend(
        [
            "Which players are documented as strong point-of-attack defenders?",
            "Compare Niko Reed and Theo Vance for a closing lineup.",
            "What can this research sandbox not calculate, and why?",
        ]
    )
    deduped: list[str] = []
    for item in out:
        if item not in deduped:
            deduped.append(item)
    return deduped[:limit]


def _quoted_bullets(items: Iterable[str]) -> str:
    return "\n".join(f'- "{item}"' for item in items)


#: The heading over the suggested questions in every refusal and every abstention. Those
#: replies name players only as a roster ("the sandbox's players are ...") or as examples
#: ("Compare Niko Reed and Theo Vance ..."), so `entities` must not read them as an answer
#: that discussed those players; `is_deterministic_reply` finds them by these headings.
REFUSAL_ALTERNATIVES_HEADING = "What you can research here instead"
ABSTENTION_ALTERNATIVES_HEADING = "Questions it can answer"
_DETERMINISTIC_HEADING = re.compile(
    rf"^\s*\**\s*(?:{re.escape(REFUSAL_ALTERNATIVES_HEADING)}|{re.escape(ABSTENTION_ALTERNATIVES_HEADING)})\s*\**\s*$",
    re.MULTILINE,
)


def is_deterministic_reply(text: str) -> bool:
    """True for a refusal or abstention written by this module (sent back as history by the
    frontend like any other answer). Clarifications are not included: the players a
    clarification lists are exactly the candidates the GM's next reply chooses between."""
    return bool(_DETERMINISTIC_HEADING.search(text))


def _clarify_answer(
    decision: PolicyDecision, ctx: QueryContext, entities: EntityResolution, manifest: Manifest
) -> str:
    lines = [decision.clarification or clarification_text(entities, manifest), ""]
    lines.append(
        "I have not guessed, because these are different players with different documented "
        "profiles, and an answer about the wrong one would read just as confidently:"
    )
    for ambiguity in entities.ambiguous:
        for cid in ambiguity.candidate_ids:
            player = manifest.player(cid)
            if player is not None:
                club = manifest.club_name(player.current_club_id)
                lines.append(f"- **{player.name}** — {player.position}, {club}. {player.archetype}.")
            else:
                lines.append(f"- **{describe_candidate(cid, manifest)}**")
    first = next(
        (manifest.player(c) for a in entities.ambiguous for c in a.candidate_ids if manifest.player(c)), None
    )
    example = f' (for example, "{first.name}")' if first else ""
    lines += ["", f"Reply with the full name{example} and I will answer from the scouting sources."]
    caveats = [u for u in decision.unsupported if u.kind != "real_nba_entity"]
    if caveats:
        lines += ["", "**Also note:** " + " ".join(u.message for u in caveats)]
    return "\n".join(lines)


def _refuse_answer(
    decision: PolicyDecision, ctx: QueryContext, entities: EntityResolution, manifest: Manifest
) -> str:
    kinds = [u.kind for u in decision.unsupported]
    cite = f" [{SANDBOX_LIMITS_SOURCE_ID}]"
    short: list[str] = []
    why: list[str] = []
    if any(k in _LEGALITY_KINDS for k in kinds):
        short.append(
            "Ask Pivot cannot tell you whether this trade is legal, whether the salaries match, "
            "or what it would do to anyone's cap — and it will not guess."
        )
        for kind in ("cba_legality", "contracts_and_cap"):
            reason = topic_reason(kind, manifest) or _FALLBACK_REASONS[kind]
            why.append(reason + cite)
        why.append(
            "A legality verdict without those inputs would be a guess dressed up as an answer."
        )
    if "fit_score" in kinds:
        short.append("Ask Pivot does not produce numeric fit or impact scores.")
        why.append((topic_reason("fit_score", manifest) or _FALLBACK_REASONS["fit_score"]) + cite)
        why.append("A number here would look precise while resting on nothing the sandbox can compute.")
    real = next((u for u in decision.unsupported if u.kind == "real_nba_entity"), None)
    if real is not None:
        names = list(entities.unrecognized)
        pronoun = "them" if len(names) > 1 else "that name"
        short.append(f"There are no sources about {pronoun} here.")
        why.append(real.message)
        why.append(
            "Anything said about a real player from memory would be unsourced and uncheckable, and "
            "Pivot's real NBA data is deliberately kept out of this sandbox."
        )
    invented = next((u for u in decision.unsupported if u.kind == "unknown_entity"), None)
    if invented is not None:
        short.append("Nobody by that name is in the sandbox, so there is nothing to look up — and Ask Pivot will not invent it.")
        why.append(invented.message)
    unknown = next((u for u in decision.unsupported if u.kind == "unknown_team"), None)
    if unknown is not None:
        why.append(unknown.message)
    if not short:  # defensive: a refusal always says why
        short.append("Ask Pivot cannot answer this from the fictional sandbox corpus.")

    parts = ["**Short answer:** " + " ".join(short), "", "**Why not**", *[f"- {w}" for w in why]]
    if any(k in _LEGALITY_KINDS for k in kinds):
        parts += [
            "",
            "**What a real answer would need**",
            "- Each player's contract: salary by season, guarantees, options and any trade restrictions.",
            "- Both teams' current cap sheets: payroll, tax and apron position, available exceptions.",
            "- A deterministic rules engine to apply the CBA to those inputs. In Pivot that is the CBA "
            "engine, reached through the read-only tools registry — neither is connected to this sandbox.",
        ]
    parts += [
        "",
        f"**{REFUSAL_ALTERNATIVES_HEADING}**",
        _quoted_bullets(in_scope_alternatives(ctx, entities, manifest)),
    ]
    return "\n".join(parts)


def deterministic_answer(
    decision: PolicyDecision, ctx: QueryContext, entities: EntityResolution, manifest: Manifest
) -> str:
    """The markdown reply for a "clarify" or "refuse" route — written by code, not a model.

    Cites exactly `deterministic_citations(decision)`."""
    if decision.route == ROUTE_CLARIFY:
        return _clarify_answer(decision, ctx, entities, manifest)
    if decision.route == ROUTE_REFUSE:
        return _refuse_answer(decision, ctx, entities, manifest)
    raise ValueError(f"route {decision.route!r} is answered by the model, not deterministically")


def abstention_answer(ctx: QueryContext, entities: EntityResolution, manifest: Manifest) -> str:
    """The reply when retrieval finds nothing relevant: say so, do not guess, offer in-scope
    questions. No model call, no citations."""
    players = _players(entities.player_ids, manifest)
    about = f" about {_join_names([p.name for p in players])}" if players else ""
    selectable = len(manifest.teams)
    lines = [
        f"**Short answer:** Nothing in the fictional scouting corpus addresses this question{about}, "
        "so Ask Pivot will not guess.",
        "",
        f"**What the sandbox covers:** the invented {manifest.league_name} ({manifest.season}) — "
        f"{len(manifest.players)} fictional players and {selectable} selectable teams, described in "
        "scouting reports, statistical profiles, team strategy memos, coaching notes and method "
        "guides. It holds no real NBA data, no contracts and no medical records.",
        "",
        f"**{ABSTENTION_ALTERNATIVES_HEADING}**",
        _quoted_bullets(in_scope_alternatives(ctx, entities, manifest)),
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- known gaps

#: Words that make a missing statistic relevant even when its exact keyword is absent
#: ("can he space the floor?" is about three-point shooting).
_RELATED_CUES: dict[str, re.Pattern[str]] = {
    "three_pt_pct": re.compile(
        r"\b(?:shoot\w*|spacing|space\s+the\s+floor|spacer|three\w*|3p\w*|3-point\w*|jumper|"
        r"jump\s+shot|range|from\s+deep|beyond\s+the\s+arc|stretch)\b",
        re.IGNORECASE,
    ),
    "ft_pct": re.compile(
        r"\b(?:shoot\w*|free[\s-]throws?|ft%?|from\s+the\s+line|at\s+the\s+line|touch)\b",
        re.IGNORECASE,
    ),
}
_RELATED_CUES["three_pa_per_game"] = _RELATED_CUES["three_pt_pct"]


def stat_relevant(stat: str, text: str, manifest: Manifest) -> bool:
    if stat in claims.stats_mentioned(text, manifest):
        return True
    cue = _RELATED_CUES.get(stat)
    return bool(cue and cue.search(text))


def relevant_unavailable_facts(
    manifest: Manifest, player_ids: Iterable[str], text: str | None = None
) -> list[UnavailableFact]:
    """Deliberately missing statistics for these players — all of them when `text` is None,
    otherwise only those whose statistic `text` is about. Manifest order, de-duplicated."""
    wanted = set(player_ids)
    return [
        gap
        for gap in manifest.unavailable_facts
        if gap.entity_id in wanted and (text is None or stat_relevant(gap.stat, text, manifest))
    ]
