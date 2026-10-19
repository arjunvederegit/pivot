/**
 * R9.3A — types and pure helpers for the contract and financial records the backend assembles
 * (`app/services/financials.py`, `app/services/trade_financials.py`).
 *
 * The browser only lays these out. Every rule that decides what may be *claimed* lives on the
 * backend; the helpers here decide only how a claim is worded, and they are written so the wrong
 * wording is unreachable: an unknown amount renders as unknown (never "$0"), a length that is only a
 * lower bound renders with a plus, a subtotal carries its label.
 */

export type FinancialStatus = "priced" | "unpriced" | "no_contract";
export type ContractEndStatus = "ends" | "through_horizon" | "unknown" | "none";

export interface SeasonFigures {
  season: string;
  cap_hit: number;
  base_salary: number | null;
  total_cash: number | null;
  guaranteed: number | null;
  player_option: boolean | null;
  team_option: boolean | null;
  salary_basis: string | null;
}

export interface ContractEnd {
  status: ContractEndStatus;
  final_season: string | null;
  years_remaining: number | null;
  years_remaining_is_lower_bound: boolean;
  has_gap: boolean;
  basis: string;
}

export interface PlayerFinancialsRecord {
  player_id: string;
  name: string;
  financial_season: string;
  status: FinancialStatus;
  status_reason: string;
  cap_hit: number | null;
  base_salary: number | null;
  total_cash: number | null;
  salary_basis: string | null;
  seasons: SeasonFigures[];
  contract_end: ContractEnd;
  contract_type: string | null;
  not_two_way: boolean | null;
  not_two_way_basis: string | null;
  source: { provider: string | null; name: string | null; retrieved_at: string | null };
}

export interface FinancialHorizon {
  financial_season: string;
  seasons_loaded: string[];
  last_season_loaded: string | null;
  loaded_at: Record<string, string>;
  partial_seasons: string[];
  note: string;
}

export interface TeamFinancialSummary {
  financial_season: string;
  roster_season: string;
  roster_basis: { provisional?: boolean; status?: string; basis?: string; snapshot_retrieved_at?: string } | null;
  roster_is_provisional: boolean;
  coverage: { roster_size: number; priced: number; unpriced: number; share: number | null; complete: boolean };
  subtotal_label: "Payroll" | "Known salary subtotal";
  known_subtotal: number | null;
  payroll: number | null;
  is_floor: boolean;
  thresholds_cleared_by_known_subtotal: string[];
  thresholds: {
    season: string;
    salary_cap: number;
    luxury_tax: number;
    first_apron: number;
    second_apron: number;
    source_name: string | null;
    source_url: string | null;
    verified_at: string | null;
  } | null;
  position: {
    payroll: number;
    room_below_cap: number;
    over_cap_by: number;
    status: string;
  } | null;
  unpriced_players: { player_id: string; name: string; status: FinancialStatus; reason: string }[];
  commitments: { season: string; known_total: number; players: number; is_financial_season: boolean }[];
  commitments_note: string;
  horizon: FinancialHorizon;
  supported: string[];
  withheld: { conclusion: string; reason: string }[];
  definitions: string;
  not_on_file: Record<string, string>;
  retrieved_at: string | null;
}

/* ----------------------------------------------------------------------- trade financials */

export type AssessmentVerdict = "prohibited" | "indeterminate" | "passes_checked_rules";

export interface AssessmentEntry {
  rule_code: string;
  title: string;
  team_id: string | null;
  team: string;
  status: "pass" | "fail" | "warning" | "unavailable";
  message: string;
  confidence: "high" | "medium" | "low";
  source_reference: string;
  calculation: Record<string, unknown>;
}

export interface LegalityAssessment {
  verdict: AssessmentVerdict;
  headline: string;
  statement: string;
  counts: { checked_passed: number; failed: number; unverified: number };
  indications: { rule_code: string; title: string; team: string; explanation: string; confidence: string }[];
  checked: AssessmentEntry[];
  failed: AssessmentEntry[];
  unverified: AssessmentEntry[];
  rules: { rule_code: string; title: string; status: string; results: AssessmentEntry[] }[];
  unchecked_categories: { category: string; note: string }[];
  engine_status: string;
}

export interface TradeSide {
  players: string[];
  count: number;
  known_cap_hit_total: number | null;
  known_count: number;
  unknown_players: string[];
  complete: boolean;
  cap_hit_total: number | null;
  total_cash: number | null;
  base_salary_total: number | null;
}

