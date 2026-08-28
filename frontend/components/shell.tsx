"use client";

/**
 * Application shell.
 *
 * The desktop header holds four primary destinations plus a "More" menu so it
 * stays on one line down to 1024px — module names are set in the condensed
 * display face, which is what buys the horizontal room. Below that the header
 * is replaced by a purpose-built drawer rather than a wrapped desktop bar.
 *
 * **Navigation follows the decision workflow, not the module inventory.** Pivot's
 * order is observe -> diagnose -> test -> decide, so the primary bar reads
 * Players, Teams, GM Lab, Research. The previous bar led with the Trade Evaluator,
 * which put the last step first and made the product read as a calculator
 * collection rather than as one decision system.
 *
 * **Route paths are deliberately unchanged.** Only the grouping and the labels
 * move. Renaming paths would break the ten legacy redirects in next.config.ts,
 * the fourteen-route visual-QA manifest, the e2e specs and every shared
 * Trade Evaluator link — for no gain a reader would notice, since the labels are
 * what a user actually reads.
 */

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { dataHealthSchema } from "@/lib/schemas";
import type { DataHealth, Team } from "@/lib/types";
import { BrandMark, BrandWordmark, PRODUCT_TAGLINE } from "@/components/brand";
import { TeamLogo } from "@/components/media";

interface NavLink {
  href: string;
  label: string;
  hint?: string;
  exact?: boolean;
}

/** Single destinations, in workflow order: observe, then diagnose. */
const PRIMARY: NavLink[] = [
  { href: "/command-center", label: "Command Center" },
  { href: "/player-explorer", label: "Players" },
  { href: "/team-outlook", label: "Teams" },
];

/**
 * GM Lab is a real grouping, not a page.
 *
 * The brief's information architecture puts Trade, Contracts, Targets, Fit and Scenario
 * under one heading. Three of those exist today and two do not, so this is a menu over
 * the three rather than an index page with placeholder tiles — an empty page that
 * promises a module Pivot has not built would be the presentation-layer version of a
 * fabricated number.
 */
const GM_LAB: NavLink[] = [
  { href: "/trade-evaluator", label: "Trade Evaluator", hint: "Build a deal and test it" },
  { href: "/strategy-lab", label: "Decision Board", hint: "Compare saved deals side by side" },
  { href: "/salary-cap-center", label: "Contracts & Cap", hint: "Payroll and commitments" },
];

/** How the product explains itself. */
const SECONDARY: NavLink[] = [
  { href: "/methodology", label: "Methodology", hint: "How every number is produced" },
  { href: "/data-health", label: "Data Health", hint: "Sources, freshness and gaps" },
  { href: "/about", label: "About Pivot", hint: "What Pivot is, and what it will not ship" },
];

/** Every destination the header can reach, for the mobile drawer. */
export const ALL_DESTINATIONS: NavLink[] = [...PRIMARY, ...GM_LAB, ...SECONDARY];

function isActive(pathname: string, href: string, exact?: boolean) {
  return exact ? pathname === href : pathname === href || pathname.startsWith(`${href}/`);
}

/* ------------------------------------------------------------ status chips */

function useHealth() {
  return useQuery({
    queryKey: ["data-health"],
    queryFn: () => api.get<DataHealth>("/data-health", dataHealthSchema),
    staleTime: 120_000,
  });
}

