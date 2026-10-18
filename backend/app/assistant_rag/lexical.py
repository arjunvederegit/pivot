"""A small BM25 keyword index — the "lexical" half of hybrid retrieval.

What BM25 does. Okapi BM25 scores a document for a query by summing, over each distinct
query term, how *rare* the term is across the corpus (IDF) times how *often* it appears in
this document (TF), with two corrections:

    score(q, d) = Σ_t  idf(t) · tf(t,d)·(k1 + 1) / (tf(t,d) + k1·(1 − b + b·|d|/avgdl))
    idf(t)      = ln(1 + (N − n_t + 0.5) / (n_t + 0.5))

* `k1` (1.5) saturates term frequency: the fifth "Reed" in a passage adds far less than
  the first, so a document cannot win by repeating a word.
* `b` (0.75) normalises for length: a term in a short section counts for more than the
  same term buried in a long one.
* This IDF is the "plus one inside the log" variant (as in Lucene). The classic Robertson
  form, ln((N − n + 0.5)/(n + 0.5)), goes *negative* for a term in more than half the
  documents, which would make matching a common word lower a score. Here a match can only
  ever help, by a little (common term) or a lot (rare term).

Why keep a keyword index next to embeddings. An embedding compresses a passage into one
vector of "what it is about", which is excellent for paraphrase ("can he stay in front of
guards?" ~ "point-of-attack defense") and poor at exactness. Exact names ("Achterberg"),
numbers ("40.4"), and rare terms ("drop coverage", "S14") are where BM25 is strong and a
small embedding model is weakest — the vector for "Theo Vance 40.4" is not reliably closer
to the passage with that number than to one about another shooter. Fusing both rankings
(see `retrieval.reciprocal_rank_fusion`) gets the strengths of each.

Why not `rank_bm25`: the whole algorithm is the forty lines below, and owning them means
the tokenizer — which decides what "matches" means — is visible and tested here.

Pure Python: no LangChain, no numpy.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence

_STOPWORD_TEXT = """
    a about after again all also am an and any are as at be been before being but by can
    could did do does doing done during each few for from had has have having he her here
    hers him his how i if im in into is it its itself just me more most my no nor not of on
    once only or other our ours out over own same she should so some such than that the
    their theirs them then there these they this those through to too under until up us very
    was we were what when where which while who whom why will with would you your yours
    dont doesnt didnt isnt arent wasnt cant wont ive youre theyre
"""
#: Function words (and apostrophe-less contractions) that carry no retrieval signal.
#: Deliberately short: domain words that look common ("defense", "guard", "team") are *not*
#: stopwords — IDF already discounts them by how common they are in this corpus, which is
#: the honest way to do it.
STOPWORDS = frozenset(_STOPWORD_TEXT.split())

#: A token is a run of letters/digits, keeping decimals whole ("40.4"), optionally joined by
#: hyphens into a compound ("point-of-attack", "2025-26", "6-7").
_ATOM = r"(?:\d+\.\d+|[a-z0-9]+)"
_TOKEN = re.compile(rf"{_ATOM}(?:-{_ATOM})*")
_POSSESSIVE = re.compile(r"['’]s\b")
_APOSTROPHE = re.compile(r"['’]")


def _fold(text: str) -> str:
    """Lowercase, strip accents ("é" -> "e"), drop possessives ("Reed's" -> "reed") and
    remaining apostrophes ("don't" -> "dont")."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _APOSTROPHE.sub("", _POSSESSIVE.sub("", stripped.lower()))


def normalise(word: str) -> str:
    """Light, symmetric suffix folding so inflections of a word meet at one token.

    Rules, applied once, in order (tokens containing a digit are never changed):

    1. plural: "-ies" -> "-y" ("abilities" -> "ability"); otherwise a trailing "s" is dropped
       unless the word ends in "ss", "us" or "is" ("rebounds" -> "rebound", "pass" stays).
    2. "-ing" is dropped from words of 6+ letters ("shooting" -> "shoot"), else "-ed" from
       words of 5+ letters ("switched" -> "switch"). Short words ("king", "reed") are left.
    3. a trailing "e" is dropped from words of 4+ letters, which is what lets "space" and
       "spacing" (both -> "spac"), "grade" and "graded" (-> "grad") match.

    This is not a linguistic stemmer and produces non-words ("defens"). That is fine: the
    same function runs over the documents and the query, so only *agreement* matters.
    """
    if any(ch.isdigit() for ch in word) or len(word) < 4:
        return word
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    if word.endswith("ing") and len(word) >= 6:
        word = word[:-3]
    elif word.endswith("ed") and len(word) >= 5:
        word = word[:-2]
    if word.endswith("e") and len(word) >= 4:
        word = word[:-1]
    return word


