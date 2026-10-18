"use client";

/**
 * Application shell: a persistent left sidebar on desktop, a top bar and drawer on phones.
 *
 * The sidebar reads in the order a front office works — the workspace (home, scouting,
 * teams), then the decisions (trade, strategy, cap), then the reference pages where the
 * product explains itself. It collapses to an icon rail, and remembers that choice.
 *
 * **Route paths are deliberately unchanged.** Renaming paths would break the legacy
 * redirects in next.config.ts, the visual-QA manifest, the e2e specs and every shared
 * Trade Evaluator link — for no gain a reader would notice. `/command-center` still
 * serves the home dashboard.
 */

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { api } from "@/lib/api";
import { dataHealthSchema } from "@/lib/schemas";
import type { DataHealth, Team } from "@/lib/types";
import { ASSISTANT_ENABLED } from "@/lib/features";
import { PivotLogo, PivotMark } from "@/components/brand";
import { TeamGrid } from "@/components/team-card";
import { setFavoriteTeam, useFavoriteTeam } from "@/lib/favoriteTeam";
import {
  IconAsk,
  IconCap,
  IconClose,
  IconCollapse,
  IconChevron,
  IconHealth,
  IconHome,
  IconInfo,
  IconMenu,
  IconMethod,
  IconScouting,
  IconSearch,
  IconStrategy,
  IconTeams,
  IconTrade,
} from "@/components/icons";
import { TeamLogo } from "@/components/media";

interface NavLink {
  href: string;
  label: string;
  hint?: string;
  /** Match the href exactly (home), rather than as a path prefix. */
  exact?: boolean;
  /** Other path prefixes that belong to this destination (a player page is Scouting). */
  also?: string[];
  icon: (p: { size?: number; className?: string }) => React.ReactNode;
  /** A tiny uppercase pill beside the label ("Lab"), hidden in the collapsed rail. */
  badge?: string;
}

/** The workspace: where a front office looks before it acts. */
const PRIMARY: NavLink[] = [
  { href: "/", label: "Home", exact: true, also: ["/command-center"], icon: IconHome },
  { href: "/player-explorer", label: "Scouting", also: ["/players"], icon: IconScouting },
  { href: "/team-outlook", label: "Teams", icon: IconTeams },
];

/** The decisions. Three of the modules the brief names exist today — no placeholder tiles. */
const GM_LAB: NavLink[] = [
  { href: "/trade-evaluator", label: "Trade Evaluator", hint: "Build a deal and test it", also: ["/trades"], icon: IconTrade },
  { href: "/strategy-lab", label: "Strategy Lab", hint: "Compare saved deals side by side", icon: IconStrategy },
  { href: "/salary-cap-center", label: "Cap Center", hint: "Payroll and commitments", icon: IconCap },
];

/** How the product explains itself. */
const SECONDARY: NavLink[] = [
  { href: "/methodology", label: "Methodology", hint: "How every number is produced", icon: IconMethod },
  { href: "/data-health", label: "Data Health", hint: "Sources, freshness and gaps", icon: IconHealth },
  { href: "/about", label: "About Pivot", hint: "What Pivot is, and what it will not ship", icon: IconInfo },
];

/**
 * Experimental surfaces, behind a build-time flag (`lib/features.ts`). Ask Pivot answers
 * from a FICTIONAL corpus; a build without NEXT_PUBLIC_ASSISTANT_ENABLED=true has no nav
 * entry for it and serves the ordinary 404 at its route.
 */
const LAB: NavLink[] = [
  { href: "/assistant", label: "Ask Pivot", hint: "Experimental research partner — fictional data", icon: IconAsk, badge: "Lab" },
];

const GROUPS = [
  { label: "Workspace", items: PRIMARY },
  { label: "Decisions", items: GM_LAB },
  ...(ASSISTANT_ENABLED ? [{ label: "Intelligence", items: LAB }] : []),
  { label: "Reference", items: SECONDARY },
];

