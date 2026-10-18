# R8 — External resource research

*Retrieved 2026-10-18. Detailed, cited working notes (with every URL and retrieval date) are the four
appendices in [research/](research/); this document is the synthesis and the decisions. Not legal
advice: every rights statement is a lead for counsel. "Verified" below means a person (the author or
the research agent) read it on the vendor's own page that day; **vendor-advertised** means marketing or
documentation not tested; **third-party** means a blog or snippet.*

## 0. The finding that outranks the rest

**Pivot's primary data source is not licensed for what Pivot is becoming.** The author read the primary
text of the NBA.com Terms of Use (https://www.nba.com/termsofuse, "Last updated July 13, 2026"). §9
allows NBA statistics only "for legitimate news reporting or private, non-commercial purposes", forbids
use "in connection with any fantasy game or other commercial product or service", forbids products that
present "archived play-by-play", and forbids use "with any website, product, or service that features a
database … of comprehensive, regularly updated statistics from NBA, WNBA or G-League games … without
the Operator's express prior consent". §1 requires written permission to republish or publicly display
"statistics … logos … photos". The `nba_api` package is MIT-licensed — that licenses the *client code*,
not the data it fetches. A private development tool is plausibly inside those terms; a commercial
decision-intelligence product serving a stored NBA statistics database is, on a plain reading,
inside the clauses that require consent.

Three further facts compound it:

* **Reliability.** nba_api issue #696 (opened 2026-10-08, closed 2026-10-10, root cause not stated) reports
  every stats.nba.com endpoint returning an Akamai HTML page with HTTP 200; cloud-host blocking has
  been reported since 2023. [third-party]
* **Basketball-Reference** (the source of Pivot's contract snapshot and 565 historical trades) prohibits,
  in its Terms of Use (last updated 2023-05-19), using its content to "train, fine-tune, prompt or
  instruct" AI models, and bars building a competing database. Ask Pivot is an LLM product. [verified]
* **No source verified delivers a licensed, structured contract feed** (per-year salary + options +
  guarantees + contract type + signed date + no-trade flag). Details in §C.

None of this stops development. It determines what Pivot may *show to the public*, and it is the first
thing to resolve before a public beta. **Recommended first action: a written request to the NBA for
consent under §9 (or a written confirmation that Pivot's intended use is outside it), and a conversation
with counsel.** That costs nothing but time and decides everything downstream. See
[owner-resource-checklist.md](owner-resource-checklist.md).

## 1. Evaluation framework (all promising resources)

Columns: Cost · Coverage · Quality · Access · Rights (Dev / Public display / Commercial / AI) ·
Integration · Benefit to Pivot · Priority · Status. Full rows with sources are in the appendices; this is
the consolidated view.

### A. NBA statistical APIs

| Resource | Cost | Coverage / quality | Rights (Dev / Display / Comm / AI) | Integration | Priority | Status |
| --- | --- | --- | --- | --- | --- | --- |
| **NBA.com + `nba_api`** (current) | free | deepest: box, advanced, hustle, tracking, PBP, shot charts, lineups, draft combine; unofficial, unstable, cloud IPs reported blocked | Dev: private non-commercial only · Display/Comm: **needs NBA consent (§9)** · AI: ToU silent | built | keep for private dev; plan replacement | Verified (terms) |
| **BALLDONTLIE GOAT** | **$39.99/mo**, $383.90/yr; ALL-STAR $9.99; free tier exists | season/team averages by category, advanced v2 incl. tracking/hustle, contracts + aggregates, injuries, starters, PBP | BDL grants commercial **and AI/ML** use, no attribution, no resale/competition; **explicitly does not clear league/team/publicity IP** | adapter built | **next — trial-gated** | Verified from its docs; live behaviour unverified |
| **SportsDataIO** | quote-only (third-party guess $500–1,000+/mo, unverified); Discovery Lab $99–149/mo personal-only | scores, box, PBP, lineups, injuries, transactions; licensed **headshots** advertised; no contract endpoint found | commercial licence quote-based; says caching/derived display/ML can be licensed; images not covered by the rights FAQ | medium | **ask for a quote** (the best-documented rights path) | Permission-required |
| **MySportsFeeds** | from **CAD $29/mo** commercial (non-live CORE); 14-day trial advertised | schedules, scores, box, PBP, lineups, injuries; 2 seasons included, older $19–69 one-time | **data licence terms not retrieved** (only website terms read) | low–medium | trial + read terms | Trial-required |
| Sportradar / Stats Perform / Genius | enterprise, unpublished | official-grade; Sportradar images via Getty/Imagn | contract only; Sportradar's published addenda restrict to editorial/non-commercial and exclude photos/logos/NIL | high | future | Permission-required |
| API-Sports, Highlightly | ~$7–35/mo (prices conflict; pages 403/404) | scores/standings/stats, depth unclear | not verified | low | skip until terms readable | Unverified |
| **ESPN hidden endpoints** | free | scores, some PBP | **unlicensed** — Disney terms bar bots, data mining, AI, business use | — | **avoid** | Verified (restrictive) |

### B. Historical and research data

| Resource | Cost | What it offers | Rights | Priority |
| --- | --- | --- | --- | --- |
| Kaggle `wyattowalsh/basketball` (already a Pivot input) | free, 4.66 GB | 1946-47→, 65k games, 13M+ PBP rows | labelled CC BY-SA 4.0, but derived from stats.nba.com — the uploader cannot grant NBA's rights; share-alike may conflict with Pivot's own licensing | **dev only** |
| hoopR / sportsdataverse, `shufinskiy/nba_data` | free | ESPN- or NBA-sourced PBP/box/shots, 1996/2002→ | code MIT/Apache; data inherits ESPN/NBA terms | dev only (backtests) |
| pbpstats (site/API/lib) | free | lineups on floor, possessions, on/off, WOWY | code MIT; data terms **not found**; inherits NBA ToU | ask the maintainer |
| Dunks & Threes (EPM) | subscription (price not visible) | EPM, skills, projections; API 90 req/min | API personal/internal; **commercial-decision models need a written agreement** | benchmark, ask |
| BBall-Index (LEBRON, 12 offensive roles) | **$5/mo or $52.50/yr** | the best archetype/role reference; shot charts to 1996; lineups to 2013-14 | private subscriber use; redistribution not authorised | subscribe privately as a benchmark |
| DARKO, nbarapm, Inpredictable, CTG | free/unclear | projections, RAPM, win probability | terms not found — ask | benchmarks/backtests only |
| Basketball-Reference / Stathead | Stathead $9/mo | BPM/VORP since 1973-74 | bulk ingest and AI use **prohibited** | link out; reimplement BPM from licensed box scores |

What each supports: impact modelling (PBP/lineups/RAPM), projection backtesting (any multi-season box
data), archetypes (BBall-Index roles as the benchmark; tracking/shot-zone families), roster fit
(lineups/WOWY), trade comparables (transactions), optimisation (contracts + rules). **None of the
redistributable-in-product status is verified for any dataset; all are private-development inputs until
the NBA question in §0 is settled.**

### C. Contracts, cap, transactions

* **Cap/tax/apron numbers are solved and free.** The NBA's own releases (pr.nba.com, 2025-06-30 and
  2026-06-30) give 2026-27: cap $164.961M, tax $200.428M, first apron $209.015M, second apron
  $221.686M, minimum team salary $148.465M, NT/Taxpayer/Room MLE $15.044M/$6.064M/$9.366M. They match
  Pivot's hand-curated YAML (`backend/app/config/cap_rules/2025-26.yaml`, `2026-27.yaml` — checked field by field; note the 2026-27 file cites a Spotrac article as its `source_url`, which should become https://pr.nba.com/2026-27-salary-cap/) and pass the CBA formula checks (tax = 121.5 % of
  cap, minimum = 90 %, aprons scale with the cap). [verified]
* **The 2023 CBA is public and free** (676 pp.; NBPA and official.nba.com copies, identical text).
  Larry Coon's cbafaq.com is retired and never updated for 2023; the NBA's **CBA 101** (34 pp., Nov 2024)
  is the free substitute.
* **No verified licensed structured contract feed.** Spotrac has the best content (per-year base, cap
  hit, incentives, guarantee/option notes, cap holds, trade exceptions) but its live terms could not be
  read (HTTP 402) and a snippet says systematic extraction and revenue-generating use are barred; no
  API/licensing page exists publicly. HoopsHype, RealGM, Pro Sports Transactions could not be fetched.
  BALLDONTLIE advertises contracts (cap hit / base / total cash per year; aggregates with
  guaranteed amounts and contract type) — **unverified live**.
* **Contract data is not a legality engine.** The CBA text shows legality depends on ≥ 15 further inputs:
  apron team salary (not payroll), cap holds, an exception ledger with creation dates, pick ownership,
  protections, frozen picks (§2(f)), the Stepien rule (NBA Constitution §7.03), signing dates and types,
  recently-signed restrictions (§8(d)), roster counts, aggregation rules (§6(j)(4)), deadline timing,
  consent rules. A contracts feed covers about 5 of ≈ 22 rows; the rest is rule text (free), event
  history (Pivot already parses transactions) and curated ownership. Two time-sensitive rules for the
  roadmap: the Board of Governors' 2026-05-28 ban on protecting traded picks landing 12th–15th
  (effective for the 2027 draft; start date for new trades unconfirmed), and a new draft lottery
  approved the same day (details unpublished).
* **No-trade clauses** are near-irrelevant as a feed: CBA Art. XXIV allows them only for 8+ years of
  service and 4+ with the team; one 2026-27 holder is listed (Hoops Rumors). A hand-curated table beats
  any vendor field. Hoops Rumors' trackers (exceptions, TPEs, traded 2027 firsts, frozen picks, veto
  lists) are an excellent **manual QA oracle**; their terms page 404s, so do not ingest.

