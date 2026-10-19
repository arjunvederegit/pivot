/**
 * R9.3A — the smallest thing that lets one investigation travel between tools.
 *
 * A link between tools carries *canonical identifiers and the period the user was looking at*, nothing
 * else: the Pivot player id (never a provider id), the team the player was on in the roster snapshot the
 * user saw, and the statistics season behind the evidence on screen. The receiving tool re-reads everything
 * else from the backend, so no number travels through a URL and no stale figure can be carried silently.
 *
 *   Player Explorer  ->  /trade-evaluator?team=<id>&player=<id>&from=player-explorer&stats=<season>
 *   Trade Evaluator  ->  /players/<id>?from=trade-evaluator
 *
 * The receiving tool must verify the context before applying it (the player is on that team's roster in its own
 * snapshot) and say so when it cannot — see `components/trade/staged-player.tsx`. R9.3B can widen this to a
 * shared investigation object (docs/r9-3a/connected-workflows.md); the parameters here are its first fields.
 */

export type InvestigationSource = "player-explorer" | "trade-evaluator";

export interface InvestigationContext {
  playerId: string;
  teamId: string | null;
  statsSeason: string | null;
  from: InvestigationSource;
}

const SEASON = /^\d{4}-\d{2}$/;
/** Canonical Pivot ids are UUIDs; anything else is refused rather than echoed into a URL or a request. */
const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isCanonicalId(value: string | null | undefined): value is string {
  return typeof value === "string" && ID.test(value);
}

/** Player Explorer -> Trade Evaluator. Returns null when there is no canonical player id to carry. */
export function tradeEvaluatorLink(ctx: Omit<InvestigationContext, "from">): string | null {
  if (!isCanonicalId(ctx.playerId)) return null;
  const params = new URLSearchParams();
  if (isCanonicalId(ctx.teamId)) params.set("team", ctx.teamId);
  params.set("player", ctx.playerId);
  params.set("from", "player-explorer");
  if (ctx.statsSeason && SEASON.test(ctx.statsSeason)) params.set("stats", ctx.statsSeason);
  return `/trade-evaluator?${params.toString()}`;
}

/** Trade Evaluator -> Player Explorer. */
export function playerFileLink(playerId: string, from: InvestigationSource = "trade-evaluator"): string {
  return isCanonicalId(playerId) ? `/players/${playerId}?from=${from}` : "/player-explorer";
}

/** Reads the context a link carried; null unless it names a canonical player and a known source. */
export function parseInvestigation(params: URLSearchParams): InvestigationContext | null {
  const playerId = params.get("player");
  const from = params.get("from");
  if (!isCanonicalId(playerId)) return null;
  if (from !== "player-explorer" && from !== "trade-evaluator") return null;
  const team = params.get("team");
  const stats = params.get("stats");
  return {
    playerId,
    teamId: isCanonicalId(team) ? team : null,
    statsSeason: stats && SEASON.test(stats) ? stats : null,
    from,
  };
}

/** The query keys a link may add that the Trade Evaluator consumes once and then drops from the URL. */
export const TRANSIENT_KEYS = ["team", "player", "from", "stats"] as const;
