> **Appendix to [resource-research.md](../resource-research.md).** Working notes written by a research sub-agent on 2026-10-18 and read by the author of the main report. Page contents were read through a summarising fetch tool; claims marked verified were checked by that agent, and the NBA.com Terms of Use §9 text and the BALLDONTLIE documents were re-read from the primary pages by the author. Treat rights summaries as leads for counsel, not legal conclusions.

# Category C: Contracts, Salary Cap, Transactions (Pivot research)

Prepared 2026-10-18. Research only: no repo files touched, no accounts, no trials. Every "retrieved" date below is 2026-10-18 unless stated. Labels: **[V]** verified from the primary page/document fetched this session; **[S]** second-hand (search snippet, third-party summary, or vendor marketing); **[B]** my background knowledge, not re-verified this session; **[X]** page inaccessible to me.

Method notes. Several sites blocked automated fetches: Spotrac (HTTP 402/403), HoopsHype (402), RealGM (403), Pro Sports Transactions (403), cbafaq.com (expired TLS cert / no connection). I did not try to evade these blocks. The 402/403 codes may reflect my tooling or IP rather than public availability. The fetch tool auto-saved two PDFs (2023 CBA, 2.7 MB; NBA Constitution, 412 KB) to a tool-results folder. I extracted their text locally with macOS PDFKit (676 pages and 88 pages) so I could read the actual rules rather than summaries.

---

## 0. Bottom line

1. **Cap-year numbers for 2025-26 and 2026-27 are confirmed against the NBA's own press releases** (section 2.1). They also pass an internal consistency check against the CBA formulas (tax = 121.5% of cap, minimum team salary = 90%, aprons = fixed multiples of cap). The hand-curated YAML is on solid ground if it matches those figures.
2. **No free source I could verify gives a licensed, structured feed of per-year salary + options + guarantees + contract type + signed date + no-trade flag.** Spotrac is the closest in content, but its terms (snippet only) bar systematic extraction and commercial use, and I could not read its live terms or find published licensing. Sports-Reference itself names Spotrac as a paid source for contract data.
3. **Basketball-Reference (BBR) is the riskiest dependency for a commercial, AI-assisted product.** Its Terms of Use prohibit using content to train, fine-tune, or prompt AI models. Its data-use page says custom data requests start at $5,000. Its bot page says most data is third-party-licensed and cannot be offered as a download. A user-supplied snapshot does not remove that exposure once Pivot ingests and displays it.
4. **"Contract data" is not a trade-legality engine.** The CBA text shows legality depends on at least 15 extra inputs: apron team salary (not just payroll), cap holds, exception ledgers with creation dates, pick ownership, protections, frozen picks, the Stepien rule, signing dates, roster counts and more (section 4). Most of those are derivable from the CBA, the NBA Constitution, and a transactions ledger, not from a contracts vendor.
5. **A rule that matters for roadmap timing:** the Board of Governors approved (2026-05-28) a ban on protecting traded picks that land 12th-15th, effective with the 2027 draft. Any pick-protection model needs to know about it.
6. **No-trade clauses are nearly irrelevant as a data feed.** CBA Art. XXIV allows an explicit no-trade clause only for players with 8+ years of service and 4+ years with the signing team. Hoops Rumors (2026-07-29) lists one such player for 2026-27. A one-row curated table beats any vendor field.

---

## 1. Comparison table

Columns: Resource | Category | Cost | Coverage | Quality/limitations | Access | Rights (dev / public display / commercial / AI use) | Integration effort | Benefit to Pivot | Priority | Status.

