"""A deterministic stand-in for the chat model, so the evaluation harness can run offline.

Why it exists. The local runner's job is plumbing: build each benchmark request, run the
real pipeline, score the answer, write `results.jsonl`, `summary.json`, `report.md` and the
review worksheet. All of that should be checkable in seconds, on a machine with no Ollama,
before anyone spends 30 minutes on a live run. `--fake` swaps the two model calls for:

* `ExtractiveFakeChatModel` — a LangChain chat model whose "answer" is the first
  informative sentence of each of the top two trusted sources in the prompt it was shown,
  each followed by that source's citation. It reads the same `<source id="S04" ...>` blocks
  the real model reads, so the answer is cited, retrieved and deterministic;
* `DeterministicFakeEmbedding` (langchain-core) — hash-seeded vectors: the same text always
  gets the same vector, but nearness means nothing. Dense rankings in fake mode are noise.

What it is NOT: a quality signal. It never reasons about the question, so it fails fact,
polarity and abstention checks whenever the lead sentence of a source does not happen to
answer them. A fake-mode report says so in its first line. Use it to test the harness and
the scorers' wiring, never to compare prompts or retrieval variants.

Being a real `BaseChatModel` (rather than a duck-typed object) matters: the pipeline's
`template | chat_model` composition, the run tree and the async path are exercised exactly as
they are with `ChatOllama`. The answer logic itself (`fake_answer`) is plain Python, testable
without LangChain; the LangChain class is built lazily on first use.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from app.assistant_rag import claims

FAKE_CHAT_MODEL_NAME = "fake-extractive"
FAKE_EMBEDDING_SIZE = 256
#: How many sources the fake answer quotes.
MAX_SOURCES = 2
#: Leads every fake answer, so a fake result can never be mistaken for a model's.
ANSWER_PREFIX = "Fake-model answer (harness check):"
NO_EVIDENCE_ANSWER = f"{ANSWER_PREFIX} the passages shown hold no sentence it can quote."

#: One source block as `prompts.py` renders it (grounded and basic formats both match).
_SOURCE_BLOCK = re.compile(r'<source id="(S\d{2,3})"([^>]*)>\n(.*?)\n</source>', re.DOTALL)
#: Lines that are labels, not prose: "[Section]", "[UNTRUSTED ...]", headings, table rows,
#: and an all-italic byline ("*Dana Whitfield, Lead scout (fictional) · 2026-01-20 · ...*").
_LABEL_LINE = re.compile(r"^\s*(?:\[.*\]|#+\s|\||\*[^*].*\*\s*$|_[^_].*_\s*$)")
_MARKUP = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+)|\*\*|__|`")
_MIN_WORDS = 6
_MAX_CHARS = 300


def _lead_sentence(body: str) -> str | None:
    """The first sentence of a source body that reads as prose (six words or more)."""
    prose = [_MARKUP.sub("", line).strip() for line in body.splitlines() if line.strip() and not _LABEL_LINE.match(line)]
    for sentence in claims.split_sentences("\n".join(prose)):
        text = claims.strip_citations(sentence).strip()
        if len(text.split()) >= _MIN_WORDS and "<" not in text and "&lt;" not in text:
            text = text if len(text) <= _MAX_CHARS else text[: _MAX_CHARS - 1].rstrip() + "…"
            return text if text.endswith((".", "!", "?", "…")) else text + "."
    return None


def fake_answer(user_message: str, max_sources: int = MAX_SOURCES) -> str:
    """The fake model's whole "reasoning": quote and cite the top trusted sources, in the
    order the prompt lists them (rank order). Untrusted sources are skipped, so the fake
    never repeats promotional or injected text as if it were its own claim."""
    quoted: list[str] = []
    for source_id, attributes, body in _SOURCE_BLOCK.findall(user_message):
        if 'trust="untrusted"' in attributes or "[UNTRUSTED" in body:
            continue
        sentence = _lead_sentence(body)
        if sentence:
            quoted.append(f"{sentence[:-1]} [{source_id}]{sentence[-1]}")  # "... shooter [S05]."
        if len(quoted) >= max_sources:
            break
    if not quoted:
        return NO_EVIDENCE_ANSWER
    return f"{ANSWER_PREFIX} " + " ".join(quoted)


def _last_message_text(messages: Any) -> str:
    """The latest message's text: the pipeline puts the SOURCES block in the final user turn."""
    if not messages:
        return ""
    content = getattr(messages[-1], "content", "")
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content or [])


@lru_cache(maxsize=1)
def _chat_model_class() -> type:
    """Define the LangChain class on first use (keeps this module importable without it)."""
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class ExtractiveFakeChatModel(BaseChatModel):
        """Deterministic, offline, and deliberately unintelligent (see module doc)."""

        model: str = FAKE_CHAT_MODEL_NAME

        @property
        def _llm_type(self) -> str:
            return "pivot-extractive-fake"

        def _reply(self, messages: Any) -> ChatResult:
            text = fake_answer(_last_message_text(messages))
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

        def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
            return self._reply(messages)

        async def _agenerate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
            return self._reply(messages)

    return ExtractiveFakeChatModel


def build_fake_chat_model() -> Any:
    """An `ExtractiveFakeChatModel` instance (needs langchain-core)."""
    return _chat_model_class()()


def build_fake_embeddings() -> Any:
    """LangChain's hash-seeded fake embeddings: deterministic, offline, semantically empty."""
    from langchain_core.embeddings import DeterministicFakeEmbedding

    return DeterministicFakeEmbedding(size=FAKE_EMBEDDING_SIZE)
