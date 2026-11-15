import { useState } from "react";

/**
 * The team whose full analysis the evaluator shows: the remembered selection if the evaluation
 * has that team, otherwise the first team in the deal.
 */
export function resolveActiveTeam(
  selected: string | null,
  evaluations: Record<string, unknown>,
  teamIds: string[],
): string {
  return selected !== null && evaluations[selected] ? selected : teamIds[0];
}

/**
 * The remembered team selection, kept above the evaluation result so that clearing the result
 * (an edit, or a new evaluation starting) does not forget which team the user was reading.
 *
 * It is tracked by team id, never by position, and is dropped when its team leaves the deal or a
 * different saved trade is adopted as the draft (`draftKey`), so a stale choice cannot come back
 * later. This is presentation state; it takes no part in which evaluation response is current.
 */
export function useSelectedTeam(teamIds: string[], draftKey: string | null) {
  const [state, setState] = useState<{ teamId: string | null; draftKey: string | null }>({
    teamId: null,
    draftKey,
  });
  let teamId = state.teamId;
  if (state.draftKey !== draftKey || (teamId !== null && !teamIds.includes(teamId))) {
    teamId = null;
    setState({ teamId: null, draftKey });
  }
  return [teamId, (next: string) => setState({ teamId: next, draftKey })] as const;
}