| Resource | Category | Cost | Coverage | Quality/limitations | Access | Rights (dev / public / commercial / AI) | Integration effort | Benefit to Pivot | Priority | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| NBA Communications cap releases (pr.nba.com) | Official cap figures | Free | Cap, tax, aprons, min team salary, NT/Tax/Room MLE; 2025-26 and 2026-27 | Primary source. Does not list BAE, Early Bird, max salaries, or the expanded TPE amount | Web pages | Dev: fine. Public: figures are facts; cite source. Commercial/AI: no license text found on release pages; facts-only use is low risk; get counsel review | Low (hand-key YAML once a year) | High: authoritative parameters | Immediate | Verified-from-docs |
| 2023 NBA-NBPA CBA (nbpa.com/cba; official.nba.com) | Rules text | Free | Full 676-page text; term 2023-24 to 2029-30 | Authoritative for trade, apron, exception, roster rules. Does not contain league-office practices or per-player contracts | PDF (two official hosts) | Dev: fine. Public/commercial/AI: no license stated on the page; quoting rules and summarizing is normal practice; do not republish the PDF | Medium (translate into code and tests) | Very high: the rulebook | Immediate | Verified-from-docs |
| NBA Constitution & By-Laws (June 2024) | Rules text | Free | Sec. 7.03 Stepien rule; draft procedures | Version is June 2024; check for later amendments | PDF on official.nba.com | As CBA | Low | High: pick-trade legality | Immediate | Verified-from-docs |
| Basketball-Reference contracts + transactions | Contracts, transactions | Free to browse | Team and player contracts 2026-27 to 2031-32 with Guaranteed column and option color key; transactions Jul 2025-Jun 2026 with free-text pick notes | No contract type, signed date, or NTC field seen. Footer credits Sportradar as data provider. Pick protection detail is free text | HTML; no API (stated) | Dev: personal use under limited license. Public display: allowed with credit only if not a substitute service. Commercial: not banned outright but cannot compete/substitute. **AI: prohibited** (train, fine-tune, prompt). Bots: 20 req/min, up to a day jail; robots crawl-delay 3 | Already built (snapshot + parser) | High, but highest rights risk | Immediate (dev only) | Verified-from-docs (rights); permission needed for production |
| Stathead (BBR paid tier) | Contracts/transactions search | $9/mo or $80/yr basketball; $16/mo All Sports [S] | Query tool over SR data | Same underlying data and terms; no commercial license verified | Login | Personal subscription; commercial/AI terms not verified | Low | Marginal | Future | Verified-from-docs for price only via search [S]; terms Unverified |
| Spotrac NBA | Contracts, cap, trade exceptions, draft | Free tier; premium exists, price not found | Player pages (per-year base, cap hit, incentives, guarantee/option notes), team cap tables with cap holds, apron tracker, trade exceptions page, draft pages, transactions | Most complete public structure [S]. One team page showed a stale second apron ($229.279M vs. official $221.686M) [S], so third-party pages can drift | HTML; no public API found | Terms (snippet of an old copy, [S]): bars systematic extraction and revenue-generating use. Live terms [X]. SR names Spotrac as a paid contract-data source [V] | Permission/licensing conversation first | Highest content fit | Next | Permission-required |
| HoopsHype salaries | Contracts | Free | Player salaries by year [B] | Could not fetch; provenance unknown | HTML | Unknown | n/a | Cross-check only | Future | Unavailable [X] |
| RealGM (salaries, transactions, future picks, trade exceptions) | Contracts, picks, TPE | Free [B] | Pages exist for contracts, transactions, draft pick considerations [B] | All fetches returned 403; contents unverified | HTML | Unknown | n/a | Potentially best free pick-ownership view | Next (manual check) | Unavailable [X] / Permission-required |
| Pro Sports Transactions | Transaction archive | Free | Basketball since Aug 2004 project start; 1.2M+ records across sports; daily updates [S] | Cloudflare-protected; older records uneven [S] | HTML search, 25 rows/page [S] | Site rights reserved; a third-party MIT client exists but covers code only [V from package page] | Medium (anti-bot friction) | Cross-check of historical trades | Next | Permission-required |
| Hoops Rumors trackers | Editorial trackers | Free | 2026-27 lists: cap figures, MLE/BAE values, outstanding TPEs, traded 2027 firsts with protections, frozen picks, trade-veto players, trade kickers, Dec 15/Jan 15 restrictions, roster counts | Human-written; one internal inconsistency found in a table (room exception year 3). Good for QA | HTML articles | Terms page not found (404). Treat as no license | Manual QA only | High as an oracle for tests | Next | Verified-from-docs (content); rights Unverified |
| NBA.com transactions / trade tracker | Official transactions | Free | "All NBA Transactions" sortable by date/team; trade tracker for 2026 offseason | Hub page showed no entries in my fetch; archive depth unknown | HTML | NBA Terms: copying needs written permission; stats not for databases or commercial products; no scraping clause found | Medium | Good official cross-check | Next | Verified-from-docs (terms) |
| SalarySwish | Contracts, TPE, trackers (Cap Friendly team) | Free; no pricing shown | Contracts, cap, trades, TPE/BAE/MLE/hard-cap trackers, trade machine | Draft-pick tracker not described. Age and accuracy unknown | HTML | Terms not read | Permission needed | Possible alternative to Spotrac | Future | Permission-required |
| Tankathon | Draft order | Free | 2027 pick order with ownership logos only; no 2028+ picks, no protections | Not a pick-obligation source | HTML | No terms listed; contact via X only | n/a | Low | Future | Verified-from-docs |
| Fanspo | Fan community/trade machine | Unknown | Forum, trade machine | No contract or pick data found | HTML | Unknown | n/a | None found | n/a | Unavailable (no relevant data) |
| CapFriendly | NHL cap site | n/a | NHL | Sold to Washington Capitals; public site sunset July 2024 [S] | n/a | n/a | n/a | None | n/a | Unavailable |
| cbafaq.com (Larry Coon) | Rules explainer | Free | Salary Cap FAQ | Maintainer retired (June 2025 report) [S]; site would not load (expired cert) | HTML | Copyright notice seen in a proxy copy was 1999-2022 [S]; no license seen | n/a | Do not rely on it for 2023 CBA | Future | Unavailable [X] |
| SportsDataIO NBA API | Commercial feed | Published price not found. Third-party Vendr median $16,496/yr is an aggregate estimate, not a quote [S] | Documented NBA endpoints include TransactionsByDate; no contract/salary/pick endpoint seen in docs | Real-life contract fields not found in docs read (first ~160k characters, odds section reached) | API key; trial limited to UEFA Champions League per docs | Commercial license via sales | Medium | Transactions only | Future | Trial-required / Permission-required |
| Sportradar NBA API | Commercial feed | Not published | Daily Transfers, Free Agents, Draft Summary, Team Draft Picks (draft selections), in-draft Trades | No contract-value or future pick-ownership endpoint seen | API key; account required | B2B license | High | Roster/transactions only | Future | Permission-required |
| BALLDONTLIE (note only, other agent covers) | API | Third-party blog says GOAT $39.99/mo advertises contracts [S] | Advertises a contracts endpoint | Not verified by me; vendor-advertised | API key | Not researched | n/a | See other report | n/a | Not researched |

