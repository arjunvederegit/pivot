import { api } from "@/lib/api";
import { evaluateResponseSchema } from "@/lib/schemas";
import type { LegalityResponse, PickMove, PlayerMove, TeamEvaluation } from "@/lib/types";

/** What the Trade Evaluator sends to `POST /trades/evaluate`. */
export interface TradeEvaluationRequest {
  team_ids: string[];
  player_moves: PlayerMove[];
  pick_moves: PickMove[];
  scenario_id: string | null;
  strategy: string;
}

/** The part of the response the evaluator page renders. */
export interface TradeEvaluationResult {
  legality: LegalityResponse;
  evaluations: Record<string, TeamEvaluation>;
}

/**
 * The response is checked against `evaluateResponseSchema` before the page may treat it as an
 * evaluation; a malformed 200 rejects with the client's `contract_mismatch` `ApiError`.
 */
export function evaluateTrade(body: TradeEvaluationRequest): Promise<TradeEvaluationResult> {
  return api.post<TradeEvaluationResult>("/trades/evaluate", body, evaluateResponseSchema);
}
