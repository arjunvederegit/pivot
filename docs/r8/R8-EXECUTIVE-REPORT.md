# R8 — Canonical Data Foundation: executive report

*For the product owner. 2026-10-18. Branch `feat/r8-canonical-data-foundation`; nothing merged to `main`.*

> **Superseded in part by [R8.1](../r8-1/R8-1-EXECUTIVE-REPORT.md)** (same day): "every dataset is stale" was a clock applied
> to finished seasons (only the rosters were); `nba_api` now runs through the canonical contract; live NBA.com
> and PostgreSQL were exercised; the BALLDONTLIE plan is 103 requests / 26 min, not ≈130 / 35.

## Did we actually complete R8?

**Yes for the foundation; with three things I want you to know are *not* done.** All ten acceptance
criteria are met as written ([validation-report.md](validation-report.md) §1). Not done:

1. **Nothing was tested against a live provider.** BALLDONTLIE's adapter and trial harness exist and are
   tested against synthetic fixtures; no key, no trial, no charge, no call. NBA.com was not contacted.
2. **`nba_api` still speaks its old dictionary protocol.** It is hardened but not yet re-expressed in the
   new canonical records; only the BALLDONTLIE adapter uses them.
3. **The new source/freshness fields are in the API but not on screen.** The redesign was preserved as
   instructed.

## What is technically stronger now

* **A statistic now says what it is.** We found one `minutes` column holding 34.8 for one player's
  per-game row and 2,265 for his season-total row. Every figure now carries its aggregation (per game /
  total), whether it was counted or modelled, and its season type (regular / playoffs) — which the database
  could not previously even store.
* **"Old" is now visible.** On your real database *every* dataset was stale — rosters 89 days, standings
  and stats 55 — and no page could have told a user. Every roster/stats payload now reports
  fresh / stale / missing and how complete it is. A player with no stats is "missing", never zero.
