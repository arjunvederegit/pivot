"""Hybrid retrieval — the "R" in RAG — with every decision kept inspectable.

What a retriever is. In LangChain terms a retriever is anything that maps a query string to
a ranked list of `Document`s. The language model never sees the corpus; it sees only what the
retriever hands it. So retrieval quality caps answer quality, and *why* a passage was chosen
is as important to show a GM as the passage itself. This module returns both.

How a question becomes a ranked list of chunks:

1. **Query expansion** (`build_query_text`). A follow-up like "would you change your
   ranking?" contains nothing searchable. The entities already resolved for it (players
   carried from history, the selected team in roster-fit mode) and the GM's top priorities
   are appended, visibly — the expanded text is returned as `query_text` and shown in the
   debug view, so nothing about the search is hidden.
2. **Three rankings** over section chunks:
     * *dense*: cosine similarity between the query's embedding and each chunk's, via
       LangChain's `InMemoryVectorStore` (good at paraphrase);
     * *lexical*: BM25 over the same chunk text (`lexical.py`; good at exact names, numbers
       and rare terms);
     * *entity*: chunks whose document is tagged with, or whose text names, a resolved
       player (a team: tagged only) — the corpus metadata knows which reports are about
       Niko Reed, so the retriever does not have to rediscover that from wording. At most
       two chunks per document enter it, so a long stat sheet cannot take every slot from
       the player's own scouting report.
3. **Reciprocal rank fusion** (`reciprocal_rank_fusion`) merges the rankings using ranks
   only: `score(d) = Σ 1/(rrf_k + rank_r(d))`. Cosine similarities (~0.3-0.8) and BM25 scores
   (0-20+) are on incomparable scales; RRF sidesteps calibrating them against each other and
   rewards chunks that several retrievers agree on. `rrf_k = 60` is the value from the
   original paper and damps the advantage of a single #1 rank.
   In roster-fit mode a fourth, *priority* ranking lists player reports whose manifest
   topics cover the GM's top priorities — so "who fits our needs?" reaches the scouting
   reports, not just the team's own memos.
4. **Deterministic filters** on the fused list:
     * *pinning*: in roster-fit mode the selected team's strategy memo is always in the
       context (its lead paragraph, plus its best-scoring section), because "fit" means
       nothing without the team's stated needs. Pinned chunks use slots inside `k`;
     * *team-context quota*: in roster-fit mode at most `max_team_context` chunks (pins
       included) come from the team's own memos, so the rest of `k` is candidate evidence;
     * *untrusted quota*: at most `max_untrusted` chunks from third-party documents, which
       never enter through the metadata rankings (entity, selected team, priority) — only
       when dense or BM25 relevance finds them;
     * *diversification*: at most `max_chunks_per_source` chunks from one document, so one
       long report cannot crowd out the second opinion that contradicts it.
5. **A low-evidence flag** for the pipeline: when nothing vouches for the corpus covering
   the question, the honest answer is "the corpus does not cover that", and the pipeline
   says so without asking a model to improvise. The flag is computed on the *raw*
   question (the expansion would make anything look on-topic), and a team or a player
   carried from earlier turns only vouches when the question has basketball cues or a
   pronoun — so "best pizza in Hartford?" abstains while "what does Hartford need?" and
   "and his shooting?" do not. BM25 vouches only for a passage that shares two
   distinctive words with the question, or one basketball word (`lexical_gate_score`):
   one rare everyday word ("What should I see in Omaha?") is not coverage.

Variants (`QueryContext.retrieval_variant`, for evaluation): "hybrid" (all three rankings),
"dense" and "lexical" (one each). Pinning and diversification apply to every variant so the
comparison isolates the ranking. Without an embedding model, "hybrid" degrades to lexical +
entity and every chunk's reasons say so; "dense" is refused rather than silently relabelled.

Why not LangChain's `EnsembleRetriever`: it fuses with RRF too, but returns bare Documents.
The evidence inspector needs each chunk's dense rank, BM25 rank, matched entities and
reasons, so fusion is done here, in a few readable lines, with LangChain providing the
embeddings and the vector store. Everything except those two is pure Python and is tested
without LangChain installed.
"""

from __future__ import annotations

import asyncio
import re
import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from time import perf_counter
from typing import TYPE_CHECKING, Any

from app.assistant_rag.claims import find_mentions
from app.assistant_rag.corpus import Chunk, Corpus
from app.assistant_rag.entities import named_entity_ids
from app.assistant_rag.errors import ProviderUnavailableError
from app.assistant_rag.lexical import BM25Index, tokenize
from app.assistant_rag.manifest import Manifest
from app.assistant_rag.types import (
    EntityRef,
    EntityResolution,
    QueryContext,
    RetrievalResult,
    RetrievedChunk,
)
from app.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from langchain_core.embeddings import Embeddings

logger = get_logger(__name__)

VARIANTS = ("hybrid", "dense", "lexical")
#: Priority levels strong enough to be part of the search text in roster-fit mode.
EXPANSION_LEVELS = ("must_preserve", "high")
#: How many of a team's `strategy_source_ids` (in manifest order) are eligible for pinning.
PINNED_STRATEGY_DOCS = 2
TIMING_KEYS = ("embed_query", "dense", "lexical", "fuse")

PIN_REASON = "selected team's strategy memo"
PRIORITY_REASON_PREFIX = "covers your priorities: "
UNTRUSTED_REASON = "untrusted third-party document"
NO_DENSE_REASON = "no embedding model: hybrid used keyword + entity search only"


