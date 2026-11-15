"use client";

/**
 * Client side of the official-headshot availability probe (development only).
 *
 * Every `PlayerPhoto` on a page asks "does the NBA have a real photo for this id?" in the same
 * render pass. Those questions are coalesced into one `/api/headshot-status?ids=...` request per
 * tick (a 30-row roster is one request, not thirty) and the answers are cached by react-query.
 */

import { useQuery } from "@tanstack/react-query";
import {
  HEADSHOT_PROBE_PATH,
  normalizeNbaPlayerId,
  type OfficialStatus,
} from "@/lib/headshots";

const BATCH_WINDOW_MS = 8;
const MAX_IDS_PER_REQUEST = 50;
const SIX_HOURS = 6 * 60 * 60 * 1000;

type Waiter = { resolve: (available: boolean) => void; reject: (error: unknown) => void };

const waiting = new Map<number, Waiter[]>();
let timer: ReturnType<typeof setTimeout> | null = null;

async function flush(): Promise<void> {
  timer = null;
  const batch = [...waiting.entries()];
  waiting.clear();
  for (let i = 0; i < batch.length; i += MAX_IDS_PER_REQUEST) {
    const chunk = batch.slice(i, i + MAX_IDS_PER_REQUEST);
    const ids = chunk.map(([id]) => id).join(",");
    try {
      const res = await fetch(`${HEADSHOT_PROBE_PATH}?ids=${ids}`);
      if (!res.ok) throw new Error(`headshot probe ${res.status}`);
      const body = (await res.json()) as { available?: Record<string, boolean> };
      for (const [id, waiters] of chunk) {
        const ok = body.available?.[String(id)] === true;
        waiters.forEach((w) => w.resolve(ok));
      }
    } catch (error) {
      chunk.forEach(([, waiters]) => waiters.forEach((w) => w.reject(error)));
    }
  }
}

export function fetchOfficialAvailability(id: number): Promise<boolean> {
  return new Promise((resolve, reject) => {
    const existing = waiting.get(id);
    if (existing) existing.push({ resolve, reject });
    else waiting.set(id, [{ resolve, reject }]);
    if (timer === null) timer = setTimeout(() => void flush(), BATCH_WINDOW_MS);
  });
}

/**
 * "off" when the official source is disabled or the id is unmapped (nothing is requested);
 * "pending" until the probe answers; then "available" or "unavailable". A failed probe counts as
 * unavailable, so a down dev server degrades to the existing local photo rather than to nothing.
 */
export function useOfficialHeadshotStatus(nbaPlayerId: unknown, enabled: boolean): OfficialStatus {
  const id = normalizeNbaPlayerId(nbaPlayerId);
  const active = enabled && id !== null;
  const query = useQuery({
    queryKey: ["official-headshot", id],
    queryFn: () => fetchOfficialAvailability(id as number),
    enabled: active,
    staleTime: SIX_HOURS,
    gcTime: SIX_HOURS,
    retry: false,
  });
  if (!active) return "off";
  if (query.isError) return "unavailable";
  if (query.isPending) return "pending";
  return query.data ? "available" : "unavailable";
}
