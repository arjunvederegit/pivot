> **Appendix to [resource-research.md](../resource-research.md).** Working notes written by a research sub-agent on 2026-10-18 and read by the author of the main report. Page contents were read through a summarising fetch tool; claims marked verified were checked by that agent, and the NBA.com Terms of Use §9 text and the BALLDONTLIE documents were re-read from the primary pages by the author. Treat rights summaries as leads for counsel, not legal conclusions.

# Pivot research: NBA stats APIs (Cat. A) and historical datasets / projects (Cat. B)

Retrieval date for every source below: **2026-10-18**. Research only; no accounts, trials, downloads, or repo changes.
This is a research summary, not legal advice. Page contents were read through a fetch tool that summarizes pages, so quoted/paraphrased terms should be re-read in the primary text (and by counsel) before Pivot relies on them.

Evidence labels: **[Verified]** = read on the vendor's own page this session. **[Vendor-claim]** = vendor marketing/doc statement, not independently tested. **[Third-party]** = blog, search snippet, or competitor page. **[Not verified]** = could not access.

---

## 0. Bottom line for Pivot

1. **The current stack is the riskiest part.** The `nba_api` client is MIT, but that only covers the *code*. The *data* it pulls from stats.nba.com is governed by NBA.com's Terms of Use (last updated July 13, 2026 per the page). Section 9 limits NBA statistics to "legitimate news reporting or private, non-commercial purposes," bars use in "any fantasy game or other commercial product or service," bars use in a product with live/archived play-by-play, and bars use on any site/product with a "regularly updated, comprehensive database" of NBA stats without NBA's prior consent. A commercial decision-intelligence web product that stores and serves NBA stats is, on a plain reading, outside those terms. [Verified, section 9 paraphrase]
2. **Operationally fragile too.** `nba_api` GitHub issues show repeated timeouts (2023-2026), cloud-host reports (AWS Lambda, EC2, GCP, Codespaces), and as recently as **Oct 7-10, 2026** every stats.nba.com endpoint returned an Akamai HTML error page with HTTP 200 (issue #696; the reporter used `cdn.nba.com` JSON as a fallback). [Third-party, GitHub issues]
3. **Basketball-Reference snapshots are not a safe fallback for AI/ML.** Sports Reference's terms prohibit using site data to train/fine-tune/prompt AI models and prohibit building a competing database; bots need written permission. User-supplied snapshots do not change that. [Verified]
4. **Only a few vendors publish terms that address caching, display, derived outputs, and ML.** SportsDataIO (commercial license, quote-only; explicitly says caching/storing and ML input can be licensed) and MySportsFeeds (cheap, NBA commercial from CAD $29/mo, data-license terms not retrieved) are the realistic license-clean paths. Sportradar / Stats Perform / Genius are enterprise-sales only.
5. **Third-party models (EPM, LEBRON, DARKO, RAPM) are best treated as paid references or comparison baselines**, not redistributable inputs. Dunks & Threes terms explicitly bar using API data to train/run models for commercial decisions without a written agreement.
6. **Recommended immediate actions** (all are "ask," not "build"): request a SportsDataIO quote with Pivot's exact use case (caching, derived analytics, public display, ML); request MySportsFeeds' 14-day trial (they advertise one) and read the data terms; email NBA for Section 9 consent or confirm Pivot's use is outside it; keep `nba_api` to private, non-public dev experiments until licensed; email Sports Reference about data licensing.

---

## 1. Comparison table

BALLDONTLIE is included only as a comparison column (price from the owner's brief; competitor-blog claims labeled).

Rights column key: **Dev** = private development/backtesting; **Display** = showing data/derived results to public users; **Comm** = commercial product; **AI** = ML/model use.

| Resource | Cat. | Cost | Coverage | Quality / limitations | Access | Rights (Dev / Display / Comm / AI) | Integration effort | Benefit to Pivot | Priority | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| **NBA.com stats endpoints + `nba_api` (MIT client)** | A | Free | Very deep: box scores (trad/adv/hustle/tracking), PBP v2/v3, shot charts, lineups, draft combine, tracking, standings, rosters | Unofficial/undocumented; frequent timeouts; cloud IPs reported blocked; Oct 2026 Akamai error-page outage | Python lib, no key; proxies/headers | Dev: private non-commercial only, arguably. Display: no (ToU s9). Comm: no without consent. AI: ToU silent, robots.txt blocks AI crawlers on most pages | Already built | Highest stat depth, but legally/operationally unfit as a production source | Keep for private dev only; plan replacement | Verified-from-docs (ToU); Permission-required for any commercial use |
| **API-Sports (API-NBA / api-basketball)** | A | Free 100 req/day; paid ~$15/$25/$35 mo (Pro/Ultra/Mega) per api-football.com guide; RapidAPI listing shows lower figures. Conflicting | Teams, players, games, standings, stats, odds; "decade+" history; 15s live updates (competitor blog) | PBP/shot data not confirmed; official pages 403 | REST + key | Not verified (terms page 403) | Low-medium | Cheap supplement for schedules/scores; unclear stat depth | Future (only after terms read) | Trial-required (free tier) / pricing page Unavailable to fetch |
| **SportsDataIO** | A | Quote-only. Third-party guess $500-1,000+/mo (unverified). Discovery Lab (personal, non-commercial, delayed): Fantasy or Odds $99/mo, both $149/mo; free tier last season | Broad: box scores, PBP, lineups, injuries, depth charts, projections, odds, transactions; history size not stated | Trial is scrambled/fake data; live feeds 3s-5min intervals; box scores can be corrected after final | REST + key; Replay for testing | Dev: Dev Keys need sales approval. Display/Comm: yes under signed license, raw-feed resale not allowed. AI: license "can permit" ML/derived analytics (confirm) | Medium | Best-documented rights path; includes caching and derived-display statements | **Immediate (request quote)** | Permission-required (sales) |
| **Sportradar NBA API (v8)** | A | Not public (B2B). Third-party guess $500-1,000+/mo | 36 feeds incl. PBP, boxscores, standings, draft, injuries; push feeds for realtime customers; NBA is "Official Source" | Tracking/lineup/historical depth not confirmed in docs read | REST/push, key | Contract only | Medium | Official-grade, highest cost/complexity | Future | Permission-required |
| **MySportsFeeds** | A | Commercial NBA from CAD $29/mo (CORE, non-live); personal from CAD $5/mo; multi-league discounts; 2 recent seasons included, older seasons $19-69 one-time; first-year fee waiver for developers (conditional) | Schedules, scores, boxscores, standings, PBP, lineups, injuries, DFS, odds (feed list) | Historical depth/rate limits/latency not retrieved; data-license terms not retrieved | REST + key; 14-day trial advertised | Website terms read (not data terms). Data/commercial grant details **not verified** | Low-medium | Cheapest apparent commercial route; check caching/AI/display terms | **Immediate (trial + terms review)** | Trial-required |
| **Stats Perform / Opta** | A | Custom enterprise | Opta Dynamic Stats API for basketball launched late 2025 (trade-press); leagues covered not confirmed | Pages 403 | Sales | Contract only | High | Overkill now | Future | Permission-required |
| **Genius Sports** | A | Sales-led | Sports Data API listed; NBA data rights per 2020 NBA release are betting-data distribution | No public API/pricing found | Sales | Contract only | High | Betting-oriented | Future / probably skip | Permission-required |
| **ESPN undocumented endpoints** | A | Free | Scores, teams, news, standings, some PBP | Undocumented, no stability guarantee; ESPN/Disney terms bar bots/scraping and AI use | Unauthenticated JSON | **Unlicensed.** Dev: terms bar. Display/Comm/AI: prohibited | Low | None legally | **Avoid** | Unavailable (no license) |
| **Highlightly** | A | Free 100 req/day; paid $7.99 to $44.99/mo (vendor's own blog) | NBA + NCAAB scores, box scores, lineups, standings, odds, highlights | Self-comparison blog; history/PBP/shots not stated; pricing page 404 | REST | Not verified | Low | Cheap live-scores supplement | Future | Trial-required |
| **BALLDONTLIE (comparison only)** | A | GOAT $39.99/mo (owner brief); competitor blog lists $9.99 ALL-STAR, All-Access $499.99 | NBA history to 1946; PBP only from 2025 season (competitor blog) | Another agent covers | REST + key | Out of scope | n/a | Baseline for comparison | n/a | Out of scope |
| **hoopR / sportsdataverse (+ data repos)** | B | Free | NBA PBP/box/shots from ESPN 2002-present; NBA Stats wrappers (127 functions) | Data is ESPN or NBA-sourced; upstream terms not addressed | R package, GitHub release assets | Code MIT; data license unstated; inherits ESPN/NBA terms. Dev only, risky | Medium | Fast backtesting corpus (private) | Next (private dev only) | Verified-from-docs (code license); data rights Unavailable/unclear |
| **pbpstats (site, API, Python lib)** | B | Free (no pricing seen) | Lineups on floor, possessions, shot zones, WOWY, on/off, win prob; API with ~35 endpoints | Derived from stats.nba.com/data.nba.com; site/API terms not found | REST; OSS client | Code MIT; data terms **not found**; inherits NBA ToU | Low (API) | Lineup/possession data without self-parsing | Next (ask author) | Permission-required |
| **Basketball-Reference / Stathead** | B | BR free; Stathead $9/mo (one sport) or $80/yr; all-sports $16/mo or $160/yr | Season/game stats, BPM/VORP from 1973-74 | No API; 20 req/min limit; jail up to a day | Browser; manual CSV | Dev: manual, small. Display: citable facts only. Comm: no competing DB. **AI: prohibited** | Manual | Reference only; do not ingest bulk | **Avoid for pipeline** | Verified-from-docs |
| **Kaggle wyattowalsh/basketball (nbadb)** | B | Free, ~4.66 GB | 1946-47 to present; 65k+ games; 13M+ PBP rows; shots, tracking | Derived from stats.nba.com via `nba_api`; last updated 2026-06-29 | Kaggle (requires account; not downloaded) | Declared CC BY-SA 4.0; repo MIT. Upstream NBA ToU not overridden by uploader's license. Dev only | Low | Fast private bootstrap for research | Next (dev only) | Verified-from-docs (label); rights Unclear |
| **Kaggle NBA shot-location sets** (e.g. jonathangmwl) | B | Free | 1997-2020 shots | License "Unknown" in Kaggle metadata; last updated 2021-05-03 | Kaggle | Unknown; NBA-derived | Low | Historical shot model backtests | Future | Verified-from-docs (metadata) |
| **shufinskiy/nba_data** | B | Free | PBP 1996/97-2024/25 from stats.nba.com, data.nba.com, pbpstats; shots; matchups 2017/18+ | Static archive; no data-rights statement | GitHub | Code Apache-2.0; data rights unstated; inherits NBA/pbpstats terms | Low | PBP archive for backtests | Next (dev only) | Verified-from-docs |
| **Dunks & Threes (EPM)** | B | Subscription (prices not visible); API exists | EPM, skills, team ratings, game predictions, box projections | 90 req/min; history range unstated | REST + token; web | API licensed for personal/internal use. Display/Comm/AI: **written agreement needed** | Low | Strong impact benchmark | Next (ask for agreement) | Permission-required |
| **DARKO (DPM)** | B | Free site (cost not stated); CSV buttons | Daily projections back to 1996-97 | No public API or terms found | Web + CSV | Terms **not found**; treat as private reference | Manual | Projection baseline for backtests | Next (ask authors) | Permission-required |
| **BBall-Index (LEBRON, roles)** | B | $5/mo or $52.50/yr (4 free tools) | LEBRON, 12 offensive roles, defensive roles, shot charts to 1996, lineups to 2013-14 | No export or API described; T&C page read says nothing on scraping/redistribution | Web | Private subscriber use; redistribution not authorized | Manual | Best archetype/role reference | Next (subscribe privately; ask for data license) | Permission-required |
| **Cleaning the Glass** | B | Price not retrieved | Filtered NBA stats (garbage-time removed), splits | ToS (2017) silent on scraping; community rules bar posting others' material | Web login | Private subscriber use | Manual | Reference | Future | Trial-required / terms incomplete |
| **nbarapm (Ryan Davis)** | B | Free site | RAPM time-decay, peaks, six-factor; aggregates RAPTOR/DARKO/LEBRON peaks | No license, download, or citation stated; `rd11490/rapm-data` repo 404 | Web | Unstated; ask | Manual | RAPM benchmarks | Next (ask) | Permission-required |
| **Inpredictable** | B | Free site | Win probability, WPA, clutch, ON/OFF | No download/API/terms found | Web | Unstated | Manual | Clutch/leverage context | Future | Permission-required |
| **BPM 2.0 / VORP (BR)** | B | Free (page); data via BR | From 1973-74 | Method public; data under Sports Reference terms | Web | Formula citable (Myers 2020); bulk data prohibited for AI | Reimplement from box scores you may license | Low-cost baseline impact metric | Next (reimplement on licensed data) | Verified-from-docs |
| **NBA draft combine** | B | Free via NBA endpoints | Anthropometrics, drills, shooting (nba_api endpoint names) | NBA ToU applies; history depth not verified | `nba_api` | Same as NBA.com | Low | Prospect inputs | Future (license first) | Verified-from-docs (endpoint names) |
| **Prosportstransactions** | B | Free | Trades, signings, waivers, draft, injuries | Page 403 to fetcher; no terms found; client lib notes all rights reserved | Web | Not verified | Manual | Transaction history for trade comps | Next (ask owner) | Permission-required |
| **RealGM** | B | Free/paid tiers not checked | Transactions, contracts, G League | Terms page 403; hoopR wraps it | Web | Not verified | Manual | Contracts/transactions | Future | Unavailable (403) |

---

## 2. Per-resource notes (sources + dates)

### A1. NBA.com stats endpoints and `nba_api`
- **Client license:** MIT. Sources: https://github.com/swar/nba_api and https://pypi.org/project/nba_api/ (retrieved 2026-10-18). PyPI shows v1.11.4, released Feb 20, 2026. Both pages only point to NBA.com's Terms and do not describe them. [Verified]
- **Endpoints (from the repo's docs listing):** boxscore traditional/advanced/hustle/misc/scoring/usage/matchups/playertrack (v2/v3), playbyplay (v1/v2/v3), leaguedashlineups, leaguedashplayer/teamshotlocations, leaguedashptstats, leaguehustlestats, draftcombine* (stats, anthro, drills, spot/non-stationary shooting), drafthistory, playerestimatedmetrics, leaguegamefinder, etc. Source: https://github.com/swar/nba_api/tree/master/docs/nba_api/stats/endpoints (2026-10-18; listing truncated; shotchartdetail and synergy not visible in truncated page but widely documented). [Verified for names shown]
- **Historical depth / latency:** Not stated by NBA; nbadb claims coverage back to 1946-47 for supported endpoints, with video data before 2004-05 blocked. [Third-party]
- **Terms (NBA.com Terms of Use, last updated July 13, 2026 per page):** https://www.nba.com/termsofuse (2026-10-18). Paraphrase:
  - Section 1: downloaded materials are for personal, noncommercial use; public or commercial use of Basketball Content needs written permission.
  - Section 9 (NBA Statistics) seven conditions: credit NBA.com prominently; use only for legitimate news reporting or private non-commercial purposes; no tie to sponsorships/commercial branding; no gambling use; no use in fantasy games or any other commercial product or service; no use in products showing live/near-live/archived play-by-play accounts; no use on a site/product with a regularly updated comprehensive database of NBA/WNBA/G League stats without express prior consent.
  - The words "scrape," "spider," "robot," "machine learning," "artificial intelligence," "data mining" do not appear in the sections reviewed. The Terms do not directly address caching or AI/ML training, but Section 1's no-copy/no-republish language and Section 9's database clause are the relevant constraints. No API or data license is offered.
  - **Net:** the plain text permits private/news use and requires consent for anything commercial or database-like. Pivot's current ingest-and-serve model is the "comprehensive database" and "commercial product" scenario. [Verified]
- **robots.txt:** https://www.nba.com/robots.txt (2026-10-18): disallows `/api/*` and `/stats/events/` for all agents; names many AI crawlers (GPTBot, ClaudeBot, anthropic-ai, CCBot, PerplexityBot, etc.) as blocked except certain pages (team game pages, standings, players, a few stats help/history pages). I did **not** retrieve robots.txt for the `stats.nba.com` host. [Verified for www.nba.com only]
- **IP blocking and reliability (practical, not contractual):** 
  - GitHub issues list: timeouts (#389/#390/#405/#472/#652/#691), AWS Lambda (#510), EC2 (#498), GCP (#419), Codespaces (#505), and #696 "all stats.nba.com endpoints return Akamai HTML error page (200 text/html)" opened Oct 8, 2026 and closed Oct 10, 2026 with no maintainer comment and unknown root cause; the reporter said different IPs failed too and `cdn.nba.com` liveData/staticData still worked. Source: https://github.com/swar/nba_api/issues and /issues/696, /issues/691 (2026-10-18). [Third-party]
  - Older community reports (Node client README, Hacker News) say cloud datacenter IPs are blocked/hang and that Akamai bot protection may use TLS fingerprinting. Source: web search results on bttmly/nba and HN, 2026-10-18. [Third-party; not independently reproduced]
  - Maintainers label several cloud issues "third-party issue," i.e., an NBA-side behavior outside the library's control.

### A2. API-Sports (API-NBA / api-basketball)
- api-sports.io pricing/docs, api-football.com guide and terms returned **HTTP 403** to the fetcher; [Not verified] from vendor pages.
- Search-engine snippets (2026-10-18): free 100 req/day; Pro $15, Ultra $25, Mega $35 per month at 7.5k/75k/150k daily requests (api-football.com NBA guide); RapidAPI listing shows lower prices ($10/$15/$20); the football guide shows $19/$29/$39. **Prices conflict; do not rely on any.** [Third-party]
- Competitor blog (https://highlightly.net/blogs/best-nba-apis-in-2026) says API-NBA has 15-second live updates, "over a decade" of history, and does not state PBP or shots. [Third-party/competitor]
- Rights: not retrieved. Treat commercial/caching/AI as unknown.

### A3. SportsDataIO
- https://sportsdata.io/developers/api-documentation/nba (2026-10-18): endpoints for scores, box scores (live/final, delta), PBP (final/live/delta), game logs, season stats, lineups, injuries, depth charts, transactions, projections-related lists, odds. Call intervals: PBP delta 3s, live scores 5s, live box scores/injuries 1 min, season stats 5 min. Box scores may change after final. No stated historical depth (examples use 2015+). All endpoints need a key. [Vendor-claim]
- Free trial = scrambled data matching production structure, small daily call limit, no SLA, not usable for analysis/display; NBA doc page notes trial only exposes a soccer competition. Replay lets you replay real past games for testing. Dev Keys (unlimited calls on scrambled data) need sales approval. Source: https://sportsdata.io/developers/getting-started. [Verified]
- Discovery Lab (personal): real data delayed one day; Fantasy or Odds $99/mo or $599/yr; both $149/mo or $899/yr; free tier last season; "not licensed for commercial redistribution." Same source. [Verified]
- Rights (https://sportsdata.io/help/data-rights-and-licensing-questions): commercial licenses for businesses and individuals operating commercially (minimum fees); can cover analytics/modeling/decision-support products; attribution required (format per agreement); displaying raw scores/stats inside a licensed product is standard, but reselling/redistributing the feed as a standalone product is not; derived outputs can be displayed internally and externally; commercial licenses permit ingesting, caching, and storing responses, no forced refresh; licenses can permit ML/statistical-model use (feeds and use cases must be confirmed); post-termination rights negotiated. Pricing per client by leagues, feeds, and use case, not call volume. [Verified; it is a help-center page, not the contract]
- Third-party price estimate $500-1,000+/mo: [Third-party; not SportsDataIO's number].

### A4. Sportradar NBA
- https://developer.sportradar.com/basketball/docs/nba-ig-api-basics and /reference/nba-overview (2026-10-18): NBA API v8, 36 feeds (schedule, boxscore, summary, PBP, profiles, depth chart, injuries, standings, draft, awards, series stats, change logs); push feeds for realtime customers; JSON/XML; NBA stated as official source with data from on-venue statisticians; full coverage for regular/postseason. Tracking/lineup/historical not mentioned. [Vendor-claim]
- Dev portal states Sportradar APIs are B2B and not meant to be called directly from client apps. https://developer.sportradar.com/getting-started/docs/get-started [Verified]
- Trial/pricing: not on pages read. Competitor blog says a 30-day trial exists and prices are contract-based [Third-party]. 
- Context: NBA press release (Oct 29, 2020) says Sportradar and Genius Sports hold non-exclusive rights to distribute official NBA/WNBA *betting* data to U.S. licensed operators. https://pr.nba.com/nba-sportradar-genius-sports-group-partnerships [Verified]. Basketball-Reference credits Sportradar as official stats partner of the NBA (BPM page footer). [Verified]

### A5. MySportsFeeds
- https://www.mysportsfeeds.com/feed-pricing/ (2026-10-18): commercial NBA from **CAD $29/mo** (CORE feeds, non-live); personal from CAD $5/mo (must qualify); feeds: CORE, STATS, DETAILS (boxscores, lineups, PBP), DFS, ODDS, PROJECTIONS, EXTRAS; subscription includes two most recent seasons; older seasons/add-ons one-time $19-69; first-year fee waiver for developers "if available"; multi-league discounts 10-30%. [Verified]
- https://www.mysportsfeeds.com/data-feeds/ : NBA feed list, 14-day free trial, "free or low-cost" personal options. Historical/latency/rate limits not stated. [Verified/Vendor-claim]
- https://www.mysportsfeeds.com/terms-use/ covers **website** use only (no downloading extracts, no competing use). It does not cover API/data licensing; data terms **not retrieved**. [Not verified for data rights]

### A6. Stats Perform / Opta, Genius Sports
- Stats Perform: search results describe Opta Data Feeds and a basketball "Opta Dynamic Stats API" launched late 2025 for sportsbook props (https://sbcamericas.com/2025/11/19/stats-perform-basketball-api/, returned 403 to the fetcher; used snippet only). Enterprise, custom licensing. [Third-party]
- Genius Sports: https://geniussports.com/ shows only "Contact Sales" and a Sports Data API product; no NBA coverage detail, pricing or self-serve. [Verified for absence]

### A7. ESPN undocumented endpoints
- Described by guides as reverse-engineered, undocumented, no stability guarantee (e.g., https://zuplo.com/learning-center/espn-hidden-api-guide). [Third-party]
- Disney Terms of Use (US, last updated May 24, 2024) https://disneytermsofuse.com/english/ : bars bots/crawlers/scripts for extracting content, data mining and building datasets, using products to build/train AI tools, and any business use (personal, noncommercial license only). **Flag: unlicensed; do not use for Pivot.** [Verified]
- Note: hoopR's `hoopR-nba-data` repository is sourced from ESPN, so it inherits this issue.

### A8. Other APIs found
- **Highlightly** (https://highlightly.net/blogs/best-nba-apis-in-2026, vendor blog): free 100 req/day; $7.99-$44.99/mo; NBA+NCAAB, odds, highlights. Pricing page 404. Terms not read. [Vendor-claim]
- **BALLDONTLIE** appears only as comparison. Competitor blog's claim that NBA PBP is available only from the 2025 season is [Third-party]; the other agent should confirm.
- TheSportsDB, Sportmonks, Goalserve: not verified for NBA depth; omitted from table. Not researched further.

### B1. hoopR / sportsdataverse
- https://hoopr.sportsdataverse.org/ : MIT R package; ESPN PBP/box scores (shot locations when available), 127 NBA Stats wrappers, KenPom scraper requiring subscription, wrappers for BR, RealGM, etc.; NBA PBP 2002-2026. Does not describe upstream terms. [Verified]
- https://github.com/sportsdataverse/hoopR-nba-data : "2002 - Present," schedules/PBP/box/rosters/shots/draft; source ESPN; license files exist but type not shown in fetched text; no data disclaimer. [Verified]
- Rights: code MIT; data inherits ESPN (Disney ToU) or NBA ToU. **Private dev only; not redistributable.**

### B2. pbpstats
- https://github.com/dblackrun/pbpstats : MIT; parses PBP from stats.nba.com and data.nba.com; adds lineups on floor, possessions, shot zones, event-order fixes. States all stats on pbpstats.com derive from those sources. [Verified]
- API spec https://api.pbpstats.com/openapi.json (2026-10-18; first 100k of 240k chars read): endpoints for totals, game logs, possessions, on/off, WOWY, lineup subunits, shots, win probability, live games. No terms/rate limits visible. [Verified for endpoints]
- Site terms, attribution requests, and coverage seasons: **not found.** Search for terms returned nothing relevant.
- Fit: very good for lineup/WOWY/possession features; ask the maintainer for permission and attribution requirements.

### B3. Basketball-Reference / Stathead
- Data Use page https://www.sports-reference.com/data_use.html : no stated requests-per-minute there; says aggressive spidering may be blocked; no use to build a competing database/service; some datasets bar redistribution; using content to train, fine-tune, prompt or instruct AI models is prohibited without permission; custom data requests declined unless at least $5,000; says facts are not copyrightable. [Verified]
- Terms of Use (last updated May 19, 2023) https://www.sports-reference.com/termsofuse.html : republishing individual-page data with credit is welcome for commercial/non-commercial use; bots/scrapers need written permission; no competing database; AI/ML use of content, including "supporting machine learning methods that predict, classify, label, or score," is prohibited. [Verified]
- Bot traffic page https://www.sports-reference.com/bot-traffic.html : Stathead/FBref block over 10 req/min; other Sports Reference sites over 20 req/min; ban "up to a day"; no API offered; most data comes from licensed third parties. [Verified]
- robots.txt https://www.basketball-reference.com/robots.txt : crawl-delay 3; GPTBot blocked entirely; disallows paths with gamelog/splits/on-off/lineups/shooting. [Verified]
- Stathead pricing https://www.sports-reference.com/stathead/ : one sport $9/mo or $80/yr; all sports $16/mo or $160/yr; first month free; no API or export limits stated. [Verified]
- **Implication:** user-supplied BR snapshots should not feed model training or a served database. Facts can be cited individually with credit.

### B4. Kaggle datasets
- `wyattowalsh/basketball` (nbadb): Kaggle metadata via https://www.kaggle.com/api/v1/datasets/view/wyattowalsh/basketball : title "NBA Database," license **CC BY-SA 4.0**, last updated 2026-06-29, ~4.66 GB, 65k+ games since 1946-47, 13M+ PBP rows, box scores for 95%+ of games. Repo https://github.com/wyattowalsh/nbadb : MIT code; data extracted from stats.nba.com via `nba_api` 1.11.4; outputs DuckDB/SQLite/Parquet/CSV; daily/monthly refresh jobs; no NBA data-rights disclaimer. [Verified for labels]
- **Rights caveat:** CC BY-SA is a share-alike license the uploader applied; it cannot grant rights NBA has not granted, and share-alike could be incompatible with Pivot's own data licensing. Treat as dev-only. Not downloaded. The "daily-updated" claim vs. a June 29 last-update date should be checked (possibly off-season).
- Shot-location set `jonathangmwl/nba-shot-locations`: license "Unknown," last updated 2021-05-03, empty description. [Verified metadata]
- `shufinskiy/nba_data` https://github.com/shufinskiy/nba_data : Apache-2.0 repo; PBP/shots 1996/97-2024/25; no data-rights statement. [Verified]

### B5. Dunks & Threes (EPM)
- https://dunksandthrees.com/ and /api/v1/documentation: EPM, skills, team EPM, game predictions, projected box scores; REST with token (emailed on sign-up); 90 req/min (team-epm with season: 3/min). Prices not visible. Operator: Snarr Data Science LLC. [Verified]
- Terms https://dunksandthrees.com/terms (paraphrase): scraping prohibited; CSV downloads for personal/internal analysis only; API license is personal or internal-organization; no sharing/sublicensing; no showing API data to outside parties; commercial use beyond internal research needs a written agreement; **barred from using API data to train, calibrate, or run predictive models for commercial decisions (betting, pricing, risk) or in automated revenue workflows without a written agreement**; internal derived analyses allowed, sharing externally needs agreement and attribution. [Verified]

### B6. DARKO
- https://www.darko.app/ and /about: daily player metrics, leaderboard CSV downloads, Roster Lab, Lineups, WOWY RAPM, history back to 1996-97; sources credited as BR, NBA.com, pbpstats.com; no terms, price, or API found. [Verified]
- DARKO itself relies on sources whose terms (above) restrict redistribution; ask authors about permitted use.

### B7. BBall-Index
- https://www.bball-index.com/ : Data & Tools $5/mo or $52.50/yr; 700+ metrics; LEBRON; 12 offensive roles; defensive roles; shot charts to 1996; lineup data to 2013-14. No export/API described. [Verified]
- T&C page fetched covers accounts/termination only; says nothing on scraping/redistribution. Absence of a rule is not permission. [Verified; interpret with caution]

### B8. Cleaning the Glass
- Home page lists no prices. ToS (last updated 2017; https://cleaningtheglass.com/terms-of-service/) is silent on scraping; user contributions are CC BY-NC-SA 3.0; community guidelines bar posting others' material without permission. Whether scraping is prohibited is **not established** by the pages I could read. [Verified for what's said]

### B9. nbarapm / Ryan Davis
- https://www.nbarapm.com/ and /datasets : time-decay RAPM, six-factor RAPM, peaks, metric history (RAPTOR, DARKO, LEBRON, LAKER); no license, download or citation text found. Maintainer not stated on page. [Verified for absence]

### B10. Inpredictable
- https://www.inpredictable.com/ : live win probability, WPA, clutch trackers, ON/OFF; no download, API, terms or pricing mentioned. [Verified for absence]

### B11. BPM / VORP
- https://www.basketball-reference.com/about/bpm2.html (Daniel Myers, Feb 2020): BPM 2.0 box-score-only estimate in points per 100 possessions; VORP uses -2.0 replacement and formula (BPM+2.0) x possession share x games/82; values from 1973-74 (season-level 1974-84; game-level 1985+). Copyright Sports Reference; no reuse license; credits Sportradar as data source. [Verified]
- The method is public, so Pivot can reimplement BPM from licensed box-score data rather than ingest BR values. (Reimplementation legality: ask counsel.)

### B12. Draft combine
- nba_api exposes `draftcombinestats`, `draftcombineplayeranthro`, `draftcombinedrillresults`, `draftcombinespotshooting`, `draftcombinenonstationaryshooting`, `drafthistory`, `draftboard`. Data comes from NBA.com so ToU above applies. Historical depth: **not verified**.

### B13. Prosportstransactions and RealGM
- prosportstransactions.com returned 403 to the fetcher; a PyPI wrapper notes the site retains all rights over data (third-party snippet). Terms not retrieved. [Not verified]
- RealGM terms (basketball.realgm.com/terms-of-service) returned 403. [Not verified]

---

## 3. Which Pivot capabilities each resource could support

Legend: Y = good fit; P = partial; N = no. "Legal" column states the clean path.

| Resource | Impact modeling | Projection backtesting | Archetypes | Roster fit | Historical trade comps | Transaction optimization | Legal path |
|---|---|---|---|---|---|---|---|
| nba_api / NBA.com | Y (PBP, lineups) | Y | P (tracking, shot zones) | Y | P | N | Needs NBA consent |
| SportsDataIO | Y | P (history size unverified) | P | P | P (transactions feed) | P | Commercial license |
| MySportsFeeds | P | P (older seasons purchasable) | N | P | N | N | Verify data terms |
| Sportradar / Stats Perform | Y | Y | P | Y | P | P | Enterprise contract |
| hoopR / nbadb / nba_data | Y | Y | P | Y | P | N | Private dev only |
| pbpstats | Y (WOWY, on/off) | Y | P | Y | N | N | Ask maintainer |
| EPM / DARKO / LEBRON / RAPM | Y (as benchmarks) | Y (compare) | P (BBall-Index roles Y) | P | N | N | Private reference; written agreement for use |
| BR / Stathead | P | P | N | N | P | N | Avoid pipeline |
| Prosportstransactions / RealGM | N | N | N | N | Y | Y | Ask owners |

### Redistributable vs private-only vs prohibited (summary)
- **Redistributable (data):** none verified. Only *code* under MIT/Apache (nba_api, hoopR, pbpstats, nbadb, nba_data). Individual facts with credit are permitted by Sports Reference's terms (page-level use), not bulk.
- **Usable privately in development only (with risk):** NBA.com via nba_api, nbadb, nba_data, hoopR NBA-Stats data, subscriber downloads (D&T CSV, DARKO CSV, BBall-Index), Stathead manual lookups.
- **Prohibited as stated:** ESPN endpoints for Pivot's purpose; BR/Stathead data for AI/ML or bulk ingest; D&T API for commercial-decision models without an agreement; NBA.com data in a commercial/database product without consent.
- **Licensable by contract:** SportsDataIO, Sportradar, Stats Perform, MySportsFeeds (terms to verify), D&T (written agreement).

---

## 4. What I could not verify

1. API-Sports: official pricing, rate-limit, and terms pages all returned 403; the price figures above come from search snippets and conflict with each other.
2. MySportsFeeds: data/API license terms (commercial caching, display, AI), rate limits, historical depth, latency. Only website terms were read.
3. Sportradar: trial terms (30 days is a competitor-blog claim), pricing, historical depth, tracking/lineup feeds, commercial license text.
4. Stats Perform/Opta and Genius Sports: whether NBA is licensed for non-betting/analytics buyers; pages 403 or silent.
5. SportsDataIO: actual contract language (only the help-center summary), historical depth, true price.
6. stats.nba.com host robots.txt and any NBA-specific developer/licensing channel; whether NBA grants Section 9 consents and on what terms.
7. Root cause of the Oct 2026 Akamai outage and whether it recurs; whether cloud IP blocking is currently enforced (evidence is community reports).
8. pbpstats site/API terms, rate limits, season coverage, cost.
9. DARKO, nbarapm, Inpredictable, CTG: terms, pricing, and permitted use of downloads. CTG pricing and scraping policy not found.
10. BBall-Index and Dunks & Threes: current price tiers; BBall-Index's real data-use terms (the page read was generic).
11. Prosportstransactions and RealGM: terms (403); RealGM pricing/tiers.
12. Kaggle `wyattowalsh/basketball` current contents (not downloaded) and whether the CC BY-SA label is compatible with NBA's terms; the sportsdataverse data-repo license types.
13. Draft combine historical depth; shot-chart sets other than the two named.
14. BALLDONTLIE intentionally not researched.
15. All "paraphrased terms" were derived from a summarizing fetch tool; confirm against primary text, and consult counsel on whether Pivot's actual use falls inside Section 9.