* **A bad answer from a provider can't wipe the league.** We reproduced it: an empty roster response
  left zero current rosters. Rosters are now refreshed as a diff with a sanity guard, and an empty or
  implausible answer is rejected and the old data kept. Roster refreshes also stopped duplicating rows
  (≈ 1.5 million a year at the worker's cadence).
* **One definition of "on this team now"**, replacing 20 hand-copied copies (two had no season filter —
  a trap when 2026-27 rosters arrive).
* **Identity that admits doubt.** A crosswalk table links other providers' ids to ours; namesakes are
  separated by draft year/birth date or reported as ambiguous — never guessed.
* **A provider-independent contract.** A call result distinguishes "the provider says there are none"
  from "the provider didn't answer". BALLDONTLIE was added with zero changes to any tool.

## How consistency across tools improved

Team Overview, Player Evaluation, Strategy Lab and Cap Lab now read the same roster, the same statistics
and the same definitions through shared code. On your real data: **1,705 cross-tool comparisons, 0
disagreements** — honest framing: they already agreed, by coincidence of one snapshot. R8 made the
agreement structural, and made the places it would have broken (traded players' totals attributed to
the new team, playoff rows overwriting regular-season rows, a stale snapshot) visible or impossible.

**Nothing in the analytics moved:** the three validation batteries are byte-identical before and after on
your real data, and 421 API responses are identical once the additive fields are ignored.

## What remains unreliable

* **Licensing, the big one.** NBA.com's Terms of Use (§9, which I read in full) limit NBA statistics to
  news reporting or private non-commercial use and require consent for a stats database or commercial
  product. `nba_api`'s MIT licence covers the code, not the data. Treat the current pipeline as
  *private development* until the NBA answers. Basketball-Reference also prohibits AI use of its content.
* NBA.com itself: every endpoint returned an error page 2026-10-07/10 (third-party report).
* **No contract data is loaded** (0 rows), and no verified licensed feed supplies guarantees, options,
  signed dates or no-trade clauses.
* Roster membership dates are first-seen times, not transaction dates.
* Your images and logos have **unknown provenance** (the photo folder is a symlink into `~/Downloads`);
  they are now flagged `unverified` and are development-only.

## Is Pivot ready to use BALLDONTLIE GOAT?

**Ready to *test* it; not ready to depend on it.** The adapter, the id-crosswalk and a 13-probe trial plan
(≈ 130 requests, ≈ 35 minutes at the trial's 5 requests/minute) are built. Documented, not observed: GOAT
$39.99/mo adds season/team averages by category (clutch, defence, shooting, playtype, tracking), advanced
v2 (≈ 100 fields), contracts + aggregates, injuries, starters, play-by-play. Documented limits: its
"lineups" are starting fives, not five-man combinations; it carries no per-year option/guarantee/no-trade
field; and BALLDONTLIE's terms explicitly do **not** clear the league's IP.

## Should you activate the trial?

**Yes — but after two free steps**, because the trial's 48-hour clock starts at signup and needs a card:
(1) refresh Pivot's stale data, and (2) create a free BALLDONTLIE key and run the identity crosswalk
(`make link-balldontlie`). Then start the trial with a cancel-by time, run `make balldontlie-evaluate`, and
subscribe **only if** the objective criteria in [the evaluation](balldontlie-goat-evaluation.md#5-objective-subscription-criteria)
pass: identity links ≥ 95 %, season averages agree with NBA.com ≥ 95 %, **and** it adds fields Pivot lacks
(defence/clutch ≥ 90 % filled; ≥ 90 % contract coverage with contract type present; a usable injury feed).
I did not start the trial or authorise any charge.

## Where to get player images and team logos legitimately

There is **no free, public, redistributable source** for current NBA headshots or team logos. The NBA and
ESPN CDNs are reachable but unlicensed; Getty/NBAE's NBA terms bar storage without NBA approval; the only
free-licensed material is a patchy Wikimedia Commons subset (59 % of NBA players on Wikidata have a photo;
attribution required; likeness rights not cleared) and almost no logos. Path: **now** — generated
monogram/abbreviation badges, local files kept dev-only; **next** — a SportsDataIO headshot quote
(vendor-licensed, quote-only) and the NBA Properties / Getty / NBPA conversation (a 22-question ask list is
in [research/D](research/D-images-and-logos.md)); **production** — three rights layers (marks, photos,
player likeness) and counsel. Pivot's own logo and design system are untouched.

## Which contract / statistical data do we still lack?

All contract data (the table is empty; the one prior source is rights-risky), and, at any price I could
verify: per-year guarantees/options as events, signed dates, no-trade clauses (CBA allows them only at
8+ years / 4+ with the team — a hand-curated table beats a feed), cap holds, exception ledgers, pick
ownership/protections. Statistically: injuries/availability, defensive/clutch/shot-zone/tracking
families, and licensed possession-level data. The cap/tax/apron numbers are **solved and free** (the
NBA's June releases; your YAML matches them field for field).

## Which documents to acquire for Ask Pivot

Free and public: the **2023 CBA** (nbpa.com/cba, 676 pp.), **CBA 101**, the **NBA Constitution**, the
**2026-27 Playing Rules**, the **stat glossary**, and the cap-year **press releases** (numbers go in a table,
not the vector store). Do not ingest Basketball-Reference or cbafaq. URLs, SHA-256s, licence posture and
the retrieval design (clause-level chunks, citation provenance, effective dates, precedence, number
guard, abstention) are in [ask-pivot-knowledge-sources.md](ask-pivot-knowledge-sources.md).

## Top five priorities

1. **Get the NBA data-rights answer** (consent under ToU §9 + an hour with counsel). It decides what can
   ship publicly. → [owner-resource-checklist.md](owner-resource-checklist.md) #1
2. **Refresh your data, then create a free BALLDONTLIE key** and run the id crosswalk. $0.
3. **Run the GOAT trial with the prepared harness** and apply the written criteria; cancel by hour 47 if
   it fails. $0 → $39.99/mo.
4. **Open the image-licensing conversations** (SportsDataIO quote; NBA Properties/Getty/NBPA) while the
   product runs on badges.
5. **Decide the contract-data path** (licence Spotrac/BDL contracts vs. build a transactions ledger plus a
   curated table), since R13–R14 are gated on it.

## What the next technical session should do

"R8.1 + R9 foundation" ([next-steps-roadmap.md](next-steps-roadmap.md) §4): season rollover (2026-27 starts
2026-10-20), `nba_api` through the canonical records, a persisted skill vector with model provenance, and a
rolling next-season backtest harness for the impact model — so R9's first claim is measured before it is
made.

## Things I did that you should know about

* **I migrated your development database.** I worked in your checkout on a feature branch; your
  `--reload` backend (port 8000) picked up the new code and returned HTTP 500 on `/season-stats` because
  `tradelab.db` lacked the new column. I backed it up first (`backend/tradelab.pre-r8.db`, gitignored),
  applied the additive migration (verified on a copy; 17,022 stat rows preserved; reversible) and your
  app recovered. The migrated DB also works with `main`'s code.
* While cleaning up my test servers a broad `pkill` also stopped your dev frontend on port 3000; I
  restarted it (it now runs under the app's preview harness rather than your terminal).
* I temporarily added QA entries to the session's `.claude/launch.json`; they are removed.
* Two research sub-agents downloaded the public CBA PDFs to a temp folder and sent four header-only
  requests to public image CDNs (more than the brief allowed); no image was downloaded and nothing was
  committed.

## Detailed reports

[audit](current-infrastructure-audit.md) · [architecture](architecture.md) ·
[implementation](implementation-report.md) · [validation](validation-report.md) ·
[resource research](resource-research.md) · [BALLDONTLIE GOAT evaluation](balldontlie-goat-evaluation.md) ·
[Ask Pivot sources](ask-pivot-knowledge-sources.md) · [**owner checklist**](owner-resource-checklist.md) ·
[roadmap](next-steps-roadmap.md)
