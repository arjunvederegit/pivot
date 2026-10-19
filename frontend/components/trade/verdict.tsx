"use client";

/**
 * The verdict vocabulary the Trade Evaluator and the saved-deal report share: legality
 * glyphs, the projected-wins band on a shared axis, a rule row from the rules engine, and
 * the refusal a deal gets when it cannot be scored.
 *
 * Two rules hold everywhere these are used:
 * - Legality is a glyph AND a word, never colour alone, and an incomplete check stays
 *   incomplete — nothing here rounds "could not verify" up to "legal".
 * - A projected wins figure never travels without its 10th–90th percentile band, and a
 *   deal that fails a verified rule gets no score at all — only the rules that stop it.
 */

import clsx from "clsx";
import { money } from "@/lib/format";
import { teamIdentity } from "@/lib/teamIdentity";
import type { RuleResult, TeamEvaluation, Uncertainty } from "@/lib/types";
import { Badge } from "@/components/ui";

/** Four visually distinct states — glyph and word, never colour alone. */
export const LEGALITY_VISUAL: Record<string, { glyph: string; color: string; text: string }> = {
  verified_legal: { glyph: "✓", color: "var(--legal)", text: "text-legal" },
  verified_illegal: { glyph: "✕", color: "var(--illegal)", text: "text-illegal" },
  conditionally_valid: { glyph: "~", color: "var(--conditional)", text: "text-conditional" },
  not_evaluated: { glyph: "—", color: "var(--unknown)", text: "text-unavail" },
};

export const RULE_STATUS_WORD: Record<string, string> = {
  pass: "passes",
  fail: "fails",
  warning: "warning",
  unavailable: "not run",
};

function isCoverage(v: unknown): v is { players_known: number; players_total: number; complete?: boolean } {
  return (
    typeof v === "object" &&
    v !== null &&
    typeof (v as { players_known?: unknown }).players_known === "number" &&
    typeof (v as { players_total?: unknown }).players_total === "number"
  );
}

export const CONTRACT_TOOLTIP =
  "Contract data isn't imported, so salaries and contract years can't be shown. Pivot never estimates a salary.";

