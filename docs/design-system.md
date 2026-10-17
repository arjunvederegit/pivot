# Pivot design system — editorial

A dark, editorial basketball-intelligence product: the restraint of a print scouting
report, the density of an analytics terminal. Every token lives in
`frontend/app/globals.css`; nothing below should be re-declared in a page component.

## Principles

- **Credible before cinematic.** One illustrated arena opens the home page. Everywhere else
  the content carries the identity: team crests, player silhouettes, court diagrams that
  mean something, the composition of a scouting report.
- **Every number has context.** What it measures, its scale or units, which direction is
  better, its baseline, its season or sample and its source. Missing data reads as "—",
  "unscored" or an `UnavailableNotice`, never as 0 or an average.
- **One accent.** A subdued mint marks the primary action, the active state and the one
  key signal on a surface. Most text is warm white or muted grey.
- **No neon, glows, heavy gradients, glassmorphism or emojis.** Radii are 4–16px. Cards
  never go giant-round.

## Colour

All surfaces are dark. The `.scope-paper`, `.scope-terminal` and `.panel` scopes from the
previous system still exist, but they now carry identical values, so old markup restyles
in place.

| Token | Value | Utility | Job |
| --- | --- | --- | --- |
| `--court-black` / `--ink` | `#0B0E0D` | `bg-court`, `bg-ink` | page ground |
| `--arena` | `#151A18` | `bg-panel`, `bg-arena` | cards, the sidebar |
| `--arena-raised` | `#1C2220` | `bg-panel2` | elevated cards, inputs |
| `--arena-high` | `#252C29` | `bg-panel3` | hover fills, meter tracks |
| `--hairline` / `--hairline-soft` | `#2D3531` / `#222A26` | `border-line` / `border-hairline` | borders, quiet dividers |
| `--chalk` | `#F0EEE8` | `text-foreground` | warm white text |
| `--chalk-dim` | `#A3ACA7` | `text-muted` | secondary text |
| `--chalk-faint` | `#808A85` | `text-faint` | tertiary text (≥ 4.5:1 on the ground) |
| `--signal` / `--volt` | `#86C9A5` | `text-signal`, `bg-signal`, `bg-volt` | the single accent |
| `--legal` | `#8CC79A` | `text-legal` | positive, passes |
| `--illegal` | `#E08A77` | `text-illegal` | negative, fails (an understated coral) |
| `--conditional` | `#DDB873` | `text-conditional` | conditional, warning |
| `--unknown` | `#8D9692` | `text-unavail` | not measured, unavailable |
| `--series-b` | `#C8C3B8` | `bg-seriesb` | the neutral comparison series |
| `--paper` | `#F0EEE8` | `bg-paper`, `bg-paperhi` | the light plate behind team logos |
| `--hardwood` | `#B98A5A` | `text-hardwood` | the hero floor and rare warm accents |

**Rules**

- Status is never carried by colour alone. Every `Badge` renders a glyph (✓ ✕ ~ —) beside
  a word.
- Team colours (`lib/teamIdentity.ts`) are accents only: the thin `--edge` line on top of a
  `.panel`, a crest plate, chart marks. Never set team-coloured body text.
- `.panel` and `.sheet` carry a soft shadow (`--shadow-panel`). Nothing glows.

## Typography

| Role | Face | Class |
| --- | --- | --- |
| Page titles, section headings, hero statements | **Instrument Serif** 400 (italic for one emphasised phrase) | `.title-xl` (the page H1), `.title-lg` (section H2), `font-serif` |
| Headline statistics | Instrument Serif with lining, tabular numerals | `.stat-serif` |
| Interface: navigation, controls, tables, player details | **Geist** 400–600 | default `font-sans` |
| Card titles | Geist 600 | `.title-md`, `.title-sm` |
| Tabular figures in rows | Geist 600, tabular | `.numeral` |
| Small labels | Geist 500, 11px caps, 0.09em tracking | `.eyebrow` |
| Code | Geist Mono | `code`, `kbd`, `pre`, `samp` |

`font-mono` maps to Geist, so older mono labels render in the interface face; new code
uses plain sans classes and keeps uppercase tracking for small labels only. No text is set
below 10px. Titles wrap with `text-balance` and are never `whitespace-nowrap`.

Instrument Serif's "1" is narrow. In large serif figures it can read close to an "l";
keep serif figures for headline statistics and use `.numeral` in rows and tables.

## Navigation

