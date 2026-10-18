# Ask Pivot — authoritative knowledge sources and future retrieval design

*Retrieved 2026-10-18. Full inventory with hashes, byte sizes and example questions:
[research/E-F-documents-and-infrastructure.md](research/E-F-documents-and-infrastructure.md). Ask
Pivot itself was **not** changed in R8. Not legal advice.*

## 1. The split that matters

**Documents** answer *what the rule is, what a term means, how a method works*. **Structured services**
answer *what the number is*. If the answer is a figure that expires on 1 July, it is not a document
question: the 2026-27 cap, tax and aprons come from a typed table populated from the NBA's press
releases, never from an embedding.

| Goes in the retrieval store (prose) | Goes in structured services (numbers) |
| --- | --- |
| CBA articles/sections (text only); CBA 101 narrative | cap / tax / apron / MLE figures per season |
| Playing-rules text and "Comments on the Rules" | salaries, contracts, cap holds |
| Stat glossary definitions and formulas | stat values, ratings |
| draft-lottery *mechanics* | lottery odds table, draft order, pick ownership |
| method descriptions from CC-BY papers | media-guide tables, injury reports, L2M reports |

## 2. Sources to acquire (all free; all URLs returned HTTP 200 on 2026-10-18)

| # | Document | Publisher / date | Direct URL | Format | Licence posture for AI ingestion | Ask Pivot could answer |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **2023 NBA–NBPA Collective Bargaining Agreement** (676 pp., 42 Articles + Exhibits A–J; effective 2023-07-01, through 2029-30 with opt-outs after 2028-29) | NBPA copy | https://nbpa.com/cba (PDF on its CDN) | PDF | AMBER: public labour contract posted by both parties; no AI licence stated | apron restrictions (Art. VII §2(e)), exceptions, Bird rights, two-way (Art. II §11), draft eligibility (Art. X), aggregation rules |
| 1b | same agreement, NBA copy (SHA-256 `cf59d43f…`, 2024-06-04) | NBA | https://official.nba.com/rulebook/ → ak-static.cms.nba.com/…/2023-NBA-Collective-Bargaining-Agreement-Final.pdf | PDF | AMBER; site footer is all-rights-reserved | as above; prefer one host and **pin its hash** (the two copies' bytes differ) |
| 2 | **CBA 101** (34 pp., "prepared by the NBA", Nov 2024; 2024-25 levels — figures stale) | NBA | https://official.nba.com/wp-content/uploads/sites/4/2024/11/2024-25-CBA-101.pdf | PDF | AMBER | plain-language "what is a non-taxpayer MLE?" — best novice document |
| 3 | **NBA Constitution and By-Laws** (June 2024) | NBA | https://official.nba.com/wp-content/uploads/sites/4/2024/06/NBA-Consitution-By-Laws-June-2024.pdf (sic) | PDF | AMBER | Stepien rule (§7.03), governance |
| 4 | **2026-27 Official NBA Playing Rules** (uploaded 2026-10-05) | NBA | https://ak-static.cms.nba.com/wp-content/uploads/sites/4/2026/10/2026-27-Official-NBA-Playing-Rules.pdf ; HTML at https://official.nba.com/rulebook/ | PDF/HTML | AMBER | shot clock, goaltending, flagrants, challenges, replay triggers |
| 5 | **Stat glossary** (~280 terms: definition, formula) | NBA | https://www.nba.com/stats/help/glossary | HTML | AMBER; ingest **definitions and formulas only** | "eFG% vs TS%", "what is PIE?" |
| 6 | **2025-26 and 2026-27 salary-cap releases** | NBA Communications | https://pr.nba.com/nba-salary-cap-2025-26-season · https://pr.nba.com/2026-27-salary-cap/ | HTML | facts-only; cite, do not republish | → load into a **table**, not the store |
| 7 | **Draft lottery explainer** (new system approved 2026-05-28 for the 2027 draft; details unpublished) | NBA | https://www.nba.com/news/nba-draft-lottery-explainer | HTML | AMBER | lottery mechanics, with an explicit "changes in 2027" abstention |
| 8 | **CC-BY arXiv papers** (e.g. 2303.04963, 2601.15000, 2608.19454) and linked-only Sloan Sports papers (Sill 2010 RAPM; Goldsberry 2012 CourtVision) | arXiv / MIT SSAC | https://arxiv.org/abs/<id> · https://www.sloansportsconference.com/research-papers/… | PDF | arXiv: GREEN where the paper says CC BY 4.0; Sloan: authors own, no open licence → link and summarise | "what is RAPM and why ridge regression?" |

**Do not ingest:** Basketball-Reference glossary/BPM/WS pages (its Terms of Use explicitly prohibit AI
use — link out only); Larry Coon's cbafaq.com (retired, 1999–2022, certificate broken, never updated
for 2023); Dean Oliver's *Basketball on Paper* (copyright; cite only); video rulebook content; team media
guides (two of 30 verified; copyright text unread; mostly numbers).