@dataclass(frozen=True)
class RetrievalConfig:
    """Retrieval knobs. Defaults are what the app runs with; evaluation varies them.

    `min_dense_score` / `min_lexical_score` are the low-evidence thresholds, measured on
    corpus v2 (162 chunks) with nomic-embed-text + task prefixes, research mode, no history:

                                         top cosine       top BM25
        in-scope   (20; 18 name nobody)  0.544 - 0.875    3.8 - 15.0
        off-topic  (13: taxes, weather,  0.475 - 0.594    0.0 - 5.0
                   photosynthesis, ...)

    Every in-scope question clears at least one bar (cosine >= 0.62 or BM25 >= 6.4), and no
    off-topic one clears either. The flag needs *both* signals low, so a question survives
    on either ("Is the agent memo trustworthy?": cosine 0.54 but BM25 9.9). The corpus is
    all basketball prose, so even nonsense scores ~0.5 cosine: the contract's placeholder
    0.45 would never fire. The cosine ranges nearly touch (0.594 vs 0.620); 0.60 errs
    toward calling the model, because a false "no evidence" refuses an answerable question
    while a false "evidence" still faces the grounded prompt and the citation checks.
    Not flagged, by design of the rule: "how do I fix my jump shot?" (basketball-adjacent:
    cosine 0.67, BM25 9.4). "Best pizza in Hartford?" is flagged: team names are removed
    before scoring (`gate_text`) and a team vouches only with a basketball cue.

    That calibration set turned out too small for BM25: one word the corpus rarely uses but
    English uses constantly ("see", "near", "history") scores 6-8 on its own, so "What
    should I see in Omaha?" cleared the bar. The BM25 the gate uses is therefore
    `lexical_gate_score` — the best passage that shares two content words, or one
    basketball word, with the question. The held-out sweep (`eval_rag retrieval`) reports
    how often the gate fires on in-scope and out-of-scope questions.
    Re-measure if the corpus, the chunking or the embedding model changes.
    """

    k: int = 6
    #: Roster-fit answers compare several candidates against a team's needs, so they get a
    #: little more context: pins (2) + team context (1) + candidate evidence (5).
    roster_fit_k: int = 8
    max_team_context: int = 3
    max_untrusted: int = 2
    #: Roster fit compares candidates, so breadth beats depth: one chunk per document and
    #: one method-guide chunk, leaving the remaining slots to different players' reports.
    roster_fit_max_per_source: int = 1
    roster_fit_max_method: int = 1
    #: Candidates per search in roster fit: the diversity caps discard many, so look wider.
    roster_fit_search_k: int = 24
    dense_k: int = 12
    lexical_k: int = 12
    entity_k: int = 12
    rrf_k: int = 60
    max_chunks_per_source: int = 2
    max_pinned: int = 2
    min_dense_score: float = 0.60
    min_lexical_score: float = 6.0
    default_variant: str = "hybrid"

    def __post_init__(self) -> None:
        for name in ("k", "roster_fit_k", "dense_k", "lexical_k", "max_chunks_per_source"):
            if getattr(self, name) < 1:
                raise ValueError(f"RetrievalConfig.{name} must be at least 1")
        if self.entity_k < 0 or self.max_pinned < 0 or self.rrf_k < 0:
            raise ValueError("RetrievalConfig.entity_k, max_pinned and rrf_k must be >= 0")
        if self.default_variant not in VARIANTS:
            raise ValueError(f"unknown retrieval variant {self.default_variant!r}")


# ------------------------------------------------------------------------ pure functions


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = 60) -> list[tuple[str, float]]:
    """Fuse ranked id lists: `score(d) = Σ_r 1 / (k + rank_r(d))`, ranks 1-based.

    An id missing from a ranking contributes nothing for it; an id repeated within one
    ranking counts at its first position only. Best first; ties break by the best single
    rank the id achieved, then by id, so the output is deterministic.
    """
    if k < 0:
        raise ValueError("rrf k must be >= 0")
    scores: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for item in ranking:
            if item in seen:
                continue
            seen.add(item)
            rank = len(seen)
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            best_rank[item] = min(best_rank.get(item, rank), rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], best_rank[kv[0]], kv[0]))


def _retrieval_entities(ctx: QueryContext, entities: EntityResolution) -> tuple[EntityRef, ...]:
    """The entities that steer retrieval. The selected team counts only in roster-fit mode:
    in research mode it is UI context, and letting it into the search would pull every
    question toward that team's documents and make an off-topic question look on-topic."""
    return tuple(e for e in entities.resolved if e.how != "selected_team" or ctx.mode == "roster_fit")


def _effective_priorities(ctx: QueryContext, manifest: Manifest) -> dict[str, str]:
    """The GM's priorities, or — in roster fit with none set — the selected team's documented
    defaults (the same rule as `policy.effective_priorities`, which this module cannot
    import without inverting the dependency direction)."""
    if ctx.priorities or ctx.mode != "roster_fit" or not ctx.team_id:
        return dict(ctx.priorities)
    team = manifest.team(ctx.team_id)
    return dict(team.default_priorities) if team else {}


def build_query_text(ctx: QueryContext, entities: EntityResolution, manifest: Manifest) -> str:
    """The text that is actually searched: the question plus a transparent expansion.

    Appended (each once, and only if the question does not already contain it): full names
    of resolved players ("Reed" -> "Niko Reed"; a carried player for "what about him?"), the
    teams/clubs the question names and — in roster-fit mode — the labels of the priorities
    set to must_preserve or high. Short follow-ups carry no searchable content of their own;
    without this, "would you change your ranking?" would retrieve whatever happens to
    mention "ranking".

    The *selected* team's name is deliberately not appended: its strategy memos are pinned
    anyway, and its name in the search text pulled every slot toward its own documents,
    leaving no room for the scouting reports on the players who might fit.
    """
    question = ctx.question.strip()
    lowered = question.lower()
    additions: list[str] = []

    def add(text: str) -> None:
        if text and text.lower() not in lowered and text not in additions:
            additions.append(text)

    refs = _retrieval_entities(ctx, entities)
    for ref in refs:
        if ref.kind == "player":
            add(ref.name)
    for ref in refs:
        if ref.kind in ("team", "club") and ref.how != "selected_team":
            add(ref.name)
    if ctx.mode == "roster_fit":
        priorities = _effective_priorities(ctx, manifest)
        for level in EXPANSION_LEVELS:
            for dimension in manifest.priority_dimensions:
                if priorities.get(dimension.key) == level:
                    add(dimension.label)
    return f"{question}\n{'; '.join(additions)}" if additions else question