`components/shell.tsx` owns the frame.

- **Desktop (≥1024px):** a persistent left sidebar with line icons
  (`components/icons.tsx`), grouped Workspace / Decisions / Reference, a global search
  (`/` focuses it), the data-status line and a collapse toggle. The collapsed state (76px
  rail with tooltips) is stored in `localStorage` under `pivot.sidebar` and read through
  `useSyncExternalStore`, so the server render is always the expanded layout.
- **Phones and tablets:** a sticky top bar with the brand, search and a menu button that
  opens a drawer (`#mobile-nav`). Escape closes it and returns focus to the button.
- `/` and `/command-center` render full-bleed; every other route sits in a padded
  `max-w-[1480px]` main with `id="main"`, the target of the skip link.

## Surfaces and components

- **`.panel` / `<Panel>`:** the card. Charcoal surface, 12px radius, a hairline frame and
  an optional `--edge` team line along the top.
- **`.sheet`:** an elevated inner card.
- **`<PageHeader>`:** an eyebrow with a round accent dot, a serif `title-xl`, a muted lede
  and the page's meta badges, closed by a hairline.
- **Section heads:** an eyebrow over a serif `title-lg` H2, with an optional muted aside.
- **`<StatBlock>`, `<MeterBar>`, `<Tabs>`, `<SegmentedControl>`, `<Badge>`,
  `<SourceRail>`, `<EmptyState>`, `<UnavailableNotice>`, `<Explainer>`:** in
  `components/ui.tsx`. Pages compose these; they do not re-implement them.
- **`<PlayerPhoto>`** (`components/media.tsx`): a photo when one exists, otherwise a
  neutral silhouette at 48px and up and initials below that. Pivot ships no player photos,
  so the silhouette is the normal state, not an error. **`<TeamLogo>`** sits on a light
  plate so every crest stays legible on dark.

Feature areas keep their own composed parts under `components/<area>/`: `home`,
`command`, `players`, `teams`, `trade`, `strategy` and `reference`.

## Impact, fit, projection: three different things

| Concept | Component | Scale |
| --- | --- | --- |
| On-court impact | `ImpactCell`, `ImpactInline`, `ImpactReadout` (`components/impact.tsx`) | league percentile and rank among every scored player; the band is placed as a likely range; the raw index (a unitless TEI, not points per 100 possessions) is shown at 2 dp in readouts and every tooltip |
| Team-conditional fit | `FitChip`, `FitReadout` (`components/fit.tsx`) | 0–100, where 50 = no change. Direction, not size. Can be withheld, with the server's reason. |
| Projected trade impact | the Trade Evaluator's Before → Proposed move → After sheet (`components/trade/`) | wins change over a full 82-game season with its p10–p90 band, on one shared axis per deal |

Never sum player indices into a "net impact", and never show a precedent's `tei` as a
league percentile. Acquisition-target wins are a point estimate (the API serves no band),
and every surface that shows them says so. See `docs/pivot/impact-metric-findings.md`.

## Basketball geometry

- `components/home/arena-hero.tsx` draws the home hero: a static, illustrated arena (light
  ring, centre-hung board, hardwood in perspective). It is atmosphere and carries no data.
- `components/court.tsx` holds the functional court furniture: `HalfCourt`, `KeyFrame`,
  `TransactionLane` and `BallGlyph`.
- Pivot has no shot-location data, so no figure may imply a shot chart. `ShotChartMotif`
  is retired and draws nothing.

## Motion

Durations are `--dur-fast` 140ms and `--dur-base` 240ms, eased with `--ease-out`.

- `.fade-in`: content arriving.
- `.hover-frame`: a card's frame warms toward the accent on hover; nothing moves.
- `.lane-in`: an asset arriving in a trade slot.
- `.pulse-live`: the live-data dot.
- The skeleton shimmer.

There is no scroll-jacking, parallax or animated background. Under
`prefers-reduced-motion` every animation stops and transitions collapse to near zero.

## Layout and wrapping

Body copy wraps freely. Labels, badges and buttons truncate rather than wrap, with the full
text in a tooltip. Wide tables live inside `overflow-x-auto` with a `min-w`. Fixed bottom
bars on phones carry `data-bottom-bar`, which pads the scroll so focus is never hidden
behind them.

`scripts/visual_qa.mjs` screenshots every route at seven viewports (1920 to 390) and
fails on any horizontal overflow, console error or empty page.
