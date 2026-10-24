> **Appendix to [resource-research.md](../resource-research.md).** Working notes written by a research sub-agent on 2026-10-18 and read by the author of the main report. Page contents were read through a summarising fetch tool; claims marked verified were checked by that agent, and the NBA.com Terms of Use §9 text and the BALLDONTLIE documents were re-read from the primary pages by the author. Treat rights summaries as leads for counsel, not legal conclusions.

# Pivot research: Category E (authoritative documents) and Category F (supporting infrastructure)

Retrieval date for everything below: **2026-10-18** (today). Method: every URL was requested (HTTP HEAD/GET or page fetch) in this session. "Verified" means it returned HTTP 200 (or a 301 to a 200) on that date. Prices and limits come from the vendor pricing pages fetched that day. Anything I could not confirm is marked **[UNVERIFIED]** and collected in the last section. This is research, not legal advice; the legal notes describe what the published terms appear to say and where permission should be sought.

---

## Part 1. Key findings (read this first)

1. **The full 2023 CBA is public, free and has two official hosts.** NBPA hosts it at `nbpa.com/cba`, and NBA hosts it at `official.nba.com/rulebook/`. I downloaded both PDFs (about 2.9 MB each) and extracted text. Both are 676 pages, about 218,000 words, 42 Articles plus Exhibits A to J. The text is effectively identical; the NBA copy adds a cover page. The bytes differ (SHA-256 below), so pin the hash of whichever you ingest. I found no public amendments or side letters on either host (absence is not proof; see the unverified list).
2. **Larry Coon's cbafaq.com is dead for the 2023 CBA.** The site's banner says the FAQ is copyright 1999-2022 and that he has retired from maintaining it; the live page at `salarycap.htm` is a retirement notice that links to the 2017-CBA edition (`salarycap17.htm`). There is no 2023 edition. Do not ingest it as a current source. The best free official replacement is the NBA's own **"CBA 101"** (34 pages, prepared by the NBA, November 2024).
3. **Cap/tax/apron figures are in NBA Communications press releases, not in the CBA.** The CBA defines the formulas. The yearly dollar figures live at `pr.nba.com`. These are numbers and belong in a structured table, not in the vector store.
4. **Rules changed under our feet.** The 2026-27 Official NBA Rulebook PDF is already live (uploaded 2026-10-05). The NBA Board of Governors approved a **new draft lottery system on 2026-05-28, effective for the 2027 draft**, but the explainer page did not give the new ball counts or odds. Any Ask Pivot answer about lottery odds must be effective-date aware.
5. **Licensing is the main constraint, not availability.** NBA.com pages and PDFs carry an all-rights-reserved notice. Basketball-Reference's terms of use (last updated 2023-05-19) **explicitly prohibit** using content for training, fine-tuning, prompting or instructing AI models, and its robots.txt disallows GPTBot. Treat Basketball-Reference as link-out only. arXiv metadata is CC0, and full texts vary per paper (some CC-BY 4.0).

---

## Part 2. Document inventory

Legend for "AI-ingestion": **GREEN** = licence or terms clearly allow; **AMBER** = public and free but all-rights-reserved or personal-use terms, so internal retrieval with short cited excerpts is plausible but written permission is advisable before shipping to public users; **RED** = terms appear to prohibit; link out only.

