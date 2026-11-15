# Official NBA headshots (opt-in, development-only, off by default)

Built on the R9.3A tip (`835c463`). Frontend only: no backend, migration, database or
environment-secret change.

> **Unresolved usage rights.** Pivot has no licence or written permission to use NBA photographs.
> The official source is therefore **off by default**. It runs only under `next dev` **and** only
> after an explicit `NEXT_PUBLIC_PIVOT_OFFICIAL_HEADSHOTS=on`; when on, the developer's own browser
> requests images from `cdn.nba.com`. Do not switch it on for tests, demos, screenshots or any shared
> or deployed environment. Production builds can never use it. Until permission is obtained this
> limitation stands, and Pivot's shipped behaviour is the local photo or the neutral placeholder.

## What it does

`PlayerPhoto` (`frontend/components/media.tsx`) — the one component that already drew every player
portrait in Pivot (17 call sites, all keyed on the canonical `nba_player_id`) — now resolves its image
through `frontend/lib/headshots.ts`:

1. **Official** — `https://cdn.nba.com/headshots/nba/latest/{1040x760|260x190}/{NBA_ID}.png`, only when the
   source is enabled (see *Rights gate*) **and** verified to exist.
2. **Local** — the existing user-supplied dataset served by `/api/v1/assets/players/{id}` (unchanged,
   still gated by the backend manifest).
3. **Silhouette / initials** — the existing neutral placeholder.

No consumer passes a URL. To swap the preferred provider later, edit `OFFICIAL_PROVIDER` in
`lib/headshots.ts`.

## Audit findings that shaped the design

| Finding | Consequence |
| --- | --- |
| One shared `PlayerPhoto`, 17 consumers, all pass `nba_player_id`; Pivot's own player ids are UUIDs | No new ID mapping needed; consumers were not edited (except one prop on the profile header) |
| Existing photos are a name-matched local dataset; **162 of 621 active players (26 %) have none** in the preview DB | The official source fills a real gap |
| **The NBA CDN never returns 404.** Any id — `0`, `999999999`, a retired player — gets HTTP 200 and a generic grey silhouette (identical bytes: 4937 B at 260x190) | An `onError` fallback never fires for a missing portrait. A server-side probe is required |
| The CDN sends no CORS / `Timing-Allow-Origin` header | The browser cannot read the bytes or their size to detect the placeholder |
| The placeholder announces itself in headers: `edge-cache-tag` names `.../fallback.png`, constant ETag | The probe (`lib/headshotProbe.ts`) HEADs the CDN and checks those |
| Images are transparent (PNG `tRNS`), 4:3, 12 h `max-age`, ~17 KB (260x190) / ~200 KB (1040x760) | Panel tone shows through; tier by display size; no URL versioning needed for freshness |
| Next config has no `images` block; every portrait is a plain `<img>` | Kept. The Next image optimizer would copy NBA bytes onto Pivot's origin (redistribution) |
| Old `PlayerPhoto` kept its `failed` state across an id change | Fixed: state is derived per source URL, element keyed by URL |

## Resolution logic

* **Tier by display size**: slots `>= 120 px` use 1040x760 (profile portrait, 152 px, with a
  `srcset` of 260w/1040w so 1x screens do not fetch 200 KB); everything smaller uses 260x190
  (all roster / list / search rows, 26–84 px).
* **Invalid ids** (`0`, negative, float, string, NaN, absent) never produce an official URL.
* **Pending**: while the probe has not answered, the neutral placeholder is drawn and *nothing* is
  requested from the CDN.
* **Probe** (`GET /api/headshot-status?ids=…`, a Next route handler): HEAD `260x190/{id}.png`; real image
  = `image/*` and not the fallback signature. Answers cached server-side 12 h (matching the CDN),
  failures 60 s. Client coalesces a whole roster into one request (≤ 50 ids) and caches via react-query.
  A failed probe degrades to the local photo.
* **Fallback chain without loops**: a URL that errors is remembered for the page session
  (`markHeadshotFailed`) and skipped; each source is tried at most once, then the placeholder (not an
  `<img>`, cannot fail).
* **Identity change**: output is derived from `nbaPlayerId` every render and the `<img>` is keyed by
  `src`; a previous player's face or failure can never carry over.
* **Loading**: `loading="lazy"` / `decoding="async"` everywhere; the profile portrait is
  `eager` + `fetchpriority="high"`; width/height are always set (no layout shift);
  `referrerpolicy="no-referrer"` on CDN requests.