### D. Player images and team logos (owner priority)

**There is no free, public, redistributable source for current NBA headshots or for team logos as
trademarks.** Verified or read: NBA.com's CDN serves both (observed `200` for one headshot and one logo,
no licence in headers) but the NBA ToU forbids public/commercial reuse without written permission and
"public availability is not a licence"; ESPN's CDN is unlicensed; Getty/NBAE's own NBA terms limit
editorial web use to 30 days / 30 images / no cropping and require separate NBA Entertainment approval
to store or redistribute, and exclude player name/likeness rights; Sportradar's published addenda
exclude photos, logos and player NIL; SportsDataIO advertises *licensed* headshots (via USA TODAY/Imagn,
quote-only) but its own dictionary says its logo/colour fields are "not licensed for public or
commercial use" and its rights FAQ does not address images.

| Option | Cost | Coverage | Rights | Fit |
| --- | --- | --- | --- | --- |
| Current local folders (`nbalogos/`, `nbaplayerimages/`) | sunk | 30/30 logos; ~84 % rostered players | **provenance unknown**; `nbaplayerimages` is a symlink into `~/Downloads` | **local dev only**; now flagged `unverified` in the manifest |
| Generated monogram + abbreviation badge | free | 100 % | no third-party image; colours are a counsel question | **safe default now** |
| Wikimedia Commons photos via Wikidata P18 | free | 2,932 of 4,949 (59 %) Wikidata NBA humans have an image; 60-file sample: all but one CC BY/BY-SA/CC0 (the other PD); median 1,251 px, 19/60 under 800 px; many action shots | attribution mandatory, share-alike on BY-SA; **personality rights not cleared by the copyright licence** | optional closed-beta layer |
| Commons/Wikipedia team logos | free | of 5 checked: Lakers PD (trademark remains), Celtics/Warriors/Nets/Heat **non-free, Wikipedia-only** | not reusable | do not rely |
| SportsDataIO headshots / Sportradar Images | quote / trial (30 days) | current players, media-day based | vendor-licensed; Pivot's use unconfirmed in writing | **request a quote with the ask list** |
| NBA Properties + NBAE/Getty + NBPA group licensing | negotiated | full | three separate rights layers (marks, photos, likeness) | production path; counsel |

