import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PlayerPhoto } from "@/components/media";
import { resetFailedHeadshots } from "@/lib/headshots";

const OFFICIAL_SMALL = (id: number) => `https://cdn.nba.com/headshots/nba/latest/260x190/${id}.png`;
const LOCAL = (id: number) => `/api/v1/assets/players/${id}`;

interface Backend {
  /** NBA ids the backend manifest lists a local photo for. */
  local: number[];
  /** NBA ids the probe reports a real official portrait for. */
  official: number[];
  probeFails?: boolean;
}

let calls: string[] = [];

function installFetch(b: Backend) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      if (url.includes("/assets/manifest")) return Response.json({ players: b.local, teams: [] });
      if (url.includes("/api/headshot-status")) {
        if (b.probeFails) return new Response(null, { status: 500 });
        const ids = new URL(url, "http://x").searchParams.get("ids")!.split(",").map(Number);
        return Response.json({ available: Object.fromEntries(ids.map((i) => [String(i), b.official.includes(i)])) });
      }
      return new Response(null, { status: 404 });
    }),
  );
}

function mount(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrap = (node: React.ReactElement) => <QueryClientProvider client={client}>{node}</QueryClientProvider>;
  const view = render(wrap(ui));
  return { ...view, rerenderWith: (next: React.ReactElement) => view.rerender(wrap(next)) };
}

const img = () => screen.getByRole("img") as HTMLImageElement;
const source = () => img().getAttribute("data-headshot-source");