* **Cropping**: unchanged `object-cover object-top` in the existing square / round masks.

## Historical protection

`PlayerPhoto` takes `historical`; it skips the official source entirely (no probe, no CDN request).
The profile header sets it from `!player.is_active`, so retired players keep their existing photo or
the silhouette (Michael Jordan, Vladimir Stepania, Scottie Lewis verified). No other view shows a
past season's portrait: `precedent.tsx` (historical trades) renders no player photos.

## Rights gate

A public CDN URL is not a licence to redistribute NBA photographs.

* `officialHeadshotsEnabled()` is true only when `process.env.NODE_ENV === "development"` (inlined by
  Next at build time, so a production bundle contains a constant `false`) **and**
  `NEXT_PUBLIC_PIVOT_OFFICIAL_HEADSHOTS === "on"` (exact value; unset or anything else is off).
  Test builds are off too. **The default is off**: nothing is requested from the NBA CDN unless a
  developer opts in.
* The probe route answers **404** whenever the gate is off — a deployed Pivot cannot be used to reach
  the CDN.
* Production behaviour is the pre-change behaviour: local photo or silhouette; no probe request, no
  CDN request (verified on a production build, see QA).
* The CDN host string still exists in the production bundle as a dead branch; no request is made.
* Screenshots of this work contain NBA portraits and are therefore **not committed** (kept locally,
  outside the repository).
* Automated tests never reach the CDN: they run with `NODE_ENV=test`, and the tests that exercise the
  official path stub `fetch`. Do not enable the flag for demos or QA runs.

Before enabling in production: obtain written NBA/licensor permission, then replace the gate (and
decide whether to proxy/cache under that licence).

## Known issues / limits

* **"Latest" is not "current team".** The CDN image can show a previous team's uniform (LeBron James
  renders in a Clippers jersey while Pivot's 2026-27 roster has him on PHI). Not a data error;
  inherent to `latest`.
* **Strategy Lab selection cards** resolve NBA ids from the *current* rosters of the deal's teams
  (existing, deliberate: "no guessing"). A saved deal naming a player no longer on those rosters
  (e.g. Christian Koloko in the E2E fixtures, who *does* have an official image, 1631132) shows initials,
  as it did before. Fix needs the trade API to return `nba_player_id` on assets (backend; out of scope).
* **Active players with no official image**: e.g. Dillon Mitchell (1641759) — the CDN serves its
  fallback, Pivot has no local photo, so the silhouette/initials show. Correct behaviour.
* The probe assumes 260x190 availability implies 1040x760 availability (true in every sample).
* Probe verified for ~20 ids by direct HEAD plus live page loads; there is no bulk scan of all
  ~620 active ids (deliberately: no mass scraping).
* Turbopack was **not** exercised for the build/dev QA (see QA report in the final summary):
  webpack was used because of the host's full disk. Nothing in the change is bundler-specific.

## Tests

`frontend/tests/unit/`: `headshots.test.ts` (URL format, invalid ids, tier, srcset, chain, historical,
gate), `headshot-probe.test.ts` (placeholder detection with real captured headers, cache/TTL, failure
handling, id parsing, route 404 in production), `player-photo.test.tsx` (component: official → local →
placeholder, no loops, id change, historical, production/test/unset-flag preserve existing behaviour,
single batched probe), `headshot-integration.test.ts` (Next config has no NBA image/rewrite entry, one
place builds URLs, every consumer uses `PlayerPhoto` with an NBA id — a new consumer must be added to
the list).

## Preview

```bash
# backend (example: a COPY of a database, never your live one)
cd backend && DATABASE_URL=sqlite:///<copy>.db PYTHONPATH=$PWD uvicorn app.main:app --port 8097
# frontend, development mode (default: official headshots OFF, local photo or placeholder)
cd frontend && NEXT_PUBLIC_API_URL=http://localhost:8097 npx next dev -p 3097
# only if you accept the rights limitation above, add NEXT_PUBLIC_PIVOT_OFFICIAL_HEADSHOTS=on to the command
```

With the flag unset, `http://localhost:3097/player-explorer` etc. show the local photo or the
placeholder. With `NEXT_PUBLIC_PIVOT_OFFICIAL_HEADSHOTS=on`, official headshots appear and your browser
requests them from the NBA CDN. `next build && next start` always uses the old behaviour.

## Revert

Nothing outside `frontend/`, `docs/headshots/` and one comment block in `.env.example` changed. `git revert <commit>` (or delete the branch /
worktree) restores the previous behaviour; no data or migration is involved.
