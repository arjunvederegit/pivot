# Pivot minimal redesign — review notes

Branch `feat/pivot-minimal-redesign`, cut from `feat/pivot-rag-synthetic` @ `1bd4ceb` (clean tree). Frontend only:
no backend, API, model, retrieval or feature-flag changes.

## What changed
- **Tokens** (`frontend/app/globals.css`): monochrome neutral palette, white as the interactive accent, 3–8px radii,
  no shadows/glows. Green/red (`--legal`/`--illegal`) are the only chromatic colours. Display serif is Source Serif 4;
  UI sans is Geist with tabular numerals.
- **Shell** (`components/shell.tsx`): logo header, compact nav (same routes, same labels), Team Context + data status
  footer, top toolbar with global search (`/` or ⌘K) and a team selector. Mobile drawer kept.
- **Trade Evaluator** (`app/trade-evaluator/page.tsx`, `components/trade/*`): "Trade Analysis" page, compact player
  tables (age, salary, contract years, league-impact percentile), Save Scenario + ⋯ menu, swap indicator, and four
  result panels — Projected Impact, Team Comparison, Player Comparison, AI Insights (`components/trade/overview.tsx`).
  Every figure is a served value; the insights are templated from the evaluation's own numbers; unavailable data
  is stated, never estimated. The detailed before/move/after sheet and full team analysis remain below.
- **Team selection** (`components/team-card.tsx`): one shared card/grid used by Home, Teams, the trade board and
  picker, and the top-bar team selector.
- **Pages**: Home header compacted (decorative arena illustration removed); everything else restyled via tokens.

## Team card decision
Logos are transparent (the backend derives them on index), but most are drawn with black outlines/wordmarks:
on charcoal SAS, UTA, BKN, MIA, POR and CHI become unreadable. So **dark-by-default fails**; the shipped
treatment is light-gray tiles (white on hover, white + ring + check when selected, white focus ring), inside dark panels.
Logos on dark chrome elsewhere get a 1px white keyline (`.logo-keyline`) instead of a plate. `[data-team-tone="dark"]`
restores the dark-default variant if wanted.

## Verification
- `tsc`, `eslint`: clean. `vitest`: 297/297. `next build`: passes with the flag off and on.
- Flag off: `/assistant` serves the ordinary 404, no nav entry. Flag on (+`ASSISTANT_ENABLED` backend): nav entry,
  a real question answered with citations and the evidence inspector.
- Browser-checked at 1440, 1280 and 375 (no horizontal overflow). Not run: Playwright e2e (the suite refuses to run
  against a non-demo database; its Trade heading expectation was updated to "Trade Analysis").

## Known gaps
- Logo: see the final message — the orange-basketball source image must be saved to disk to extract it exactly
  (`scripts/brand/extract_logo.py`). `public/brand/pivot-logo.svg` is currently the earlier all-white trace.
- No player headshots render in this environment (asset manifest lists none); the silhouette fallback shows.
- Reference columns BPM/OVR, team-selector account and cap figures are not shown: there is no served data for them.
