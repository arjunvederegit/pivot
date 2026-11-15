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
import { useState, useSyncExternalStore } from "react";
import { api } from "@/lib/api";
import { useOfficialHeadshotStatus } from "@/lib/headshotAvailability";
import {
  failedHeadshotSet,
  failedHeadshotsVersion,
  markHeadshotFailed,
  normalizeNbaPlayerId,
  officialHeadshotsEnabled,
  resolveHeadshot,
  subscribeFailedHeadshots,
} from "@/lib/headshots";
import { teamIdentity, teamLogoSrc } from "@/lib/teamIdentity";

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
  onLight = false,
}: {
  abbreviation: string | null | undefined;
  name?: string;
  size?: number;
  className?: string;
  /** True when an adjacent label already names the team. */
  decorative?: boolean;
  /** The logo sits on a light surface (a team card), so it needs no keyline. */
  onLight?: boolean;
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
      // The backend serves transparent artwork (opaque sources are normalised on index), so
      // the logo is drawn as-is and never stretched. Many marks are drawn with black outlines
      // and wordmarks that vanish on a dark ground, so on dark surfaces `.logo-keyline` adds a
      // one-pixel white keyline around the artwork — colours untouched, no plate behind it.
      // On a light surface (a team card) the artwork needs nothing.
      className={clsx("shrink-0 object-contain", !onLight && "logo-keyline", className)}
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

/**
 * A player's portrait. Source order (see lib/headshots.ts): the NBA's official headshot when that
 * source is enabled (development only) and verified to exist, then Pivot's own photo, then a neutral
 * silhouette. Callers pass the canonical NBA id and a display size; they never build a URL.
 *
 * - Identity-safe: what is drawn is derived from `nbaPlayerId` on every render and the `<img>` is
 *   keyed by its URL, so navigating from one player to another can never leave the first player's
 *   face on screen or inherit his "failed" state.
 * - No loops: a URL that errors is remembered for the session and skipped, so each source is tried
 *   at most once before the placeholder; the placeholder is not an image and cannot fail.
 */
export function PlayerPhoto({
  nbaPlayerId,
  name,
  size = 36,
  className,
  square = false,
  historical = false,
  priority = false,
}: {
  nbaPlayerId: number | null | undefined;
  name: string;
  size?: number;
  className?: string;
  /** Rounded-rect crop for card layouts; default is a circular token. */
  square?: boolean;
  /**
   * A past-season or retired-player view. Today's official portrait would misstate that context,
   * so the official source is skipped and the existing photo (or the placeholder) is used.
   */
  historical?: boolean;
  /** Above-the-fold portrait: load eagerly and at high priority instead of lazily. */
  priority?: boolean;
}) {
  const manifest = useAssetManifest();
  const id = normalizeNbaPlayerId(nbaPlayerId);
  const official = useOfficialHeadshotStatus(id, officialHeadshotsEnabled() && !historical);
  useSyncExternalStore(subscribeFailedHeadshots, failedHeadshotsVersion, failedHeadshotsVersion);
  const resolution = resolveHeadshot({
    nbaPlayerId: id,
    size,
    historical,
    official,
    localKnown: manifest && id ? manifest.players.includes(id) : false,
    failed: failedHeadshotSet(),
  });
  const shape = square ? "rounded-lg" : "rounded-full";

  if (resolution.state !== "image") {
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
        data-headshot-source={resolution.state === "pending" ? "pending" : "none"}
        className={clsx(
          "relative flex shrink-0 items-center justify-center overflow-hidden border border-hairline bg-gradient-to-b from-panel3 to-panel2 text-muted",
          shape,
          className,
        )}
        style={{ width: size, height: size, fontSize: Math.max(10, size * 0.34) }}
      >
        {portrait ? (
          <>
            <svg viewBox="0 0 64 64" className="absolute inset-x-0 bottom-0 h-[88%] w-full text-[#4a4a50]" aria-hidden>
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

  const { kind, src, srcSet } = resolution.source;
  return (
    // eslint-disable-next-line @next/next/no-img-element -- the official CDN and the backend asset route are both runtime-verified sources with a fallback; the Next optimizer would copy NBA bytes onto Pivot's origin
    <img
      key={src}
      src={src}
      srcSet={srcSet}
      sizes={srcSet ? `${size}px` : undefined}
      alt={`${name} headshot`}
      width={size}
      height={size}
      data-headshot-source={kind}
      loading={priority ? "eager" : "lazy"}
      fetchPriority={priority ? "high" : undefined}
      decoding="async"
      // Third-party image requests carry no referrer.
      referrerPolicy={kind === "official" ? "no-referrer" : undefined}
      // Official portraits are transparent 4:3 cut-outs, so the panel tone behind them is the
      // backdrop; the crop is anchored to the top so the head survives a square or round mask.
      className={clsx("shrink-0 bg-panel3 object-cover object-top", shape, className)}
      style={{ width: size, height: size }}
      onError={() => markHeadshotFailed(src)}
    />
  );
}