def select_chunks(
    ranked_ids: Sequence[str],
    pinned_ids: Sequence[str],
    source_of: Mapping[str, str],
    *,
    k: int,
    max_per_source: int,
    group_of: Mapping[str, str] | None = None,
    group_limits: Mapping[str, int] | None = None,
) -> list[str]:
    """Choose up to `k` chunk ids: pinned ones first, then the fused ranking, never more
    than `max_per_source` from one source and never more than `group_limits[g]` from a
    group (e.g. "team_context", "untrusted"; pins count toward their group). Returned in
    final order — fused-rank order, with any pinned chunk the rankings never surfaced placed
    last — so rank stays monotonic in fused score and pinning only guarantees *inclusion*,
    not position."""
    chosen: list[str] = []
    per_source: Counter[str] = Counter()
    per_group: Counter[str] = Counter()
    groups = group_of or {}
    limits = group_limits or {}
    pinned = set(pinned_ids)
    for chunk_id in [*pinned_ids, *ranked_ids]:
        if len(chosen) >= k:
            break
        source = source_of[chunk_id]
        # Pins are deliberate (a memo's lead *and* its best section), so the per-source cap
        # does not apply to them; it still counts them against later picks.
        if chunk_id in chosen or (chunk_id not in pinned and per_source[source] >= max_per_source):
            continue
        group = groups.get(chunk_id)
        if group is not None and group in limits and per_group[group] >= limits[group]:
            continue
        chosen.append(chunk_id)
        per_source[source] += 1
        if group is not None:
            per_group[group] += 1
    position = {chunk_id: i for i, chunk_id in enumerate(ranked_ids)}
    order = {chunk_id: i for i, chunk_id in enumerate(chosen)}
    return sorted(chosen, key=lambda cid: (position.get(cid, len(position)), order[cid]))


def is_low_evidence(
    *,
    has_entities: bool,
    top_dense: float | None,
    top_lexical: float | None,
    config: RetrievalConfig,
) -> bool:
    """True when nothing vouches for the corpus covering the question: no fictional entity,
    and every retriever that ran found only weak matches. A retriever that did not run
    (dense without embeddings, BM25 in the dense-only variant) cannot vouch for anything."""
    if has_entities:
        return False
    dense_ok = top_dense is not None and top_dense >= config.min_dense_score
    lexical_ok = top_lexical is not None and top_lexical >= config.min_lexical_score
    return not dense_ok and not lexical_ok


#: Words that make a question about basketball analysis. A team or a carried player only
#: vouches for coverage when one of these (or a pronoun) is present: "best pizza in
#: Hartford?" names the Comets' city but asks nothing the corpus could answer. `defen\w*`
#: covers defense/defensive/defend/defender/defending; "passer", "scorer" and "handle" are
#: listed exactly, because "pass" and "score" alone are everyday words ("the score of
#: tonight's game"). The skill words here and `policy._RESEARCH_CUES` are kept in step by
#: a test.
_DOMAIN_CUE = re.compile(
    r"\b(?:defen\w*|shoot\w*|shot|spacing|spacer|three|threes|3p\w*|3-point|rebound\w*|glass|"
    r"pass(?:er|ers|ing)|handl\w*|scor(?:er|ers|ing)|durab\w*|"
    r"rim|block\w*|assist\w*|playmak\w*|creat\w*|creator|guard\w*|wing\w*|forward|center|big|"
    r"lineup\w*|rotation\w*|roster|trade\w*|target\w*|fit\w*|need\w*|priorit\w*|scout\w*|"
    r"report\w*|film|playoff\w*|switch\w*|pick-and-roll|coverage|contain\w*|upside|prospect\w*|"
    r"develop\w*|young|veteran|compare\w*|comparison|recommend\w*|acquir\w*|pursue|stat\w*|"
    r"percentage|average\w*|minutes|points|steal\w*|injur\w*|availab\w*|size|length|offen[cs]\w*|"
    r"transition|perimeter|paint|closing|closer|starter|bench|strengths?|weakness\w*|risk\w*|"
    r"regret|evidence|sources?|reports?|analyst|grade\w*|plan|strategy|memo|ranking|rank|option\w*|"
    r"candidate\w*|player\w*|team|front\s+office|coach\w*)\b",
    re.IGNORECASE,
)
_ANAPHORA = re.compile(r"\b(?:he|him|his|they|them|their|that|those|these|this|it|its|either|both|same)\b", re.IGNORECASE)


def gate_text(question: str, manifest: Manifest) -> str:
    """The question as the low-evidence gate scores it: as asked, minus fictional team and
    club names. A team name is not evidence of coverage by itself — every Comets memo says
    "Hartford" — so a team counts only through `vouching_entities` (with a basketball cue),
    and the rest of the question has to match the corpus on its own."""
    spans = [m for m in find_mentions(question, manifest) if m.kind in ("team", "club")]
    text = question
    for mention in sorted(spans, key=lambda m: m.start, reverse=True):
        text = text[: mention.start] + text[mention.end :]
    return re.sub(r"\s+", " ", text).strip() or question


