"use client";

/**
 * Team selection — the one card pattern every "pick a franchise" surface renders.
 *
 * White is an interaction colour here, not a theme: a selectable team is a light tile on
 * the dark terminal, so the official logo (many of which carry black outlines and black
 * wordmarks that vanish on charcoal) always sits on a surface it was drawn for. States
 * live in `.team-card` (globals.css), so hover, selected, focus and disabled read the same
 * everywhere:
 *
 *   rest      soft light-gray tile, dark abbreviation, thin neutral border
 *   hover     brightens to white
 *   selected  white, a two-step ring and a check — still distinct after hover ends
 *   focus     the global white focus ring, drawn outside the tile
 *
 * The logo is never given its own box: assets are transparent, scaled with object-contain.
 */

import clsx from "clsx";
import Link from "next/link";
import type { Team } from "@/lib/types";
import { TeamLogo } from "@/components/media";
import { Skeleton } from "@/components/ui";

type CardTeam = Pick<Team, "abbreviation" | "full_name">;

export function TeamCard({
  team,
  selected,
  disabled = false,
  onSelect,
  href,
  size = "md",
  label,
  title,
  ariaLabel,
  className,
}: {
  team: CardTeam;
  /** Omit for a plain navigation tile; pass a boolean for a selectable one. */
  selected?: boolean;
  disabled?: boolean;
  onSelect?: () => void;
  /** Render as a link (navigation) instead of a button (selection). */
  href?: string;
  size?: "sm" | "md" | "lg";
  /** Overrides the visible abbreviation. */
  label?: string;
  title?: string;
  ariaLabel?: string;
  className?: string;
}) {
  const logo = size === "sm" ? 26 : size === "lg" ? 40 : 32;
  const body = (
    <>
      {selected && (
        <span aria-hidden className="team-card-check">
          <svg viewBox="0 0 12 12" width="9" height="9" fill="none">
            <path d="M2.5 6.2 5 8.6l4.5-5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      )}
      <TeamLogo abbreviation={team.abbreviation} name={team.full_name} size={logo} decorative onLight />
      <span className="team-card-label">{label ?? team.abbreviation}</span>
    </>
  );
  const cls = clsx("team-card", `team-card-${size}`, className);

  if (href) {
    return (
      <Link
        href={href}
        className={cls}
        title={title ?? team.full_name}
        aria-label={ariaLabel ?? `${team.full_name}`}
        data-selected={selected || undefined}
      >
        {body}
      </Link>
    );
  }
  return (
    <button
      type="button"
      className={cls}
      disabled={disabled}
      onClick={onSelect}
      aria-pressed={selected === undefined ? undefined : selected}
      aria-label={ariaLabel ?? team.full_name}
      title={title ?? team.full_name}
      data-selected={selected || undefined}
    >
      {body}
    </button>
  );
}

/** Conference-ordered grid of team cards, the shape every picker shares. */
export function TeamGrid({
  teams,
  selectedIds = [],
  disabledIds = [],
  onSelect,
  hrefFor,
  grouped = true,
  columns = "grid-cols-5 sm:grid-cols-6",
  size = "md",
  labelFor,
  ariaLabelFor,
  loading = false,
}: {
  teams: Team[] | undefined;
  selectedIds?: string[];
  disabledIds?: string[];
  onSelect?: (team: Team) => void;
  hrefFor?: (team: Team) => string;
  grouped?: boolean;
  /** Tailwind grid-cols classes for each conference block (or the whole grid). */
  columns?: string;
  size?: "sm" | "md" | "lg";
  labelFor?: (team: Team) => string | undefined;
  ariaLabelFor?: (team: Team, state: { selected: boolean; disabled: boolean }) => string;
  loading?: boolean;
}) {
  const cell = (team: Team) => {
    const selected = selectedIds.includes(team.id);
    const disabled = disabledIds.includes(team.id);
    return (
      <li key={team.id}>
        <TeamCard
          team={team}
          size={size}
          selected={hrefFor ? undefined : selected}
          disabled={disabled}
          href={hrefFor?.(team)}
          onSelect={() => onSelect?.(team)}
          label={labelFor?.(team)}
          ariaLabel={ariaLabelFor?.(team, { selected, disabled })}
        />
      </li>
    );
  };

  if (!teams || loading) {
    return (
      <div className={`grid gap-1.5 ${grouped ? "" : columns}`} role="status" aria-label="Loading teams">
        {grouped ? (
          <div className="grid gap-x-8 gap-y-6 lg:grid-cols-2">
            {[0, 1].map((c) => (
              <div key={c} className={`grid gap-1.5 ${columns}`}>
                {Array.from({ length: 15 }).map((_, i) => (
                  <Skeleton key={i} className="h-[68px]" />
                ))}
              </div>
            ))}
          </div>
        ) : (
          Array.from({ length: 30 }).map((_, i) => <Skeleton key={i} className="h-[68px]" />)
        )}
      </div>
    );
  }

  if (!grouped) {
    return <ul className={`grid gap-1.5 ${columns}`}>{teams.map(cell)}</ul>;
  }

  const east = teams.filter((t) => t.conference === "East");
  const west = teams.filter((t) => t.conference === "West");
  const other = teams.filter((t) => t.conference !== "East" && t.conference !== "West");
  const blocks: [string, Team[]][] = [
    ["Eastern Conference", east],
    ["Western Conference", west],
    ...(other.length > 0 ? ([["Conference not listed", other]] as [string, Team[]][]) : []),
  ];
  return (
    <div className="grid gap-x-8 gap-y-6 lg:grid-cols-2">
      {blocks.map(([name, list]) => (
        <section key={name} aria-label={name}>
          <h3 className="mb-2.5 flex items-center gap-3 text-[11px] font-medium uppercase tracking-[0.1em] text-muted">
            {name}
            <span aria-hidden className="h-px flex-1 bg-hairline" />
            <span className="numeral font-normal text-faint">{list.length}</span>
          </h3>
          <ul className={`grid gap-1.5 ${columns}`}>{list.map(cell)}</ul>
        </section>
      ))}
    </div>
  );
}
