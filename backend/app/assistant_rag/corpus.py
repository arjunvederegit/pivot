"""Document loading and chunking — the "load" and "split" steps of RAG.

LangChain models a retrievable unit as a `Document(page_content, metadata, id)`. This module
produces those in two steps so the logic stays testable without LangChain installed:

1. `load_corpus()` reads every file the manifest lists, verifies its content hash against the
   manifest (so a citation always resolves to the exact text that was indexed), and splits it
   on `## ` section headings into `Chunk`s — plain frozen dataclasses.
2. `to_langchain_documents()` converts Chunks into LangChain `Document`s for the vector store.

Why section chunks rather than a fixed-size splitter: the documents are short scouting
reports whose sections are already the natural unit of evidence ("Defense", "Shooting",
"Open questions"). A section is what the evidence inspector shows a GM as an excerpt, and
what a citation should point at. `RecursiveCharacterTextSplitter` would cut sentences in
half for no retrieval gain at this document size.

Every chunk carries the full document metadata (source id, version, author, date, trust,
entities) because metadata is what lets retrieval filter, the prompt label untrusted text,
and the UI show provenance.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.assistant_rag import DATA_MODE, PROVIDER_KEY
from app.assistant_rag.manifest import DOCUMENTS_DIR, DocumentMeta, Manifest, load_manifest

if TYPE_CHECKING:  # pragma: no cover - typing only
    from langchain_core.documents import Document

#: The first line of every fixture is a human-readable fictional-data banner. It is file
#: metadata, not evidence, so it is stripped before chunking.
BANNER_PREFIX = "> FICTIONAL DATA"
_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


class CorpusError(ValueError):
    """A document is missing, unreadable or does not match the manifest."""


@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_id: str  # "S03@v2#2"
    source_id: str  # "S03"
    version: str  # "v2"
    ordinal: int  # 0 = the document's lead paragraph, 1.. = sections in order
    section: str | None
    text: str  # the section body as a reader sees it (no heading, no banner)
    content_sha256: str  # hash of the whole source document
    meta: DocumentMeta

    @property
    def embedding_text(self) -> str:
        """What gets embedded and keyword-indexed: title and section give a short passage
        the context it needs ("Defense" alone says nothing about *whose* defense)."""
        heading = f"{self.meta.title}" + (f" — {self.section}" if self.section else "")
        return f"{heading}\n{self.text}"

    def metadata(self) -> dict[str, Any]:
        m = self.meta
        return {
            "chunk_id": self.chunk_id,
            "source_id": self.source_id,
            "version": self.version,
            "section": self.section,
            "title": m.title,
            "doc_type": m.doc_type,
            "author": m.author,
            "author_role": m.author_role,
            "report_date": m.report_date,
            "season": m.season,
            "entity_ids": list(m.entity_ids),
            "team_ids": list(m.team_ids),
            "topics": list(m.topics),
            "trust": m.trust,
            "data_mode": DATA_MODE,
            "provider": PROVIDER_KEY,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class Corpus:
    manifest: Manifest
    chunks: tuple[Chunk, ...]
    texts: dict[str, str]  # source_id -> full document text (banner stripped)

    def chunk(self, chunk_id: str) -> Chunk | None:
        return next((c for c in self.chunks if c.chunk_id == chunk_id), None)

    def chunks_for(self, source_id: str) -> list[Chunk]:
        return [c for c in self.chunks if c.source_id == source_id]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def strip_banner(text: str) -> str:
    lines = text.splitlines()
    while lines and (lines[0].startswith(BANNER_PREFIX) or not lines[0].strip()):
        lines.pop(0)
    return "\n".join(lines).strip()


def split_sections(body: str) -> list[tuple[str | None, str]]:
    """`# Title\\nlead...\\n## Defense\\n...` -> [(None, lead), ("Defense", ...), ...].

    The `# Title` line is dropped (the title lives in metadata). Empty sections are skipped.
    """
    lines = [ln for ln in body.splitlines() if not ln.startswith("# ")]
    text = "\n".join(lines)
    parts: list[tuple[str | None, str]] = []
    matches = list(_HEADING.finditer(text))
    lead = text[: matches[0].start()] if matches else text
    if lead.strip():
        parts.append((None, lead.strip()))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section_body = text[match.end() : end].strip()
        if section_body:
            parts.append((match.group(1).strip(), section_body))
    return parts


def load_document(meta: DocumentMeta, documents_dir: Path = DOCUMENTS_DIR) -> tuple[str, str]:
    """Return `(raw_text, sha256)` for one document, verifying the manifest hash."""
    path = documents_dir / meta.file
    if not path.is_file():
        raise CorpusError(f"document {meta.source_id} is missing: {path.name}")
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith(BANNER_PREFIX):
        raise CorpusError(f"document {meta.source_id} does not start with the fictional-data banner")
    digest = sha256_text(raw)
    if meta.sha256 is not None and meta.sha256 != digest:
        raise CorpusError(
            f"document {meta.source_id} changed without a manifest update (sha256 {digest[:12]}… "
            f"!= {meta.sha256[:12]}…). Bump its version and record the new hash."
        )
    return raw, digest


def build_corpus(manifest: Manifest, documents_dir: Path = DOCUMENTS_DIR) -> Corpus:
    chunks: list[Chunk] = []
    texts: dict[str, str] = {}
    for meta in manifest.documents:
        raw, digest = load_document(meta, documents_dir)
        body = strip_banner(raw)
        texts[meta.source_id] = body
        sections = split_sections(body)
        if not sections:
            raise CorpusError(f"document {meta.source_id} has no content")
        for ordinal, (section, text) in enumerate(sections):
            chunks.append(
                Chunk(
                    chunk_id=f"{meta.versioned_id}#{ordinal}",
                    source_id=meta.source_id,
                    version=meta.version,
                    ordinal=ordinal,
                    section=section,
                    text=text,
                    content_sha256=digest,
                    meta=meta,
                )
            )
    return Corpus(manifest=manifest, chunks=tuple(chunks), texts=texts)


@lru_cache(maxsize=1)
def load_corpus() -> Corpus:
    """The process-wide corpus. Raises `CorpusError`/`ManifestError` loudly if inconsistent."""
    return build_corpus(load_manifest())


def to_langchain_documents(chunks: tuple[Chunk, ...] | list[Chunk]) -> list[Document]:
    """Chunks -> LangChain `Document`s. `id` is the stable chunk id, so the vector store's
    keys, the retrieval diagnostics and the citation map all agree."""
    from langchain_core.documents import Document

    return [
        Document(id=c.chunk_id, page_content=c.embedding_text, metadata=c.metadata()) for c in chunks
    ]