def _keep(part: str) -> bool:
    if part in STOPWORDS:
        return False
    # Single letters ("e" from "e.g.") carry nothing; single digits ("3" in "3-point") might.
    return len(part) > 1 or part.isdigit()


def tokenize(text: str) -> list[str]:
    """Text -> index terms, in order (duplicates kept: term frequency is the point).

    >>> tokenize("Reed's point-of-attack defense: 40.4% on 6.1 attempts")
    ['reed', 'point', 'attack', 'point-of-attack', 'defens', '40.4', '6.1', 'attempt']

    A hyphenated compound yields its parts *and* the joined form, so "point of attack" in a
    query still matches "point-of-attack" in a report, while the joined token rewards the
    exact phrase. Stopwords and single letters are dropped; numbers are kept verbatim.
    """
    tokens: list[str] = []
    for match in _TOKEN.finditer(_fold(text)):
        parts = match.group(0).split("-")
        tokens.extend(normalise(part) for part in parts if _keep(part))
        if len(parts) > 1:
            tokens.append("-".join(normalise(part) for part in parts))
    return tokens


class BM25Index:
    """An in-memory Okapi BM25 index over `(doc_id, text)` pairs.

    Built once (the corpus is small and fixed); `search` touches only the documents that
    contain at least one query term, via a term -> documents posting list.
    """

    def __init__(self, documents: Sequence[tuple[str, str]], k1: float = 1.5, b: float = 0.75) -> None:
        ids = [doc_id for doc_id, _ in documents]
        if len(set(ids)) != len(ids):
            raise ValueError("BM25Index document ids must be unique")
        self.k1 = k1
        self.b = b
        self._ids: list[str] = ids
        self._tf: list[Counter[str]] = [Counter(tokenize(text)) for _, text in documents]
        self._length: list[int] = [sum(tf.values()) for tf in self._tf]
        self.avgdl: float = (sum(self._length) / len(self._length)) if self._length else 0.0
        self._postings: dict[str, list[int]] = {}
        for index, tf in enumerate(self._tf):
            for term in tf:
                self._postings.setdefault(term, []).append(index)
        n_docs = len(self._ids)
        self._idf: dict[str, float] = {
            term: math.log(1.0 + (n_docs - len(docs) + 0.5) / (len(docs) + 0.5))
            for term, docs in self._postings.items()
        }

    def __len__(self) -> int:
        return len(self._ids)

    @property
    def vocabulary_size(self) -> int:
        return len(self._idf)

    def idf(self, term: str) -> float:
        """IDF of an already-tokenized term; 0.0 for a term no document contains."""
        return self._idf.get(term, 0.0)

    def scores(self, query: str) -> dict[str, float]:
        """`doc_id -> score` for every document sharing at least one term with the query.

        Each distinct query term counts once: query expansion that repeats a name already in
        the question ("Reed ... Niko Reed") must not double that name's weight.
        """
        terms = list(dict.fromkeys(tokenize(query)))
        totals: dict[int, float] = {}
        for term in terms:
            idf = self._idf.get(term)
            if idf is None:
                continue
            for index in self._postings[term]:
                tf = self._tf[index][term]
                norm = 1.0 - self.b + self.b * (self._length[index] / self.avgdl) if self.avgdl else 1.0
                gain = idf * tf * (self.k1 + 1.0) / (tf + self.k1 * norm)
                totals[index] = totals.get(index, 0.0) + gain
        return {self._ids[index]: score for index, score in totals.items() if score > 0.0}

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        """The top `k` `(doc_id, score)` pairs, best first. Only documents with score > 0
        are returned — a document with no query term is not a weak match, it is no match.
        Ties break by doc id so the ranking is deterministic."""
        if k <= 0:
            return []
        ranked = sorted(self.scores(query).items(), key=lambda item: (-item[1], item[0]))
        return ranked[:k]
