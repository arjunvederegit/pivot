import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET } from "@/app/api/headshot-status/route";
import {
  MAX_IDS_PER_REQUEST,
  PLACEHOLDER_ETAG,
  clearProbeCache,
  isPlaceholderResponse,
  parseProbeIds,
  probeMany,
  probeOfficialHeadshot,
} from "@/lib/headshotProbe";

beforeEach(clearProbeCache);
afterEach(() => vi.unstubAllEnvs());

// Real header sets, captured from the CDN on 2026-10-20.
const REAL = {
  "content-type": "image/png",
  etag: '"120c739c158dd01907250e21a1a771a1"',
  "edge-cache-tag": "nba-headshots,wp-content/uploads/headshots/nba/latest/260x190/2544.png",
};
const PLACEHOLDER = {
  "content-type": "image/png",
  etag: PLACEHOLDER_ETAG,
  "edge-cache-tag":
    "nba-headshots,wp-content/uploads/headshots/nba/latest/260x190/999999999.png,wp-content/uploads/headshots/nba/latest/260x190/fallback.png",
};
const respond = (headers: Record<string, string>, status = 200) => new Response(null, { status, headers });

describe("placeholder detection", () => {
  it("recognises the CDN's generic silhouette by its fallback tag", () => {
    expect(isPlaceholderResponse(new Headers(PLACEHOLDER))).toBe(true);
  });

  it("recognises it by its ETag alone if the tag header is ever dropped", () => {
    expect(isPlaceholderResponse(new Headers({ etag: PLACEHOLDER_ETAG }))).toBe(true);
  });

  it("does not mistake a real portrait for the placeholder", () => {
    expect(isPlaceholderResponse(new Headers(REAL))).toBe(false);
  });
});

describe("probeOfficialHeadshot", () => {
  it("is available for a real portrait and unavailable for the placeholder (both are HTTP 200)", async () => {
    const fetcher = vi.fn(async (url: string) => respond(url.includes("/2544.") ? REAL : PLACEHOLDER));
    expect(await probeOfficialHeadshot(2544, fetcher)).toBe(true);
    expect(await probeOfficialHeadshot(999999999, fetcher)).toBe(false);
  });

  it("uses HEAD on the fixed CDN host — no image bytes are fetched", async () => {
    const fetcher = vi.fn(async () => respond(REAL));
    await probeOfficialHeadshot(2544, fetcher);
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://cdn.nba.com/headshots/nba/latest/260x190/2544.png");
    expect(init.method).toBe("HEAD");
  });

  it("treats a non-image 200 as unavailable", async () => {
    const fetcher = vi.fn(async () => respond({ "content-type": "text/html" }));
    expect(await probeOfficialHeadshot(1, fetcher)).toBe(false);
  });

  it("remembers answers, so a repeat costs no request", async () => {
    const fetcher = vi.fn(async () => respond(REAL));
    await probeOfficialHeadshot(2544, fetcher);
    await probeOfficialHeadshot(2544, fetcher);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("re-asks after the CDN's own 12 h max-age, so a refreshed photo is picked up", async () => {
    const fetcher = vi.fn(async () => respond(REAL));
    let t = 1_000;
    await probeOfficialHeadshot(2544, fetcher, () => t);
    t += 12 * 3600 * 1000 + 1;
    await probeOfficialHeadshot(2544, fetcher, () => t);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("degrades to unavailable on a network failure and retries soon rather than caching the failure", async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValue(respond(REAL));
    let t = 0;
    expect(await probeOfficialHeadshot(2544, fetcher, () => t)).toBe(false);
    t += 61_000;
    expect(await probeOfficialHeadshot(2544, fetcher, () => t)).toBe(true);
  });

  it("treats a 5xx as unavailable but not as a durable answer", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(respond({}, 503)).mockResolvedValue(respond(REAL));
    let t = 0;
    expect(await probeOfficialHeadshot(7, fetcher, () => t)).toBe(false);
    t += 61_000;
    expect(await probeOfficialHeadshot(7, fetcher, () => t)).toBe(true);
  });
});

describe("parseProbeIds", () => {
  it("keeps positive integers, de-duplicates, and drops everything else", () => {
    expect(parseProbeIds("2544, 201939,2544,0,-5,abc,1.5,,../x,99999999999")).toEqual([2544, 201939]);
  });

  it("caps the batch", () => {
    const many = Array.from({ length: 500 }, (_, i) => i + 1).join(",");
    expect(parseProbeIds(many)).toHaveLength(MAX_IDS_PER_REQUEST);
  });

  it("handles a missing parameter", () => {
    expect(parseProbeIds(null)).toEqual([]);
    expect(parseProbeIds("")).toEqual([]);
  });
});

describe("probeMany", () => {
  it("returns a verdict per id", async () => {
    const fetcher = vi.fn(async (url: string) => respond(url.includes("/2544.") ? REAL : PLACEHOLDER));
    expect(await probeMany([2544, 1735], fetcher)).toEqual({ "2544": true, "1735": false });
  });
});

describe("the route", () => {
  const call = (qs: string) => GET(new Request(`http://localhost:3097/api/headshot-status${qs}`));

  it("answers 404 in a production build — it cannot be used to reach the CDN from a deployment", async () => {
    vi.stubEnv("NODE_ENV", "production");
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const res = await call("?ids=2544");
    expect(res.status).toBe(404);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("answers 404 in development unless the developer opted in, and makes no CDN request", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("NEXT_PUBLIC_PIVOT_OFFICIAL_HEADSHOTS", undefined);
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const res = await call("?ids=2544");
    expect(res.status).toBe(404);
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("answers per-id availability in development once opted in (fetch is stubbed: no real request)", async () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("NEXT_PUBLIC_PIVOT_OFFICIAL_HEADSHOTS", "on");
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => respond(String(input).includes("/2544.") ? REAL : PLACEHOLDER));
    const res = await call("?ids=2544,1735,bogus");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ available: { "2544": true, "1735": false } });
    expect(fetchSpy).toHaveBeenCalledTimes(2); // "bogus" never reached the network
    fetchSpy.mockRestore();
  });
});