def vouching_entities(ctx: QueryContext, entities: EntityResolution) -> tuple[EntityRef, ...]:
    """The resolved entities that count as evidence the corpus covers the question.

    A player named in the question always counts. A team or club named in it, and the
    selected team in roster-fit mode, count only with a basketball cue. A player carried
    from earlier turns counts only with a cue or a pronoun ("and his shooting?").
    """
    cue = bool(_DOMAIN_CUE.search(ctx.question))
    pronoun = bool(_ANAPHORA.search(ctx.question))
    vouching: list[EntityRef] = []
    for ref in _retrieval_entities(ctx, entities):
        if ref.how == "named" and ref.kind == "player":
            vouching.append(ref)
        elif ref.how == "carried_from_history":
            if cue or pronoun:
                vouching.append(ref)
        elif cue:
            vouching.append(ref)
    return tuple(vouching)


#: Evaluative and filler words that turn up in questions on any subject ("best pizza",
#: "good hikes", "show me", "what should I see"). Sharing one with a passage says nothing
#: about whether the corpus covers the question, so the gate does not count them.
_GENERIC_GATE_WORDS = (
    "best", "better", "good", "great", "top", "worst", "bad", "really", "think", "like", "want",
    "get", "know", "tell", "give", "show", "see", "look", "find", "make", "way", "lot", "thing",
    "something", "anything", "everything",
)  # fmt: skip
_GENERIC_GATE_TERMS = frozenset(term for word in _GENERIC_GATE_WORDS for term in tokenize(word))


#: Basketball-specific words: one of them shared with a passage is coverage on its own.
#: Narrower than `_DOMAIN_CUE`, whose organisational words ("player", "team", "coach",
#: "plan") suit a question that already names a team, but not one that names nothing
#: ("Who is the best player in soccer history?").
_SKILL_CUE = re.compile(
    r"\b(?:defen\w*|shoot\w*|shot|spacing|spacer|three|threes|3p\w*|3-point|rebound\w*|glass|"
    r"pass(?:er|ers|ing)|handl\w*|scor(?:er|ers|ing)|rim|block\w*|assist\w*|playmak\w*|"
    r"creator\w*|creation|guard\w*|wing\w*|forward|center|lineup\w*|rotation\w*|roster|"
    r"switch\w*|pick-and-roll|coverage|perimeter|paint|transition|offen[cs]\w*|playoff\w*|"
    r"steal\w*|prospect\w*|closing|closer|starter|bench|scout\w*|film)\b",
    re.IGNORECASE,
)
#: A term in at least this share of all passages is this corpus's background vocabulary
#: ("player", "team", "game", "defense"): sharing it says nothing about coverage.
GATE_BACKGROUND_SHARE = 0.2


def background_terms(passage_terms: Mapping[str, frozenset[str]], share: float = GATE_BACKGROUND_SHARE) -> frozenset[str]:
    """Terms that appear in at least `share` of the passages."""
    counts: Counter[str] = Counter(term for terms in passage_terms.values() for term in terms)
    cutoff = share * len(passage_terms)
    return frozenset(term for term, n in counts.items() if n >= cutoff)


def lexical_gate_score(
    question: str,
    hits: Sequence[tuple[str, float]],
    passage_terms: Mapping[str, frozenset[str]],
    background: frozenset[str] = frozenset(),
) -> float | None:
    """The BM25 score the low-evidence gate may trust: that of the best passage sharing at
    least two distinctive words with the question, or one basketball word (`_SKILL_CUE`).

    Why not just the top BM25 score. BM25 is dominated by IDF, and a word this corpus
    rarely uses is often an everyday one: "What should I see in Omaha?" matches only "see",
    yet one short passage containing it scores 6.8 — over the bar. One shared rare word is
    a coincidence; two shared words, or one basketball word, is coverage. Neither generic
    filler ("best", "see") nor the corpus's `background` vocabulary ("player", "team")
    counts as a distinctive word: "best player in soccer history" shares only "history"
    that way. `hits` must be best first (as `BM25Index.search` returns them); None when no
    passage qualifies."""
    terms = set(tokenize(question)) - _GENERIC_GATE_TERMS
    skill = {term for match in _SKILL_CUE.finditer(question) for term in tokenize(match.group(0))}
    for chunk_id, score in hits:
        shared = terms & passage_terms.get(chunk_id, frozenset())
        if len(shared - background) >= 2 or shared & skill:
            return score
    return None


def _dimension_label(manifest: Manifest, key: str) -> str:
    dimension = manifest.dimension(key)
    return dimension.label if dimension is not None else key


def _day(value: str) -> int:
    try:
        return date.fromisoformat(value).toordinal()
    except ValueError:
        return 0


def _ms(started: float) -> float:
    return round((perf_counter() - started) * 1000.0, 3)


def _model_name(embeddings: Any) -> str | None:
    if embeddings is None:
        return None
    name = getattr(embeddings, "model", None)
    return str(name) if name else type(embeddings).__name__


def _vector_store(chunks: Sequence[Chunk], embeddings: Embeddings) -> Any:
    """Embed every chunk (one batched `embed_documents` call) into LangChain's
    `InMemoryVectorStore`, keyed by chunk id so vector hits, diagnostics and citations agree.
    It is a dict of vectors searched by brute-force cosine — exactly right for ~170 chunks."""
    from langchain_core.vectorstores import InMemoryVectorStore

    from app.assistant_rag.corpus import to_langchain_documents

    store = InMemoryVectorStore(embedding=embeddings)
    if chunks:
        store.add_documents(to_langchain_documents(list(chunks)), ids=[c.chunk_id for c in chunks])
    if len(store.store) != len(chunks):
        # `add_documents` zips texts with vectors and would silently drop a short response.
        raise RuntimeError(f"embedding returned vectors for {len(store.store)} of {len(chunks)} chunks")
    return store


@dataclass(frozen=True)
class _Plan:
    variant: str
    k: int
    query_text: str
    use_dense: bool
    use_lexical: bool
    degraded: bool  # hybrid requested, no embeddings
    search_k: int = 12


