/**
 * Player headshot resolution — the one place that knows where a portrait can come from.
 *
 * Two sources, tried in order:
 *  1. `official` — the NBA's own CDN, keyed by the canonical NBA player id (`players.nba_player_id`;
 *     Pivot's internal player ids are UUIDs and are never used here).
 *  2. `local` — the user-supplied photo dataset served by the backend asset route
 *     (`/api/v1/assets/players/{id}`), which is what Pivot has always shown.
 * When neither has a usable image the caller draws its neutral silhouette.
 *
 * RIGHTS: a public CDN URL is not a licence to redistribute NBA photographs. The official source is
 * therefore opt-in and development-only (`officialHeadshotsEnabled`): off unless explicitly switched
 * on, and it compiles to "off" in a production build, which leaves the local source — Pivot's
 * existing public behaviour — exactly as it was.
 *
 * Swapping the preferred provider later means editing `OFFICIAL_PROVIDER` below; no consumer
 * passes a URL, so none of them changes.
 */

import { playerPhotoSrc } from "@/lib/teamIdentity";

export type HeadshotTier = "large" | "small";
export type HeadshotSourceKind = "official" | "local";

/** What the availability probe has concluded about the official image for one player. */
export type OfficialStatus = "off" | "pending" | "available" | "unavailable";

export interface HeadshotSource {
  kind: HeadshotSourceKind;
  src: string;
  /** Present only for the large tier, so the browser picks 260w or 1040w for its pixel density. */
  srcSet?: string;
}

export type HeadshotResolution =
  /** The availability probe has not answered yet; draw the neutral placeholder, request nothing. */
  | { state: "pending" }
  | { state: "image"; source: HeadshotSource }
  | { state: "placeholder" };

const NBA_HEADSHOT_BASE = "https://cdn.nba.com/headshots/nba/latest";

const DIMENSIONS: Record<HeadshotTier, string> = { large: "1040x760", small: "260x190" };
const WIDTHS: Record<HeadshotTier, number> = { large: 1040, small: 260 };

/**
 * Display sizes (CSS px) from this up use the 1040x760 file; everything below uses 260x190. A 260 px
 * source still covers a 130 px slot at 2x, and roster-heavy views draw 26-84 px slots, where a
 * ~200 KB file per row would be a pure cost. Only the profile portrait (152) and larger qualify.
 */
export const LARGE_HEADSHOT_MIN_PX = 120;

/** Where the official availability probe lives (a development-only route of this app). */
export const HEADSHOT_PROBE_PATH = "/api/headshot-status";

/**
 * The canonical NBA id, or null. Real NBA person ids are positive integers; anything else
 * (0, negatives, NaN, floats, numeric strings, absent) is "unmapped" and gets no official URL,
 * because the CDN answers *any* id — including garbage — with HTTP 200.
 */
export function normalizeNbaPlayerId(id: unknown): number | null {
  return typeof id === "number" && Number.isSafeInteger(id) && id > 0 ? id : null;
}

export function headshotTierForSize(sizePx: number): HeadshotTier {
  return sizePx >= LARGE_HEADSHOT_MIN_PX ? "large" : "small";
}

const OFFICIAL_PROVIDER = {
  url(id: number, tier: HeadshotTier): string {
    return `${NBA_HEADSHOT_BASE}/${DIMENSIONS[tier]}/${id}.png`;
  },
};

export function officialHeadshotUrl(id: unknown, tier: HeadshotTier): string | null {
  const nbaId = normalizeNbaPlayerId(id);
  return nbaId === null ? null : OFFICIAL_PROVIDER.url(nbaId, tier);
}

/**
 * The official source is OFF by default and development-only. It needs BOTH `next dev` and an
 * explicit `NEXT_PUBLIC_PIVOT_OFFICIAL_HEADSHOTS=on`; any other value, or none, leaves it off.
 * `process.env.NODE_ENV` is inlined by Next, so a production bundle contains `false` here, not a
 * runtime check an environment variable could flip. Tests run with NODE_ENV "test" and so never
 * reach the network unless they stub both values. NBA reuse rights are NOT cleared: switching this
 * on makes the developer's own browser request images from the NBA CDN.
 */
export function officialHeadshotsEnabled(): boolean {
  if (process.env.NODE_ENV !== "development") return false;
  return process.env.NEXT_PUBLIC_PIVOT_OFFICIAL_HEADSHOTS === "on";
}

export function officialSource(id: unknown, tier: HeadshotTier): HeadshotSource | null {
  const src = officialHeadshotUrl(id, tier);
  if (src === null) return null;
  if (tier === "small") return { kind: "official", src };
  const small = officialHeadshotUrl(id, "small") as string;
  return {
    kind: "official",
    src,
    srcSet: `${small} ${WIDTHS.small}w, ${src} ${WIDTHS.large}w`,
  };
}

export interface ResolveInput {
  nbaPlayerId: unknown;
  /** Display size in CSS pixels; decides the tier. */
  size: number;
  /** A past-season view: the current official portrait would misstate the context. */
  historical?: boolean;
  official: OfficialStatus;
  /** Whether the backend manifest lists a local photo for this player. */
  localKnown: boolean;
  /** Source URLs that already failed to load this session — never retried, so no loop. */
  failed: ReadonlySet<string>;
}

/** First usable source in preference order; pure, so the whole fallback chain is testable. */
export function resolveHeadshot(input: ResolveInput): HeadshotResolution {
  const { nbaPlayerId, size, historical = false, official, localKnown, failed } = input;
  const useOfficial = !historical && official !== "off" && official !== "unavailable";
  if (useOfficial && official === "pending") return { state: "pending" };

  const candidates: HeadshotSource[] = [];
  if (useOfficial && official === "available") {
    const source = officialSource(nbaPlayerId, headshotTierForSize(size));
    if (source) candidates.push(source);
  }
  const localSrc = localKnown ? playerPhotoSrc(normalizeNbaPlayerId(nbaPlayerId)) : null;
  if (localSrc) candidates.push({ kind: "local", src: localSrc });

  const usable = candidates.find((c) => !failed.has(c.src));
  return usable ? { state: "image", source: usable } : { state: "placeholder" };
}

// ---------------------------------------------------------------------------------------------
// Session memory of images that failed to load. A module-level set (not component state) so a
// roster row that unmounts and remounts — a filter, a tab, a virtualised scroll — does not request
// a known-bad URL again; it lives only until the page reloads, so nothing is locked in for good.

const failedSources = new Set<string>();
const listeners = new Set<() => void>();
let failedVersion = 0;

export function markHeadshotFailed(src: string): void {
  if (failedSources.has(src)) return;
  failedSources.add(src);
  failedVersion += 1;
  listeners.forEach((l) => l());
}

export function subscribeFailedHeadshots(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function failedHeadshotsVersion(): number {
  return failedVersion;
}

export function failedHeadshotSet(): ReadonlySet<string> {
  return failedSources;
}

/** Test seam. */
export function resetFailedHeadshots(): void {
  failedSources.clear();
  failedVersion += 1;
  listeners.forEach((l) => l());
}