---

## 2. Per-resource notes (source URL, retrieval date, claim)

### 2.1 Official NBA cap announcements [V]

- **2025-26.** https://pr.nba.com/nba-salary-cap-2025-26-season, published 2025-06-30, retrieved 2026-10-18. Cap $154.647M; tax level $187.895M; minimum team salary $139.182M; first apron $195.945M; second apron $207.824M; Non-Taxpayer MLE $14.104M; Taxpayer MLE $5.685M; Room MLE $8.781M. Cap and tax effective 12:01 a.m. ET Tue July 1, 2025; moratorium ended noon ET Sunday July 6. Also reported on https://www.nba.com/news/nba-salary-cap-set-2025-26-season (search result only [S]).
- **2026-27.** https://pr.nba.com/2026-27-salary-cap/, published 2026-06-30, retrieved 2026-10-18. Cap $164.961M; tax level $200.428M; minimum team salary $148.465M; first apron $209.015M; second apron $221.686M; Non-Taxpayer MLE $15.044M; Taxpayer MLE $6.064M; Room MLE $9.366M. Effective 12:01 a.m. ET Wed July 1, 2026; negotiation window opened 6 p.m. ET June 30; moratorium ended noon ET Monday July 6. The release does **not** list the BAE, minimum exception, or max salaries.
- **Figures that come from Hoops Rumors, not the NBA** (https://www.hoopsrumors.com/2026/06/salary-cap-tax-line-set-for-2026-27-nba-season.html, published 2026-06-30, retrieved 2026-10-18) [S for these items]: BAE $5.477M; Early Bird $15.2355M; expanded traded-player exception amount $9.096M; maximum salaries $41,240,250 / $49,488,300 / $57,736,350 (25/30/35% of cap, which I verified arithmetically); two-way $678,882; cash limit $8.495M; estimated average salary $15.163M. A 2027-28 cap projection of about $174M (about +5.5%) is attributed to a Bleacher Report writer and is a projection only.
- **Consistency checks I ran on the retrieved numbers.** Tax / cap = 1.2150 in both years, matching CBA Art. VII §2(a)(4)(ii) (121.5%). Minimum / cap = 0.90, matching §2(a)(4)(i). First apron / cap = 1.2671 and second apron / cap = 1.3439 in both years, matching the CBA's "scale the 2023-24 level by cap ratio" formula. The cash limit $8.495M equals 5.15% of $164.961M, matching Art. VII §8(a). Increase 2025-26 to 2026-27 is 6.67%, under the CBA's 10% annual ceiling (§2(a), 110% rule).
- BBR's contracts page header also shows "2026-27 Salary Cap: $164,961,000" (https://www.basketball-reference.com/contracts/, retrieved 2026-10-18).

### 2.2 The 2023 CBA [V]

- Official copy linked from https://nbpa.com/cba (retrieved 2026-10-18): PDF titled "Final 2023 NBA Collective Bargaining Agreement 6-28-23" (hosted on an imgix/cosmic CDN). The same page links the 2017 and 2011 CBAs, which matter because Pivot's 565 historical trades (2016-17 to 2025-26) span two rule eras. The NBA also hosts a 2023 CBA copy at https://official.nba.com/ (link to ak-static.cms.nba.com), plus a "2023 CBA 101" explainer PDF (2024-25 edition) that I did not read.
- Announcement: https://pr.nba.com/2023-nba-collective-bargaining-agreement/ (2023-06-28): seven-year agreement effective 2023-07-01 through 2029-30; opt-out after 2028-29 is reported in press [S].
- NBPA page shows only "©2025 NBPA" and no license terms for the PDFs.
- The 2026-27 cap-year numbers are in 2.1. They are not in the CBA text itself, which gives formulas and a 2023-24 base.

### 2.3 NBA Constitution and By-Laws [V]

- https://official.nba.com/ links the June 2024 Constitution PDF. Sec. 7.03 (read in the extracted text): a team may not sell a first-round pick for cash, and may not trade a first-round pick if the result could leave it without first-round picks in any two consecutive future drafts. That is the Stepien rule. The CBA text contains no Stepien provision (my search found none).
- The "only 7 drafts ahead" limit for pick trades is reported by CBS/Bleacher Report summaries [S]; I did not find it in either primary document.

### 2.4 Basketball-Reference [V rights, V data shape]

- **Data shape.** https://www.basketball-reference.com/contracts/players.html (2026-10-18): columns Rk, Player, Tm, six seasons (2026-27 to 2031-32), Guaranteed; color key for player option and team option. Glossary (https://www.basketball-reference.com/contracts/glossary.html) defines guaranteed/unguaranteed, buyout and setoff adjustments, player/team/early-termination options, likely/unlikely incentives, trade kicker, cap hit, cap holds, trade incoming/outgoing. It does not define contract type or signed date.
- **Transactions.** https://www.basketball-reference.com/leagues/NBA_2026_transactions.html: entries by date; signings carry qualifiers such as two-way, Exhibit 10, multi-year, extension (so contract type and signing date can be derived by parsing); trades carry free-text pick notes (original owner, protections such as "protected 31-55, did not convey", swaps, "more favorable"), and trade-exception notes.
- **Rights.**
  - https://www.sports-reference.com/termsofuse.html: grants a limited personal revocable license; sharing or repackaging data from individual pages is welcome for commercial or non-commercial purposes with credit, but not as a database/service that competes with or substitutes for SR or its data providers; bots/scrapers prohibited without written permission when they hurt performance; using content to train, fine-tune, prompt, or instruct AI models is prohibited.
  - https://www.sports-reference.com/data_use.html: no aggressive spidering; custom data requests have a $5,000 minimum; some datasets have no-redistribution licenses; facts are not copyrightable. Lists Spotrac and Sports Direct (Gracenote) as paid sources.
  - https://www.sports-reference.com/bot-traffic.html: other SR sites block above 20 requests/minute (FBref and Stathead 10); jail up to a day; most data is third-party-licensed and cannot be offered as a download; no API is part of its business model.
  - https://www.basketball-reference.com/robots.txt (fetched with curl too): crawl-delay 3; GPTBot fully disallowed; play-index CGI query pages disallowed; /contracts and /leagues are not disallowed.
- **Implication.** The user-supplied snapshot is a manual-download pattern, but Pivot's own ingestion, storage, display, and any LLM use of that data fall under these terms. For a product that uses AI, the prompt/AI clause is the sharpest risk.

### 2.5 Spotrac [S for terms, content]

- **Access.** https://www.spotrac.com/ , /terms, /service, /about all returned HTTP 402 to my tools; the in-app browser returned a blank page. I could not read the live terms.
- **Terms (snippet, search result for https://www.spotrac.com/service, retrieved 2026-10-18 [S]).** The snippet says users agree not to systematically extract or harvest data or data fields by electronic means, and a prohibited-uses list includes using the services or content for a revenue-generating or commercial enterprise. The search tool flagged that copy as roughly 2,480 days old, so the current text may differ. Treat as "likely restrictive, needs a lawyer plus the live page."
- **Content (search snippets [S]).** Player pages break each year into age, status, base salary, incentives, cap hit, cap percentage, and luxury-tax figures, with notes on guarantee timing and option deadlines (https://www.sportskeeda.com/basketball/buddy-hield-contract quoted Spotrac-style notes). Team cap tables list guaranteed amounts, incentives, trade-bonus proration, and cap holds by type (Bird, Early Bird, Non-Bird) (https://www.spotrac.com/nba/san-antonio-spurs/cap/_/year/2026). An apron tracker shows first/second apron space (https://www.spotrac.com/nba/apron/_/year/2026), a trade-exceptions page exists (https://spotrac.com/nba/transactions/trade-exceptions), and draft pages list picks with trade notes (https://www.spotrac.com/nba/draft).
- **Licensing.** I found no published API, enterprise, or data-licensing page or price. Spotrac's About page (via search) says it was founded in 2007 by Michael Ginnitti and Scott Allen. SR's own data-use page lists Spotrac as a paid contract-data source [V], which suggests a commercial data channel exists. Pricing, scope, and terms are unknown; contact is the only route.
- **Not verified:** whether Spotrac pages expose contract type, signed date, or no-trade flags in structured form.

### 2.6 HoopsHype and RealGM [X]

- HoopsHype (https://hoopshype.com/salaries/, /terms-of-service/) returned 402 to WebFetch and curl. Search found only low-quality mirror pages, so I cannot say how its salary data is sourced or licensed. No claim made.
- RealGM: https://www.realgm.com/ redirected to https://basketball.realgm.com/ and then returned 403 for the future draft considerations page, terms page, transactions page, and salary cap page. I know from background that RealGM publishes contracts, transactions, trade-exception and draft-pick pages [B], but I could not check any of it, its terms, or its currency.

### 2.7 Pro Sports Transactions [S]

- Homepage returned 403. Per search snippets [S]: archive for five leagues starting with basketball in Aug 2004, 1.2M+ records, updated daily, older records compiled from newspapers with uneven coverage, 25 rows per page.
- https://pypi.org/project/pro-sports-transactions/ (retrieved 2026-10-18) [V]: async client requires a Cloudflare workaround; disclaimer says site rights remain reserved; MIT covers code only; no site rate limit stated. No terms of use found.

### 2.8 Hoops Rumors (cross-check source) [V content]

Pages fetched 2026-10-18:
- Features index https://www.hoopsrumors.com/2025/08/hoops-rumors-lists-trackers-features-5.html lists the 2026-27 trackers cited below.
- Exceptions: https://www.hoopsrumors.com/2026/07/values-of-2026-27-mid-level-bi-annual-exceptions.html gives NT MLE $15.044M with four-year schedule, Taxpayer MLE $6.064M (two years), Room $9.366M (three years), BAE $5.477M (two years); I found one internal inconsistency (room exception year 3 should be $10.326M at a 5% raise but is shown as $10.3026M). Five teams cannot use the BAE in 2026-27.
- Outstanding TPEs: https://www.hoopsrumors.com/2012/02/outstanding-trade-exceptions.html, last updated 2026-09-27; entries show amount, source player/team, expiration, partial usage; 2025-26 exceptions are italicized because over-apron teams cannot use them.
- Traded 2027 firsts: https://www.hoopsrumors.com/2026/09/traded-first-round-picks-for-2027-nba-draft.html (2026-09-11): top-N protections, swaps, fallback picks, Stepien flags, all 30 teams.
- Frozen picks: https://www.hoopsrumors.com/2026/09/nba-frozen-draft-pick-tracker.html (2026-09-08): four teams with frozen firsts (three for 2032, one for 2033). Updated once a year.
- Trade veto: https://www.hoopsrumors.com/2026/07/nba-players-who-can-veto-trades-in-2026-27.html (2026-07-29): one explicit no-trade clause, two matched-offer-sheet vetoes, nine re-sign vetoes, plus waivers. Other pages for kickers, Dec 15/Jan 15 restrictions, roster counts, and cash sent/received exist per the features index.
- Terms: https://www.hoopsrumors.com/terms-of-use returned 404; I did not find an alternate. Treat content as copyrighted editorial; use for human QA, not ingestion.

### 2.9 NBA.com [V terms, V page existence]

- https://www.nba.com/termsofuse (2026-10-18): copying or reusing basketball content needs written permission; downloads are for personal noncommercial use; Section 9 limits statistics to news reporting or private noncommercial use with attribution, prohibits commercial products, and bars use in a database of comprehensive, regularly updated league stats without consent. Scraping and AI are not addressed by name. Contract and transaction text may or may not count as "statistics"; unresolved.
- https://www.nba.com/freeagents/2026/transactions is a hub linking an official "All NBA Transactions" list (sortable by date and team) and a Trade Tracker described as official. No bulk-data license or API found.

### 2.10 SalarySwish, Tankathon, Fanspo, CapFriendly

- SalarySwish: https://salaryswish.com/about (retrieved 2026-10-18): launched 2023-09-12 by the CapFriendly founders (SalarySwish Inc.), tracks contracts, cap, transactions, trades; trackers for BAE, MLE, TPE, hard cap, luxury tax; Trade Machine marked new; no pricing; terms not read. Search-result detail [S]: CapFriendly (NHL) was sold to the Washington Capitals and its public site sunset July 10, 2024.
- Tankathon: https://www.tankathon.com/full-draft (2026-10-18): 2027 order with ownership; no trade notes or protections; no 2028+. No terms; contact via X.
- Fanspo: https://fanspo.com (2026-10-18): fan forum, mock-draft and trade-machine tools; no contracts or pick-obligation data described. I did not identify a site matching the user's "Early-bird" tracker label; "Early Bird" is a CBA exception type, not a source I could locate.

### 2.11 cbafaq.com [S/X]

- https://www.blazersedge.com/2025/6/21/24453051/larry-coon-cba-salary-cap-retirement-faq-site-history returned 403; via search summary [S]: Larry Coon announced retirement from maintaining the FAQ in June 2025, intends to keep the site and domain up, and leaves continuation to others. http://www.cbafaq.com/salarycap.htm failed with an expired TLS certificate. Whether it was ever updated for the 2023 CBA could not be verified. License: none found.

### 2.12 Commercial vendors [S]

- SportsDataIO: https://sportsdata.io/developers/api-documentation/nba (read to about 160k characters). Listed: Transactions (TransactionsByDate), free agents, injuries, odds/props/futures; no contract, salary, trade-detail, or draft-pick endpoint found in the portion read. All endpoints need a key; trial covers only UEFA Champions League. https://www.sportsdata.io/pricing returned 404. Third-party Vendr listing gives a median of $16,496/year across buyers [S, not a quote].
- Sportradar: https://developer.sportradar.com/basketball/reference/nba-overview (retrieved 2026-10-18) lists Daily Transfers, Free Agents, Draft Summary, Team Draft Picks, in-draft Trades, injuries; no contract-value or future-pick-ownership feed seen; no pricing or trial details on the page.
- BALLDONTLIE: not researched, per instructions. Noted only that third-party posts say it advertises a contracts endpoint (tier pricing in those posts conflicts); the other agent should verify.

---

## 3. Answers to the key analytic questions

### 3.1 Which source gives structured per-year salary, options, guarantees, contract type, no-trade clause, signed date?

| Field | Best verified source | Gap |
|---|---|---|
| Salary by year | BBR snapshot (verified shape); Spotrac (snippet) | Rights |
| Option markers (player/team/ETO) | BBR color key; Spotrac notes [S]; Hoops Rumors option-decision lists | Option exercised/declined status is event data, not a static field |
| Guarantees and guarantee dates | BBR "Guaranteed" column; Hoops Rumors early-guarantee and non-guaranteed lists; Spotrac [S] | Guarantee dates are not in a structured BBR column |
| Contract type (standard, two-way, Exhibit 10, extension, rookie scale, minimum) | BBR transactions text qualifiers [V]; Spotrac [B/S] | Needs parsing; not a clean enum |
| Signed date | Derivable from BBR/NBA transactions [V] | Needs a signings parser; Pivot currently has NULL |
| No-trade clause | CBA Art. XXIV §2(b): only 8+ years of service and 4+ years with the team; Hoops Rumors lists one 2026-27 holder | Curate by hand |
| Trade kicker | Hoops Rumors list; BBR glossary defines it; Spotrac proration [S] | CBA §2(a): max 15% of remaining base comp; payable once |

No source I could verify delivers all of these as a licensed structured feed. Contract type, signed date, and no-trade flag should be built from a transactions ledger plus curated overrides, not bought.

### 3.2 Licensing and ToS constraints for a commercial product

| Source | Commercial | Public display | AI use | Net |
|---|---|---|---|---|
| NBA press releases / CBA / Constitution | Facts and rule paraphrase are low risk; no license text found | Cite source | No restriction found | Best base |
| BBR / Stathead | Allowed only if not competing/substituting; third-party data not downloadable | OK with credit, limited | **Prohibited** (train, fine-tune, prompt) | High risk |
| Spotrac | Restricted (snippet [S]) | Unknown | Unknown | Permission needed |
| NBA.com site content | Written permission; stats restricted | Restricted | Not addressed | Treat as restricted |
| Hoops Rumors | Terms not found | Editorial copyright assumed | Unknown | QA only |
| RealGM / HoopsHype / PST | Not verifiable | Unknown | Unknown | Do not ingest |
| SportsDataIO / Sportradar | Licensed B2B | Per contract | Per contract | Neither shows a contracts endpoint |

### 3.3 Contract data vs. a reliable trade-legality engine

Contracts feeds supply player-level salary. The CBA shows legality turns on team-level state and event history. See section 4.

### 3.4 Minimum-cost path

**Dev (now to local/staging).** Cost about $0.
- Keep the user-supplied BBR snapshot for dev only, with no LLM ever seeing it (AI clause). Honor 20 req/min and robots if any fetching is added.
- Source all league parameters from the NBA releases (section 2.1) and all rules from the 2023 CBA and NBA Constitution. Build tests from CBA examples (the CBA contains worked examples in Art. VII §2(e)).
- Use Hoops Rumors trackers as a manual oracle for QA (not ingested).
- Parse signings and waivers from the BBR transactions pages already being parsed for trades, to fill contract type and signed date.
- Version rules by era: the 2017 CBA (nbpa.com) for 2016-17 to 2022-23 trades, the 2023 CBA after.

**Public beta.** Estimated $0 to low four figures plus legal review.
- Show only user-entered or user-uploaded contract data per session, or a small hand-curated seed with no bulk copy of BBR/Spotrac, until rights are settled.
- Send written permission requests to Spotrac and Sports-Reference now (Sports-Reference custom requests start at $5,000 per its data-use page). Ask for: scope, redistribution, AI/LLM use, attribution, price.
- Use pick ownership from primary sources and manual curation, checked against Hoops Rumors; label "unverified" in UI.
- Keep LLM features operating on Pivot-derived rule outputs, not on BBR content.

**Commercial.** Price unknown; requires quotes.
- Obtain a written license from Spotrac or an equivalent vendor covering display, storage, derived analytics, and AI use. Alternatively, build an in-house contract ledger from primary events (signings, extensions, options, waivers in transactions) and official announcements, with a human verification process. Whether Sportradar or SportsDataIO can supply contract values is unconfirmed; ask both specifically.
- Hire counsel to review NBA.com terms (Section 9), trademark use of team names/logos, and the Spotrac/SR licenses.

---

## 4. Rules-engine input gap analysis

CBA references are to the 2023 CBA text I extracted (Art. VII unless noted).

| Input the engine needs | Why (CBA hook) | In a contracts feed? | Where to source |
|---|---|---|---|
| **Cap, tax, aprons, floor, MLE/BAE/Room, max/min salaries, cash limit** | §2(a)(4) formulas; §8(a) cash 5.15% of cap | Partly | NBA releases (verified); Hoops Rumors for BAE, Early Bird, expanded TPE amount, cash limit (second-hand) |
| **Team Salary vs. Apron Team Salary** | §2(e)(1): Apron Team Salary adds unlikely bonuses and certain signing amounts, subtracts free-agent amounts and unsigned first-rounders, adds RFA tender, adjusts for exceptions deemed included | No | Compute from contracts plus cap holds; check against Spotrac/BBR totals |
| **Cap holds (Bird/Early Bird/Non-Bird, QOs, draft-pick holds)** | §4 determines Team Salary; matters for Room and apron | No (Spotrac shows them [S]) | Compute from rights status; verify against Spotrac [S] |
| **Likely vs. unlikely incentives** | Counts toward Team Salary differently | Partly (BBR glossary lists both) | Contract data plus curation |
| **Exception ledger (TPEs with created date, amount, expiry; MLE/BAE usage)** | §6(j); one-year expiry; row F bars use after the season in which it arose for first-apron teams | No | NBA transaction text, Hoops Rumors outstanding TPE list, Spotrac TPE page [S], SalarySwish tracker |
| **Which exception type fits** | §6(j)(1): standard 100%+$250k; aggregated standard; expanded: greater of lesser of (200%+$250k or 100%+$7.5M scaled by cap ratio) and (125%+$250k); room: room + $250k. The $250k becomes $0 for a team ending above the first apron (§6(j)(3)) | No | CBA text (verified). The 2023-24-only 110% transition exception is in the text and should not apply now |
| **First and second apron transaction restrictions** | §2(e)(4) table rows A-K (e.g., aggregated TPE and cash bars at second apron; acquiring via sign-and-trade TPE) and §2(e)(2)(ii) forward-looking test after the regular season | No | CBA text; needs current-year Apron Team Salary *and* next-year projection assuming options exercised (§2(e)(3)) |
| **Hard-cap triggers** | Using certain exceptions imposes an apron ceiling for the rest of the year (§2(e)(2)(i)(B)) | No | Transaction history; Hoops Rumors hard-capped list |
| **Pick ownership, protections, swaps, conveyance history** | Needed to evaluate pick-for-pick trades and the Stepien rule | No | BBR transaction free text; Hoops Rumors 2027 tracker; Spotrac draft pages; RealGM [X]. No licensed structured feed found |
| **Stepien rule** | NBA Constitution §7.03 | No | Primary text (verified); compute from ownership |
| **Frozen first-round pick (second apron)** | §2(f): a second-apron team cannot trade its first in the draft after the seventh season ahead; two or more second-apron years in the following four moves it to the end of the first round | No | CBA text; Hoops Rumors frozen tracker (four teams, updated yearly); needs apron status as of each team's last regular-season game |
| **New protection ban (12th-15th slots)** | Board of Governors vote, 2026-05-28, 29-1; effective 2027 draft; existing protections stay; sunsets after 2029 | No | ESPN (https://www.espn.com/nba/story/_/id/48903380/sources-nba-bog-votes-expand-lottery-disincentivizes-tanking) [S]; I could not confirm the date on which the ban starts applying to *new* trades; confirm from a league memo |
| **Aggregation rules** | §6(j)(4): a player acquired via exception within two months cannot be aggregated (with Dec 16 / deadline carve-out); in 3+ for fewer trades, at most one minimum-salary player outside Dec 15-deadline window | No | CBA; needs acquisition dates |
| **"2-for-1" popular rule** | The documented rules are the aggregation limits above, not a standalone "2-for-1" rule. Confirm what the engine's current check actually encodes | No | CBA text |
| **Roster minimums and maximums** | Art. XXIX: 14-15 players in-season (12-13 allowed up to two weeks at a time and 28 days total); 21 in the offseason window; Art. II requires 14 contracted players for non-guaranteed training-camp signings | No | CBA text; roster counts from transactions (Hoops Rumors 2026-27 roster counts page) |
| **Recently signed / acquired restrictions** | §8(d): rookies 30 days; free agents the later of 3 months or Dec 15; over-cap re-signs above 120% of prior salary the later of 3 months or Jan 15; extensions/renegotiations 6 months; designated veteran contracts 1 year; §8(f)/(g) rookie extension room treatment | No (needs signing date and type) | Transactions ledger; Hoops Rumors Dec 15 / Jan 15 lists |
| **Player consent** | §8(b): one-year deals that would end in veteran free agency need consent unless waived; no-trade clause only at 8 years total / 4 with team (Art. XXIV §2(b)); matched offer sheet gives a year of veto (per Hoops Rumors) | No | Hoops Rumors veto list; transaction history |
| **Trade bonus** | Art. XXIV §2(a): up to 15% of remaining base comp, once | Partly | Contract data / Hoops Rumors kicker list |
| **Trade deadline timing** | §8(c): no trade after the deadline in the last season of a contract, including seasons that could be last depending on an option | Partly (option data) | NBA calendar; contract options |
| **Waive-and-reacquire** | §8(h) one-year bar on re-signing/claiming a player the assignee waived | No | Transactions ledger |
| **Sign-and-trade** | §8(e)(1): needs 3-4 year deal, first year fully protected, signed before regular season, acquiring team has room | No | CBA; transaction history |
| **League-year calendar and event timestamps** | Many rules are date-relative (moratorium, July 1, Dec 15, Jan 15, regular-season end) | No | NBA releases; NBA calendar |
| **Rule-era versioning** | Pivot's 565 historical trades span the 2017 CBA and the 2023 CBA | No | Both CBA PDFs on nbpa.com |

Takeaway: contract data covers about 5 of these rows. The other rows are rule text (free, official), event history (a transactions ledger Pivot partly already parses), and curated ownership data. A vendor contract feed would not close the legality gap.

---

## 5. What I could not verify

- Live Spotrac terms of service, any API/enterprise/licensing page, premium price, and whether player pages expose contract type, signed date, or no-trade flags in structured form (all blocked or snippet-only).
- HoopsHype: any content, terms, or sourcing (402 errors).
- RealGM: content and terms for salaries, transactions, future picks, trade exceptions (403 errors). My description of what RealGM offers is background knowledge only.
- Pro Sports Transactions: terms, copyright, rate policy, and NBA coverage start year (403; the "1947" start-year claim is unconfirmed).
- Hoops Rumors terms of use (404), and Hoops Rumors' data provenance.
- NBA.com "All NBA Transactions" archive depth and whether it can be accessed programmatically; whether NBA terms Section 9 applies to transactions or contract data.
- cbafaq.com: current status, whether it covers the 2023 CBA, and its license.
- SportsDataIO: whether any real-contract endpoint exists beyond the first ~160k characters of docs; any list price. Sportradar: whether any contract data feed exists; any price.
- BALLDONTLIE: left to the other agent; only advertised and third-party-reported claims seen.
- Stathead's current commercial terms; the $9/$80 price is from a search-surfaced FAQ that appears 2024-era.
- The effective date from which the 12th-15th pick-protection ban applies to newly negotiated trades (ESPN says protections already agreed remain in place and the new lottery starts with the 2027 draft; the exact start date for new trades is unreported in what I fetched).
- The 7-drafts-ahead pick-trading limit: found only in press summaries, not in the CBA or Constitution text I extracted.
- Max salary, BAE, Early Bird, expanded-TPE, cash-limit, and two-way figures for 2026-27: from Hoops Rumors, not the NBA release (I checked several for arithmetic consistency with the cap but did not find an NBA primary page for them).
- The 2027-28 cap projection (~$174M): Bleacher Report attribution via Hoops Rumors; projection only.
- Why one Spotrac team page snippet showed a second apron of $229.279M versus the official $221.686M; I could not open it to confirm the cause.
- I did not read Pivot's code, YAML, or schema. I cannot say whether the engine currently encodes the rules in section 4, or whether the "verified" YAML matches section 2.1 beyond what you told me.
- Legal conclusions: none of the rights notes here are legal advice; the commercial path needs counsel.