/** Sign taken from the rounded value, so a tiny negative never prints as "−0.0". */
export function signedWins(x: number): string {
  const r = Number(x.toFixed(1));
  return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r).toFixed(1)}`;
}

/** True when the simulation produced an outcome distribution to draw. */
export function hasOutcomeBand(u: Uncertainty | null | undefined): boolean {
  return Boolean(u && u.prob_positive !== null && u.prob_positive !== undefined);
}

/** The symmetric wins axis every side of one deal is drawn on: ±ceil(largest |p10|, |p90|). */
export function sharedWinsAxis(bands: Uncertainty[]): number {
  return Math.ceil(Math.max(1, ...bands.flatMap((u) => [Math.abs(u.p10), Math.abs(u.p90)])));
}

/**
 * The 10th–90th percentile band and its median on a symmetric wins axis. The zero line is
 * the honest reference — "this deal changes nothing" — so it is always drawn.
 */
export function WinsBand({
  u,
  axis,
  className,
  ticks = false,
}: {
  u: Uncertainty;
  axis: number;
  className?: string;
  /** Print −axis / 0 / +axis under the track. */
  ticks?: boolean;
}) {
  const toPct = (x: number) => 50 + (Math.max(-axis, Math.min(axis, x)) / axis) * 50;
  const lo = Math.min(u.p10, u.p90);
  const hi = Math.max(u.p10, u.p90);
  return (
    <div className={clsx("min-w-0", className)}>
      <div className="relative h-4" aria-hidden>
        <span className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-line" />
        <span className="absolute inset-y-0 left-1/2 w-px bg-faint/70" />
        <span
          className="absolute inset-y-[3px] rounded-full"
          style={{
            left: `${toPct(lo)}%`,
            width: `${Math.max(0.8, toPct(hi) - toPct(lo))}%`,
            background: "color-mix(in srgb, var(--signal) 30%, transparent)",
            boxShadow: "inset 0 0 0 1px color-mix(in srgb, var(--signal) 45%, transparent)",
          }}
        />
        <span
          className="absolute inset-y-0 w-[2px] rounded-full bg-signal"
          style={{ left: `calc(${toPct(u.median)}% - 1px)` }}
        />
      </div>
      {ticks && (
        <div className="numeral mt-1 flex justify-between text-[10px] font-medium text-faint" aria-hidden>
          <span>−{axis}</span>
          <span>0</span>
          <span>+{axis}</span>
        </div>
      )}
    </div>
  );
}

/** One rule from the rules engine. The code stays visible on the closed row; the
 *  calculation that produced the result opens underneath. */
export function RuleItem({ rule, teamAbbr }: { rule: RuleResult; teamAbbr?: string }) {
  const entries = Object.entries(rule.calculation ?? {});
  return (
    <li className="rounded-lg border border-hairline bg-panel2/60 transition-colors hover:border-line">
      <details className="group">
        <summary className="flex cursor-pointer list-none items-start gap-3 px-3 py-2.5">
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <Badge status={rule.status} className="min-w-[4.75rem] shrink-0">
                {RULE_STATUS_WORD[rule.status] ?? rule.status}
              </Badge>
              {teamAbbr && (
                <span className="flex shrink-0 items-center gap-1.5 text-[12px] font-medium text-foreground">
                  <span
                    aria-hidden
                    className="h-1.5 w-1.5 rounded-full"
                    style={{ background: teamIdentity(teamAbbr).bright }}
                  />
                  {teamAbbr}
                </span>
              )}
              <span className="min-w-0 text-[11px] font-medium tracking-[0.02em] text-faint [overflow-wrap:anywhere]">
                {rule.rule_code}
              </span>
            </span>
            <span className="mt-1.5 block text-[13px] leading-snug text-foreground">{rule.message}</span>
          </span>
          <span
            aria-hidden
            className="mt-0.5 shrink-0 text-faint transition-transform group-open:rotate-90"
          >
            ›
          </span>
        </summary>
        <div className="space-y-2 border-t border-hairline px-3 py-2.5 text-[12px]">
          <p className="text-faint">Confidence: {rule.confidence}</p>
          {entries.length > 0 && (
            <dl className="grid gap-x-5 gap-y-1 sm:grid-cols-2">
              {entries.map(([key, value]) => (
                <div
                  key={key}
                  className="flex flex-wrap justify-between gap-x-2 border-b border-hairline pb-1"
                >
                  <dt className="text-muted">{key.replaceAll("_", " ")}</dt>
                  <dd className="numeral ml-auto min-w-0 text-right font-medium text-foreground [overflow-wrap:anywhere]">
                    {typeof value === "number"
                      ? Math.abs(value) >= 100_000
                        ? money(value)
                        : String(value)
                      : Array.isArray(value)
                        ? value.join(", ") || "—"
                        : isCoverage(value)
                          ? `${value.players_known} of ${value.players_total} priced${value.complete ? "" : " (floor)"}`
                          : value !== null && typeof value === "object"
                            ? JSON.stringify(value)
                            : String(value)}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {rule.source_reference && (
            <p className="text-[11px] leading-snug text-faint">Reference: {rule.source_reference}</p>
          )}
        </div>
      </details>
    </li>
  );
}

/**
 * Players the impact model has never scored are excluded from the projection and named.
 * Before R1-4 they arrived carrying `tei = 0.0` — the 63rd percentile of rostered players —
 * so a player with no data quietly moved the number.
 */
export function UnmodeledNote({ teamEval, className }: { teamEval: TeamEvaluation; className?: string }) {
  const names = teamEval.unmodeled_players ?? [];
  if (!teamEval.has_unmodeled_players || names.length === 0) return null;
  const inDeal = [...teamEval.incoming, ...teamEval.outgoing]
    .filter((p) => p.tei === null)
    .map((p) => p.name);
  return (
    <p className={clsx("flex gap-2 text-[12px] leading-snug text-muted", className)}>
      <span aria-hidden className="text-unavail">
        —
      </span>
      <span>
        {names.length === 1 ? "1 player has" : `${names.length} players have`} no impact
        estimate and {names.length === 1 ? "was" : "were"} left out of the projection rather
        than given a league-average stand-in: {names.join(", ")}.
        {inDeal.length > 0 && (
          <>
            {" "}
            {inDeal.length === 1 ? "One of them is" : "Some of them are"} in this deal (
            {inDeal.join(", ")}), so confidence is reported as low.
          </>
        )}{" "}
        They still count against the roster limits.
      </span>
    </p>
  );
}

/**
 * The refusal. A deal that fails a verified rule cannot happen, so it gets no verdict, no
 * score and no breakdown — only the reason and the rules that caused it, each naming the
 * side it fails for (a deal can be illegal because of the counterparty).
 */
export function RefusalBlock({
  evaluation,
  abbreviation,
  teamAbbreviations,
  closing,
}: {
  evaluation: TeamEvaluation;
  abbreviation: string;
  teamAbbreviations: Record<string, string>;
  /** The last line — what the reader can do about it on this page. */
  closing: string;
}) {
  const suppressed = evaluation.decision_status === "suppressed_illegal";
  const rules = evaluation.suppression?.failing_rules ?? [];
  return (
    <div
      className={clsx(
        "rounded-xl border px-5 py-5",
        suppressed ? "border-illegal/45 bg-illegal/[0.06]" : "border-dashed border-unavail/55 bg-unavail/[0.06]",
      )}
    >
      <div className="eyebrow flex items-center gap-2">
        <span
          aria-hidden
          className={clsx("h-1.5 w-1.5 rounded-full", suppressed ? "bg-illegal" : "bg-unavail")}
        />
        Verdict for {abbreviation}
      </div>
      <h3 className="title-lg mt-2 text-balance text-foreground">
        {suppressed ? "No decision score — this deal is illegal" : "No decision score"}
      </h3>
      <p className="mt-2.5 max-w-prose text-[14px] leading-relaxed text-muted">
        {evaluation.suppression?.message ??
          "No component could be scored for this team with the data available."}
      </p>
      {rules.length > 0 && (
        <ul className="mt-4 grid gap-2 md:grid-cols-2">
          {rules.map((rule, index) => (
            <li
              key={`${rule.rule_code}-${rule.team_id ?? "all"}-${index}`}
              className="rounded-lg border border-illegal/30 bg-panel px-3.5 py-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge status="fail">{rule.rule_code}</Badge>
                {rule.team_id && teamAbbreviations[rule.team_id] && (
                  <span className="text-[12px] text-muted">
                    fails for {teamAbbreviations[rule.team_id]}
                  </span>
                )}
              </div>
              <p className="mt-2 text-[13px] leading-snug text-foreground">{rule.message}</p>
              {rule.source_reference && (
                <p className="mt-1.5 text-[11px] text-faint">Reference: {rule.source_reference}</p>
              )}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-4 border-t border-hairline pt-3 text-[12px] leading-snug text-faint">
        Salary in {money(evaluation.legality.incoming_salary)} · out{" "}
        {money(evaluation.legality.outgoing_salary)} · roster {evaluation.legality.roster_before}{" "}
        → {evaluation.legality.roster_after}. {closing}
      </p>
    </div>
  );
}