The research's full ask list (22 questions for NBA Properties/Entertainment, Getty/NBAE, NBPA, and
vendors) is in the appendix [D](research/D-images-and-logos.md) §4; the owner checklist carries the short
version. Preserve Pivot's own logo and design system — none of this touches them.

### E. Ask Pivot knowledge sources → [ask-pivot-knowledge-sources.md](ask-pivot-knowledge-sources.md)

### F. Supporting infrastructure

Favour what exists. **Keep** APScheduler, Pydantic, optional Redis, SQLite/Postgres. **Add (free):**
Sentry Developer plan, Better Stack free (uptime + job heartbeats), Pandera for DataFrame checks, local
MLflow when tuning. **Optional:** Neon free Postgres (1 GB, scale-to-zero cold starts) or Railway
(~$5–15/mo) for a staging host; Cloudflare R2 free tier (10 GB) only if documents must leave the box;
GitHub Actions scheduled re-fetch. **Do not buy yet:** any vector DB (sqlite-vec/pgvector cover < 5,000
chunks), Prefect/Dagster, Great Expectations Cloud, W&B, paid Sentry/Better Stack seats, Supabase Pro.
Prices fetched 2026-10-18 (see appendix E-F §Part 5).

## 2. Cost scenarios and marginal value

| # | Stack | $/month (advertised) | What the next dollar buys | What it does **not** fix |
| --- | --- | --- | --- | --- |
| 1 | **Free development stack**: nba_api (private), BBRef files (dev only, no LLM), Kaggle nbadb (dev), NBA releases + CBA + CBA 101, Hoops Rumors as manual QA, local images, Commons optional | **0** | a complete private build of R9–R14 on 10 seasons | public display, commercial rights, contracts, injuries, images |
| 2 | **BALLDONTLIE-centred**: stack 1 + GOAT | **39.99** (383.90/yr) | a second, commercially licensed-by-BDL source: contracts, injuries, advanced/tracking families, clutch/defence; a fallback when NBA.com blocks | NBA IP clearance (BDL disclaims it); images; legality inputs; guarantees/options/clauses |
| 2a | stack 1 + ALL-STAR | 9.99 | injuries + game box lines | everything GOAT-gated |
| 3 | **Alternative low-cost**: stack 1 + MySportsFeeds CORE | from ~CAD 29 | an independent commercial feed (terms unread) | same as 2, plus unknown rights |
| 4 | **Future commercially licensed**: SportsDataIO (quote) + NBA §9 consent / licence + Getty/NBAE or SDIO headshots + NBPA + counsel | quote-only; third-party guesses $500–1,000+/mo for data alone (unverified) | the right to ship publicly | — |

Marginal value: **$0** first (a free-tier BDL key and the NBA/counsel questions); **$9.99** next only if
injuries matter now; **$39.99** only if the trial meets the criteria in
[balldontlie-goat-evaluation.md](balldontlie-goat-evaluation.md) §5; everything in scenario 4 is a
business decision gated on the §0 answer, not a technical purchase.

## 3. What could not be verified (consolidated)

API-Sports, Spotrac live terms, HoopsHype, RealGM, Pro Sports Transactions (blocked or 403/402);
MySportsFeeds data terms; Sportradar/Stats Perform/Genius commercial terms and NBA scope; SportsDataIO's
contract text and price; pbpstats/DARKO/nbarapm/Inpredictable/CTG terms; the exact start date for the
pick-protection ban on new trades; the new lottery's odds; NBA Media Central access; NBPA licensing
structure (nbpa.com/licensing 404); Commons coverage mapped to Pivot's actual roster; Getty and NBA
terms from primary text (summaries only); all prices as of 2026-10-18. The research agents also fetched
two CBA PDFs and ran four header-only requests against public image CDNs (one more than the brief
allowed); no image was downloaded and nothing was committed.
