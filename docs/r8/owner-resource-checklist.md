# What to connect to Pivot next — owner checklist

*Ranked by practical return on investment. Prices are as advertised on 2026-10-18 and can change.
"Now / Later / After testing" is the timing recommendation. Evidence and caveats:
[resource-research.md](resource-research.md). I did not start any trial, create any account or authorise
any charge; every item below is yours to do or decide.*

## 0. Read this first

Two things shape every row:

* **NBA.com's Terms of Use (§9) limit NBA statistics to "news reporting or private, non-commercial
  purposes" and require consent for any product with a database of comprehensive, regularly updated
  stats.** Pivot's primary source, `nba_api`, is therefore a *private-development* source until the NBA
  says otherwise. Nothing you buy changes that by itself — including BALLDONTLIE, which grants you a
  generous licence of *its* rights and explicitly does not clear the league's.
* **Your data is stale right now** (rosters 89 days, standings and stats 55 days; the 2026-27 season
  starts 2026-10-20). Refreshing it is free and comes before any comparison or trial.

## 1. Ranked list

| # | Obtain | Why it matters | Where | Cost | What I need from you | Improves | When |
| ---: | --- | --- | --- | --- | --- | --- | --- |
| **1** | **A written answer on NBA data rights** — request NBA consent under Terms of Use §9 (or written confirmation that Pivot's intended use is outside it), and book an hour with a sports/IP lawyer | It decides whether anything public can ship, and which of items 2–9 are worth buying. Highest leverage per dollar in this list. | NBA.com legal/licensing contact (no public intake page was found — ask for the digital-licensing / data-partnerships contact); your counsel | $0 + counsel's fee (unknown) | the intended use stated plainly (public subscription analytics web app; no betting, no fantasy, no live play-by-play); your decision whether to approach the NBA yourself | everything public-facing | **Now** |
| **2** | **Refresh Pivot's own data** (`make refresh DATASETS=rosters,standings,player-stats,team-stats,games`, then `make data-status`) and, once 2026-27 begins, set `CURRENT_SEASON=2026-27` and re-sync | Every dataset is stale; any comparison, trial or demo on 55–89-day-old data misleads. `data/imports/contracts/` is also empty. | your machine (NBA.com must be reachable; it was unreliable 2026-10-07/10 — if it fails, that *is* a finding) | $0 | nothing but running it; `ROSTERLAB_OFFLINE` must be unset | all tools | **Now** |
| **3** | **A free BALLDONTLIE account and key** (no card), then `make link-balldontlie DRY=1` | Builds and tests the cross-provider identity crosswalk (~13 min at 5 requests/min) and gives an independent teams/players/games source — the cheapest possible look at BDL's data quality. | https://www.balldontlie.io (sign up; free tier = teams, players, games) | $0 | create the account; put `BALLDONTLIE_API_KEY=` in `backend/.env` (gitignored); set `BALLDONTLIE_TIER=free`. **Do not paste the key into chat.** | identity foundation for R9–R14 | **Now** |
| **4** | **BALLDONTLIE GOAT 48-hour trial → subscribe only if the criteria pass** | Contracts, advanced/tracking/clutch/defence families, injuries, a second source if NBA.com blocks. $39.99/mo ($383.90/yr). | https://www.balldontlie.io (GOAT trial). **A payment method is required at signup; cancel in the dashboard before 48 h if not subscribing.** | $0 for 48 h, then $39.99/mo | complete items 2 and 3 first; start the trial when you can spend ~1 hour watching; `make balldontlie-plan`, then `make balldontlie-evaluate`; read [the criteria](balldontlie-goat-evaluation.md#5-objective-subscription-criteria) | player evaluation (R9), archetypes (R10), Cap Lab, availability | **After testing** (items 1–3) |
| **5** | **A licensed-images conversation**: ask SportsDataIO for a quote for NBA headshots (and what covers logos); ask the NBA/NBPA the questions in the research ask list | There is no free licensed source for current headshots or team logos. Until then Pivot's local files are development-only and are now marked `unverified`. | https://sportsdata.io/developers/sports-player-headshots-api (sales); NBA Properties / NBAE via Getty; NBPA group licensing (via nbpa.com) | quote-only | your use case; the 22-question ask list in [research/D](research/D-images-and-logos.md) §4 | headshots/logos in every tool | **Now (ask)**; buy **later** |
| 6 | **Contract-data licence decision**: request Spotrac's data-licensing terms in writing; compare with BALLDONTLIE contracts after the trial | No verified licensed, structured contract feed exists. Basketball-Reference (your current snapshot) forbids AI use of its content. Contract *type*, signed date and no-trade clauses must be built from a transactions ledger plus a hand-curated table. | Spotrac (contact only — live terms returned HTTP 402 to my tools); Sports-Reference custom data requests start at $5,000 per their data-use page | unknown / ≥ $5,000 | your decision on whether the BBRef snapshot is dev-only (recommended: yes, and never shown to an LLM) | Cap Lab, trade legality, R13/R14 | **Later**, after items 1 and 4 |
| 7 | **The free official documents for Ask Pivot**: 2023 CBA (nbpa.com/cba), CBA 101, NBA Constitution, 2026-27 Rulebook, stat glossary, the two cap-year releases | They are the authoritative text for every rules question; free and public. | URLs and SHA-256s in [ask-pivot-knowledge-sources.md](ask-pivot-knowledge-sources.md) | $0 | download into a gitignored `data/documents/`; do not commit; ask the NBA (item 1) before shipping their text to public users | Ask Pivot (rules answers), CBA engine tests | **Later** (when Ask Pivot moves off the fictional corpus) |
| 8 | **BBall-Index subscription** (LEBRON, 12 offensive roles) | The best external benchmark for archetype work; $5/mo or $52.50/yr; private subscriber use only. | https://www.bball-index.com | $5/mo | your personal account; the data is not redistributable | R10 archetypes (validation, not ingestion) | **Later** (start of R10) |
| 9 | **A SportsDataIO quote** (NBA data + headshots, commercial licence) | The only vendor whose published rights page addresses caching, derived display and ML. | https://sportsdata.io (sales) | quote-only; third-party guess $500–1,000+/mo, unverified | the use-case paragraph from item 1 | the commercial production stack | **After testing**, once item 1 answers |
| 10 | **Free monitoring**: Sentry (free), Better Stack (free uptime + job heartbeats) | A missed heartbeat tells you the scheduler died — and nothing today would. | sentry.io, betterstack.com | $0 | accounts; DSN in `.env` | reliability | **Later** (when deployed) |
| — | **MySportsFeeds** (CAD ~29/mo commercial; 14-day trial) | An independent commercial feed — but its data licence was not readable. | https://www.mysportsfeeds.com | CAD ~29/mo | read its data terms *before* the trial | fallback if BDL fails | **Only if BALLDONTLIE fails** |

## 2. What I could **not** tell you

Which NBA contract fields Pivot still lacks is the *whole* set: **no contract data is loaded on this
machine** (the table is empty), and the one source you had (a Basketball-Reference snapshot) is the
rights risk above. What no feed I could verify supplies at any price: per-year **guarantees and
options as events**, **no-trade clauses**, **signed dates**, **cap holds**, **exception ledgers** and
**pick protections** — those come from the CBA, the NBA Constitution and a transactions ledger, which
is why [next-steps-roadmap.md](next-steps-roadmap.md) builds them rather than buys them.

## 3. Your five minutes of preparation for the trial

1. `make refresh DATASETS=rosters,standings,player-stats,team-stats,games` → `make data-status` (all `fresh`).
2. Put the key in `backend/.env`; `BALLDONTLIE_TIER=trial`.
3. `make balldontlie-plan` (≈ 130 requests, ≈ 35 minutes at trial pacing).
4. Decide your cancel-by time (start + 47 h) *before* you start.
5. Run `make balldontlie-evaluate`. Re-read results offline any time with `make balldontlie-evaluate REPLAY=<dir>`.
