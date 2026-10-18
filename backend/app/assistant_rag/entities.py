"""Entity resolution: which fictional players and teams is this question about?

Retrieval, policy and the evidence grid all need the same answer to that question, so it is
computed once, deterministically, before any search or model call. The rules, in order:

1. **Named mentions.** `claims.find_mentions` finds every manifest alias, longest match
   first, so "Mateo Cruz" claims its span before the bare "Cruz" inside it can. Surnames
   that are also English words ("Reed", "Pike") match only when capitalised — except in a
   message typed entirely in lowercase ("compare reed and vance"), where nothing is
   capitalised and so capitals cannot tell a name from a word.
2. **Ambiguous bare names.** "Cruz" is two players (Mateo, Dante); "Niko" is two (Reed,
   Valdez). A bare name is ambiguous whenever it is the alias, first name or last name of
   more than one player — the manifest's alias list alone is not trusted to say so. It is
   resolved only from evidence, never guessed:
     * if the same question names exactly one of the candidates in full ("Mateo Cruz ...
       is Cruz a passer?"), the bare name is that player;
     * otherwise the newest history turns (up to 4) are read newest first; the first turn
       that names any candidate unambiguously decides it, and only if it names exactly one
       (`how="carried_from_history"`). A turn naming both keeps it ambiguous.
   Anything still undecided becomes an `Ambiguity`, which policy turns into a clarifying
   question instead of an answer about the wrong player.
3. **Carry-forward for follow-ups.** "Would you change your ranking?", "What about him?" and
   "the first target" name nobody. When the question names no player at all, the players
   named unambiguously in the most recent assistant turn, then the most recent user turn,
   are carried forward (at most 4, most recent turn first). A question that names a new
   player but points back with a pronoun ("Is he a better shooter than Theo Vance?",
   "Compare him with Holloway") carries the earlier players too (at most 2), after the
   named one. A refusal or abstention written by `policy` is skipped: the players it names
   are a roster or example questions, not players the conversation discussed.
4. **Teams.** Teams and clubs named in the text resolve as `named`; the GM's selected team
   is added as `selected_team`.
5. **Real NBA names.** `detect_real_nba_names` flags real players and teams (from
   `nba_api`'s bundled static lists — local JSON, no network). The corpus is fictional, so
   a real name routes to a deterministic refusal rather than a hallucinated answer.
6. **Invented names.** `detect_unknown_names` flags name-shaped phrases that exist nowhere
   ("Corwin Ashby"). It is a heuristic and errs toward missing a name, because a false
   hit refuses a legitimate question ("Who is the best Rim Protector?").

Output order is deterministic: named entities in text order, then carried ones, then the
selected team; each entity appears once. Pure Python apart from the optional `nba_api` data.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache

from app.assistant_rag.claims import CASE_SENSITIVE_ALIASES, Mention, find_mentions
from app.assistant_rag.manifest import Manifest
from app.assistant_rag.policy import is_deterministic_reply
from app.assistant_rag.types import Ambiguity, EntityRef, EntityResolution, HistoryTurn

#: How many history turns (newest first) may disambiguate a bare name.
HISTORY_LOOKBACK = 4
#: At most this many players are carried into a follow-up that names nobody.
MAX_CARRIED = 4
#: At most this many earlier players are carried beside a newly named one ("Is he a better
#: shooter than Theo Vance?"): the pronoun points at one or two players, not a roster.
MAX_CARRIED_BESIDE_NAMED = 2


# ------------------------------------------------------------------------- fictional names


@lru_cache(maxsize=4)
def _shared_name_parts(manifest: Manifest) -> dict[str, tuple[str, tuple[str, ...]]]:
    """`"cruz" -> ("Cruz", (DANTE_CRUZ, MATEO_CRUZ))`: first/last names held by 2+ players."""
    owners: dict[str, tuple[str, set[str]]] = {}
    for player in manifest.players:
        for part in (player.first_name, player.last_name):
            owners.setdefault(part.lower(), (part, set()))[1].add(player.id)
    return {key: (display, tuple(sorted(ids))) for key, (display, ids) in owners.items() if len(ids) > 1}


def _strip_possessive(text: str) -> str:
    return re.sub(r"(?:'s|’s)$", "", text)


@lru_cache(maxsize=4)
def _lowercase_player_aliases(manifest: Manifest) -> tuple[re.Pattern[str], ...]:
    """Patterns that capitalise the case-sensitive player surnames ("reed" -> "Reed")."""
    aliases = sorted(
        {a for p in manifest.players for a in p.aliases if a.lower() in CASE_SENSITIVE_ALIASES and " " not in a}
    )
    return tuple(re.compile(rf"(?<![\w-]){re.escape(a.lower())}(?=(?:'s|’s)?(?![\w-]))") for a in aliases)


def _matching_text(text: str, manifest: Manifest) -> str:
    """`text` as the alias matcher should read it.

    `claims.find_mentions` accepts "Reed" and "Pike" only when capitalised, so prose like
    "the reed bent" is not Niko Reed. That signal only exists when the writer uses capitals:
    chat is often typed all lowercase ("compare reed and vance"), and there a lowercase
    surname is the only way the GM could have written the name. So in a message with no
    capital letter at all, those player surnames are capitalised before matching. The
    replacement keeps every character offset, so mention spans still index `text`. Team
    words ("tides", "foxes", "herons") stay case-sensitive even then: "the tides" in casual
    prose is the sea far more often than the Savannah Tides."""
    if any(ch.isupper() for ch in text):
        return text
    for pattern in _lowercase_player_aliases(manifest):
        text = pattern.sub(lambda m: m.group(0).capitalize(), text)
    return text


def player_mentions(text: str, manifest: Manifest) -> list[Mention]:
    """`claims.find_mentions`, with bare shared names widened to every player who has them.

    The manifest lists "Niko" as an alias of Niko Valdez only, but Niko Reed is a Niko too,
    so a bare "Niko" is ambiguous here. A capitalised shared name that is not an alias at
    all is also caught. Non-player mentions pass through unchanged. All-lowercase text is
    matched with its case-sensitive player surnames capitalised (`_matching_text`).
    """
    shared = _shared_name_parts(manifest)
    matched_text = _matching_text(text, manifest)
    mentions: list[Mention] = []
    for mention in find_mentions(matched_text, manifest):
        ids = mention.entity_ids
        bare = _strip_possessive(mention.text).lower()
        if mention.kind == "player" and bare in shared:
            ids = tuple(sorted(set(ids) | set(shared[bare][1])))
        # spans are the same in both texts; report the GM's own spelling
        mentions.append(Mention(text[mention.start : mention.end], mention.start, mention.end, ids, mention.kind))
    taken = [(m.start, m.end) for m in mentions]
    for display, ids in shared.values():
        # Case-sensitive: a shared name that is not an alias counts only as a proper noun.
        pattern = re.compile(rf"(?<![\w-]){re.escape(display)}(?:'s|’s)?(?![\w-])")
        for match in pattern.finditer(matched_text):
            start, end = match.span()
            if any(start < t1 and end > t0 for t0, t1 in taken):
                continue
            taken.append((start, end))
            mentions.append(Mention(text[start:end], start, end, ids, "player"))
    mentions.sort(key=lambda m: m.start)
    return mentions


def _named_players(text: str, manifest: Manifest) -> list[str]:
    """Player ids named unambiguously in `text`, in text order, de-duplicated."""
    seen: list[str] = []
    for mention in player_mentions(text, manifest):
        if mention.kind == "player" and not mention.ambiguous and mention.entity_ids[0] not in seen:
            seen.append(mention.entity_ids[0])
    return seen


def named_entity_ids(text: str, manifest: Manifest) -> frozenset[str]:
    """Every player, team and club id named unambiguously in `text` (same widening rules)."""
    return frozenset(m.entity_ids[0] for m in player_mentions(text, manifest) if not m.ambiguous)


def _entity_name(entity_id: str, kind: str, manifest: Manifest) -> str:
    if kind == "player":
        player = manifest.player(entity_id)
        return player.name if player is not None else entity_id
    return manifest.club_name(entity_id)


def _discussed(turn: HistoryTurn) -> bool:
    """False for a refusal or abstention `policy` wrote: it lists the whole roster or example
    questions ("Compare Niko Reed and Theo Vance ..."), and those names say nothing about who
    the conversation is about. The frontend sends such replies back as ordinary history."""
    return not (turn.role == "assistant" and is_deterministic_reply(turn.content))


def _from_history(candidates: Sequence[str], history: Sequence[HistoryTurn], manifest: Manifest) -> str | None:
    """The one candidate the newest history turn that names any of them names, else None."""
    for turn in list(reversed(history))[:HISTORY_LOOKBACK]:
        if not _discussed(turn):
            continue
        named = [pid for pid in _named_players(turn.content, manifest) if pid in candidates]
        if named:
            return named[0] if len(named) == 1 else None
    return None


def _carry_forward(history: Sequence[HistoryTurn], manifest: Manifest) -> list[str]:
    """Players from the latest assistant turn and the latest user turn, most recent first
    (an assistant turn that was a deterministic refusal or abstention does not count)."""
    latest: dict[str, HistoryTurn] = {}
    order: list[HistoryTurn] = []
    for turn in reversed(history):
        if not _discussed(turn):
            continue
        if turn.role in ("assistant", "user") and turn.role not in latest:
            latest[turn.role] = turn
            order.append(turn)
        if len(latest) == 2:
            break
    carried: list[str] = []
    for turn in order:
        for pid in _named_players(turn.content, manifest):
            if pid not in carried:
                carried.append(pid)
    return carried[:MAX_CARRIED]


#: Third-person pronouns that can point back at a player from earlier turns.
_BACK_REFERENCE = re.compile(r"\b(?:he|him|his|himself|they|them|their|theirs)\b", re.IGNORECASE)
#: A comparison word just before a pronoun: "better than him", "compare Vance with him".
_COMPARED_WITH = re.compile(r"\b(?:than|with|or|and|vs\.?|versus|to|against|over)\s+$", re.IGNORECASE)


def _refers_back(question: str, mentions: Sequence[Mention]) -> bool:
    """True when the question names a player *and* uses a pronoun for someone else.

    A pronoun before the first named player cannot mean that player ("Is he a better
    shooter than Theo Vance?", "Compare him with Holloway"), and neither can one on the
    other side of a comparison ("Is Theo Vance better than him?"). A pronoun after the name,
    outside a comparison, is read as that player ("Does Theo Vance fit, and can he guard
    wings?") and carries nothing."""
    first = min((m.start for m in mentions if m.kind == "player"), default=None)
    if first is None:
        return False
    return any(
        match.start() < first or _COMPARED_WITH.search(question[: match.start()])
        for match in _BACK_REFERENCE.finditer(question)
    )


# --------------------------------------------------------------------------- resolution


def resolve_entities(
    question: str,
    history: tuple[HistoryTurn, ...],
    team_id: str | None,
    manifest: Manifest,
) -> EntityResolution:
    """Resolve the question's entities against the fictional manifest (rules: module doc)."""
    mentions = player_mentions(question, manifest)
    named: list[EntityRef] = []
    carried: list[EntityRef] = []
    ambiguous: list[Ambiguity] = []

    named_in_question = {m.entity_ids[0] for m in mentions if m.kind == "player" and not m.ambiguous}
    for mention in mentions:
        if not mention.ambiguous:
            entity_id = mention.entity_ids[0]
            named.append(EntityRef(entity_id, _entity_name(entity_id, mention.kind, manifest), mention.kind, "named"))
            continue
        in_question = [pid for pid in mention.entity_ids if pid in named_in_question]
        if in_question:
            # "Mateo Cruz vs Dante — which Cruz?" The question itself already resolved every
            # candidate it means; the bare name adds no new entity and needs no clarification.
            continue
        chosen = _from_history(mention.entity_ids, history, manifest) if mention.kind == "player" else None
        if chosen is not None:
            carried.append(EntityRef(chosen, _entity_name(chosen, mention.kind, manifest), mention.kind, "carried_from_history"))
        else:
            ambiguous.append(Ambiguity(_strip_possessive(mention.text), mention.entity_ids))

    if not any(m.kind == "player" for m in mentions):
        to_carry = _carry_forward(history, manifest)
    elif named_in_question and _refers_back(question, mentions):
        # "Is he a better shooter than Theo Vance?": the comparison needs both players.
        to_carry = [pid for pid in _carry_forward(history, manifest) if pid not in named_in_question]
        to_carry = to_carry[:MAX_CARRIED_BESIDE_NAMED]
    else:
        to_carry = []
    for pid in to_carry:
        carried.append(EntityRef(pid, _entity_name(pid, "player", manifest), "player", "carried_from_history"))

    resolved: list[EntityRef] = []
    seen: set[tuple[str, str]] = set()
    selected: list[EntityRef] = []
    team = manifest.team(team_id) if team_id else None
    if team is not None:
        selected.append(EntityRef(team.id, team.name, "team", "selected_team"))
    for ref in [*named, *carried, *selected]:
        if (ref.kind, ref.id) not in seen:
            seen.add((ref.kind, ref.id))
            resolved.append(ref)

    unique_ambiguities: list[Ambiguity] = []
    for item in ambiguous:
        if all(a.candidate_ids != item.candidate_ids for a in unique_ambiguities):
            unique_ambiguities.append(item)

    real = detect_real_nba_names(question, exclude=_fictional_names(manifest))
    return EntityResolution(
        resolved=tuple(resolved),
        ambiguous=tuple(unique_ambiguities),
        unrecognized=real,
        unknown_names=tuple(n for n in detect_unknown_names(question, manifest) if _name_key(n) not in {_name_key(r) for r in real}),
    )


# --------------------------------------------------------------------- unknown names

#: Capitalised words that start a sentence or a request, or greet, thank or title someone —
#: never part of a name: "Tell", "Hey Pivot", "Thanks Pivot", "Coach Grant", "Per Dana
#: Whitfield", "All Star". Stripped from the front of a candidate before it is judged.
_LEADING_WORDS = frozenset(
    [
        "a", "an", "and", "are", "as", "at", "but", "can", "compare", "could", "did", "do",
        "does", "explain", "for", "from", "give", "given", "has", "have", "how", "i", "if",
        "in", "is", "it", "let", "my", "no", "not", "now", "of", "on", "or", "our", "please",
        "scout", "should", "show", "since", "so", "summarize", "summarise", "tell", "than",
        "that", "the", "their", "then", "there", "these", "they", "this", "those", "to", "was",
        "we", "what", "when", "where", "which", "who", "why", "will", "with", "would", "yes",
        "you", "your", "actually", "also", "okay", "ok",
        # greetings, thanks and chat openers
        "hey", "hi", "hello", "dear", "thanks", "thank", "sorry", "quick", "great", "cool", "all",
        # titles and roles in front of a staff member's name
        "coach", "analyst", "mr", "mrs", "ms", "dr", "per", "about", "according",
    ]
)  # fmt: skip
#: Multi-word capitalised terms the product and corpus use that are not players or teams.
_KNOWN_TERMS = frozenset(
    {
        "ask pivot", "pivot intelligence", "pivot synthetic league", "pivot synthetic data desk",
        "pivot scouting methods", "pivot coaching clinic", "pivot intelligence lab",
        "cartwright sports representation", "data desk", "trade evaluator", "strategy lab",
        "cap center", "data health", "fictional data", "roster fit", "point guard",
        "shooting guard", "small forward", "power forward", "regular season", "reader mailbag",
    }
)
#: Everyday and basketball words a GM may capitalise ("Quick Question", "All Star", "Best
#: Fit") that are never part of a name. Most such words come from the manifest's own prose
#: (`_ordinary_words`); these are the chat words it has no reason to contain.
_COMMON_WORDS = frozenset(
    [
        "hey", "hi", "hello", "thanks", "thank", "sorry", "quick", "question", "great", "cool",
        "please", "all", "star", "stars", "superstar", "mvp", "rookie", "veteran", "draft",
        "deadline", "playoffs", "finals", "team", "teams", "player", "players", "guide", "report",
        "memo", "coach", "scout", "analyst", "league", "season", "game", "games", "pivot",
    ]
)  # fmt: skip
#: Function words that a sentence capitalises only in Title Case ("Who Is The Best
#: Defender?", "Pick And Roll", "Which Players Fit Our Needs?"). Not "A": grades ("an A
#: defender") are capitalised in ordinary prose.
_TITLE_CASE_WORDS = frozenset(
    [
        "is", "are", "was", "were", "the", "of", "our", "your", "their", "my", "and", "or", "but",
        "for", "to", "in", "on", "with", "at", "by", "from", "an", "do", "does", "did", "can",
        "should", "would", "will", "who", "what", "which", "how", "why", "when", "where", "that",
        "this", "these", "those", "it", "we", "you", "they", "me", "us", "them", "about",
    ]
)  # fmt: skip
_CAPITALISED = re.compile(r"(?<![\w'’-])([A-Z][a-z]*)(?![\w'’-])")
_SENTENCE_BREAK = ".!?:;\"“‘'([—–-"
_CAP_WORD = r"[A-Z][a-z]{1,}(?:['’][A-Za-z]+)?"
_CANDIDATE_NAME = re.compile(rf"(?<![\w-])({_CAP_WORD}(?:\s+{_CAP_WORD}){{1,2}})(?![\w-])")
_MONTHS = frozenset(
    [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ]
)


def _known_name_keys(manifest: Manifest) -> frozenset[str]:
    keys = set(_fictional_names(manifest))
    for doc in manifest.documents:
        keys.add(_name_key(doc.author))
    keys.update(_name_key(term) for term in _KNOWN_TERMS)
    return frozenset(keys)


@lru_cache(maxsize=4)
def _known_name_parts(manifest: Manifest) -> frozenset[str]:
    """Every word of a fictional alias, plus every corpus author's surname: a candidate
    containing one is a known person seen through a typo, a title or a shared name ("Carlos
    Cruz" is left to the ambiguity rules; "Coach Grant" is Hollis Grant)."""
    parts = {_normalise_word(w) for p in manifest.players for alias in p.aliases for w in alias.split()}
    parts |= {_normalise_word(w) for t in manifest.teams for alias in t.aliases for w in alias.split()}
    parts |= {_normalise_word(w) for c in manifest.clubs for alias in c.aliases for w in alias.split()}
    parts |= {_name_key(doc.author).split()[-1] for doc in manifest.documents if len(_name_key(doc.author).split()) > 1}
    return frozenset(parts)


@lru_cache(maxsize=4)
def _ordinary_words(manifest: Manifest) -> frozenset[str]:
    """Words the manifest's own descriptions write in lowercase ("rim", "protector", "fit",
    "guide", "method"), plus `_COMMON_WORDS`.

    The test is the case the sandbox itself uses: a word that appears in lowercase somewhere
    in its prose is an ordinary word, while a name part ("Corwin", "Whitfield", "Ospreys")
    is always capitalised. A capitalised phrase containing an ordinary word is a Title-Cased
    term ("Rim Protector", "Method Guide"), not an invented player."""
    texts: list[str] = [manifest.label, manifest.disclaimer]
    for player in manifest.players:
        texts += [player.position, player.archetype, player.summary]
    for team in manifest.teams:
        texts += [team.identity, *team.default_constraints]
    for dimension in manifest.priority_dimensions:
        texts += [dimension.label, dimension.description, *dimension.keywords]
    for stat in manifest.stats.values():
        texts += [stat.label, *stat.keywords]
    for topic in manifest.unavailable_topics:
        texts += [topic.label, topic.reason]
    texts += [gap.reason for gap in manifest.unavailable_facts]
    texts += [assessment.summary for assessment in manifest.assessments]
    for conflict in manifest.conflicts:
        texts += [conflict.summary, conflict.guidance]
    for doc in manifest.documents:
        texts += [doc.title, doc.author_role, doc.doc_type.replace("_", " ")]
    words = {w for text in texts for w in re.findall(r"(?<![A-Za-z])[a-z]+(?![A-Za-z])", text)}
    return frozenset(words | _COMMON_WORDS)


def _is_title_case(text: str) -> bool:
    """True when a function word is capitalised mid-sentence ("Who Is The Best Defender?").
    In Title Case every word looks like a name, so capitals say nothing and nothing is
    flagged."""
    for match in _CAPITALISED.finditer(text):
        if match.group(1).lower() not in _TITLE_CASE_WORDS:
            continue
        before = text[: match.start()].rstrip()
        if before and before[-1] not in _SENTENCE_BREAK:
            return True
    return False


def detect_unknown_names(text: str, manifest: Manifest) -> tuple[str, ...]:
    """Two- or three-word capitalised names ("Corwin Ashby", "Portland Ospreys") that are
    not a fictional player, team, club, analyst or product term.

    A false hit refuses a legitimate question, so this errs toward missing a name. A
    candidate must be *name-shaped*:

    * two or three capitalised words once greetings, request words and titles are stripped
      from its front ("Scout the Portland Ospreys" → "Portland Ospreys"; "Hey Pivot",
      "Coach Grant" → one word, never flagged);
    * none of them an ordinary word — one the manifest writes in lowercase ("Rim
      Protector", "Best Fit", "Method Guide") or a chat word ("Quick Question");
    * none of them a known name part: an alias word ("Carlos Cruz" is left to the ambiguity
      rules) or a corpus author's surname ("Coach Grant" is Hollis Grant);
    * no month ("In January Theo ...");
    * and the text is not in Title Case ("Who Is The Best Defender?"), where every word is
      capitalised and capitals carry no signal.

    Policy refuses on these only when nothing fictional is in play; beside a real player
    or team they become a caveat."""
    if _is_title_case(text):
        return ()
    known = _known_name_keys(manifest)
    name_parts = _known_name_parts(manifest)
    ordinary = _ordinary_words(manifest)
    found: list[str] = []
    for match in _CANDIDATE_NAME.finditer(text):
        words = [_POSSESSIVE.sub("", w) for w in match.group(1).split()]
        while words and words[0].lower() in _LEADING_WORDS:
            words.pop(0)
        if len(words) < 2:
            continue
        name = " ".join(words)
        lowered = [_normalise_word(w) for w in words]
        if _name_key(name) in known or any(w in name_parts for w in lowered):
            continue
        if any(w in _MONTHS or w in ordinary for w in lowered):
            continue
        if name not in found:
            found.append(name)
    return tuple(found)


# ------------------------------------------------------------------------ real NBA names

#: Team nicknames distinctive enough to flag on their own. Matched case-sensitively, so
#: "heat check", "magic", "thunder" and "jazz" in ordinary prose are never flagged (and
#: those nicknames are not listed at all: "the Heat" is too common an English phrase).
DISTINCTIVE_NICKNAMES = (
    "Lakers", "Celtics", "Knicks", "Warriors", "Clippers", "Mavericks", "Nuggets",
    "Timberwolves", "Grizzlies", "Pelicans", "Raptors", "Cavaliers", "76ers", "Sixers",
    "Pistons", "Pacers", "Bucks", "Hornets", "Wizards", "Spurs", "Rockets", "Trail Blazers",
)  # fmt: skip
_NICKNAME_ALIASES = {"Sixers": "76ers"}
_NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})
#: A word, possibly with internal hyphens, periods or apostrophes: "Gilgeous-Alexander",
#: "P.J", "D'Angelo", "James's".
_WORD = re.compile(r"[^\W_]+(?:[-'’.][^\W_]+)*")
_POSSESSIVE = re.compile(r"['’]s$")


