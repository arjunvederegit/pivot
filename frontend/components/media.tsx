"use client";

/**
 * Team crests and player photos.
 *
 * Sources are user-supplied local datasets served by the backend asset manifest
 * and keyed by stable NBA identity. Two invariants:
 *  - nothing is ever stretched: logos letterbox with object-contain, headshots
 *    fill with object-cover anchored to the top so faces survive the crop;
 *  - a missing file can never break a page — logos fall back to a team-colored
 *    abbreviation crest, players to an initials disc.
 */

import clsx from "clsx";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { playerPhotoSrc, teamIdentity, teamLogoSrc } from "@/lib/teamIdentity";

interface AssetManifest {
  players: number[];
  teams: string[];
}

/**
 * Which identities actually have an indexed image. Fetched once per session so a
 * player without a matched photo renders its fallback immediately rather than
 * costing a 404 round-trip — roughly 12% of rostered players are unmatched.
 */
function useAssetManifest() {
  const { data } = useQuery({
    queryKey: ["asset-manifest"],
    queryFn: () => api.get<AssetManifest>("/assets/manifest"),
    staleTime: Infinity,
    gcTime: Infinity,
    retry: 1,
  });
  return data;
}

export function TeamLogo({
  abbreviation,
  name,
  size = 32,
  className,
  decorative = false,
}: {
  abbreviation: string | null | undefined;
  name?: string;
  size?: number;
  className?: string;
  /** True when an adjacent label already names the team. */
  decorative?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const manifest = useAssetManifest();
  const abbr = abbreviation?.toUpperCase() ?? "";
  const identity = teamIdentity(abbr);
  const known = manifest ? manifest.teams.includes(abbr) : false;
  const src = known ? teamLogoSrc(abbr) : null;
  const label = name ?? identity.name;

  if (!src || failed) {
    return (
      <span
        role={decorative ? undefined : "img"}
        aria-label={decorative ? undefined : `${label} crest`}
        aria-hidden={decorative || undefined}
        className={clsx(
          "numeral flex shrink-0 items-center justify-center rounded-sm",
          className,
        )}
        style={{
          width: size,
          height: size,
          fontSize: size * 0.34,
          color: identity.contrast,
          background: `linear-gradient(140deg, ${identity.primary}, ${identity.secondary}88)`,
        }}
      >
        {abbr || "NBA"}
      </span>
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element -- backend-served asset with runtime fallback
    <img
      src={src}
      alt={decorative ? "" : `${label} logo`}
      aria-hidden={decorative || undefined}
      width={size}
      height={size}
      loading="lazy"
      decoding="async"
      // Every logo sits on the same small off-white plate: source assets mix transparent and
      // white backgrounds, and dark marks (Brooklyn, San Antonio) vanish on an ink tile.
      className={clsx("shrink-0 rounded-[3px] bg-paperhi object-contain p-[2px]", className)}
      style={{ width: size, height: size }}
      onError={() => setFailed(true)}
    />
  );
}

/** Larger crest treatment for team headers — the logo sits in its own light. */
export function TeamCrest({
  abbreviation,
  name,
  size = 72,
}: {
  abbreviation: string | null | undefined;
  name?: string;
  size?: number;
}) {
  const identity = teamIdentity(abbreviation);
  return (
    <span
      className="relative flex shrink-0 items-center justify-center"
      style={{ width: size, height: size }}
    >
      <span
        aria-hidden
        className="absolute inset-0 rounded-full blur-xl"
        style={{ background: identity.primary, opacity: 0.28 }}
      />
      <TeamLogo abbreviation={abbreviation} name={name} size={size} className="relative" />
    </span>
  );
}

export function PlayerPhoto({
  nbaPlayerId,
  name,
  size = 36,
  className,
  square = false,
}: {
  nbaPlayerId: number | null | undefined;
  name: string;
  size?: number;
  className?: string;
  /** Rounded-rect crop for card layouts; default is a circular token. */
  square?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const manifest = useAssetManifest();
  const known = manifest && nbaPlayerId ? manifest.players.includes(nbaPlayerId) : false;
  const src = known ? playerPhotoSrc(nbaPlayerId) : null;
  const shape = square ? "rounded-lg" : "rounded-full";

  if (!src || failed) {
    const initials = name
      .split(" ")
      .map((part) => part[0])
      .filter(Boolean)
      .slice(0, 2)
      .join("");
    // No headshot on file: a player silhouette at portrait sizes, initials at row sizes
    // (where a silhouette would be an unreadable smudge). Either way it reads as "no photo",
    // never as somebody else's face.
    const portrait = size >= 48;
    return (
      <span
        role="img"
        aria-label={`${name} — no photo available`}
        className={clsx(
          "relative flex shrink-0 items-center justify-center overflow-hidden border border-hairline bg-gradient-to-b from-panel3 to-panel2 text-muted",
          shape,
          className,
        )}
        style={{ width: size, height: size, fontSize: Math.max(10, size * 0.34) }}
      >
        {portrait ? (
          <>
            <svg viewBox="0 0 64 64" className="absolute inset-x-0 bottom-0 h-[88%] w-full text-[#3a433f]" aria-hidden>
              <circle cx="32" cy="24" r="11" fill="currentColor" />
              <path d="M10 64c1.5-14 10.5-22 22-22s20.5 8 22 22Z" fill="currentColor" />
            </svg>
            <span className="numeral relative mt-auto mb-[8%] text-[0.42em] tracking-[0.06em] text-muted">
              {initials}
            </span>
          </>
        ) : (
          <span className="numeral">{initials}</span>
        )}
      </span>
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element -- backend-served asset with runtime fallback
    <img
      src={src}
      alt={`${name} headshot`}
      width={size}
      height={size}
      loading="lazy"
      decoding="async"
      className={clsx("shrink-0 bg-panel3 object-cover object-top", shape, className)}
      style={{ width: size, height: size }}
      onError={() => setFailed(true)}
    />
  );
}
