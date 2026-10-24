# R8.3 — What you need to do

Only what cannot be done for you. In order.

## A. Today / this week

- [ ] **Put the key where the app reads it.** The app reads `BALLDONTLIE_API_KEY` from `backend/.env` (or
      `<repo>/.env`) — **not** from `.env.local`, which is where the key was handed to this session. In your real
      checkout, edit the gitignored `backend/.env`:
      ```
      BALLDONTLIE_API_KEY=<your key>
      BALLDONTLIE_TIER=goat
      ```
      Never commit it; never paste it in chat. (`git check-ignore -v backend/.env` should print a rule.)
- [ ] **Delete stray copies of the key** (the loose `.env.local` files). Rotate it in the BALLDONTLIE dashboard only
      if one was ever synced or backed up somewhere shared; it was never printed, logged or committed here.
- [ ] **Review and merge `feat/r8-3-goat-live-integration`** (based on R8.2's activation checkpoint). It is **not**
      merged to `main`, and **nothing in it ran against your real database**. Do not copy
      `data/imports/balldontlie/` into Git (it is gitignored: ~12 MB of provider responses).
- [ ] **10-contract spot check** (15 min): pick ten players from `GET /api/v1/teams/{id}/payroll`, compare their
      cap hits with a source you trust. Until this passes, treat contract figures as *coverage-verified, accuracy-unverified*.

## B. Activation checkpoint (a separate, reviewed step — not done here)

Your `tradelab.db` is at migration `a9d4c6e18b53` (R8): it still needs R8.1's `c5e2b7d91f34` **and** R8.3's
`e8b3f5c2a914`. Both are additive.

```bash
cp backend/tradelab.db ~/pivot-backups/tradelab-pre-r83-$(date +%F).db     # backup first
cd backend && .venv/bin/alembic upgrade head
make refresh-balldontlie WHAT=identities           # ~7 requests
make refresh-balldontlie WHAT=contracts            # 30 requests
make refresh-balldontlie WHAT=injuries             # 1 request
make refresh-balldontlie WHAT=stats                # ~14 requests (2025-26 base + advanced)
# optional, +~40 requests: GROUPS=general/base,general/advanced,clutch/base,defense/overall,shooting/by_zone,tracking/possessions,hustle
make contract-coverage                              # expect ≈ 434/526 priced; 0/30 teams verified
```
`DRY=1` rolls everything back; `CACHE=<dir>` re-uses recorded responses. Then decide:

- [ ] **`CONTRACT_DATA_PROVIDER=balldontlie` in `backend/.env`** — recommended *after* the checkpoint. Off: payroll
      still shows as a lower bound but rules say "no contract provider is configured". On: rules say "this player has
      no salary on file", which is the true statement. Neither makes any trade look legal.

## C. Decisions only you can make

- [ ] **Subscription.** Recommendation: **keep GOAT ($39.99/mo) for now**, re-decide about **2026-11-15** against
      three tests: contract coverage after the cut-down (≥ 90 %, or the remainder labelled non-counting), injury
      freshness measured on two game nights, and R9.1's backfill completed. If you only need injuries,
      ALL-STAR ($9.99) covers them; contracts and the statistic families are the GOAT-only part. Do nothing in the
      dashboard automatically — this session did not touch billing.
- [ ] **Licensing / counsel.** BALLDONTLIE's licence does not clear NBA.com's terms for the underlying statistics, and
      R8.3 found the two sources numerically identical. This blocks any public product, not private development.
- [ ] **Request budgets** (each needs a yes, because it exceeds the ≈200 guideline): contract aggregates
      ≈ 454 requests (lengths, guarantees, option text); R9.1 history backfill ≈ 260 requests.
- [ ] **Roster type source** (two-way / Exhibit 10 / camp): the 60 + 30 uncovered players cannot be classified
      without one. Options: NBA transactions ledger (free, work), a licensed feed (money).

## D. Dated

- [ ] After **2026-10-20** (opening night) and again after the **cut-down**: `make refresh DATASETS=rosters` then
      `make refresh-balldontlie WHAT=identities,contracts`.
- [ ] **Do not run `make train` / `make score`** until R9.1 makes training candidate-only (R8.2 runbook §5 still applies).
- [ ] Your own servers (8010 etc.) were never touched; nothing to restart.