/** Every destination, for the mobile drawer. */
export const ALL_DESTINATIONS: NavLink[] = [
  ...PRIMARY,
  ...GM_LAB,
  ...(ASSISTANT_ENABLED ? LAB : []),
  ...SECONDARY,
];

function isActive(pathname: string, link: NavLink) {
  const own = link.exact
    ? pathname === link.href
    : pathname === link.href || pathname.startsWith(`${link.href}/`);
  return own || (link.also ?? []).some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

/* -------------------------------------------------------- collapsed state */

/**
 * The sidebar's collapsed state lives in localStorage. Read through an external store so
 * the server render and the hydration render agree (expanded), and the stored choice is
 * applied straight after — no setState inside an effect, no hydration mismatch.
 */
const SIDEBAR_KEY = "pivot.sidebar";
const sidebarListeners = new Set<() => void>();
function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_KEY) === "collapsed";
  } catch {
    return false;
  }
}
function writeCollapsed(collapsed: boolean) {
  try {
    window.localStorage.setItem(SIDEBAR_KEY, collapsed ? "collapsed" : "expanded");
  } catch {
    /* private mode: the choice simply does not persist */
  }
  sidebarListeners.forEach((l) => l());
}
function useCollapsed(): [boolean, (v: boolean) => void] {
  const collapsed = useSyncExternalStore(
    (listener) => {
      sidebarListeners.add(listener);
      return () => sidebarListeners.delete(listener);
    },
    readCollapsed,
    () => false,
  );
  return [collapsed, writeCollapsed];
}

/* ------------------------------------------------------------ data status */

function useHealth() {
  return useQuery({
    queryKey: ["data-health"],
    queryFn: () => api.get<DataHealth>("/data-health", dataHealthSchema),
    staleTime: 120_000,
  });
}

