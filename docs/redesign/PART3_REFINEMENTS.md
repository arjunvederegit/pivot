# Frontend refinements, Part 3 — review notes

Branch `feat/pivot-minimal-redesign`. Frontend only; no API, model, prompt or flag changes.

## Typography and surfaces
- One application sans: **Inter** (next/font/google, SIL OFL 1.1 — see `THIRD_PARTY_NOTICES.md`). OpenAI Sans was a visual
  reference only; no licensed asset is available, so Inter is the practical stand-in, not an exact match. Geist Mono stays for code.
  The serif Pivot logo is an image and is unchanged. Verified loaded in-browser (`document.fonts`: Inter 100–900).
- True black (`#000`) canvas, sidebar and top bar; cards `#0c0c0d`, raised `#141415`, borders `#2d2d30`.
- Roles: page title 1.65–2.1rem/600, section 1.25–1.5rem/600, descriptions 15.5px, dense tables stay 12–13px. No blanket scale-up.

## Browser tab titles
Root layout `title: { default: "Pivot", template: "Pivot - %s" }`; each route (or route layout) exports its tool name.
Verified on direct load, client navigation, refresh, back/forward and return home.

## Headshots — root cause
`<repo>/nbaplayerimages` (gitignored, user-supplied) did not exist in this checkout, so `make index-assets` had indexed 0 player
photos (`/assets/manifest` → `players: []`, coverage 0.0). A local copy of the dataset existed outside the repo (2,476 name-keyed
folders). Fix: symlink it to the supported default location (excluded via `.git/info/exclude`) and run the project's own
`index-assets`. The indexer's conservative exact/normalised name match plus recorded confidence is the *existing* mapping
(the dataset is name-keyed; there is no ID-keyed source). Result: 446 of 530 rostered players matched (84%); 84 have no photo in the
dataset (mostly 2025 rookies) and render the silhouette/initials fallback with no broken image. No frontend change was needed;
`PlayerPhoto` already gates on the manifest, fixes its size, and never retries.

## Loading investigation
Not reproducible as a production problem. Playwright, 1440×900, cold + repeat, production build: Team Outlook 163–353 ms to all 30
logos decoded; Strategy Lab 260–361 ms cold. Dev server (routes compiled): 260–540 ms. All 30 logos (≈3 MB, avg ~100 KB each, served at
source resolution) cold-fetch in ~80 ms locally. The slowness I saw earlier came from (a) first-compile in `next dev`, (b) the Browser
pane being hidden (throttled), and (c) a stale standalone server whose chunk files I had deleted. Upstream note: logos are served at
~900 px source size for 26–72 px slots; resized derivatives would cut payload ~10× (backend/asset task, out of scope).

## Metric-display trace (no model audit)
| Issue | UI → field → source | Finding |
|---|---|---|
| Knicks rim protection 3rd / 59th / 90th | diagnosis `needs[].percentile` ← `analytics/needs.py` (team **BLK per game** vs the other 29 teams, 3.4); skill coverage ← `intelligence._skill_coverage` (rim-protection **skill**, third-best of the **top-10 rotation**, vs league players); acquisition benchmark ← `acquisition._roster_strength` (third-best among **all** rostered players with minutes, 0.897) | Different measures by design (team stat / rotation skill / roster-wide skill) — labels clarified. **Upstream:** the two skill-based numbers use different populations (top-10 vs all with minutes) although a docstring says they are "the same statistic"; left unforced. |
| Ball security / defense not labelled strengths | `strengths-needs.tsx` ← backend classification (`domain/needs.py`: strength = severity 0 **and raw** percentile ≥ threshold) | Rule reads the raw percentile, which is not direction-aware for lower-is-better stats. Frontend already discloses it; no new rule invented. **Upstream/model.** |
| Chicago "Clear win" with incomplete checks | `fanVerdict(composite_utility, confidence)` (unchanged) | New `lib/assessment.ts`: headline is qualified ("Model favors … on the evaluated factors"), the category is labelled "Model category", main driver, range, model-reported chance, and limitations (unscored components, rule check incomplete, low confidence, unscored roster players) sit together. |
| Provenance | evaluation `evaluated_at` vs roster `source_retrieved_at`, season, `league_year` | Shown as separate facts; "rescaled weights" copy now says scores with different missing components are not directly comparable. |

## Ask Pivot
Conversation first (centred, 48 rem), compact header, one-line sandbox banner, five chips above the composer (Team, Mode, Strategy,
Constraints, Suggestions) opening popovers; one "Context" chip + drawer below `md`. Same editors, same payload; evidence docks beside the
conversation only after a citation/“Sources” is opened (drawer on narrow screens). Suggestions still send on selection (stated in the popover).

## Reuse notes (future workspace)
Shared: `team-card.tsx`, `lib/assessment.ts`, `components/trade/overview.tsx` panels take sides/ids as props and use existing hooks
(`useTeamDetail`, season-stats query). Coupling left alone: pages own routing/`useSearchParams`; the trade builder state lives in the page.