function StatusChips({ compact = false }: { compact?: boolean }) {
  const { data } = useHealth();
  const [now] = useState(() => Date.now());
  if (!data) return null;

  // The backend decides freshness; the header only renders the verdict. Recomputing it
  // here against a second threshold (48 h, against the backend's configurable
  // NBA_API_STALE_AFTER_SECONDS) let the chip and the Data Health page disagree about
  // the same database.
  const card = data.source_cards?.find((c) => c.key === "current_nba_data");
  const state = card?.status === "fresh" ? "live" : card?.status === "stale" ? "stale" : "none";
  const dot =
    state === "live" ? "bg-legal" : state === "stale" ? "bg-conditional" : "bg-unavail";
  const label = state === "live" ? "Live data" : state === "stale" ? "Data aging" : "No data";
  const synced = data.last_successful_sync;
  const ageHours = synced ? (now - new Date(synced).getTime()) / 3_600_000 : Infinity;
  const detail =
    state === "stale" && Number.isFinite(ageHours)
      ? `${label} — NBA.com data is ${Math.floor(ageHours / 24)} day(s) old`
      : label;

  return (
    <div className="flex items-center gap-2">
      <span className="eyebrow hidden whitespace-nowrap rounded-full border border-line px-2.5 py-1 text-[0.625rem] text-muted 2xl:inline-block">
        {data.current_season} season
      </span>
      <Link
        href="/data-health"
        title={`Open Data Health — ${detail}`}
        className="flex shrink-0 items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-[11px] text-muted transition-colors hover:border-signal/50 hover:text-foreground"
      >
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot} ${state === "live" ? "pulse-live" : ""}`} aria-hidden />
        <span className={compact ? "sr-only" : "hidden whitespace-nowrap xl:inline"}>{label}</span>
      </Link>
    </div>
  );
}

/* ----------------------------------------------------------------- search */

interface Hit {
  kind: "team" | "player";
  id: string;
  label: string;
  sub: string;
  abbreviation?: string;
}

function GlobalSearch({ onNavigate }: { onNavigate?: () => void }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  const { data: teams } = useQuery({
    queryKey: ["teams"],
    queryFn: () => api.get<Team[]>("/teams"),
    staleTime: 300_000,
  });
  const { data: playerHits } = useQuery({
    queryKey: ["search-players", query],
    queryFn: () =>
      api.get<{
        players: { id: string; full_name: string; current_team: Team | null }[];
      }>(`/players?q=${encodeURIComponent(query)}&rostered_only=true&limit=6`),
    enabled: query.trim().length >= 2,
    staleTime: 60_000,
  });

  useEffect(() => {
    function onPointer(event: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  const q = query.trim().toLowerCase();
  const hits: Hit[] =
    q.length < 2
      ? []
      : [
          ...(teams ?? [])
            .filter(
              (t) =>
                t.full_name.toLowerCase().includes(q) || t.abbreviation.toLowerCase().startsWith(q),
            )
            .slice(0, 4)
            .map<Hit>((t) => ({
              kind: "team",
              id: t.id,
              label: t.full_name,
              sub: "Team Outlook",
              abbreviation: t.abbreviation,
            })),
          ...(playerHits?.players ?? []).map<Hit>((p) => ({
            kind: "player",
            id: p.id,
            label: p.full_name,
            sub: p.current_team?.abbreviation ?? "Free agent",
          })),
        ];

  function go(hit: Hit) {
    setOpen(false);
    setQuery("");
    onNavigate?.();
    router.push(hit.kind === "team" ? `/team-outlook/${hit.id}` : `/players/${hit.id}`);
  }

  return (
    <div ref={boxRef} className="relative w-full lg:w-auto">
      <input
        type="search"
        role="combobox"
        aria-expanded={open && hits.length > 0}
        aria-controls="global-search-results"
        aria-label="Search players and teams"
        placeholder="Search players & teams"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && hits.length > 0) go(hits[0]);
        }}
        className="w-full rounded-md border border-line bg-panel2 px-3 py-1.5 text-sm text-foreground placeholder:text-faint focus:border-signal/60 lg:w-44 xl:w-56"
      />
      {open && hits.length > 0 && (
        <ul
          id="global-search-results"
          role="listbox"
          className="absolute right-0 top-11 z-50 w-full min-w-[16rem] overflow-hidden rounded-lg border border-line bg-panel shadow-[var(--shadow-pop)] lg:w-72"
        >
          {hits.map((hit) => (
            <li key={`${hit.kind}-${hit.id}`} role="option" aria-selected={false}>
              <button
                type="button"
                onClick={() => go(hit)}
                className="flex w-full items-center gap-2.5 px-3 py-2 text-left hover:bg-panel2"
              >
                {hit.kind === "team" ? (
                  <TeamLogo abbreviation={hit.abbreviation} name={hit.label} size={22} />
                ) : (
                  <span className="flex h-[22px] w-[22px] items-center justify-center rounded-full bg-panel3 text-[10px] text-muted">
                    {hit.label
                      .split(" ")
                      .map((p) => p[0])
                      .slice(0, 2)
                      .join("")}
                  </span>
                )}
                <span className="min-w-0 flex-1 truncate text-sm">{hit.label}</span>
                <span className="eyebrow shrink-0 text-[0.5625rem]">{hit.sub}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- nav group */

/** A labelled dropdown over several destinations. Used for GM Lab and for More. */
function NavGroup({
  label,
  items,
  pathname,
  align = "right",
}: {
  label: string;
  items: NavLink[];
  pathname: string;
  align?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const anyActive = items.some((item) => isActive(pathname, item.href));

  useEffect(() => {
    function onPointer(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((v) => !v)}
        className={`display relative whitespace-nowrap rounded-md px-2.5 py-1.5 text-[15px] tracking-wide transition-colors ${
          anyActive || open
            ? "bg-panel2 text-foreground"
            : "text-muted hover:bg-panel2 hover:text-foreground"
        }`}
      >
        {label}
        <span aria-hidden className="ml-1 text-[10px]">
          ▾
        </span>
        {anyActive && (
          <span
            aria-hidden
            className="absolute inset-x-2.5 -bottom-[7px] h-0.5 rounded-full bg-signal"
          />
        )}
      </button>
      {open && (
        <div
          role="menu"
          className={`absolute ${align === "right" ? "right-0" : "left-0"} top-11 z-50 w-64 overflow-hidden rounded-lg border border-line bg-panel py-1 shadow-[var(--shadow-pop)]`}
        >
          {items.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              role="menuitem"
              onClick={() => setOpen(false)}
              className={`block px-3 py-2 hover:bg-panel2 ${
                isActive(pathname, item.href) ? "bg-panel2" : ""
              }`}
            >
              <span className="block text-sm text-foreground">{item.label}</span>
              {item.hint && <span className="block text-[11px] text-muted">{item.hint}</span>}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ shell */

export function AppNav() {
  const pathname = usePathname();
  const [drawer, setDrawer] = useState(false);

  // Navigating closes the drawer. Adjusting state during render (rather than in
  // an effect) avoids the cascading re-render an effect would cause here.
  const [drawerPath, setDrawerPath] = useState(pathname);
  if (drawerPath !== pathname) {
    setDrawerPath(pathname);
    if (drawer) setDrawer(false);
  }

  return (
    <header className="sticky top-0 z-40 border-b border-hairline bg-court/92 backdrop-blur-md">
      <div className="mx-auto flex max-w-[1600px] items-center gap-3 px-4 py-2.5 lg:px-8">
        <Link href="/" className="flex shrink-0 items-center gap-2.5" aria-label="Pivot — home">
          <BrandMark size={30} />
          <BrandWordmark />
        </Link>

        {/* Desktop navigation — one line from 1024px up */}
        <nav className="ml-auto hidden items-center gap-0.5 lg:flex" aria-label="Primary">
          {PRIMARY.map((item) => {
            const active = isActive(pathname, item.href, item.exact);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`display relative whitespace-nowrap rounded-md px-2.5 py-1.5 text-[15px] tracking-wide transition-colors ${
                  active
                    ? "bg-panel2 text-foreground"
                    : "text-muted hover:bg-panel2 hover:text-foreground"
                }`}
              >
                {item.label}
                {active && (
                  <span
                    aria-hidden
                    className="absolute inset-x-2.5 -bottom-[7px] h-0.5 rounded-full bg-signal"
                  />
                )}
              </Link>
            );
          })}
          <NavGroup label="GM Lab" items={GM_LAB} pathname={pathname} align="left" />
          <NavGroup label="More" items={SECONDARY} pathname={pathname} />
        </nav>

        <div className="ml-auto hidden items-center gap-2 lg:ml-3 lg:flex">
          <GlobalSearch />
          <StatusChips />
        </div>

        {/* Mobile: status + drawer trigger only */}
        <div className="ml-auto flex items-center gap-2 lg:hidden">
          <StatusChips compact />
          <button
            type="button"
            aria-expanded={drawer}
            aria-controls="mobile-nav"
            aria-label={drawer ? "Close menu" : "Open menu"}
            onClick={() => setDrawer((v) => !v)}
            className="flex h-9 w-9 items-center justify-center rounded-md border border-line bg-panel2 text-foreground"
          >
            <span aria-hidden className="text-lg leading-none">
              {drawer ? "✕" : "☰"}
            </span>
          </button>
        </div>
      </div>

      {/* Mobile drawer: full-width destination list built for thumbs */}
      {drawer && (
        <div id="mobile-nav" className="border-t border-hairline bg-court lg:hidden">
          <div className="px-4 py-3">
            <GlobalSearch onNavigate={() => setDrawer(false)} />
          </div>
          <nav aria-label="Primary" className="pb-3">
            {ALL_DESTINATIONS.map((item) => {
              const active = isActive(pathname, item.href, item.exact);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`flex items-center justify-between border-l-2 px-4 py-3 ${
                    active
                      ? "border-signal bg-panel2 text-foreground"
                      : "border-transparent text-muted"
                  }`}
                >
                  <span className="display text-[17px] tracking-wide">{item.label}</span>
                  {item.hint && (
                    <span className="ml-3 truncate text-[11px] text-faint">{item.hint}</span>
                  )}
                </Link>
              );
            })}
          </nav>
        </div>
      )}
    </header>
  );
}

export function AppFooter() {
  return (
    <footer className="mt-8 border-t border-hairline">
      <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-5 lg:flex-row lg:items-start lg:justify-between lg:px-8">
        <div className="flex shrink-0 items-center gap-2.5">
          <BrandMark size={22} />
          <span className="display whitespace-nowrap text-base tracking-tight">
            PIV<span className="text-brand">O</span>T
          </span>
          <span className="eyebrow whitespace-nowrap text-[0.5625rem]">
            {PRODUCT_TAGLINE}
          </span>
        </div>
        <p className="max-w-3xl text-[11px] leading-relaxed text-faint">
          Independent analytical simulator — not affiliated with or endorsed by the NBA, and not an
          official cap-management product. Basketball data comes from NBA.com via{" "}
          <a
            className="underline hover:text-muted"
            href="https://github.com/swar/nba_api"
            target="_blank"
            rel="noreferrer"
          >
            nba_api
          </a>{" "}
          and user-imported sources, each labeled with provenance in{" "}
          <Link className="underline hover:text-muted" href="/data-health">
            Data Health
          </Link>
          . Team names, logos and player images belong to their owners and are used locally for
          identification. Missing data is always shown as unavailable — never estimated.
        </p>
      </div>
    </footer>
  );
}