function DataStatus({ collapsed = false }: { collapsed?: boolean }) {
  const { data } = useHealth();
  const [now] = useState(() => Date.now());
  if (!data) return null;

  // The backend decides freshness; the shell only renders the verdict.
  const card = data.source_cards?.find((c) => c.key === "current_nba_data");
  const state = card?.status === "fresh" ? "live" : card?.status === "stale" ? "stale" : "none";
  const dot =
    state === "live" ? "bg-legal" : state === "stale" ? "bg-conditional" : "bg-unavail";
  const label = state === "live" ? "Live data" : state === "stale" ? "Data aging" : "No data";
  const synced = data.last_successful_sync;
  const ageDays = synced
    ? Math.floor((now - new Date(synced).getTime()) / 86_400_000)
    : null;
  const detail =
    state === "stale" && ageDays !== null
      ? `${label} — NBA.com data is ${ageDays} day(s) old`
      : label;

  return (
    <Link
      href="/data-health"
      title={`Open Data Health — ${detail}`}
      className={`flex items-center gap-2.5 rounded-md border border-hairline px-2.5 py-2 text-[12px] text-muted transition-colors hover:border-line hover:text-foreground ${
        collapsed ? "justify-center" : ""
      }`}
    >
      <span
        className={`h-2 w-2 shrink-0 rounded-full ${dot} ${state === "live" ? "pulse-live" : ""}`}
        aria-hidden
      />
      {collapsed ? (
        <span className="sr-only">{detail}</span>
      ) : (
        <span className="min-w-0 leading-tight">
          <span className="block truncate font-medium text-foreground">{label}</span>
          <span className="block truncate text-[11px] text-faint">
            {data.current_season} season
            {ageDays !== null ? ` · ${ageDays}d since sync` : ""}
          </span>
        </span>
      )}
    </Link>
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

function GlobalSearch({
  onNavigate,
  hotkey = false,
  listId,
  inputRef: externalRef,
}: {
  onNavigate?: () => void;
  /** Bind "/" to focus this instance. Only one mounted instance should take it. */
  hotkey?: boolean;
  listId: string;
  inputRef?: React.RefObject<HTMLInputElement | null>;
}) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const ownRef = useRef<HTMLInputElement>(null);
  const inputRef = externalRef ?? ownRef;

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
      if (hotkey && event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        inputRef.current?.focus();
        return;
      }
      if (!hotkey || event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      const typing =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable);
      if (typing) return;
      event.preventDefault();
      inputRef.current?.focus();
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [hotkey, inputRef]);

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
              sub: "Team",
              abbreviation: t.abbreviation,
            })),
          ...(playerHits?.players ?? []).map<Hit>((p) => ({
            kind: "player",
            id: p.id,
            label: p.full_name,
            sub: p.current_team?.abbreviation ?? "Free agent",
          })),
        ];
  const activeIndex = Math.min(active, Math.max(0, hits.length - 1));
  const showList = open && hits.length > 0;

  function go(hit: Hit) {
    setOpen(false);
    setQuery("");
    setActive(0);
    onNavigate?.();
    router.push(hit.kind === "team" ? `/team-outlook/${hit.id}` : `/players/${hit.id}`);
  }

  return (
    <div ref={boxRef} className="relative w-full">
      <IconSearch
        size={17}
        className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-muted"
      />
      <input
        ref={inputRef}
        type="search"
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showList ? `${listId}-${activeIndex}` : undefined}
        aria-label="Search players and teams"
        placeholder="Search players and teams…"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" && hits.length) {
            event.preventDefault();
            setOpen(true);
            setActive((i) => (Math.min(i, hits.length - 1) + 1) % hits.length);
          } else if (event.key === "ArrowUp" && hits.length) {
            event.preventDefault();
            setActive((i) => (Math.min(i, hits.length - 1) - 1 + hits.length) % hits.length);
          } else if (event.key === "Enter" && hits.length > 0) {
            go(hits[activeIndex]);
          }
        }}
        className="peer w-full rounded-md border border-line bg-arena py-2.5 pl-10 pr-14 text-[14px] text-foreground placeholder:text-faint focus:border-[#55555b]"
      />
      {hotkey && (
        <kbd
          aria-hidden
          className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 rounded border border-line px-1.5 text-[10.5px] leading-[18px] text-faint peer-focus:hidden peer-[:not(:placeholder-shown)]:hidden"
        >
          ⌘ K
        </kbd>
      )}
      {showList && (
        <ul
          id={listId}
          role="listbox"
          className="absolute left-0 top-12 z-50 w-full min-w-[18rem] overflow-hidden rounded-md border border-line bg-panel2 py-1 shadow-[var(--shadow-pop)]"
        >
          {hits.map((hit, i) => (
            <li
              key={`${hit.kind}-${hit.id}`}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === activeIndex}
            >
              <button
                type="button"
                tabIndex={-1}
                onMouseEnter={() => setActive(i)}
                onClick={() => go(hit)}
                className={`flex w-full items-center gap-2.5 px-3 py-2 text-left ${
                  i === activeIndex ? "bg-panel3" : ""
                }`}
              >
                {hit.kind === "team" ? (
                  <TeamLogo abbreviation={hit.abbreviation} name={hit.label} size={22} />
                ) : (
                  <span className="numeral flex h-[22px] w-[22px] items-center justify-center rounded-full bg-panel3 text-[10px] text-muted">
                    {hit.label
                      .split(" ")
                      .map((p) => p[0])
                      .slice(0, 2)
                      .join("")}
                  </span>
                )}
                <span className="min-w-0 flex-1 truncate text-[13px] text-foreground">{hit.label}</span>
                <span className="shrink-0 text-[11px] text-faint">{hit.sub}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ nav list */

function NavItems({
  pathname,
  collapsed = false,
  onNavigate,
}: {
  pathname: string;
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  return (
    <>
      {GROUPS.map(({ label, items }) => (
        <div key={label} className="mt-6 first:mt-0">
          {collapsed ? (
            <div aria-hidden className="mx-auto mb-2 h-px w-6 bg-hairline" />
          ) : (
            <p className="mb-1.5 px-3 text-[10.5px] font-medium uppercase tracking-[0.12em] text-faint">
              {label}
            </p>
          )}
          <ul className="space-y-0.5">
            {items.map((item) => {
              const active = isActive(pathname, item);
              const Icon = item.icon;
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    title={collapsed ? item.label : undefined}
                    onClick={onNavigate}
                    className={`group relative flex items-center gap-3.5 rounded-md px-3 py-2.5 text-[14.5px] transition-colors ${
                      active
                        ? "bg-panel3 font-medium text-foreground"
                        : "text-muted hover:bg-panel2 hover:text-foreground"
                    } ${collapsed ? "justify-center px-0" : ""}`}
                  >
                    <Icon
                      size={20}
                      className={active ? "text-foreground" : "text-muted group-hover:text-foreground"}
                    />
                    <span className={collapsed ? "sr-only" : "min-w-0 truncate"}>{item.label}</span>
                    {item.badge && !collapsed && (
                      <span className="ml-auto shrink-0 rounded-[3px] border border-line px-1.5 py-px text-[10px] font-medium uppercase leading-[1.4] tracking-[0.1em] text-faint">
                        {item.badge}
                      </span>
                    )}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </>
  );
}

/* --------------------------------------------------------------------- shell */

/** The team the workspace opens on — stored in this browser, set here or on the home page. */
function useTeamContext() {
  const favorite = useFavoriteTeam();
  const { data: teams } = useQuery({
    queryKey: ["teams"],
    queryFn: () => api.get<Team[]>("/teams"),
    staleTime: 300_000,
  });
  const team = favorite ? teams?.find((t) => t.id === favorite.id) : undefined;
  return { favorite, teams, team };
}

function SidebarTeamContext({ collapsed }: { collapsed: boolean }) {
  const { favorite, team } = useTeamContext();
  const { data: health } = useHealth();
  if (!favorite) return null;
  const name = team?.full_name ?? favorite.abbreviation;
  return (
    <div className={collapsed ? "flex justify-center" : ""}>
      {!collapsed && <p className="mb-2 px-1 text-[12px] text-muted">Team Context</p>}
      <Link
        href={`/team-outlook/${favorite.id}`}
        title={`${name} — open Team Outlook`}
        className={`flex items-center gap-3 rounded-md px-1 py-1 transition-colors hover:bg-panel2 ${
          collapsed ? "justify-center" : ""
        }`}
      >
        <TeamLogo abbreviation={favorite.abbreviation} name={name} size={30} decorative />
        {collapsed ? (
          <span className="sr-only">{name}</span>
        ) : (
          <span className="min-w-0 leading-tight">
            <span className="block truncate text-[13.5px] text-foreground">{name}</span>
            {health?.current_season && (
              <span className="block text-[11.5px] text-faint">{health.current_season} season</span>
            )}
          </span>
        )}
      </Link>
    </div>
  );
}

function Sidebar({ pathname }: { pathname: string }) {
  const [collapsed, setCollapsed] = useCollapsed();

  return (
    <aside
      aria-label="Sidebar"
      className="fixed inset-y-0 left-0 z-40 hidden w-[var(--sidebar-w)] flex-col border-r border-hairline bg-arena transition-[width] duration-200 lg:flex"
    >
      <div className={`flex h-[var(--topbar-h)] shrink-0 items-center border-b border-hairline ${collapsed ? "justify-center px-0" : "px-5"}`}>
        <Link
          href="/"
          className={`flex min-w-0 items-center ${collapsed ? "mx-auto" : ""}`}
          aria-label="Pivot — home"
        >
          {collapsed ? <PivotMark size={36} /> : <PivotLogo height={34} />}
        </Link>
      </div>

      <nav aria-label="Primary" className="scroll-thin flex-1 overflow-y-auto px-3 py-5">
        <NavItems pathname={pathname} collapsed={collapsed} />
      </nav>

      <div className="space-y-3 border-t border-hairline p-3">
        <SidebarTeamContext collapsed={collapsed} />
        <DataStatus collapsed={collapsed} />
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className={`flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[12px] text-faint transition-colors hover:text-foreground ${
            collapsed ? "justify-center" : ""
          }`}
        >
          <IconCollapse size={17} collapsed={collapsed} />
          {!collapsed && <span>Collapse</span>}
        </button>
      </div>
    </aside>
  );
}

/** Top toolbar: global search on the left, the selected team on the right. */
function TeamSelector() {
  const { favorite, teams, team } = useTeamContext();
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointer(event: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const label = team?.full_name ?? (favorite ? favorite.abbreviation : "Select team");
  return (
    <div ref={boxRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex h-11 items-center gap-3 rounded-md border border-line bg-arena px-3.5 text-[14px] text-foreground transition-colors hover:border-[#55555b]"
      >
        {favorite ? (
          <>
            <span className="numeral text-[13px] text-muted">{favorite.abbreviation}</span>
            <TeamLogo abbreviation={favorite.abbreviation} name={label} size={24} decorative />
            <span className="hidden max-w-[11rem] truncate xl:block">{label}</span>
          </>
        ) : (
          <span>Select team</span>
        )}
        <IconChevron size={16} className="text-muted" />
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Choose your team"
          className="fade-in absolute right-0 top-full z-50 mt-2 w-[min(calc(100vw-2rem),30rem)] overflow-hidden rounded-md border border-line bg-arena shadow-[var(--shadow-pop)]"
        >
          <p className="border-b border-hairline px-4 py-3 text-[12px] text-muted">
            Team context — Team Outlook and the Trade Evaluator open on it.
          </p>
          <div className="scroll-thin max-h-[26rem] overflow-y-auto p-3">
            <TeamGrid
              teams={teams}
              grouped={false}
              columns="grid-cols-5"
              size="sm"
              selectedIds={favorite ? [favorite.id] : []}
              onSelect={(t) => {
                setFavoriteTeam({ id: t.id, abbreviation: t.abbreviation });
                setOpen(false);
              }}
              ariaLabelFor={(t, { selected }) =>
                selected ? `${t.full_name} is your team` : `Set ${t.full_name} as your team`
              }
            />
          </div>
          {favorite && (
            <button
              type="button"
              onClick={() => {
                setFavoriteTeam(null);
                setOpen(false);
              }}
              className="w-full border-t border-hairline px-4 py-2.5 text-left text-[12px] text-muted transition-colors hover:text-foreground"
            >
              Clear team context
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function TopBar() {
  const searchRef = useRef<HTMLInputElement>(null);
  return (
    <div className="sticky top-0 z-30 hidden h-[var(--topbar-h)] items-center gap-4 border-b border-hairline bg-ink/95 px-6 backdrop-blur lg:flex xl:px-8">
      <div className="w-full max-w-[34rem]">
        <GlobalSearch hotkey listId="global-search-results" inputRef={searchRef} />
      </div>
      <div className="ml-auto flex items-center gap-3">
        <TeamSelector />
      </div>
    </div>
  );
}

function MobileBar({ pathname }: { pathname: string }) {
  const [open, setOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);

  // Navigating closes the drawer (state adjusted during render, not in an effect).
  const [openPath, setOpenPath] = useState(pathname);
  if (openPath !== pathname) {
    setOpenPath(pathname);
    if (open) setOpen(false);
  }

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpen(false);
      toggleRef.current?.focus();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <header className="sticky top-0 z-40 flex h-[var(--nav-h)] items-center gap-3 border-b border-hairline bg-ink/95 px-4 backdrop-blur lg:hidden">
        <Link href="/" className="flex min-w-0 items-center" aria-label="Pivot — home">
          <PivotLogo height={26} />
        </Link>
        <div className="ml-auto flex items-center gap-2">
          <DataStatus collapsed />
          <button
            ref={toggleRef}
            type="button"
            aria-expanded={open}
            aria-controls="mobile-nav"
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen((v) => !v)}
            className="flex h-10 w-10 items-center justify-center rounded-md border border-hairline text-foreground"
          >
            {open ? <IconClose size={19} /> : <IconMenu size={19} />}
          </button>
        </div>
      </header>

      {open && (
        <div className="fixed inset-0 z-[55] lg:hidden" role="presentation">
          <button
            type="button"
            aria-label="Close menu"
            tabIndex={-1}
            onClick={() => setOpen(false)}
            className="absolute inset-0 bg-ink/70 backdrop-blur-sm"
          />
          <div
            id="mobile-nav"
            className="fade-in scroll-thin absolute inset-y-0 left-0 flex w-[min(86vw,20rem)] flex-col overflow-y-auto border-r border-hairline bg-arena px-3 pb-6 pt-4"
          >
            <div className="mb-4 flex items-center justify-between px-1">
              <Link href="/" onClick={() => setOpen(false)} className="flex items-center" aria-label="Pivot — home">
                <PivotLogo height={30} />
              </Link>
              <button
                type="button"
                aria-label="Close menu"
                onClick={() => {
                  setOpen(false);
                  toggleRef.current?.focus();
                }}
                className="flex h-10 w-10 items-center justify-center rounded-md text-muted hover:text-foreground"
              >
                <IconClose size={19} />
              </button>
            </div>
            <div className="mb-4">
              <GlobalSearch onNavigate={() => setOpen(false)} listId="mobile-search-results" />
            </div>
            <nav aria-label="Primary">
              <NavItems pathname={pathname} onNavigate={() => setOpen(false)} />
            </nav>
          </div>
        </div>
      )}
    </>
  );
}

export function AppFooter() {
  return (
    <footer className="border-t border-hairline">
      <div className="mx-auto flex w-full max-w-[1480px] flex-col gap-3 px-4 py-6 text-[12px] leading-relaxed text-faint sm:px-6 lg:flex-row lg:items-start lg:justify-between lg:px-10">
        <p className="shrink-0">
          <span className="font-serif text-[15px] text-muted">Pivot</span> · Basketball intelligence
        </p>
        <p className="max-w-3xl">
          Independent analytical simulator — not affiliated with or endorsed by the NBA, and not an
          official cap-management product. Basketball data comes from NBA.com via{" "}
          <a
            className="underline decoration-line underline-offset-2 hover:text-muted"
            href="https://github.com/swar/nba_api"
            target="_blank"
            rel="noreferrer"
          >
            nba_api
          </a>{" "}
          and user-imported sources, each labeled with provenance in{" "}
          <Link className="underline decoration-line underline-offset-2 hover:text-muted" href="/data-health">
            Data Health
          </Link>
          . Team names, logos and player images belong to their owners and are used locally for
          identification. Missing data is always shown as unavailable — never estimated.
        </p>
      </div>
    </footer>
  );
}

/**
 * The frame every route renders in. Dashboard routes (home) lay out their own full-width
 * hero, so they get a bare content column; every other route gets the standard padded,
 * width-capped one.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [collapsed] = useCollapsed();
  const fullBleed = pathname === "/" || pathname === "/command-center";

  return (
    <div
      className="min-h-dvh lg:pl-[var(--sidebar-w)]"
      style={{ "--sidebar-w": collapsed ? "76px" : "256px" } as React.CSSProperties}
    >
      <Sidebar pathname={pathname} />
      <MobileBar pathname={pathname} />
      <TopBar />
      <div className="flex min-h-[calc(100dvh-var(--nav-h)-var(--topbar-h))] flex-col">
        {fullBleed ? (
          <main id="main" className="flex-1">
            {children}
          </main>
        ) : (
          <main
            id="main"
            className="mx-auto w-full max-w-[1560px] flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-7"
          >
            {children}
          </main>
        )}
        <AppFooter />
      </div>
    </div>
  );
}

/** Kept for any import of the old header name; the shell now renders it. */
export function AppNav() {
  return null;
}
