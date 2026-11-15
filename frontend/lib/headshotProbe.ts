/**
 * Server-side existence check for official NBA headshots (development only).
 *
 * Why this exists: the NBA CDN never 404s. Any id — retired, never-listed, `0`, `999999999` — answers
 * HTTP 200 with a generic grey silhouette, and the CDN sends no CORS or Timing-Allow-Origin header,
 * so the browser can neither read those bytes nor their size. An `<img onError>` fallback therefore
 * never fires for a missing portrait. The server can see response headers, and the placeholder
 * announces itself: its `edge-cache-tag` names `.../fallback.png` and its ETag is a stable constant.
 *
 * The probe is a HEAD request — no image bytes are downloaded or stored here — and each answer is
 * remembered for the CDN's own max-age (12 h), so a roster-heavy page costs at most one HEAD per
 * player per half-day. The browser still loads every image directly from the CDN.
 */

import { officialHeadshotUrl, normalizeNbaPlayerId } from "@/lib/headshots";

/** ETag of the CDN's generic 260x190 placeholder, observed 2026-10-20. A second, independent signal. */
export const PLACEHOLDER_ETAG = '"7475ba9619305909d7998dd9dba02481"';

const POSITIVE_TTL_MS = 12 * 60 * 60 * 1000; // matches the CDN's `max-age=43200`
const NEGATIVE_TTL_MS = 12 * 60 * 60 * 1000;
const ERROR_TTL_MS = 60 * 1000; // a network failure is not an answer; look again soon
const MAX_CACHE_ENTRIES = 5000;
const PROBE_TIMEOUT_MS = 4000;
const CONCURRENCY = 8;
export const MAX_IDS_PER_REQUEST = 60;

type Fetcher = (url: string, init: RequestInit) => Promise<Response>;

interface CacheEntry {
  available: boolean;
  expires: number;
}

const cache = new Map<number, CacheEntry>();

/** True when response headers say the CDN served its generic placeholder, not a player's photo. */
export function isPlaceholderResponse(headers: Headers): boolean {
  const tag = headers.get("edge-cache-tag") ?? "";
  if (/\/fallback\.[a-z]+/i.test(tag)) return true;
  return headers.get("etag") === PLACEHOLDER_ETAG;
}

/** One uncached probe. Resolves to a verdict plus whether it is a real answer (vs. a failure). */
async function probeOnce(id: number, fetcher: Fetcher): Promise<{ available: boolean; definitive: boolean }> {
  const url = officialHeadshotUrl(id, "small") as string;
  try {
    const res = await fetcher(url, {
      method: "HEAD",
      cache: "no-store",
      signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
    });
    if (res.status === 404 || res.status === 403) return { available: false, definitive: true };
    if (!res.ok) return { available: false, definitive: false };
    const isImage = (res.headers.get("content-type") ?? "").startsWith("image/");
    return { available: isImage && !isPlaceholderResponse(res.headers), definitive: true };
  } catch {
    return { available: false, definitive: false };
  }
}

export async function probeOfficialHeadshot(
  id: number,
  fetcher: Fetcher = fetch,
  now: () => number = Date.now,
): Promise<boolean> {
  const hit = cache.get(id);
  if (hit && hit.expires > now()) return hit.available;
  const { available, definitive } = await probeOnce(id, fetcher);
  if (cache.size >= MAX_CACHE_ENTRIES) cache.clear();
  const ttl = !definitive ? ERROR_TTL_MS : available ? POSITIVE_TTL_MS : NEGATIVE_TTL_MS;
  cache.set(id, { available, expires: now() + ttl });
  return available;
}

/** Parse `?ids=1,2,3`: positive integers only, de-duplicated, capped. Anything else is dropped. */
export function parseProbeIds(raw: string | null): number[] {
  if (!raw) return [];
  const seen = new Set<number>();
  for (const part of raw.split(",")) {
    const id = /^\d{1,10}$/.test(part.trim()) ? normalizeNbaPlayerId(Number(part.trim())) : null;
    if (id !== null) seen.add(id);
    if (seen.size >= MAX_IDS_PER_REQUEST) break;
  }
  return [...seen];
}

/** Probe many ids with bounded concurrency. */
export async function probeMany(
  ids: number[],
  fetcher: Fetcher = fetch,
): Promise<Record<string, boolean>> {
  const out: Record<string, boolean> = {};
  let next = 0;
  const worker = async () => {
    while (next < ids.length) {
      const id = ids[next++];
      out[String(id)] = await probeOfficialHeadshot(id, fetcher);
    }
  };
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, ids.length) }, worker));
  return out;
}

/** Test seam. */
export function clearProbeCache(): void {
  cache.clear();
}