beforeEach(() => {
  // The official source is opt-in: these tests stub the opt-in (and fetch, via installFetch), so no
  // test ever makes a real request to the NBA CDN. Tests of the default clear the flag explicitly.
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("NEXT_PUBLIC_PIVOT_OFFICIAL_HEADSHOTS", "on");
  resetFailedHeadshots();
});
afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("official source on (development)", () => {
  it("draws the official standard-resolution image for a row-sized slot, lazily", async () => {
    installFetch({ local: [2544], official: [2544] });
    mount(<PlayerPhoto nbaPlayerId={2544} name="LeBron James" size={30} />);
    await waitFor(() => expect(source()).toBe("official"));
    expect(img().src).toBe(OFFICIAL_SMALL(2544));
    expect(img().getAttribute("srcset")).toBeNull();
    expect(img().getAttribute("loading")).toBe("lazy");
    expect(img().getAttribute("alt")).toBe("LeBron James headshot");
    expect(img().getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(img().width).toBe(30);
  });

  it("draws the high-resolution image with a srcset for a portrait, eagerly when prioritised", async () => {
    installFetch({ local: [], official: [2544] });
    mount(<PlayerPhoto nbaPlayerId={2544} name="LeBron James" size={152} square priority />);
    await waitFor(() => expect(source()).toBe("official"));
    expect(img().src).toContain("/1040x760/2544.png");
    expect(img().getAttribute("srcset")).toContain("260w");
    expect(img().getAttribute("srcset")).toContain("1040w");
    expect(img().getAttribute("loading")).toBe("eager");
  });

  it("requests nothing from the CDN until the probe has verified the player", async () => {
    installFetch({ local: [], official: [] });
    mount(<PlayerPhoto nbaPlayerId={1735} name="Vladimir Stepania" size={30} />);
    expect(source()).toBe("pending");
    await waitFor(() => expect(source()).toBe("none"));
    expect(document.querySelector('img[src^="https://cdn.nba.com"]')).toBeNull();
  });

  it("coalesces a whole roster into one probe request", async () => {
    installFetch({ local: [], official: [1, 2, 3, 4, 5] });
    mount(
      <>
        {[1, 2, 3, 4, 5].map((id) => (
          <PlayerPhoto key={id} nbaPlayerId={id} name={`Player ${id}`} size={30} />
        ))}
      </>,
    );
    await waitFor(() => expect(document.querySelectorAll('[data-headshot-source="official"]')).toHaveLength(5));
    expect(calls.filter((c) => c.includes("headshot-status"))).toHaveLength(1);
  });

  it("falls back to the existing local photo when the official image fails to load", async () => {
    installFetch({ local: [2544], official: [2544] });
    mount(<PlayerPhoto nbaPlayerId={2544} name="LeBron James" size={30} />);
    await waitFor(() => expect(source()).toBe("official"));
    fireEvent.error(img());
    await waitFor(() => expect(source()).toBe("local"));
    expect(img().getAttribute("src")).toBe(LOCAL(2544));
  });

  it("falls back to the neutral placeholder when the local photo fails too — and stops there", async () => {
    installFetch({ local: [2544], official: [2544] });
    mount(<PlayerPhoto nbaPlayerId={2544} name="LeBron James" size={30} />);
    await waitFor(() => expect(source()).toBe("official"));
    fireEvent.error(img());
    await waitFor(() => expect(source()).toBe("local"));
    fireEvent.error(img());
    await waitFor(() => expect(source()).toBe("none"));
    expect(screen.getByRole("img").tagName).toBe("SPAN");
    expect(screen.getByLabelText("LeBron James — no photo available")).toBeInTheDocument();
  });

  it("uses the local photo directly when the official image does not exist (the CDN's HTTP-200 placeholder)", async () => {
    installFetch({ local: [1735], official: [] });
    mount(<PlayerPhoto nbaPlayerId={1735} name="Vladimir Stepania" size={30} />);
    await waitFor(() => expect(source()).toBe("local"));
  });

  it("uses the local photo if the probe itself is unavailable", async () => {
    installFetch({ local: [2544], official: [2544], probeFails: true });
    mount(<PlayerPhoto nbaPlayerId={2544} name="LeBron James" size={30} />);
    await waitFor(() => expect(source()).toBe("local"));
  });

  it("never retries a source that already failed — remounting does not re-request it", async () => {
    installFetch({ local: [], official: [2544] });
    const first = mount(<PlayerPhoto nbaPlayerId={2544} name="LeBron James" size={30} />);
    await waitFor(() => expect(source()).toBe("official"));
    fireEvent.error(img());
    await waitFor(() => expect(source()).toBe("none"));
    first.unmount();
    mount(<PlayerPhoto nbaPlayerId={2544} name="LeBron James" size={30} />);
    await waitFor(() => expect(source()).toBe("none"));
    expect(document.querySelector("img")).toBeNull();
  });
});

describe("identity changes", () => {
  it("never leaves the previous player's image on screen when the id changes", async () => {
    installFetch({ local: [], official: [2544, 201939] });
    const { rerenderWith } = mount(<PlayerPhoto nbaPlayerId={2544} name="LeBron James" size={30} />);
    await waitFor(() => expect(img().src).toBe(OFFICIAL_SMALL(2544)));

    rerenderWith(<PlayerPhoto nbaPlayerId={201939} name="Stephen Curry" size={30} />);
    // Synchronously after the rerender: not LeBron's face, whatever the probe has or has not said.
    expect(document.querySelector(`img[src="${OFFICIAL_SMALL(2544)}"]`)).toBeNull();
    await waitFor(() => expect(img().src).toBe(OFFICIAL_SMALL(201939)));
    expect(img().getAttribute("alt")).toBe("Stephen Curry headshot");
  });

  it("does not carry the previous player's failure over to the next player", async () => {
    installFetch({ local: [], official: [1, 2] });
    const { rerenderWith } = mount(<PlayerPhoto nbaPlayerId={1} name="One" size={30} />);
    await waitFor(() => expect(source()).toBe("official"));
    fireEvent.error(img());
    await waitFor(() => expect(source()).toBe("none"));

    rerenderWith(<PlayerPhoto nbaPlayerId={2} name="Two" size={30} />);
    await waitFor(() => expect(source()).toBe("official"));
    expect(img().src).toBe(OFFICIAL_SMALL(2));
  });

  it("moves to the placeholder, not the old face, when the new player has no image anywhere", async () => {
    installFetch({ local: [], official: [2544] });
    const { rerenderWith } = mount(<PlayerPhoto nbaPlayerId={2544} name="LeBron James" size={30} />);
    await waitFor(() => expect(source()).toBe("official"));
    rerenderWith(<PlayerPhoto nbaPlayerId={null} name="Unmapped Player" size={30} />);
    expect(source()).toBe("none");
    expect(document.querySelector("img")).toBeNull();
  });
});

describe("historical views", () => {
  it("keeps the existing photo and never touches the official source or the probe", async () => {
    installFetch({ local: [893], official: [893] });
    mount(<PlayerPhoto nbaPlayerId={893} name="Michael Jordan" size={152} historical />);
    await waitFor(() => expect(source()).toBe("local"));
    expect(img().src).toContain(LOCAL(893));
    expect(calls.some((c) => c.includes("headshot-status"))).toBe(false);
    expect(document.querySelector('img[src^="https://cdn.nba.com"]')).toBeNull();
  });

  it("is the placeholder for a historical player with no local photo, even if the CDN has one", async () => {
    installFetch({ local: [], official: [893] });
    mount(<PlayerPhoto nbaPlayerId={893} name="Michael Jordan" size={152} historical />);
    await waitFor(() => expect(source()).toBe("none"));
  });
});

describe("official source off (production, test, or not opted in) — existing behaviour preserved", () => {
  for (const env of ["production", "test"] as const) {
    it(`NODE_ENV=${env}: shows the local photo and makes no official or probe request`, async () => {
      vi.stubEnv("NODE_ENV", env);
      installFetch({ local: [2544], official: [2544] });
      mount(<PlayerPhoto nbaPlayerId={2544} name="LeBron James" size={30} />);
      await waitFor(() => expect(source()).toBe("local"));
      expect(img().getAttribute("src")).toBe(LOCAL(2544));
      expect(img().getAttribute("referrerpolicy")).toBeNull();
      expect(calls.some((c) => c.includes("headshot-status"))).toBe(false);
      expect(document.querySelector('img[src*="cdn.nba.com"]')).toBeNull();
    });

    it(`NODE_ENV=${env}: shows the silhouette when there is no local photo (and the CDN is never consulted)`, async () => {
      vi.stubEnv("NODE_ENV", env);
      installFetch({ local: [], official: [2544] });
      mount(<PlayerPhoto nbaPlayerId={2544} name="LeBron James" size={64} />);
      await waitFor(() => expect(source()).toBe("none"));
      expect(calls.some((c) => c.includes("headshot-status"))).toBe(false);
    });
  }

  it("development without the opt-in flag (the default): local photo, no probe, no CDN image", async () => {
    vi.stubEnv("NEXT_PUBLIC_PIVOT_OFFICIAL_HEADSHOTS", undefined);
    installFetch({ local: [2544], official: [2544] });
    mount(<PlayerPhoto nbaPlayerId={2544} name="LeBron James" size={30} />);
    await waitFor(() => expect(source()).toBe("local"));
    expect(calls.some((c) => c.includes("headshot-status"))).toBe(false);
    expect(document.querySelector('img[src*="cdn.nba.com"]')).toBeNull();
  });

  it("development with the flag set to anything but `on`: still off", async () => {
    vi.stubEnv("NEXT_PUBLIC_PIVOT_OFFICIAL_HEADSHOTS", "off");
    installFetch({ local: [], official: [2544] });
    mount(<PlayerPhoto nbaPlayerId={2544} name="LeBron James" size={64} />);
    await waitFor(() => expect(source()).toBe("none"));
    expect(calls.some((c) => c.includes("headshot-status"))).toBe(false);
  });

  it("local photo failing still reaches the placeholder (pre-existing behaviour)", async () => {
    vi.stubEnv("NODE_ENV", "production");
    installFetch({ local: [2544], official: [] });
    mount(<PlayerPhoto nbaPlayerId={2544} name="LeBron James" size={30} />);
    await waitFor(() => expect(source()).toBe("local"));
    await act(async () => {
      fireEvent.error(img());
    });
    await waitFor(() => expect(source()).toBe("none"));
  });
});

describe("placeholder presentation", () => {
  it("uses the silhouette at portrait sizes and initials at row sizes", async () => {
    installFetch({ local: [], official: [] });
    const { container } = mount(
      <>
        <PlayerPhoto nbaPlayerId={null} name="Ada Lovelace" size={64} />
        <PlayerPhoto nbaPlayerId={null} name="Ada Lovelace" size={28} />
      </>,
    );
    const [big, small] = Array.from(container.querySelectorAll('[role="img"]'));
    expect(big.querySelector("svg")).not.toBeNull();
    expect(small.querySelector("svg")).toBeNull();
    expect(small.textContent).toBe("AL");
  });
});