def _normalise_word(word: str) -> str:
    """Fold one word for matching: "Dončić" -> "doncic", "P.J" -> "pj", "O'Neal" -> "oneal",
    "James's" -> "james"."""
    decomposed = unicodedata.normalize("NFKD", word)
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()
    folded = _POSSESSIVE.sub("", folded)
    return re.sub(r"['’.]", "", folded)


def _words(text: str) -> list[tuple[str, str]]:
    """`[(original, normalised), ...]` for every word in `text`."""
    return [(m.group(0), _normalise_word(m.group(0))) for m in _WORD.finditer(text)]


def _name_key(name: str) -> str:
    return " ".join(norm for _, norm in _words(name))


@dataclass(frozen=True)
class _RealNameIndex:
    full_names: dict[str, str]  # normalised "lebron james" -> display "LeBron James"
    nicknames: dict[str, str]  # case-sensitive "Lakers" -> "Los Angeles Lakers"
    max_words: int


@lru_cache(maxsize=1)
def _real_name_index() -> _RealNameIndex | None:
    """Build the lookup once from nba_api's bundled JSON. None when nba_api is unavailable."""
    try:
        from nba_api.stats.static import players, teams

        player_rows = players.get_players()
        team_rows = teams.get_teams()
    except Exception:  # noqa: BLE001 - optional data; absence means "cannot check", not an error
        return None
    full_names: dict[str, str] = {}
    for row in player_rows:
        display = str(row.get("full_name") or "").strip()
        key = _name_key(display)
        if len(key.split()) < 2:
            continue  # a one-word name ("Nene") is too likely to be an ordinary word
        full_names.setdefault(key, display)
        words = key.split()
        if words[-1] in _NAME_SUFFIXES and len(words) > 2:
            full_names.setdefault(" ".join(words[:-1]), display)  # "Kelly Oubre" for "... Jr."
    by_nickname: dict[str, str] = {}
    for row in team_rows:
        display = str(row.get("full_name") or "").strip()
        if display:
            full_names.setdefault(_name_key(display), display)
            by_nickname[str(row.get("nickname") or "")] = display
    nicknames = {}
    for nickname in DISTINCTIVE_NICKNAMES:
        team_name = by_nickname.get(_NICKNAME_ALIASES.get(nickname, nickname))
        if team_name:
            nicknames[nickname] = team_name
    max_words = max((len(k.split()) for k in full_names), default=2)
    return _RealNameIndex(full_names=full_names, nicknames=nicknames, max_words=max_words)


