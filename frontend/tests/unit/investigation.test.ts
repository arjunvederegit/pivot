import { describe, expect, it } from "vitest";
import {
  isCanonicalId,
  parseInvestigation,
  playerFileLink,
  tradeEvaluatorLink,
} from "@/lib/investigation";

const PLAYER = "a2ed238d-7cca-48e8-9fcb-e4703c27b421";
const TEAM = "fd37faf5-fbaa-40bf-bfe7-d8da496cf9b3";

describe("investigation links", () => {
  it("carries the canonical ids and the statistics season, and nothing else", () => {
    const href = tradeEvaluatorLink({ playerId: PLAYER, teamId: TEAM, statsSeason: "2025-26" });
    expect(href).toBe(
      `/trade-evaluator?team=${TEAM}&player=${PLAYER}&from=player-explorer&stats=2025-26`,
    );
  });

  it("round-trips through parseInvestigation", () => {
    const href = tradeEvaluatorLink({ playerId: PLAYER, teamId: TEAM, statsSeason: "2025-26" })!;
    const ctx = parseInvestigation(new URL(href, "http://x").searchParams);
    expect(ctx).toEqual({ playerId: PLAYER, teamId: TEAM, statsSeason: "2025-26", from: "player-explorer" });
  });

  it("refuses a provider id or a name where a canonical id belongs", () => {
    expect(tradeEvaluatorLink({ playerId: "1628369", teamId: TEAM, statsSeason: null })).toBeNull();
    expect(tradeEvaluatorLink({ playerId: "Jayson Tatum", teamId: TEAM, statsSeason: null })).toBeNull();
    expect(isCanonicalId("../../etc/passwd")).toBe(false);
  });

  it("drops a malformed team or season instead of echoing it", () => {
    const href = tradeEvaluatorLink({ playerId: PLAYER, teamId: "<script>", statsSeason: "last year" })!;
    expect(href).toBe(`/trade-evaluator?player=${PLAYER}&from=player-explorer`);
  });

  it("parse ignores a link with no source or a hostile source", () => {
    expect(parseInvestigation(new URLSearchParams(`player=${PLAYER}`))).toBeNull();
    expect(parseInvestigation(new URLSearchParams(`player=${PLAYER}&from=evil`))).toBeNull();
    expect(parseInvestigation(new URLSearchParams(`player=nope&from=player-explorer`))).toBeNull();
  });

  it("the way back to a player file keeps the canonical id", () => {
    expect(playerFileLink(PLAYER)).toBe(`/players/${PLAYER}?from=trade-evaluator`);
    expect(playerFileLink("nope")).toBe("/player-explorer");
  });
});
