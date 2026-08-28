"use client";

/**
 * What the narrative homepage reads, and the rule it follows.
 *
 * **Every number on the page is fetched from the running Pivot API.** Nothing here
 * computes a basketball value, re-implements a formula, or hard-codes a score — the page
 * is a read-only consumer of the same endpoints the product uses, through the same
 * `lib/api` client and the same React Query cache. That is deliberate on two counts: the
 * brief forbids presenting invented analytics as production calculations, and it forbids
 * duplicating application logic into a marketing surface.
 *
 * When the API is not reachable the acts fall back to their **structure** — the labels,
 * the method, the shape of the argument — and simply do not show numbers. They never
 * substitute plausible ones.
 *
 * Requests are lazy: each act enables its own query only once the reader is near it, so
 * landing on the hero costs one small request, not the whole narrative.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Team } from "@/lib/types";

/* ------------------------------------------------------------------ shapes */

export type SkillDim = {
  key: string;
  label: string;
  side: string;
  definition: string;
  available: boolean;
  method: string;
  unavailable_reason: string;
  evidence: string;
  confidence: string;
  limitations: string[];
};

export type Vocabulary = {
  skills: SkillDim[];
  archetypes: { key: string; label: string; family: string; definition: string }[];
  needs: { key: string; label: string; definition: string; addressed_by: string }[];
  evidence_ladder: { key: string; label: string; definition: string }[];
  confidence_levels: { key: string; label: string; definition: string }[];
  thresholds: Record<string, number>;
};

export type ProfileEntry = {
  key: string;
  label: string;
  severity: number;
  percentile: number | null;
  explanation: string;
  addressed_by: string;
};

export type TeamProfile = {
  team: { id: string; abbreviation: string; full_name: string };
  season: string;
  roster_size: number;
  needs: ProfileEntry[];
  weaknesses: ProfileEntry[];
  strengths: ProfileEntry[];
  archetype_distribution: { key: string; label: string; family: string; count: number }[];
  skill_coverage: { key: string; label: string; value: number | null; available: boolean }[];
  needs_available: boolean;
  needs_unavailable_reason: string;
  classification_note: string;
};

export type Fit = {
  available: boolean;
  score?: number;
  reason?: string;
  already_on_roster?: boolean;
};

export type AcquisitionTargets = {
  available: boolean;
  target_need: { need_key: string; severity: number; explanation: string; skill: string } | null;
  sort_rule: string;
  filter_rule: string;
  search: {
    players_considered: number;
    no_skill_measured: number;
    does_not_improve_the_need: number;
    candidates: number;
  };
  feasibility: { trades_evaluated: number; applied: boolean } | null;
  targets: {
    player_id: string;
    name: string;
    team: { abbreviation: string };
    tei: number | null;
    skill_percentile: number | null;
  }[];
};

/* ------------------------------------------------------------------ queries */

/**
 * Every query below returns through this gate, and none of them may skip it.
 *
 * The app shell hydrates before this page and warms the shared `["teams"]` cache, so a
 * query can already hold data on the page's first client render while the server HTML
 * rendered a skeleton. React calls that "server rendered text didn't match the client",
 * and because this page streams inside the `loading.tsx` Suspense boundary the failure is
 * not cosmetic: the boundary never resolves and the whole narrative stays hidden behind
 * the loading state.
 *
 * The flag flips in a mount effect rather than via `useSyncExternalStore`. The store's
 * server snapshot covers the *initial* hydration pass, which is not the same thing as the
 * pass that hydrates a streamed boundary later — and that is exactly the case here. A
 * mount effect is unconditionally after commit, so the render React compares against the
 * server HTML always sees `false`.
 */
function useGated<T>(result: { data: T | undefined }): T | undefined {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    // Deferred by a tick so this is not a synchronous setState cascade inside the effect.
    const id = setTimeout(() => setReady(true), 0);
    return () => clearTimeout(id);
  }, []);
  return ready ? result.data : undefined;
}

/** The league board. Shares the shell's cache key, so this is usually free. */
export function useTeams(enabled = true) {
  const q = useQuery({
    queryKey: ["teams"],
    queryFn: () => api.get<Team[]>("/teams"),
    staleTime: 300_000,
    enabled,
  });
  return { data: useGated(q) };
}

/**
 * The dimension vocabulary: what Pivot claims to measure, what it does not, and why.
 * Static for a deployment, so it is cached hard.
 */
export function useVocabulary(enabled = true) {
  const q = useQuery({
    queryKey: ["intelligence-vocabulary"],
    queryFn: () => api.get<Vocabulary>("/intelligence/vocabulary"),
    staleTime: Infinity,
    enabled,
  });
  return { data: useGated(q) };
}

export function useTeamProfile(teamId: string | undefined, enabled = true) {
  const q = useQuery({
    queryKey: ["team-profile", teamId],
    queryFn: () => api.get<TeamProfile>(`/intelligence/teams/${teamId}/profile`),
    staleTime: 300_000,
    enabled: enabled && Boolean(teamId),
  });
  return { data: useGated(q) };
}

/** One player against one roster. The number the COMPARE act is built on. */
export function useFit(playerId: string | undefined, teamId: string | undefined, enabled = true) {
  const q = useQuery({
    queryKey: ["fit", playerId, teamId],
    queryFn: () => api.get<Fit>(`/intelligence/fit?player_id=${playerId}&team_id=${teamId}`),
    staleTime: 300_000,
    enabled: enabled && Boolean(playerId) && Boolean(teamId),
  });
  return { data: useGated(q) };
}

/** Resolve a player by name to the id the fit endpoint needs. */
export function usePlayerId(name: string, enabled = true) {
  const q = useQuery({
    queryKey: ["scroll-home-player", name],
    queryFn: async () => {
      const res = await api.get<{ players: { id: string; full_name: string }[] }>(
        `/players?q=${encodeURIComponent(name)}&rostered_only=true&limit=1`,
      );
      return res.players?.[0] ?? null;
    },
    staleTime: Infinity,
    enabled,
  });
  return { data: useGated(q) };
}

/** Need → candidates → evaluated trades. The RECOMMEND act's funnel is this response. */
export function useAcquisitionTargets(teamId: string | undefined, enabled = true) {
  const q = useQuery({
    queryKey: ["acquisition-targets", teamId],
    queryFn: () => api.get<AcquisitionTargets>(`/teams/${teamId}/acquisition-targets`),
    staleTime: 300_000,
    enabled: enabled && Boolean(teamId),
  });
  return { data: useGated(q) };
}

/* ------------------------------------------------------------------ helpers */

export const abbrOf = (teams: Team[] | undefined, abbr: string) =>
  teams?.find((t) => t.abbreviation === abbr);

/** Percent for display. Returns null rather than 0 when there is nothing measured. */
export const pct = (value: number | null | undefined) =>
  value === null || value === undefined ? null : Math.round(value * 100);