# ---------------------------------------------------------------------------- retriever


class HybridRetriever:
    """Dense + BM25 + entity retrieval over a `Corpus`, with per-chunk diagnostics.

    `build()` indexes once (thread-safe, idempotent); `retrieve()`/`aretrieve()` build on
    first use if needed. Pass `embeddings=None` for a keyword + entity retriever that needs
    no model at all (tests, CI, an Ollama-less demo).
    """

    def __init__(
        self,
        corpus: Corpus,
        embeddings: Embeddings | None = None,
        config: RetrievalConfig | None = None,
        *,
        vector_store_factory: Callable[[Sequence[Chunk], Embeddings], Any] | None = None,
    ) -> None:
        self.corpus = corpus
        self.config = config or RetrievalConfig()
        self.embedding_model: str | None = _model_name(embeddings)
        self.build_ms: float | None = None
        self._embeddings = embeddings
        #: Builds the dense index; anything with LangChain's
        #: `similarity_search_with_score_by_vector(vector, k) -> [(doc_with_id, score)]`.
        #: Default: `InMemoryVectorStore`. Swapping in a persistent store (Chroma, pgvector)
        #: later changes this one argument, not the fusion logic.
        self._vector_store_factory = vector_store_factory or _vector_store
        self._lock = threading.Lock()
        self._built = False
        self._lexical: BM25Index | None = None
        self._store: Any = None
        self._mentions: dict[str, frozenset[str]] = {}
        self._terms: dict[str, frozenset[str]] = {}  # chunk id -> its BM25 terms (gate)
        self._background: frozenset[str] = frozenset()  # terms in >= 20% of passages (gate)
        self._source_of = {c.chunk_id: c.source_id for c in corpus.chunks}
        self._by_id = {c.chunk_id: c for c in corpus.chunks}
        self._by_source: dict[str, list[Chunk]] = {}
        for chunk in corpus.chunks:
            self._by_source.setdefault(chunk.source_id, []).append(chunk)

    @property
    def built(self) -> bool:
        return self._built

    @property
    def chunk_count(self) -> int:
        """Chunks in the built index (0 until `build()` succeeds)."""
        return len(self.corpus.chunks) if self._built else 0

    @property
    def has_dense(self) -> bool:
        return self._embeddings is not None

    # ------------------------------------------------------------------------- build

    def build(self) -> None:
        """Index every chunk: BM25 always; embeddings into an `InMemoryVectorStore` when an
        embedding model was given. Raises whatever the embedding call raises (the service
        maps provider errors); a failed build leaves the retriever unbuilt, so it can retry."""
        if self._built:
            return
        with self._lock:
            if self._built:
                return
            started = perf_counter()
            chunks = self.corpus.chunks
            manifest = self.corpus.manifest
            lexical = BM25Index([(c.chunk_id, c.embedding_text) for c in chunks])
            mentions = {c.chunk_id: named_entity_ids(c.embedding_text, manifest) for c in chunks}
            terms = {c.chunk_id: frozenset(tokenize(c.embedding_text)) for c in chunks}
            store = None if self._embeddings is None else self._vector_store_factory(chunks, self._embeddings)
            self._lexical, self._mentions, self._terms, self._store = lexical, mentions, terms, store
            self._background = background_terms(terms)
            self.build_ms = _ms(started)
            self._built = True
        logger.info(
            "assistant retrieval index built: %d chunks, dense=%s, %.0f ms",
            len(chunks),
            self.embedding_model or "off",
            self.build_ms,
        )

    # ---------------------------------------------------------------------- retrieve

    def _plan(self, ctx: QueryContext, entities: EntityResolution) -> _Plan:
        variant = ctx.retrieval_variant or self.config.default_variant
        if variant not in VARIANTS:
            raise ValueError(f"unknown retrieval variant {variant!r}")
        if variant == "dense" and self._embeddings is None:
            raise ProviderUnavailableError(
                "dense retrieval needs the embedding model, and this retriever has none"
            )
        default_k = self.config.roster_fit_k if ctx.mode == "roster_fit" and ctx.team_id else self.config.k
        k = ctx.k if ctx.k and ctx.k > 0 else default_k
        return _Plan(
            variant=variant,
            k=k,
            query_text=build_query_text(ctx, entities, self.corpus.manifest),
            use_dense=variant in ("dense", "hybrid") and self._embeddings is not None,
            use_lexical=variant in ("lexical", "hybrid"),
            degraded=variant == "hybrid" and self._embeddings is None,
            search_k=self.config.roster_fit_search_k
            if ctx.mode == "roster_fit" and ctx.team_id
            else max(self.config.dense_k, self.config.lexical_k),
        )

    def _dense_search(self, vector: list[float], k: int | None = None) -> list[tuple[str, float]]:
        hits = self._store.similarity_search_with_score_by_vector(vector, k=k or self.config.dense_k)
        return [(str(doc.id), float(score)) for doc, score in hits]

    def _lexical_search(self, plan: _Plan, timings: dict[str, float]) -> list[tuple[str, float]] | None:
        if not plan.use_lexical or self._lexical is None:
            return None
        started = perf_counter()
        hits = self._lexical.search(plan.query_text, max(self.config.lexical_k, plan.search_k) if plan.search_k > self.config.lexical_k else self.config.lexical_k)
        timings["lexical"] = _ms(started)
        return hits

    def retrieve(self, ctx: QueryContext, entities: EntityResolution) -> RetrievalResult:
        self.build()
        plan = self._plan(ctx, entities)
        timings = dict.fromkeys(TIMING_KEYS, 0.0)
        dense_hits: list[tuple[str, float]] | None = None
        if plan.use_dense:
            assert self._embeddings is not None
            started = perf_counter()
            vector = self._embeddings.embed_query(plan.query_text)
            timings["embed_query"] = _ms(started)
            started = perf_counter()
            dense_hits = self._dense_search(vector, k=plan.search_k if plan.search_k > self.config.dense_k else None)
            timings["dense"] = _ms(started)
        lexical_hits = self._lexical_search(plan, timings)
        gate_dense: float | None = dense_hits[0][1] if dense_hits else None
        gate_query = gate_text(ctx.question, self.corpus.manifest)
        if plan.use_dense and plan.query_text != gate_query:
            assert self._embeddings is not None
            raw = self._dense_search(self._embeddings.embed_query(gate_query), k=1)
            gate_dense = raw[0][1] if raw else None
        return self._assemble(ctx, entities, plan, dense_hits, lexical_hits, timings, gate_dense)

    async def aretrieve(self, ctx: QueryContext, entities: EntityResolution) -> RetrievalResult:
        """`retrieve` for the async request path: the query is embedded with `aembed_query`
        (Ollama's async client), so waiting on the embedding never blocks the event loop."""
        if not self._built:
            await asyncio.to_thread(self.build)
        plan = self._plan(ctx, entities)
        timings = dict.fromkeys(TIMING_KEYS, 0.0)
        dense_hits: list[tuple[str, float]] | None = None
        if plan.use_dense:
            assert self._embeddings is not None
            started = perf_counter()
            vector = await self._embeddings.aembed_query(plan.query_text)
            timings["embed_query"] = _ms(started)
            started = perf_counter()
            search = getattr(self._store, "asimilarity_search_with_score_by_vector", None)
            dense_k = max(plan.search_k, self.config.dense_k) if plan.search_k > self.config.dense_k else self.config.dense_k
            if search is not None:
                hits = await search(vector, k=dense_k)
                dense_hits = [(str(doc.id), float(score)) for doc, score in hits]
            else:
                dense_hits = await asyncio.to_thread(self._dense_search, vector, dense_k)
            timings["dense"] = _ms(started)
        lexical_hits = self._lexical_search(plan, timings)
        gate_dense: float | None = dense_hits[0][1] if dense_hits else None
        gate_query = gate_text(ctx.question, self.corpus.manifest)
        if plan.use_dense and plan.query_text != gate_query:
            # The low-evidence gate judges the question as asked, not the expansion (which
            # would make "best pizza in Hartford?" look like a Comets question).
            assert self._embeddings is not None
            raw_vector = await self._embeddings.aembed_query(gate_query)
            raw = await asyncio.to_thread(self._dense_search, raw_vector, 1)
            gate_dense = raw[0][1] if raw else None
        return self._assemble(ctx, entities, plan, dense_hits, lexical_hits, timings, gate_dense)

    # ---------------------------------------------------------------------- assemble

    def _entity_ranking(self, ctx: QueryContext, entities: EntityResolution) -> list[str]:
        """Chunks about the resolved entities, most specific first, one document at a time.

        What counts as "about". A chunk is about a *player* when its document is tagged with
        them (`entity_ids`) or its own text names them; it is about a *team or club* only
        when its document is tagged with it (`team_ids`) — a stat sheet whose club column
        says "Hartford Comets" is not about the Comets. A team the question names that is
        also the selected team ("We're the Hartford Comets ...") is treated as the selected
        team. Third-party (untrusted) documents never enter: metadata says what a document
        is about, not whether to believe it, and an entity boost put a fan letter above the
        internal scouting report on the same player. They remain retrievable through dense
        and BM25 relevance, capped by `max_untrusted`.

        Order: chunks about the question's entities before chunks only about the selected
        team; then chunks whose own text names more of them (the Reed section of a
        four-player stat sheet beats its generic lead); then documents tagged with more of
        them; then the newest report; then document order.

        Coverage: the first `entity_k` chunks by that order, then the best chunk of every
        document *tagged* with one of the players that missed the cut, ranked after them. A
        few newer multi-section documents (stat sheets naming four players per section)
        used to fill all twelve slots, so a player's own older scouting report got no entity
        credit at all. Appending gives every such report credit without taking any from the
        top twelve; capping chunks per document or taking documents in turn instead lifted
        weak chunks above the ones dense search found relevant, and the held-out sweep lost
        recall (0.93 -> 0.88 at k=6 with nomic embeddings).
        """
        refs = _retrieval_entities(ctx, entities)
        selected_ids = {e.id for e in refs if e.how == "selected_team"}
        if ctx.mode == "roster_fit" and ctx.team_id:
            selected_ids |= {e.id for e in refs if e.kind == "team" and e.id == ctx.team_id}
        player_ids = {e.id for e in refs if e.kind == "player"}
        team_ids = {e.id for e in refs if e.kind in ("team", "club") and e.id not in selected_ids}
        if not (player_ids or team_ids or selected_ids) or self.config.entity_k == 0:
            return []
        rows: list[tuple[tuple[int, int, int, int, str, int], str]] = []
        for chunk in self.corpus.chunks:
            if chunk.meta.trust == "untrusted":
                continue
            in_text = self._mentions.get(chunk.chunk_id, frozenset())
            tags = set(chunk.meta.team_ids)
            matched, tier = (player_ids & (set(chunk.meta.entity_ids) | in_text)) | (team_ids & tags), 0
            if not matched:
                matched, tier = selected_ids & tags, 1
            if not matched:
                continue
            key = (
                tier,
                -len(matched & in_text),
                -len(matched),
                -_day(chunk.meta.report_date),
                chunk.source_id,
                chunk.ordinal,
            )
            rows.append((key, chunk.chunk_id))
        rows.sort()
        ranking = [chunk_id for _, chunk_id in rows[: self.config.entity_k]]
        ranked_sources = {self._source_of[chunk_id] for chunk_id in ranking}
        for _, chunk_id in rows[self.config.entity_k :]:
            chunk = self._by_id[chunk_id]
            if chunk.source_id not in ranked_sources and player_ids & set(chunk.meta.entity_ids):
                ranking.append(chunk_id)  # rows are sorted, so this is the document's best chunk
                ranked_sources.add(chunk.source_id)
        return ranking

    def _team_players(self, team_id: str | None) -> frozenset[str]:
        if not team_id:
            return frozenset()
        return frozenset(p.id for p in self.corpus.manifest.players if p.current_club_id == team_id)

    def _is_team_context(self, chunk: Chunk, team_id: str | None, asked_about: frozenset[str] = frozenset()) -> bool:
        """A chunk of the selected team's own documents (memos, coaching notes, analysis
        of the team) — context for fit, not evidence about a candidate. A report on one of
        the team's own players is team context too, unless the question is about that
        player (`asked_about`): "Valdez or Sorensen?" needs both players' reports as
        evidence, not as two of the three team-context slots."""
        if not team_id or team_id not in chunk.meta.team_ids:
            return False
        if asked_about & set(chunk.meta.entity_ids):
            return False
        return set(chunk.meta.entity_ids) <= self._team_players(team_id)

    def _is_other_team_context(self, chunk: Chunk, ctx: QueryContext, entities: EntityResolution) -> bool:
        """A chunk of a *different* club's own team documents (no player in them), when the
        question did not name that club. A report on another club's player is still
        candidate evidence — Sorensen plays for the Comets and could be a Tides target — and
        another club's memo that names players (the Sundogs "open to discussing Holloway")
        is market context, so both stay eligible."""
        if not chunk.meta.team_ids or ctx.team_id in chunk.meta.team_ids or chunk.meta.entity_ids:
            return False
        named = {e.id for e in entities.resolved if e.kind in ("team", "club") and e.how == "named"}
        return not (named & set(chunk.meta.team_ids))

    def _priority_ranking(self, ctx: QueryContext) -> tuple[list[str], dict[str, tuple[str, ...]]]:
        """Roster-fit only: internal reports about candidate players whose manifest topics
        cover the GM's priorities — high/must_preserve count double, medium once. Players
        already on the selected team are not candidates. Ties: the chunk naming the topic in
        its own text, then the newest report. Returns (ranking, matched priority labels)."""
        if ctx.mode != "roster_fit" or self.config.entity_k == 0:
            return [], {}
        weights = {"must_preserve": 2, "high": 2, "medium": 1}
        wanted = {key: weights[level] for key, level in _effective_priorities(ctx, self.corpus.manifest).items() if level in weights}
        if not wanted:
            return [], {}
        own = self._team_players(ctx.team_id)
        manifest = self.corpus.manifest
        rows: list[tuple[tuple[int, int, int, str, int], str]] = []
        labels: dict[str, tuple[str, ...]] = {}
        for chunk in self.corpus.chunks:
            meta = chunk.meta
            candidates = set(meta.entity_ids) - own
            if meta.trust != "internal" or not candidates:
                continue
            covered = [key for key in wanted if key in meta.topics]
            if not covered:
                continue
            text = chunk.embedding_text.lower()
            named_topic = sum(
                1
                for key in covered
                if (dim := manifest.dimension(key)) is not None
                and any(word in text for word in dim.keywords)
            )
            score = sum(wanted[key] for key in covered)
            rows.append(((-score, -named_topic, -_day(meta.report_date), chunk.source_id, chunk.ordinal), chunk.chunk_id))
            labels[chunk.chunk_id] = tuple(_dimension_label(manifest, key) for key in covered)
        rows.sort()
        ranking = [chunk_id for _, chunk_id in rows[: self.config.entity_k]]
        return ranking, {cid: labels[cid] for cid in ranking}

    def _pins(self, ctx: QueryContext, fused: Mapping[str, float]) -> list[str]:
        """Roster-fit only: the selected team's strategy memos — each one's lead chunk, then
        each one's best-fused other chunk (if any was retrieved) — up to `max_pinned`."""
        if ctx.mode != "roster_fit" or not ctx.team_id or self.config.max_pinned == 0:
            return []
        team = self.corpus.manifest.team(ctx.team_id)
        if team is None:
            return []
        leads: list[str] = []
        extras: list[str] = []
        for source_id in team.strategy_source_ids[:PINNED_STRATEGY_DOCS]:
            chunks = self._by_source.get(source_id, [])
            if not chunks:
                continue
            lead = min(chunks, key=lambda c: c.ordinal)
            leads.append(lead.chunk_id)
            others = [c for c in chunks if c.chunk_id != lead.chunk_id and c.chunk_id in fused]
            if others:
                best = max(others, key=lambda c: (fused[c.chunk_id], -c.ordinal))
                extras.append(best.chunk_id)
        return [*leads, *extras][: self.config.max_pinned]

    def _assemble(
        self,
        ctx: QueryContext,
        entities: EntityResolution,
        plan: _Plan,
        dense_hits: list[tuple[str, float]] | None,
        lexical_hits: list[tuple[str, float]] | None,
        timings: dict[str, float],
        gate_dense: float | None = None,
    ) -> RetrievalResult:
        started = perf_counter()
        cfg = self.config
        rankings: list[list[str]] = []
        if dense_hits is not None:
            rankings.append([cid for cid, _ in dense_hits])
        if lexical_hits is not None:
            rankings.append([cid for cid, _ in lexical_hits])
        entity_ranking = self._entity_ranking(ctx, entities) if plan.variant == "hybrid" else []
        if entity_ranking:
            rankings.append(entity_ranking)
        priority_ranking, priority_labels = (
            self._priority_ranking(ctx) if plan.variant == "hybrid" else ([], {})
        )
        if priority_ranking:
            rankings.append(priority_ranking)

        fused = reciprocal_rank_fusion(rankings, k=cfg.rrf_k)
        fused_score = dict(fused)
        ranked_ids = [cid for cid, _ in fused]
        pins = self._pins(ctx, fused_score)
        group_of: dict[str, str] = {}
        asked_about = frozenset(e.id for e in _retrieval_entities(ctx, entities) if e.kind == "player")
        for chunk_id in {*ranked_ids, *pins}:
            chunk = self._by_id[chunk_id]
            if chunk.meta.trust == "untrusted":
                group_of[chunk_id] = "untrusted"
            elif ctx.mode == "roster_fit" and self._is_team_context(chunk, ctx.team_id, asked_about):
                group_of[chunk_id] = "team_context"
            elif ctx.mode == "roster_fit" and self._is_other_team_context(chunk, ctx, entities):
                group_of[chunk_id] = "other_team_context"
            elif ctx.mode == "roster_fit" and chunk.meta.doc_type == "method_guide":
                group_of[chunk_id] = "method"
        roster_fit = ctx.mode == "roster_fit" and bool(ctx.team_id)
        selected_ids = select_chunks(
            ranked_ids,
            pins,
            self._source_of,
            k=plan.k,
            max_per_source=cfg.roster_fit_max_per_source if roster_fit else cfg.max_chunks_per_source,
            group_of=group_of,
            group_limits={
                "team_context": cfg.max_team_context,
                "untrusted": cfg.max_untrusted,
                "method": cfg.roster_fit_max_method,
                # another club's own memos say nothing about this team's fit unless the
                # question names that club
                "other_team_context": 0,
            },
        )
        pinned = {cid for cid in pins if cid in selected_ids}

        # Scores are rounded once, here, so the numbers in `reasons` and the score fields agree.
        dense = {cid: (rank, round(score, 4)) for rank, (cid, score) in enumerate(dense_hits or [], start=1)}
        lexical = {cid: (rank, round(score, 4)) for rank, (cid, score) in enumerate(lexical_hits or [], start=1)}
        in_entity_ranking = set(entity_ranking)
        refs = _retrieval_entities(ctx, entities)

        def describe(chunk_id: str, rank: int) -> RetrievedChunk:
            chunk = self._by_id[chunk_id]
            about = set(chunk.meta.entity_ids) | set(chunk.meta.team_ids) | self._mentions.get(chunk_id, frozenset())
            matched = [ref for ref in refs if ref.id in about]
            reasons: list[str] = []
            dense_rank, dense_score = dense.get(chunk_id, (None, None))
            lexical_rank, lexical_score = lexical.get(chunk_id, (None, None))
            if dense_rank is not None and dense_score is not None:
                reasons.append(f"semantic match #{dense_rank} (cosine {dense_score:.2f})")
            if lexical_rank is not None and lexical_score is not None:
                reasons.append(f"keyword match #{lexical_rank} (BM25 {lexical_score:.1f})")
            if chunk_id in in_entity_ranking:
                named = [ref.name for ref in matched if ref.how != "selected_team"]
                if named:
                    reasons.append("about " + ", ".join(named))
                elif matched:
                    reasons.append(f"about {matched[0].name} (selected team)")
            if chunk_id in priority_labels:
                reasons.append(PRIORITY_REASON_PREFIX + ", ".join(priority_labels[chunk_id]))
            if chunk_id in pinned:
                reasons.append(PIN_REASON)
            if chunk.meta.trust == "untrusted":
                reasons.append(UNTRUSTED_REASON)
            if plan.degraded:
                reasons.append(NO_DENSE_REASON)
            return RetrievedChunk(
                chunk=chunk,
                rank=rank,
                fused_score=round(fused_score.get(chunk_id, 0.0), 6),
                dense_score=dense_score,
                dense_rank=dense_rank,
                lexical_score=lexical_score,
                lexical_rank=lexical_rank,
                matched_entities=tuple(ref.id for ref in matched),
                pinned=chunk_id in pinned,
                reasons=tuple(reasons),
            )

        candidate_ids = ranked_ids + [cid for cid in selected_ids if cid not in fused_score]
        candidates = tuple(describe(cid, rank) for rank, cid in enumerate(candidate_ids, start=1))
        by_id = {item.chunk.chunk_id: item for item in candidates}
        selected = tuple(replace(by_id[cid], rank=rank) for rank, cid in enumerate(selected_ids, start=1))

        top_dense = dense_hits[0][1] if dense_hits else None
        # The gate scores the question as asked: BM25 on the raw text (counting only passages
        # that share two content words or one basketball word with it), and the
        # raw-question embedding when expansion changed the text (`gate_dense`, from the caller).
        gate_lexical: float | None = None
        gate_query = gate_text(ctx.question, self.corpus.manifest)
        if plan.use_lexical and self._lexical is not None:
            raw_hits = self._lexical.search(gate_query, len(self._lexical))
            gate_lexical = lexical_gate_score(gate_query, raw_hits, self._terms, self._background)
        if not plan.use_dense:
            gate_dense = None
        elif gate_dense is None:
            gate_dense = top_dense
        low_evidence = is_low_evidence(
            has_entities=bool(vouching_entities(ctx, entities)),
            top_dense=gate_dense,
            top_lexical=gate_lexical,
            config=cfg,
        )
        timings["fuse"] = _ms(started)
        logger.debug(
            "assistant retrieval: variant=%s selected=%d candidates=%d low_evidence=%s",
            plan.variant,
            len(selected),
            len(candidates),
            low_evidence,
            extra={"request_id": ctx.request_id},
        )
        return RetrievalResult(
            query_text=plan.query_text,
            variant=plan.variant,
            k=plan.k,
            selected=selected,
            candidates=candidates,
            top_dense_score=None if top_dense is None else round(top_dense, 4),
            min_evidence_threshold=cfg.min_dense_score,
            low_evidence=low_evidence,
            timings_ms=timings,
        )
