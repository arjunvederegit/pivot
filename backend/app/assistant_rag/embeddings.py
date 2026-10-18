"""Embeddings for the dense half of retrieval — Ollama's `nomic-embed-text`, with task prefixes.

What an embedding is. An embedding model maps a piece of text to a fixed-length vector
(768 numbers for nomic-embed-text) such that texts with similar meaning land near each other.
"Nearness" is cosine similarity: 1.0 means the same direction, ~0 means unrelated. Dense
retrieval embeds every chunk once at startup, embeds the question at request time and returns
the chunks whose vectors point most nearly the same way — which is how "can he stay in front of
quick guards?" finds a section titled "Point-of-attack defense" with no word in common.

LangChain's `Embeddings` interface has two methods for this, on purpose:
`embed_documents(texts)` for what is stored and `embed_query(text)` for what is searched. Many
models treat them identically. Retrieval-trained models such as nomic-embed-text do not: they
were trained on asymmetric pairs (a short question against a long passage that answers it)
with a task instruction prepended to each side, and expect the same at inference time:

    stored passage  ->  "search_document: <passage>"
    user question   ->  "search_query: <question>"

Omitting the prefixes still "works" — it returns vectors — but the vectors come from outside
the distribution the model was trained on, and similarity scores get flatter (relevant and
irrelevant passages score closer together). Nothing errors; retrieval quietly degrades. That
is why the prefixes live in one wrapper class, applied to every call, rather than being
remembered at each call site.

`OllamaEmbeddings` (langchain-ollama) does not add these prefixes itself, so
`NomicPrefixedEmbeddings` wraps it. Importing this module requires the optional
`.[assistant]` extra (it subclasses `langchain_core.embeddings.Embeddings`); nothing on
`app.main`'s import path imports it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.embeddings import Embeddings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.config import Settings

KEEP_ALIVE_SECONDS = 30 * 60
DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


class NomicPrefixedEmbeddings(Embeddings):
    """Wrap any `Embeddings` so documents and queries carry nomic's task prefixes.

    Sync and async methods both prefix, so the vector store (sync `add_documents`) and the
    async request path (`aembed_query`) can never disagree about the format.
    """

    def __init__(self, inner: Embeddings) -> None:
        self.inner = inner

    @property
    def model(self) -> str | None:
        """The wrapped model's name (for status and diagnostics), if it has one."""
        name = getattr(self.inner, "model", None)
        return str(name) if name is not None else None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.inner.embed_documents([DOCUMENT_PREFIX + text for text in texts])

    def embed_query(self, text: str) -> list[float]:
        return self.inner.embed_query(QUERY_PREFIX + text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self.inner.aembed_documents([DOCUMENT_PREFIX + text for text in texts])

    async def aembed_query(self, text: str) -> list[float]:
        return await self.inner.aembed_query(QUERY_PREFIX + text)


def needs_nomic_prefixes(model_name: str) -> bool:
    return "nomic" in model_name.lower()


def build_embeddings(settings: Settings) -> Embeddings:
    """The embedding model the retriever uses: Ollama, prefixed when the model is a nomic one.

    * `keep_alive` of 30 minutes keeps the model loaded between questions, so the second
      question does not pay a cold model load just to embed eight words. It is given in
      seconds (1800): `OllamaEmbeddings.keep_alive` is typed `int | None` and rejects the
      "30m" duration string that `ChatOllama` accepts.
    * the HTTP timeout is the request budget capped at 60 s: embedding the whole corpus at
      startup is one batched call and should take seconds, not minutes; if it does not, the
      server is unhealthy and failing loudly is better than hanging the first request.

    Constructing the client does not contact Ollama; the first embed call does.
    """
    try:
        from langchain_ollama import OllamaEmbeddings
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        from app.assistant_rag.errors import AssistantDependencyError

        raise AssistantDependencyError(
            "Ask Pivot needs the optional assistant dependencies: pip install -e '.[assistant]'"
        ) from exc

    timeout = min(60.0, float(settings.assistant_request_timeout_seconds))
    inner = OllamaEmbeddings(
        model=settings.assistant_embedding_model,
        base_url=settings.ollama_base_url,
        keep_alive=KEEP_ALIVE_SECONDS,
        client_kwargs={"timeout": timeout},
    )
    if needs_nomic_prefixes(settings.assistant_embedding_model):
        return NomicPrefixedEmbeddings(inner)
    return inner