export interface TradeTeamFinancials {
  team_id: string;
  abbreviation: string;
  name: string;
  outgoing: TradeSide;
  incoming: TradeSide;
  net_salary_change: number | null;
  incoming_to_outgoing_ratio: number | null;
  net_salary_note: string | null;
  known_subtotal_before: number | null;
  known_subtotal_after: number | null;
  coverage_before: { known: number; players_known: number; players_total: number; complete: boolean } | null;
  coverage_after: { known: number; players_known: number; players_total: number; complete: boolean } | null;
  subtotal_label: "Payroll" | "Known salary subtotal";
  apron_status_at_least_after: string | null;
  apron_status_after: string | null;
  commitments_by_season: {
    season: string;
    is_financial_season: boolean;
    outgoing: { known_total: number; unknown_players: string[]; complete: boolean };
    incoming: { known_total: number; unknown_players: string[]; complete: boolean };
    net_known: number | null;
  }[];
  matching_eligible: { outgoing: number | null; incoming: number | null; note: string };
}

export interface TradeFinancialAnalysis {
  financial_season: string;
  season_basis: {
    financial: string;
    roster: string | null;
    roster_is_provisional: boolean;
    statistics: string | null;
    note: string;
  };
  thresholds: {
    season: string;
    salary_cap: number;
    luxury_tax: number;
    first_apron: number;
    second_apron: number;
    source_name: string;
  };
  definitions: string;
  players: {
    player_id: string;
    name: string;
    from_team_id: string;
    to_team_id: string;
    financials: PlayerFinancialsRecord | null;
    contract_type_stated: string | null;
    matching_class: "standard" | "two-way" | null;
    matching_class_basis: string | null;
  }[];
  teams: Record<string, TradeTeamFinancials>;
  not_on_file: Record<string, string>;
  legality_assessment: LegalityAssessment;
  contract_provider_configured: boolean;
}

/* ------------------------------------------------------------------------------- wording */

/** Exact dollars for the figures a salary decision turns on. Unknown is "—", never "$0". */
export function dollars(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `$${Math.round(value).toLocaleString("en-US")}`;
}

/** A contract's remaining length as words; a lower bound carries a plus, an unknown length says so. */
export function contractLength(end: ContractEnd | null | undefined): string {
  if (!end) return "length unknown";
  if (end.status === "ends" && end.final_season && end.years_remaining !== null) {
    const years = end.years_remaining;
    // A gap means the seasons with a row are not consecutive: say so instead of implying an unbroken run.
    return `${years} season${years === 1 ? "" : "s"} on file${end.has_gap ? " (with a gap)" : ""} · through ${end.final_season}`;
  }
  if (end.status === "through_horizon" && end.final_season && end.years_remaining !== null) {
    return `${end.years_remaining}+ seasons${end.has_gap ? " (with a gap)" : ""} · through at least ${end.final_season}`;
  }
  if (end.status === "none") return "no contract on file";
  return "length unknown";
}

/** Compact form for a table cell. */
export function contractLengthShort(end: ContractEnd | null | undefined): string {
  if (!end) return "—";
  if (end.status === "ends" && end.final_season) return `thru ${end.final_season.slice(2)}`;
  if (end.status === "through_horizon" && end.final_season) return `thru ≥ ${end.final_season.slice(2)}`;
  return "—";
}

export const VERDICT_WORD: Record<AssessmentVerdict, string> = {
  prohibited: "Prohibited under a checked rule",
  indeterminate: "Indeterminate",
  passes_checked_rules: "Passes the rules Pivot checks",
};

/** Whether the figure is a subtotal that must carry its label. */
export function subtotalLabel(complete: boolean): "Payroll" | "Known salary subtotal" {
  return complete ? "Payroll" : "Known salary subtotal";
}

/** `$12.3M`-style with an explicit sign; null means "not stated", not zero. */
export function signedMillions(value: number | null | undefined): string {
  if (value === null || value === undefined) return "not stated";
  const m = value / 1_000_000;
  const r = Number(m.toFixed(1));
  // A real but tiny difference must not read as "$0.0M" (which looks like "no change").
  if (r === 0 && value !== 0) return `${value > 0 ? "+" : "−"}<$0.1M`;
  return `${r > 0 ? "+" : r < 0 ? "−" : ""}$${Math.abs(r).toFixed(1)}M`;
}

/** What a source rail may say about the figure's basis: only "cap hit" when the rows say so. */
export function basisLabel(basis: string | null | undefined): string {
  return basis === "cap_hit" ? "cap-hit basis" : "basis not stated";
}