| # | Title | Publisher | Date / version | Direct URL (verified 2026-10-18) | Format | Cost | Legal / AI-ingestion notes | Ask Pivot use |
|---|---|---|---|---|---|---|---|---|
| 1a | 2023 NBA-NBPA Collective Bargaining Agreement (NBPA copy; "Final-2023-NBA-Collective-Bargaining-Agreement-6-28-23") | NBPA (hosted via cosmicjs/imgix CDN) | Effective 2023-07-01, runs through 2029-30 with opt-out rights after 2028-29 (per nbpa.com/cba). CDN Last-Modified 2026-06-29 (a CDN timestamp, not a legal effective date). SHA-256 `bf178ca0f2d64f9dfe6fde095d3ae43d576b12e19ce7a679618d632584f7ab32` | https://imgix.cosmicjs.com/25da5eb0-15eb-11ee-b5b3-fbd321202bdf-Final-2023-NBA-Collective-Bargaining-Agreement-6-28-23.pdf (200, application/pdf, 2,850,534 bytes). Landing page: https://nbpa.com/cba | PDF, 676 pp. | Free | No copyright or terms notice found on the landing page. Public labour contract published by both parties. AMBER: no explicit AI licence; quoting clauses with citation is the normal use. | Canonical source for rules: exceptions, Bird rights, aprons, two-way, rookie scale, draft eligibility, trades |
| 1b | 2023 Collective Bargaining Agreement (NBA copy; "NBA / NBPA Collective Bargaining Agreement, July 2023") | NBA (official.nba.com) | CDN Last-Modified 2024-06-04. SHA-256 `cf59d43fe46f63d7ba07364563046d766c487c26032fcc88432310d47effd9d9`. Press release announcing signing: 2023-06-28 | https://ak-static.cms.nba.com/wp-content/uploads/sites/4/2024/06/2023-NBA-Collective-Bargaining-Agreement-Final.pdf (200, 2,903,978 bytes). Listed on https://official.nba.com/rulebook/ | PDF, 676 pp. | Free | Site footer: "Copyright (c) 2026 NBA Media Ventures, LLC. All rights reserved. No portion of NBA.com may be duplicated, redistributed or manipulated in any form." AMBER (see legal section). | Same as 1a; prefer this host for provenance if you want one NBA-controlled URL |
| 1c | NBA-NBPA Collective Bargaining Agreement, 2017 and 2011 editions | NBPA | Superseded (2017: through 2023-06-30; 2011: through 2017-06-30) | Both listed on https://nbpa.com/cba; not individually HEAD-checked | PDF | Free | As 1a | Only for historical questions ("what changed from 2017 to 2023"). Mark `superseded` |
| 1d | CBA 101: Highlights of the Collective Bargaining Agreement (file named "2024-25-CBA-101"; listed as "2023 CBA 101") | NBA | "Prepared by the NBA (November 2024)"; file Last-Modified 2024-11-07; 34 pp. | https://official.nba.com/wp-content/uploads/sites/4/2024/11/2024-25-CBA-101.pdf (200, 528,881 bytes) | PDF | Free | Same NBA footer. It is a secondary summary; check its own disclaimer before presenting it as binding (not read in full). Contains 2024-25 system levels, so it is cap-year-stale for figures. AMBER. | Plain-language explanations of exceptions, aprons, contract structure; best RAG document for novice questions |
| 1e | NBA Constitution and By-Laws (June 2024) | NBA | File dated 2024-06-07 | https://official.nba.com/wp-content/uploads/sites/4/2024/06/NBA-Consitution-By-Laws-June-2024.pdf (200, 422,247 bytes; note the "Consitution" typo in the real URL) | PDF | Free | As above. AMBER. | Franchise relocation, ownership, Board of Governors rules, league governance |
| 1f | NBA Salary Cap FAQ 2017-CBA edition, "NBA Salary Cap/Collective Bargaining Agreement FAQ" (Larry Coon, cbafaq.com) | Larry Coon | Copyright 1999-2022; author states retirement and no 2023 update. Index page Last-Modified 2025-06-19 | http://www.cbafaq.com/salarycap.htm (200 over HTTP; the site's **HTTPS certificate is broken**: `curl` reports a self-signed cert, WebFetch reports expired) and http://www.cbafaq.com/salarycap17.htm (200, 696,944 bytes) | HTML | Free | Copyright retained by Coon; no licence statement found. RED/AMBER: do not ingest without his permission; also out of date. | Historical flavour only; do not use for current rules |
| 2a | NBA Salary Cap for 2025-26 season set at $154.647 million | NBA Communications | Published 2025-06-30; cap in force 2025-07-01 | https://pr.nba.com/nba-salary-cap-2025-26-season (200). Figures: cap 154.647M, tax 187.895M, min team salary 139.182M, 1st apron 195.945M, 2nd apron 207.824M, MLE non-taxpayer 14.104M, taxpayer 5.685M, room 8.781M | HTML | Free | Footer shows NBA Media Ventures all-rights-reserved text. Facts (numbers) are not copyrightable in themselves, but do not republish the page; cite it. | **Do not embed.** Load numbers into a `cap_year_thresholds` table keyed by season, with `source_url` and `retrieved_at` |
| 2b | NBA sets Salary Cap for 2026-27 season at $164.961 million | NBA Communications | Published 2026-06-30; effective 2026-07-01 | https://pr.nba.com/2026-27-salary-cap/ (200). Figures: cap 164.961M, tax 200.428M, min team salary 148.465M, 1st apron 209.015M, 2nd apron 221.686M, MLE non-taxpayer 15.044M, taxpayer 6.064M, room 9.366M | HTML | Free | As 2a | Same. Release does **not** list bi-annual, rookie scale, two-way or minimum contract values (confirmed absent in search, [UNVERIFIED] on the page body for all of them); source those from the CBA formulas plus a licensed or hand-entered table |
| 2c | NBA Collective Bargaining Agreement signed (press release) | NBA Communications | 2023-06-28 | https://pr.nba.com/2023-nba-collective-bargaining-agreement-signed/ (the unslashed-ending URL 301-redirects here) | HTML | Free | As above | Provenance: confirms the CBA text is available at NBA.com/official and NBPA.com |
| 3a | 2026-27 NBA Rulebook ("Official NBA Playing Rules") | NBA | File Last-Modified 2026-10-05 (the 2026-27 season begins 2026-10-20 per a third-party guide) | https://ak-static.cms.nba.com/wp-content/uploads/sites/4/2026/10/2026-27-Official-NBA-Playing-Rules.pdf (200, 1,671,437 bytes). HTML by rule: https://official.nba.com/rulebook/ (Rule 1 to Rule 14, plus "Comments on the Rules" https://official.nba.com/comments-on-the-rules/) | PDF + HTML | Free | NBA all-rights-reserved footer. AMBER. | Rules questions: shot clock, goaltending, flagrant fouls, coach's challenge, replay triggers |
| 3b | 2026-27 NBA Officials Guide (dated 10-14-26) | NBA | File Last-Modified 2026-10-14 | https://ak-static.cms.nba.com/wp-content/uploads/sites/4/2026/10/2026-27-NBA-Officials-Guide-10-14-26.pdf (200, about 10.3 MB) | PDF | Free | AMBER. Mostly officials roster/bios; I did not read it for case-play content. | Officiating context only |
| 3c | NBA Video Rulebook; Replay Center (Replay Archive, Replay Triggers) | NBA | Live sites | https://videorulebook.nba.com/ ; https://official.nba.com/replay/archive/ ; https://official.nba.com/replay/triggers/ (all 200) | HTML / video | Free | Video content: do not ingest; link out. Triggers page is text and AMBER. | "What can be reviewed in the last two minutes?" |
| 3d | NBA "Case Book" | - | - | **No public NBA Case Book found** (search surfaced only NCAA and softball casebooks) | - | - | [UNVERIFIED] it does not exist behind a login; I found none on official.nba.com | Do not promise case-play answers |
| 4a | Stat Glossary, Stats, NBA.com | NBA | Undated live page; `stats.nba.com/help/glossary` 301s to this URL | https://www.nba.com/stats/help/glossary (200). About 280 terms; each has Name, Definition, Formula (some), Type, Contexts | HTML | Free | Page shows no copyright or terms text itself. NBA.com Terms of Use (last updated 2026-07-13): content use needs written permission, personal non-commercial downloads allowed; NBA statistics limited to news reporting or private non-commercial use with attribution, and not for databases of regularly updated statistics without consent. The Terms I read do not mention scraping or AI training explicitly. AMBER. | Definitions: "What is PIE?", "eFG% vs TS%", "What does DEFRTG measure?" Only the definitions and formulas, never the stat values |
| 4b | NBA Terms of Use | NBA | Last updated 2026-07-13 | https://www.nba.com/termsofuse (200) | HTML | Free | Governing terms for 2, 3, 4, 9 | Read before any public launch |
| 5a | NBA Draft Lottery: How it works, odds and where to watch | NBA | Page last updated 2026-06-01 | https://www.nba.com/news/nba-draft-lottery-explainer (200). 2026 rules: 14 teams, 4 balls from 14, 1,001 combinations with 1,000 assigned; top three teams 14.0% each. Notes the **new lottery system approved 2026-05-28 for the 2027 draft**, details not on the page | HTML | Free | AMBER | Lottery mechanics, but flag "changes in 2027" |
| 5b | 2026 NBA Draft: how to watch, selection order, history | NBA | Updated 2026-06-23 | https://www.nba.com/news/nba-draft-faq (200). Early entry deadlines (apply by April 24, withdraw by June 13) and order-of-selection rules; no eligibility criteria on the page | HTML | Free | AMBER | Draft order explanations |
| 5c | CBA Article X "Player Eligibility and NBA Draft" (CBA p. 296 onward), Article VIII "Rookie Scale", Article II Section 11 "Two-Way Contracts" (pp. 50-58), Section 9 "10-Day Contracts" | NBA/NBPA | Inside 1a/1b | Same PDFs | PDF | Free | As 1 | Authoritative draft eligibility, two-way and Exhibit-10-style rules. **No stand-alone Exhibit 10 document found**; Exhibit 10 is a contract-rider concept, summarised only by secondary sources (Hoops Rumors) |
| 6a | Basketball-Reference Glossary | Sports Reference LLC | Page footer: "Copyright 2000-2026 Sports Reference LLC"; 73 terms | https://www.basketball-reference.com/about/glossary.html (200) | HTML | Free | Sports Reference Terms of Use (https://www.sports-reference.com/termsofuse.html, last updated 2023-05-19) forbid using content for training, fine-tuning, prompting or instructing AI models, including generating answers. robots.txt: GPTBot disallowed, Crawl-delay 3. **RED.** | Link out only. Write Pivot's own definitions from NBA.com glossary instead |
| 6b | BPM 2.0 (includes VORP), Win Shares, Ratings, PER, Four Factors explainer pages | Sports Reference LLC | Live pages | https://www.basketball-reference.com/about/bpm2.html ; /about/ws.html ; /about/ratings.html ; /about/per.html ; /about/factors.html (all 200). `/about/vorp.html` is **404**; VORP is explained inside bpm2.html | HTML | Free | As 6a. RED. | Link out. Underlying public sources for the methods are Daniel Myers's BPM write-up and Dean Oliver's work |
| 7a | Basketball on Paper: Rules and Tools for Performance Analysis (Dean Oliver) | Potomac Books (Brassey's imprint in some records) | Hardcover 2003-11-09; paperback 2004 (ISBN-13 9781574886887, 392 pp. per listings) | No publisher landing page verified; listings on AbeBooks and a library catalog | Print | Paid (used copies seen at about US$7; list price not found) | Fully copyrighted book. RED for ingestion. | Cite only as a pointer; do not embed |
| 7b | MIT Sloan Sports Analytics Conference research papers (archive; Previous Finalists 2012-2025) | MIT Sloan SAC (42 Analytics) | Papers by year, e.g. 2010, 2012, 2013 | Index: https://www.sloansportsconference.com/past-conferences (200); competition and finalists: https://www.sloansportsconference.com/research-paper-competition (200); each paper at `/research-papers/<slug>` | HTML landing + PDF/DOCX | Free | Site notice "(c) Copyright MIT Sloan Sports Analytics Conference. All Rights Reserved." Competition page: authors keep ownership and grant the organizer rights to publish for conference use; no open licence stated; submissions must link open-source code. AMBER: link, cite, summarise; do not mirror PDFs. | Methodology context |
| 7c | "Improved NBA Adjusted +/- Using Regularization and Out-of-Sample Testing" (Joe Sill, 2010) | SSAC 2010 | 2010 | Page https://www.sloansportsconference.com/research-papers/improved-nba-adjusted-using-regularization-and-out-of-sample-testing (200); PDF link on that page (cdn.prod.website-files.com ... SSAC10 - Basketball_Sill.pdf) | PDF | Free | As 7b | The RAPM origin paper: "what is RAPM and why ridge regression?" |
| 7d | "CourtVision: New Visual and Spatial Analytics for the NBA" (Kirk Goldsberry, 2012) | SSAC 2012 | 2012 | https://www.sloansportsconference.com/research-papers/courtvision-new-visual-and-spatial-analytics-for-the-nba (200) | PDF | Free | As 7b. I did **not** find a document titled "Court Counts" ([UNVERIFIED] title). | Shot-location/spatial methodology |
| 7e | arXiv NBA papers, e.g. 2303.04963 "Predicting Elite NBA Lineups Using Individual Player Order Statistics"; 2601.15000 "Lineup Regularized Adjusted Plus-Minus (L-RAPM)"; 2608.19454 "Dummy RAPM..."; 1405.0231 "Characterizing the spatial structure of defensive skill in professional basketball"; 1703.07030 "An Investigation of Three-point Shooting through an Analysis of NBA Player Tracking Data"; 2604.06548 "A Rolling-Horizon Stochastic Optimization Framework for NBA Franchise Management..." | Authors via arXiv | Submitted 2023-03-09; 2026-01-21; 2026-08-19 (v2 2026-08-21); 2014-05-01 (v3 2015-05-28); 2017-03-21; 2026-04-08 | https://arxiv.org/abs/<id> for each (all 200). Licence per page: **CC BY 4.0** for 2303.04963, 2601.15000, 2608.19454; **arXiv non-exclusive distribution licence** for 1405.0231, 1703.07030, 2604.06548 | PDF/HTML | Free | arXiv Terms of Use: metadata is CC0; full-text e-prints retain author copyright, so redistribution depends on each paper's licence; do not exceed 1 API request per 3 seconds. GREEN for CC-BY papers; AMBER otherwise. | Method explanations and citations ("how is RAPM different from BPM?") |
| 8a | USA Basketball / Atlanta Hawks free coach e-learning; FIBA coaching practice plans | USA Basketball, FIBA, Atlanta Hawks Basketball Academy | FIBA PDF file dated 2024-09-23 (30.6 MB) | https://basketballacademy.hawks.com/coachesresources/ (200); https://assets.fiba.basketball/image/upload/documents-corporate-wabc-start-coaching-eng-practice-plans.pdf (200, about 30.6 MB, not downloaded); https://www.usab.com/ (200) | Web / PDF / video | Free (per search summaries) | Youth-coaching, not scouting. Terms unread. AMBER. | Fundamentals only; low priority |
| 8b | NBA Coaches Association (NBCA) | NBCA | Live site | https://www.nbacoaches.com/ (WebFetch 200; plain `curl` got HTTP 403) | Web | Free pages only | Public content is news, directories and awards; **no coaching manuals or scouting methodology found public**. | Skip; "who won Coach of the Year" is structured data anyway |
| 8c | Scouting-report methodology | - | - | No authoritative public document found | - | Typically paywalled | [UNVERIFIED] | Do not build around this |
| 9 | Team media guides, e.g. 2025-26 New Orleans Pelicans and Oklahoma City Thunder | Individual teams, hosted on cdn.nba.com | Uploaded 2025-10-20/21 | https://cdn.nba.com/teams/uploads/sites/1610612740/2025/10/2025-26-Pelicans-Media-Guide-v2.pdf (200, 9,292,200 bytes); https://cdn.nba.com/teams/uploads/sites/1610612760/2025/10/Thunder-Media-Guide-2025-26_WEB.pdf (200, 10,872,536 bytes) | PDF, large | Free | A search summary said the Pelicans guide carries "(c) 2025 New Orleans Pelicans" and personal/editorial-use terms; I could not read the PDF's copyright text myself ([UNVERIFIED]). **No 2026-27 guides found yet** (searched 2026-10-18). Only two of 30 teams verified. AMBER/RED. | Mostly numbers (records, rosters, bios). Treat as structured-data inputs or link out. Prose bios are the only RAG-able part |
| 10 | NBA history and records pages; Official NBA Guide / Official NBA Basketball Encyclopedia | NBA.com; historically Sporting News / Villard | Live HTML pages; print books (2004-05 Guide; 1989 Encyclopedia) | https://www.nba.com/history (200) links to all-time records, awards, champions, season recaps, draft history, All-Star history (pages under nba.com/news/history-...). No public PDF record book found. | HTML / print | Free (web), paid (print) | HTML AMBER; books copyrighted RED | Historical narrative ("how did the apron rules begin"); records themselves are structured data |

### Other official items on official.nba.com (listed, not deeply examined)
2026-27 NBA Concussion Policy Summary, Officiating Staff, Officials Headshots, Undergraduate Advisory Committee brochure (2026-03), Last Two Minute Reports and Team Injury Reports. The last two are recurring data/report feeds and belong with the structured pipeline rather than the document store.

### Legal and AI-ingestion summary (not legal advice)
- **NBA-owned material** (rulebook, CBA copy on nba.com, press releases, glossary): free to read, "all rights reserved". Terms of Use I read grant personal, non-commercial use and require written permission for other use; they restrict NBA statistics from regularly updated databases. They do not mention scraping or AI explicitly. A retrieval system that embeds the text and returns short cited quotes with a link is technically plausible, but because Pivot is a public web product, ask the NBA in writing before shipping the text itself to public users. Until then, ingest for retrieval internally, quote briefly, and link out.
- **CBA**: a public labour agreement the parties themselves post; low risk in practice, but the NBA's host carries the footer above. The NBPA host shows no notice on its landing page.
- **Sports Reference**: explicit AI prohibition. Do not ingest.
- **Sloan papers**: authors own them; no open licence. Index and link; summarise in your own words.
- **arXiv**: use the licence shown on each paper. Store `license` per document. CC BY 4.0 is the safest.
- **Books and cbafaq**: not ingestible without permission.

---

## Part 3. Documents for retrieval vs numbers for services

| Goes into the RAG store (prose, rules, definitions, method) | Goes into structured services (numbers that change) |
|---|---|
| CBA articles and sections (text only) | Cap/tax/apron/MLE figures per season (press releases 2a, 2b) |
| CBA 101 narrative | Player salaries, contracts, cap holds |
| Rulebook rules and "Comments on the Rules" | Rookie scale, minimum salaries, two-way salary amounts (CBA Exhibits B and C give baselines, but yearly amounts are derived) |
| Stat glossary definitions and formulas | Player and team stat values |
| Draft lottery mechanics (prose) | Lottery odds table, draft order, pick ownership |
| History narrative | Awards winners, records, champions |
| CC BY and permitted papers: method descriptions | Team media guide tables |
| Press-release prose (headline fact only, pointing to the table) | Injury reports and Last Two Minute Reports |

Rule of thumb: if the answer is a number that expires on 1 July each year, it is not a document question.

---

## Part 4. Future retrieval architecture for Ask Pivot (modest, no vendor lock-in)

### 4.1 Pipeline
1. **Registry first.** A `documents` table (SQLite/Postgres) holds one row per source file, before any chunking.
2. **Fetch and fingerprint.** Download with a polite fixed User-Agent, store the original file on local disk or any S3-compatible bucket, compute SHA-256 of the bytes.
3. **Parse by structure, not by size.** Use text extraction plus the table of contents: the CBA has Articles, Sections, lettered subsections (e.g. Article VII Section 2(e) "Operation of Apron Levels", pp. 186-195). Ghostscript's `txtwrite` gave a clean TOC and headings in my test; a better parser (PyMuPDF, pdfplumber) will do tables and page numbers more reliably.
4. **Chunk per clause.** Target one chunk per lettered subsection or numbered paragraph, merged up to roughly 300 to 700 tokens, never crossing a Section boundary. Each chunk carries its heading path ("Art. VII > Sec. 2 > (e) Operation of Apron Levels") in the embedded text. Keep a parent pointer so the generator can pull the whole Section when a clause refers back ("subject to Section 6(h)").
5. **Embed locally** (Ollama embedding model), store vectors with SQLite `sqlite-vec` (MIT/Apache, version 0.1.9 on PyPI 2026-10-18) or Postgres `pgvector` (0.5.1 Python client). Both are open and portable. Pair with a lexical index (SQLite FTS5 or Postgres full-text) because clause numbers, defined terms ("Qualifying Offer", "Apron Level") and acronyms retrieve better lexically; fuse the two result lists.
6. **Never store numeric tables as chunks.** See 4.6.

### 4.2 Citation provenance (store on every chunk and echo in every answer)
`doc_id`, `doc_title`, `publisher`, `doc_version` (human label, e.g. "CBA July 2023"), `content_sha256` (file hash), `chunk_id`, `chunk_sha256` (hash of normalised chunk text), `section_path`, `page_start`, `page_end`, `source_url`, `retrieved_at`, `effective_from`, `effective_to`, `supersedes`, `license_class` (GREEN/AMBER/RED), `authority_tier` (see 4.5), `numeric_content` (bool).

Answer UI: show "CBA (July 2023), Art. VII Sec. 2(e), pp. 186-195; nba.com PDF; retrieved 2026-10-18; sha256 cf59d43f..." and make quotes short. Verified example hashes from this research are in the inventory.

### 4.3 Versioning and effective dates
- Two distinct clocks: **document version** (what text we hold) and **rule effective period** (when it applies). Keep both.
- 2023 CBA: `effective_from 2023-07-01`, `effective_to 2030-06-30` nominally (runs through 2029-30; opt-out rights after 2028-29, per nbpa.com/cba). Do not infer effective date from file Last-Modified: the NBPA copy's CDN timestamp is 2026-06-29, three years after signing.
- If a side letter or amendment appears, add it as its own document with `amends: <doc_id>` and `section_path` targets; retrieval should surface both and prefer the later in the same section. Today I found no public amendments, so the registry starts with none.
- Cap-year-specific content: key everything by `season` (e.g. "2026-27"). Press releases create rows in the structured table; prose chunks that quote figures (CBA 101's 2024-25 levels) are tagged `numeric_content=true`, `season=2024-25`, and are excluded from numeric answers.
- Rulebook: one document per season (`2026-27`); keep earlier seasons with `effective_to` set, so "what was the rule in 2019" can be answered when asked, defaulting to the current season otherwise.
- Draft lottery: model the 2027 change explicitly: current rule `effective_to 2026 draft`; new rule `effective_from 2027 draft` (approved 2026-05-28), with `details_published = false` until the NBA posts ball counts. The assistant should say the new details are not yet in the corpus rather than guess.
- Refresh jobs: re-fetch rulebook and the nba.com explainers in September to October yearly; press releases around 30 June; compare hashes and open a review item on change instead of auto-replacing.

### 4.4 Temporal relevance at query time
Parse "as of" cues ("this season", "in 2025-26", "after the 2027 draft") into a target date; default to today (2026-10-18). Filter chunks to `effective_from <= date < effective_to` before ranking. If the question's date falls in a gap or on a superseded document, say so.

### 4.5 Conflict and precedence rules
Order of authority for rules (highest first):
1. 2023 CBA text and any official amendment (binding contract).
2. NBA Constitution and By-Laws (league governance).
3. Official NBA Playing Rules for on-court questions (CBA does not cover them).
4. Official NBA/NBPA statements and NBA Communications releases for yearly figures (these implement CBA formulas; if a release conflicts with the CBA formula, flag a discrepancy for human review, do not silently choose).
5. NBA-prepared summaries (CBA 101), explicitly labelled secondary.
6. NBA.com explainers and glossary (definitions).
7. Peer-reviewed or conference papers (method context, never rules).
8. Third-party commentary (never ingested as authority).

Within the same tier prefer the later effective version. When two retrieved chunks disagree, present both with citations and say which takes precedence; never merge.

### 4.6 Keeping numerics out of the vector store
- Tag at ingestion: any chunk where digits/currency exceed a threshold, or inside CBA Exhibits B/C/D, tables, or "salary cap" schedules, gets `numeric_content=true` and is not embedded; instead it is stored raw with a pointer.
- Cap-year numbers come from a typed table (`season, cap, tax, apron1, apron2, min_team, mle_*`) populated from the press releases, with `source_doc_id` pointing at the registry. Ask Pivot calls the service for numbers and the retriever for clauses, then composes.
- **Generation guard:** after drafting, extract every number, percent and dollar value from the answer; each must match either a service result in the same turn or a quoted chunk with citation. Otherwise strip it or abstain. Percent formulas in clauses (e.g. cap growth limits) are allowed as quoted rule text with a "formula, not a current dollar amount" note.
- Pull numbers out of embeddings because embedded numbers go stale silently, retrieve poorly, and cannot be audited.

### 4.7 Abstention behaviour
Abstain (and say what is missing) when: top retrieval score below a calibrated threshold; the only supporting chunks are `numeric_content` or RED licence; the question needs a document not in the registry (NBA Case Book, scouting reports); the target date has no effective document (e.g. new lottery odds); or the sources conflict and precedence is unclear. Offer a link to the primary source instead. Always return "not legal or contractual advice" on CBA interpretation questions.

### 4.8 Evaluation
Keep a small golden set (30 to 50 questions per document family with expected section citations) and run it on each corpus change. Track citation accuracy and abstention correctness, not just answer text.

### 4.9 Example Ask Pivot questions by document
- **CBA:** What is the difference between the first and second apron restrictions? (Art. VII Sec. 2(e).) When does a Bird right apply? How do two-way contracts work and how many games can a two-way player be active? (Art. II Sec. 11.) What are draft eligibility rules? (Art. X.) What are the rules on aggregating salaries in trades?
- **CBA 101:** Explain the non-taxpayer mid-level exception in plain language. What is the draft pick penalty? (Section F.)
- **Cap releases (via structured table, not RAG):** What are the 2026-27 tax and apron levels? What changed from 2025-26?
- **Rulebook:** When is a defensive three-second violation called? How does the coach's challenge work (Rule 14)? What can be reviewed in replay (Rule 13)?
- **Glossary:** What is the difference between eFG% and TS%? What does PIE measure? Define usage percentage.
- **Draft pages:** How does the lottery draw the top four picks? What changes for the 2027 draft?
- **Papers (CC-BY or linked):** What is regularised adjusted plus-minus and why is it regularised? How do published papers evaluate lineups from limited minutes?
- **History:** How did the second apron originate? (Needs CBA and press context.)

---

## Part 5. Category F: infrastructure (favour free tiers; stay simple)

All figures fetched 2026-10-18 from the cited pages. Where a page omitted something it is marked.

| Need | Option | Price / limits (cited, retrieved 2026-10-18) | Recommendation |
|---|---|---|---|
| Managed Postgres | **Neon** | Free: $0, 1 GB Postgres storage per project (20 GB across projects), 100 CU-hours per project per month, 100 projects, 10 branches, scale-to-zero after 5 min (not disableable), 5 GB egress per project. Launch plan is usage-based with no monthly minimum: $0.106/CU-hour, $0.35/GB-month storage, 500 GB egress included then $0.10/GB. Source: https://neon.com/pricing | Best free fit for staging and low-traffic prod. Cold starts matter for Ask Pivot latency; paid plan lets you turn scale-to-zero off. |
| Managed Postgres | **Supabase** | Free: 500 MB database, 2 active projects, 5 GB egress, 1 GB file storage (50 MB max upload), **paused after 1 week of inactivity**. Pro starts at $25/month. pgvector on free plan: the pricing and pgvector doc pages I fetched do not say [UNVERIFIED]. Source: https://supabase.com/pricing | Weaker free tier than Neon for a data product (500 MB, pausing). Skip unless you want its auth and storage. |
| Managed Postgres / hosting | **Railway** | Free: $0 with $1 monthly credit, up to 1 vCPU / 0.5 GB per service. Hobby: $5/month including $5 usage. Pro: $20/month. Usage: RAM about $10/GB-month, CPU about $20/vCPU-month, volume $0.15/GB-month, egress $0.05/GB. Source: https://railway.com/pricing | Reasonable for hosting the FastAPI app and a small Postgres in one place at about $5 to $15/month. Not needed yet. |
| Object storage | **Cloudflare R2** | Free monthly: 10 GB-month storage, 1M Class A ops, 10M Class B ops; egress free. Beyond: $0.015/GB-month Standard, Class A $4.50/M, Class B $0.36/M. Source: https://developers.cloudflare.com/r2/pricing/ | Best option when you need object storage: S3-compatible API, no egress fees. For raw source PDFs (a few hundred MB) free tier is ample. |
| Object storage | **AWS S3** | New customers: "up to $200 in AWS Free Tier credits", free plan for 6 months. GET $0.0004/1,000 requests; first 100 GB/month internet egress free. S3 Standard per-GB rate not captured from the page [UNVERIFIED]. Source: https://aws.amazon.com/s3/pricing/ | Not necessary. Credits expire; stay with local disk or R2. |
| Local files | Local disk / repo `data/raw` with hash manifest | $0 | Enough today: the whole document set above is under about 30 MB if you skip the FIBA PDF and media guides. |
| Orchestration | **APScheduler** (already used; MIT, v3.11.3) | $0 | **Keep.** Add a tiny `ingest_runs` table and make each job idempotent. |
| Orchestration | **Cron on a small VM** | VM cost only (e.g. Railway Hobby above) | Fine if you outgrow in-process scheduling. |
| Orchestration | **GitHub Actions scheduled workflows** | GitHub Free: 2,000 minutes/month for private repos, 500 MB artifact storage, 10 GB cache per repo; public repos free; Linux 2-core $0.006/minute after. Source: https://docs.github.com/en/billing/concepts/product-billing/github-actions. Scheduled-workflow limitations were not on the page I fetched [UNVERIFIED]. | Good free option for weekly document re-fetch and hash checks (a few minutes per run). |
| Orchestration | **Prefect Cloud** | Hobby: free, 2 users, 5 deployments, 500 minutes Prefect Serverless compute/month, 7-day run retention. Starter $100/month. Source: https://www.prefect.io/pricing | **Do not adopt yet.** Free tier works, but adds a second system for a handful of jobs. |
| Orchestration | **Dagster** | Solo $10/month plus $0.040 per credit, 30-day trial; Starter $100/month. Source: https://dagster.io/pricing | Do not adopt yet. The page did not mention self-hosting [UNVERIFIED]. |
| Error reporting | **Sentry** | Developer (free): 5k errors, 1 user, 30-day lookback, 5M spans, 50 replays (period not stated on page). Team from $29/month monthly ($26 annual). Source: https://sentry.io/pricing/ | **Adopt the free plan** for backend and Next.js frontend errors. |
| Monitoring / uptime | **Better Stack** | Free: 10 monitors and heartbeats combined, 1 status page, Slack/email alerts only, 3 GB logs and traces (3-day retention), 100,000 exceptions/month. Paid responder seat $34/month monthly. Source: https://betterstack.com/pricing | Good free choice for uptime plus heartbeats on ingestion jobs (a missed heartbeat tells you the scheduler died). |
| Monitoring / uptime | **UptimeRobot** | Free: 50 monitors, 5-minute interval; page says "Good for hobby and non-profit projects" (commercial restriction not explicit). Solo $12/month annual. Source: https://uptimerobot.com/pricing/ | Alternative; use Better Stack to avoid the hobby-only wording. |
| Data quality | **Pydantic v2** (MIT, 2.14.1) | $0 | **Primary.** Validate every ingested record and every API model at the boundary. Already in the FastAPI stack. |
| Data quality | **Pandera** (MIT, 0.34.1, released 2026-10-06) | $0 | **Add** for DataFrame-level checks (ranges, uniqueness, cap-year consistency) on stats and cap tables. Lightweight. |
| Data quality | **Great Expectations** (Apache-2.0, 1.24.0, released 2026-10-07) | GX Cloud pricing page lists no free-tier limits or prices; mentions a free Developer option [UNVERIFIED]. Source: https://greatexpectations.io/pricing/ | Do not adopt yet: heavy for the current scale. Revisit if you need docs-as-data-contracts and stakeholders. |
| Caching | **Redis** (already optional) | Upstash free: 500K commands/month, 250 MB; pay-as-you-go $0.20 per 100K commands. Source: https://upstash.com/pricing/redis | Keep Redis optional. In-process LRU plus HTTP caching is enough. If needed, local Redis in the same container, not a paid service. |
| Experiment tracking | **MLflow** (open source, local; Apache licence classifier on PyPI, 3.17.1) | $0. `mlflow server --port 5000` uses SQLite by default (from MLflow 3.7.0); can move to Postgres and S3-compatible storage later. Source: https://mlflow.org/docs/latest/self-hosting/ | **Adopt local MLflow** if and when you tune models. |
| Experiment tracking | **Weights & Biases** | The old W&B pricing URL 301-redirects to https://coreweave.com/forge-pricing. Free: $0, 5 model seats, 5 GB/month storage, 1 GB Weave ingestion; "Designed for personal development". Pro from $60/month. | Do not adopt; no need yet. |
| Cost monitoring | Provider dashboards plus a monthly spend budget alert, and token/latency logging in Pivot's own DB | $0 | Enough. No dedicated tool. |

### Do not buy yet (list)
- Any vector database service (Pinecone, Weaviate Cloud, etc.): `sqlite-vec` or pgvector in the existing database covers a corpus of fewer than 5,000 chunks.
- Prefect Cloud paid, Dagster Cloud, Airflow hosting.
- Great Expectations Cloud; Weights & Biases Pro; Sentry Team; Better Stack paid seats.
- Supabase Pro / Neon paid plans until storage, compute hours or cold-start latency actually bite.
- Railway Pro, any Kubernetes or managed Redis.
- Basketball on Paper (print) or any commercial stats licence for the document store; commercial stat feeds belong to a different workstream.
- Paid NBA data/license until a lawyer or NBA contact confirms the position on public redistribution.

### Suggested minimal stack now
Existing Postgres/SQLite + APScheduler + Pydantic + Pandera; add Sentry free, Better Stack free (uptime + heartbeats), R2 free only if raw documents must live off-box; GitHub Actions weekly re-fetch job for the document registry; local MLflow when needed.

---

## Part 6. What I could not verify

- **Amendments or side letters to the 2023 CBA:** none appear on nbpa.com/cba or official.nba.com, but I cannot prove none exist.
- **Whether CBA 101 has a disclaimer** that it is non-binding; I read only the cover and table of contents.
- **cbaweb.nbpa.org:** TLS handshake failed (unrecognized name), so I could not check that site; `nbpa.com/cba` is the working landing page.
- **cbafaq.com HTTPS:** certificate is invalid, so only the HTTP page was read.
- **NBA Case Book:** no public document found; its existence is unverified.
- **Exhibit 10 as a stand-alone public form/document:** not found; only secondary descriptions.
- **Official 2026-27 values for rookie scale, two-way salary, minimum contracts, bi-annual exception:** not in the cap press release; not located elsewhere.
- **New draft-lottery system details** (ball counts and odds for the 2027 draft): the NBA explainer says it was approved 2026-05-28 but does not list the details.
- **NBA.com Terms of Use on scraping and AI:** my read of the page did not find explicit language, but I read a summarised extract; a full legal read is needed.
- **Team media guide terms:** the copyright and editorial-use text was reported by a search summary but I could not read it in the PDF; only two team guides were verified; no 2026-27 guides found.
- **"Court Counts" (Goldsberry):** no document with this exact title found; I located "CourtVision" (SSAC 2012) instead.
- **Sloan paper licences:** no per-paper licence stated; whether the competition page is the only legal statement is unconfirmed.
- **NBA Coaches Association, scouting-report methodology, Official NBA Guide / Encyclopedia public editions:** none found freely public.
- **Dean Oliver's book list price:** not found (resale prices only).
- **Supabase free tier pgvector:** not stated on the pages fetched.
- **Neon page date:** the pricing page carries no date (its own note points to the plans docs for confirmation).
- **S3 per-GB storage rate;** **Prefect and Dagster free-tier limits on schedules/runs;** **Sentry free quota period (monthly assumed);** **GitHub Actions scheduled-workflow caveats;** **Great Expectations Cloud free limits.**
- **All pricing** reflects the day fetched and can change.