@lru_cache(maxsize=4)
def _fictional_names(manifest: Manifest) -> frozenset[str]:
    names: set[str] = set()
    for player in manifest.players:
        names.update([player.name, *player.aliases])
    for team in manifest.teams:
        names.update([team.name, team.nickname, *team.aliases])
    for club in manifest.clubs:
        names.update([club.name, *club.aliases])
    return frozenset(_name_key(n) for n in names)


def _default_exclusions() -> frozenset[str]:
    try:
        from app.assistant_rag.manifest import load_manifest

        return _fictional_names(load_manifest())
    except Exception:  # noqa: BLE001 - a broken manifest is reported by its own loader
        return frozenset()


def detect_real_nba_names(text: str, *, exclude: Iterable[str] | None = None) -> tuple[str, ...]:
    """Real NBA player/team names in `text`, canonical spelling, in text order, de-duplicated.

    * players: full names only ("LeBron James", "Luka Doncic" == "Luka Dončić"),
      case-insensitive, whole words; a bare surname is never flagged;
    * teams: full names case-insensitively ("los angeles lakers"), plus the distinctive
      nicknames in `DISTINCTIVE_NICKNAMES` case-sensitively ("Lakers", not "lakers");
    * never a fictional manifest name: `exclude` (normalised by the same rules) defaults to
      every name and alias in the packaged manifest.

    Returns `()` when nba_api's static data cannot be imported. Fast: one pass over the
    words, trying the longest n-gram first at each position against a dict.
    """
    index = _real_name_index()
    if index is None or not text:
        return ()
    excluded = _default_exclusions() if exclude is None else frozenset(_name_key(n) for n in exclude)
    words = _words(text)
    found: list[str] = []
    i = 0
    while i < len(words):
        matched = 0
        for size in range(min(index.max_words, len(words) - i), 1, -1):
            key = " ".join(norm for _, norm in words[i : i + size])
            if key in index.full_names and key not in excluded:
                found.append(index.full_names[key])
                matched = size
                break
        if not matched:
            original = _POSSESSIVE.sub("", words[i][0])
            pair = f"{words[i][0]} {_POSSESSIVE.sub('', words[i + 1][0])}" if i + 1 < len(words) else ""
            for candidate, size in ((pair, 2), (original, 1)):
                if candidate in index.nicknames and _name_key(candidate) not in excluded:
                    found.append(index.nicknames[candidate])
                    matched = size
                    break
        i += matched or 1
    return tuple(dict.fromkeys(found))