**Not found / do not promise:** an NBA "Case Book"; a stand-alone Exhibit 10 document; any public
scouting-report methodology; NBCA coaching manuals; 2026-27 team media guides; a document titled "Court
Counts" (CourtVision, SSAC 2012, was found instead).

**Before shipping text to public users,** ask the NBA in writing (this is the same conversation as
the statistics-consent question): all NBA-hosted documents are "all rights reserved", the Terms of Use
do not mention AI explicitly, and quoting short cited excerpts with a link is the normal practice
but is not a licence.

## 3. Acquisition procedure (when the time comes)

1. Create a `documents` registry row per file *before* chunking (title, publisher, version label, URL,
   retrieved-at, SHA-256, licence class, authority tier, effective-from/to, supersedes).
2. Fetch with a fixed polite User-Agent; keep the original bytes; record the hash. Pin the hash of
   whichever CBA copy is ingested.
3. Re-fetch on a schedule (rulebook Sept–Oct; press releases ≈ 30 June); on a hash change open a review
   item — never auto-replace.

## 4. Recommended retrieval architecture (modest, no vendor lock-in)

* **Parse by structure, chunk per clause.** One chunk per lettered subsection/numbered paragraph, 300–700
  tokens, never crossing a Section boundary; the heading path ("Art. VII › Sec. 2 › (e) Operation of
  Apron Levels") is part of the embedded text; a parent pointer lets the generator pull the whole
  Section when a clause says "subject to Section 6(h)".
* **Hybrid retrieval.** Local embeddings (the existing Ollama path) stored in `sqlite-vec` or `pgvector`
  in the *existing* database, fused with a lexical index (SQLite FTS5 / Postgres full-text) — defined
  terms and clause numbers retrieve better lexically. A corpus of a few thousand chunks needs no
  vector service.
* **Citation provenance on every chunk and echoed in every answer:** `doc_id, doc_title, publisher,
  doc_version, content_sha256, chunk_id, chunk_sha256, section_path, page_start/end, source_url,
  retrieved_at, effective_from/to, supersedes, license_class, authority_tier, numeric_content`.
  Example: "CBA (July 2023), Art. VII Sec. 2(e), pp. 186–195; nba.com PDF; retrieved 2026-10-18; sha256 cf59d43f…".
* **Two clocks.** Document version (what text Pivot holds) and rule effective period (when it applies).
  Never infer the effective date from a file's Last-Modified. Key all cap-year content by season;
  rulebooks one document per season; model the 2027 lottery change explicitly with
  `details_published = false` and abstain rather than guess.
* **Temporal relevance.** Parse "this season / in 2025-26 / after the 2027 draft" into a target date;
  filter chunks by `effective_from <= date < effective_to` before ranking; say so when the date falls in
  a gap or on a superseded document.
* **Precedence** (highest first): CBA text and official amendments → NBA Constitution → Playing Rules
  (on-court) → NBA Communications releases (implement CBA formulas; a conflict is flagged for human
  review, never silently resolved) → NBA-prepared summaries (secondary, labelled) → NBA.com
  explainers/glossary → conference/peer papers (method, never rules) → third-party commentary (never
  authority). Within a tier the later effective version wins; disagreeing chunks are presented with
  citations, never merged.
* **Numbers stay out of the vector store.** Tag digit/currency-dense chunks and CBA Exhibits B–D
  `numeric_content=true`, do not embed them, and store raw with a pointer. A **generation guard**
  extracts every number in a drafted answer and requires each to match a structured-service result from
  the same turn or a quoted, cited chunk — otherwise strip it or abstain. (This is the same principle as
  Pivot's "no silent default": an unsupported number is a defect.)
* **Abstain** when the top score is below a calibrated threshold; the only support is `numeric_content`
  or a RED licence; the needed document is not in the registry (Case Book, scouting reports); the target
  date has no effective document; or sources conflict with unclear precedence. Offer the primary link.
  CBA interpretation always carries "not legal or contractual advice".
* **Evaluate** with a 30–50-question golden set per document family (expected section citation, expected
  abstentions), run on every corpus change, scoring citation accuracy and abstention correctness — the
  same discipline as the existing Ask Pivot scorers.

## 5. Relationship to the data foundation

R8's `SourceMeta`, `effective_from`, provider registry and `ProviderResult` semantics are the structured
side of this design: a numeric answer should carry a `data_status` (provider, season, retrieved-at,
freshness, observed/estimated) exactly as a clause carries a citation. A future Ask Pivot tool that
queries Pivot's services (the R8-readiness note's `simulate_trade` entry point) can cite `data_status`
verbatim.
