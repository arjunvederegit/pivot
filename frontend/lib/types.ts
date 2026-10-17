export interface Provenance {
  source_provider: string;
  upstream: string;
  source_retrieved_at: string | null;
}

export interface Team {
  id: string;
  nba_team_id: number;
  full_name: string;
  abbreviation: string;
  nickname?: string;
  city: string;
  conference: string | null;
  division: string | null;
  provenance?: Provenance | null;
}

/**
 * The role a player was assigned.
 *
 * Three incompatible local shapes existed for this: `string`, `{label, cluster_id}`, and
 * an inline object on the comparables list. One name per shape, defined once.
 *
 * `role_id` replaced `cluster_id` in R4-3. The old field was an arbitrary k-means index
 * that could change meaning on every retrain; this one comes from a frozen append-only
 * map, so it is stable across retrains and comparable across seasons.
 */
export interface ArchetypeAssignment {
  label: string;
  role_id: number;
}

/** A comparable player, as returned alongside a player detail. */
export interface ComparablePlayer {
  player_id: string;
  name: string;
  tei: number | null;
  /** The role *label* only — comparables share the subject's role by construction. */
  archetype: string | null;
}

export interface RosterPlayer {
  player_id: string;
  nba_player_id: number;
  name: string;
  position: string | null;
  jersey_number: string | null;
  age: number | null;
  height_inches: number | null;
  years_experience: number | null;
  tei: number | null;
  /** The estimate's 10th / 90th percentile band; null exactly when `tei` is null. Optional
   *  because servers older than the impact-reference change did not send it. */
  tei_low?: number | null;
  tei_high?: number | null;
  /** Cluster label only; the full assignment lives on the player detail. */
  archetype: string | null;
  availability: number | null;
  /** Cap-league-year salary from the contracts snapshot; null when not on file. */
  salary: number | null;
  /** Seasons the snapshot carries from the cap league year on; null when not on file. */
  contract_years_remaining: number | null;
  /** null means the provider does not report contract type — never assume "standard". */
  contract_type: string | null;
}

export interface RosterResponse {
  team: Team;
  season: string;
  roster: RosterPlayer[];
  /** Human-readable upstream(s), derived from the rows — never assumed. */
  source: string;
  /** The raw `source_provider` keys behind `source`, for anything that needs to branch. */
  source_providers: string[];
  source_retrieved_at: string | null;
}

export interface TeamNeedItem {
  need_key: string;
  severity: number;
  percentile: number | null;
  explanation: string;
}

/** How many of how many contracts a payroll figure was built from (R2c). */
export interface PayrollCoverage {
  known: number;
  players_known: number;
  players_total: number;
  players_unknown: number;
  share: number | null;
  complete: boolean;
  /** True when salaries are missing: the figure is a floor, not the payroll. */
  is_lower_bound: boolean;
}

export interface PayrollResponse {
  team_id: string;
  league_year: string;
  roster_size: number;
  players_with_salary: number;
  players_without_salary: number;
  /**
   * The verified payroll: null unless every rostered player is priced. This is the only
   * figure that may be compared against a cap threshold.
   */
  payroll: number | null;
  payroll_available: boolean;
  /**
   * The disclosed payroll: the sum of the contracts on file, which is a LOWER BOUND when
   * coverage is partial. Never render it without `payroll_coverage_note` beside it.
   */
  payroll_known: number | null;
  payroll_is_lower_bound: boolean;
  payroll_coverage: PayrollCoverage;
  payroll_coverage_note: string;
  players_missing_salary: string[];
  unavailable_reason?: string;
  contract_provider_configured: boolean;
  players: {
    player_id: string;
    name: string;
    salary: number | null;
    contract_type: string | null;
    source_name: string | null;
    source_date: string | null;
  }[];
  cap_context?: {
    salary_cap: number;
    luxury_tax: number;
    first_apron: number;
    second_apron: number;
    room_below_tax: number;
    cap_source: string;
  };
  /**
   * Present instead of `cap_context` under partial coverage. Carries no room/space
   * figure — that needs the missing salaries — only the thresholds the contracts on file
   * already exceed, which no completion of the data can undo.
   */
  cap_context_partial?: {
    salary_cap: number;
    luxury_tax: number;
    first_apron: number;
    second_apron: number;
    cap_source: string;
    thresholds_already_cleared: string[];
    note: string;
  };
}

export type RuleStatus = "pass" | "fail" | "warning" | "unavailable";
export type LegalityStatus =
  | "verified_legal"
  | "verified_illegal"
  | "conditionally_valid"
  | "not_evaluated";

export interface RuleResult {
  rule_code: string;
  status: RuleStatus;
  team_id: string | null;
  message: string;
  calculation: Record<string, unknown>;
  source_reference: string;
  confidence: "high" | "medium" | "low";
}

export interface TeamLegality {
  abbreviation: string;
  status: LegalityStatus;
  outgoing_salary: number | null;
  incoming_salary: number | null;
  /** Verified payroll — null unless every rostered player is priced. */
  payroll_before: number | null;
  payroll_after: number | null;
  apron_status_before: string | null;
  apron_status_after: string | null;
  /** Disclosed payroll — a lower bound; always render the coverage with it (R2c). */
  payroll_known_before: number | null;
  payroll_known_after: number | null;
  payroll_coverage_before: PayrollCoverage | null;
  payroll_coverage_after: PayrollCoverage | null;
  payroll_coverage_note: string | null;
  /**
   * The highest threshold the known salaries alone already clear. Null means nothing is
   * proven — never "below the tax", which partial data cannot establish.
   */
  apron_status_at_least_before: string | null;
  apron_status_at_least_after: string | null;
  roster_before: number;
  roster_after: number;
}

export interface LegalityResponse {
  league_year: string;
  overall_status: LegalityStatus;
  teams: Record<string, TeamLegality>;
  rule_results: RuleResult[];
  cap_parameters_source: string;
  contract_provider_configured: boolean;
}

export interface Uncertainty {
  n_draws: number;
  median: number;
  p10: number;
  p90: number;
  /** null when nothing moves: an all-zero draw array has no probability to report. */
  prob_positive: number | null;
  unavailable?: string;
  top_uncertainty_drivers: { side: string; spread_wins: number }[];
}

export interface TornadoBar {
  component: string;
  utility_low: number;
  utility_high: number;
  base: number;
}

/**
 * Why `composite_utility: null` — the two cases are not the same and must not render
 * the same way.
 *
 * - `suppressed_illegal`: the deal fails a verified CBA rule, so it cannot happen. We
 *   refuse to score it; `suppression.failing_rules` says which rule and why.
 * - `insufficient_data`: no component could be scored with the data available.
 * - `scored`: `composite_utility` is a number.
 */
export type DecisionStatus = "scored" | "suppressed_illegal" | "insufficient_data";

export interface Suppression {
  reason: string;
  message: string;
  failing_rules?: {
    rule_code: string;
    team_id: string | null;
    message: string;
    calculation: Record<string, unknown>;
    source_reference: string;
  }[];
}

export interface EvaluatedPlayer {
  player_id: string;
  name: string;
  /** null when the player has no impact estimate — never substituted with 0. */
  tei: number | null;
}

export interface TeamEvaluation {
  team_id: string;
  legality: TeamLegality;
  decision_status: DecisionStatus;
  suppression: Suppression | null;
  composite_utility: number | null;
  confidence: string;
  components: Record<string, number | null>;
  excluded_components: string[];
  drivers?: { component: string; score: number; weight: number; contribution: number }[];
  weights: Record<string, number>;
  detail: Record<string, Record<string, unknown>>;
  uncertainty: Uncertainty;
  sensitivity_tornado: TornadoBar[];
  incoming: EvaluatedPlayer[];
  outgoing: EvaluatedPlayer[];
  has_unmodeled_players?: boolean;
  unmodeled_players?: string[];
  evaluated_at: string;
}

export interface PlayerMove {
  player_id: string;
  from_team_id: string;
  to_team_id: string;
}

export interface PickMove {
  from_team_id: string;
  to_team_id: string;
  draft_year: number;
  round_number: number;
  protections?: string | null;
  is_hypothetical: boolean;
}

export interface Scenario {
  id: string;
  name: string;
  focal_team_id: string;
  focal_team: { abbreviation: string; full_name: string };
  strategy: string;
  horizon_years: number;
  risk_tolerance: string;
  untouchable_player_ids: string[];
  preferred_outgoing_player_ids: string[];
  weights: Record<string, number>;
  created_at: string;
}

export interface TradeSummary {
  id: string;
  name: string;
  scenario_id: string | null;
  created_at: string;
  teams: string[];
  n_players: number;
  n_picks: number;
}

export interface TradeDetail {
  id: string;
  name: string;
  scenario_id: string | null;
  notes: string | null;
  created_at: string;
  teams: { team_id: string; abbreviation: string; name: string }[];
  assets: {
    asset_type: string;
    from_team_id: string;
    to_team_id: string;
    player_id: string | null;
    player_name: string | null;
    draft_year: number | null;
    round_number: number | null;
    protections: string | null;
    is_hypothetical: boolean;
  }[];
  legality: LegalityResponse;
  evaluations: Record<string, TeamEvaluation>;
}

export interface ComparisonAlternative {
  trade_id: string;
  name: string;
  legality_status: LegalityStatus;
  decision_status: DecisionStatus;
  suppression: Suppression | null;
  /** The backend's confidence — never re-derived on the client (C13). */
  confidence: string;
  has_unmodeled_players: boolean;
  unmodeled_players: string[];
  composite_utility: number | null;
  components: Record<string, number | null>;
  delta_wins: number | null;
  uncertainty: Uncertainty;
  payroll_after: number | null;
  apron_status_after: string | null;
  incoming: { name: string; tei: number | null }[];
  outgoing: { name: string; tei: number | null }[];
  dominated_by: string | null;
}

export interface ComparisonResponse {
  id: string;
  name: string;
  focal_team_id: string | null;
  weights: Record<string, number>;
  alternatives: ComparisonAlternative[];
  sensitivity: {
    first_place_share: Record<string, number>;
    rank_volatility: Record<string, number>;
    median_rank: Record<string, number>;
  };
  note: string;
}

export interface DataHealth {
  generated_at: string;
  current_season: string;
  cap_league_year: string;
  providers: Record<
    string,
    {
      configured?: boolean;
      enabled?: boolean;
      package_version?: string;
      upstream?: string;
      provider?: string | null;
      note?: string | null;
      endpoints?: {
        endpoint: string;
        successes: number;
        failures: number;
        last_error: string | null;
        last_latency_ms: number | null;
      }[];
    }
  >;
  cache_backend: string;
  tables: Record<string, { rows: number; last_retrieved_at: string | null; stale: boolean | null }>;
  cap_parameter_years: string[];
  recent_sync_runs: {
    job: string;
    status: string;
    rows: number;
    started_at: string | null;
    finished_at: string | null;
    error: string | null;
  }[];
  /** When NBA.com data was last *retrieved* — not when some job last finished. */
  last_successful_sync: string | null;
  /** When any successful job last finished, including local-file ones. */
  last_job_finished_at: string | null;
  nba_tables_stale: string[];
  open_quality_issues: { check: string; severity: string; message: string; detected_at: string | null }[];
  /** The list above is capped; these describe the real backlog behind it. */
  open_quality_issue_total: number;
  open_quality_issue_counts: Record<string, number>;
  open_quality_issues_truncated: boolean;
  active_models: {
    name: string;
    version: string;
    algorithm: string;
    trained_at: string | null;
    validation: Record<string, unknown>;
  }[];
  source_cards: SourceCard[];
  asset_coverage: Record<string, number>;
}

/** Fan-readable data-source summary card served by /data-health. */
export interface SourceCard {
  key: string;
  title: string;
  status: "fresh" | "stale" | "derived" | "incomplete" | "unavailable" | "failed";
  last_update: string | null;
  coverage: string;
  source: string;
  action: string | null;
}

export interface GeneratedCandidate {
  counterparty: { team_id: string; abbreviation: string; name: string };
  outgoing: { player_id: string; name: string; tei: number }[];
  incoming: { player_id: string; name: string; tei: number }[];
  legality_status: LegalityStatus;
  focal_utility: number;
  counterparty_utility: number;
  focal_components: Record<string, number | null>;
  rationale: string;
}

export interface SeasonTotalsPlayer {
  player_id: string;
  nba_player_id: number;
  name: string;
  position: string | null;
  team_abbr: string | null;
  gp: number;
  totals: Record<string, number | null>;
  per_game: Record<string, number | null>;
  rates: Record<string, number | null>;
}

export interface SeasonTotalsResponse {
  season: string;
  count: number;
  available: boolean;
  note: string | null;
  source: string;
  imported_at: string | null;
  players: SeasonTotalsPlayer[];
}

export interface CapOutlookContractSeason {
  season: string;
  salary: number;
  player_option: boolean;
  team_option: boolean;
}

export interface CapOutlookPlayer {
  player_id: string;
  name: string;
  seasons: CapOutlookContractSeason[];
  expiring: boolean;
  no_trade_clause: boolean;
  source_name: string | null;
  source_date: string | null;
}

export interface CapParameters {
  salary_cap: number;
  luxury_tax: number;
  first_apron: number;
  second_apron: number;
}

export interface CapOutlookUnavailable {
  team_id: string;
  available: false;
  reason: string;
  contract_provider_configured: boolean;
}

export interface CapOutlookAvailable {
  team_id: string;
  available: true;
  cap_league_year: string;
  roster_size: number;
  players_with_contracts: number;
  complete: boolean;
  seasons: { season: string; total: number; players: number }[];
  players: CapOutlookPlayer[];
  cap_parameters: CapParameters | null;
  note: string;
}

export type CapOutlookResponse = CapOutlookUnavailable | CapOutlookAvailable;

/* ------------------------------------------------------ R6: precedent and discovery */

export interface RoleShareRow {
  role: string;
  minutes_before: number;
  minutes_after: number;
  delta: number;
  league_median: number | null;
  league_threshold: number | null;
  congested: boolean;
  lost: boolean;
}

export interface RosterShapeDetail {
  unavailable?: string;
  roles?: RoleShareRow[];
  arriving_roles?: string[];
  congested_roles?: string[];
  roles_lost?: string[];
  congestion_percentile?: number;
  basis?: string;
  lineup_fit?: { available: boolean; reason: string; also: string; recheck: string };
}

export interface ComparableLeg {
  name: string;
  player_id: string | null;
  tei: number | null;
  minutes: number | null;
  age: number | null;
  no_prior_nba_season: boolean;
}

export interface ComparablePick {
  draft_year: number;
  round_number: number;
  conveyance: string;
}

export interface ComparableSide {
  key: string;
  team_abbreviation: string;
  team_name: string | null;
  counterparties: string[];
  season: string;
  feature_season: string;
  transaction_date: string | null;
  is_in_season: boolean;
  n_teams: number;
  incoming: ComparableLeg[];
  outgoing: ComparableLeg[];
  picks_in: ComparablePick[];
  picks_out: ComparablePick[];
  similarity: number;
  distance: number;
  why: string[];
  dimension_similarity: Record<
    string,
    { similarity: number; weight: number; features: Record<string, unknown> }
  >;
  contributions: { dimension: string; share: number }[];
  dimensions_unavailable: string[];
  reported_not_scored: { cash_involved: boolean; trade_exception_received: boolean };
  source_text: string;
  notes_text: string | null;
  unparsed_assets: string[];
}

export interface ComparablesCoverage {
  trades_ingested: number;
  seasons_ingested: string[];
  sides_total: number;
  sides_with_production: number;
  sides_rankable: number;
  /** Distinct completed trades at least one rankable side came from. Always <= `trades_ingested`. */
  trades_rankable: number;
  sides_blocked_by_unmodelled_players: number;
  seasons_with_production: string[];
  /** False when no season calendar has been ingested and each feature season was decided
   *  from the trade's calendar month instead. */
  calendar_backed: boolean;
  seasons_with_calendar: string[];
  note: string;
}

export interface ComparablesResponse {
  available: boolean;
  unavailable_reason?: string;
  unmodelled_players?: string[];
  query: { feature_season: string; team_abbreviation: string; rankable: boolean };
  coverage: ComparablesCoverage;
  weights?: Record<string, number>;
  dimensions?: Record<string, { features: string[]; label: string }>;
  not_scored?: { field: string; reason: string }[];
  comparables: ComparableSide[];
}

export interface AcquisitionTarget {
  player_id: string;
  name: string;
  team: { id: string; abbreviation: string; name: string };
  tei: number | null;
  minutes: number | null;
  age: number | null;
  need_skill: string;
  skill_percentile: number;
  need_improvement: number;
  projected_delta_wins: number | null;
  fit_score: number | null;
  redundancy: number;
  acquisition_cost: {
    package_value: number;
    package_value_projected_wins: number;
    salary: number | null;
    minimum_outgoing_salary: number | null;
    salary_note: string | null;
    minutes_share_of_own_team: number | null;
    rank_on_own_team_by_minutes: number | null;
    reported_not_scored: string;
  };
  suggested_package: { player_id: string; name: string; tei: number | null; minutes: number | null; salary: number | null }[];
  suggested_package_note: string;
  trade_evaluation?: {
    team_ids: string[];
    player_moves: PlayerMove[];
    legality_status: string;
    focal_utility: number;
    counterparty_utility: number;
    projected_delta_wins: { focal: number | null; counterparty: number | null };
  };
  why: string[];
}

export interface AcquisitionDiagnosisRow {
  need_key: string;
  severity: number;
  percentile: number | null;
  explanation: string;
  skill: string | null;
  addressable: boolean;
  not_addressable_reason: string | null;
  roster_strength_percentile?: number;
}

export interface AcquisitionResponse {
  team: { id: string; abbreviation: string; name: string };
  available: boolean;
  unavailable_reason?: string;
  season?: string;
  diagnosis: AcquisitionDiagnosisRow[];
  target_need?: AcquisitionDiagnosisRow;
  sort?: string;
  sort_rule?: string;
  filter_rule?: string;
  untouchable_player_ids?: string[];
  search?: Record<string, number>;
  feasibility?: {
    applied: boolean;
    budget: number;
    trades_evaluated: number;
    rejected: Record<string, number>;
    truncated_by_budget: boolean;
    conditions: { both_sides_above: number; max_projected_win_loss: number; source: string };
  };
  targets: AcquisitionTarget[];
  notes?: string[];
}

/* ------------------------------------------------- evaluation detail sections
 *
 * The typed shapes behind `TeamEvaluation.detail`, moved here from the trade-evaluator
 * page in R7. They were declared as "local types" in a page module while describing an
 * API contract three modules read, and `components/evaluation-tabs.tsx` could not be
 * extracted without them.
 *
 * Every field is optional on purpose: a section the backend could not compute is absent
 * rather than null-filled, and each panel renders its own unavailable state from what it
 * finds missing. */

export interface RotationRow {
  player_id: string;
  name: string;
  minutes: number;
  tei: number;
  availability: number;
}

export interface PerformanceDetail {
  delta_wins?: number;
  delta_net_rating?: number;
  rotation_before?: RotationRow[];
  rotation_after?: RotationRow[];
}

export interface FitDetail {
  unavailable?: string;
  needs?: Record<string, number>;
  needs_addressed?: Record<string, number>;
  redundancies?: Record<string, number>;
  skill_delta?: Record<string, number>;
  /** Needs the model measures but declines to claim any player skill addresses (R4-2). */
  needs_not_addressable?: Record<string, string>;
}

export interface TimelineDetail {
  unavailable?: string;
  strategy?: string;
  incoming_alignment?: number;
  outgoing_alignment?: number;
}

export interface PickValuation {
  pick: string;
  direction: "in" | "out";
  low: number;
  point: number | null;
  high: number;
  /** interval = priced; range = protected/swapped; unknown = ownership unverified. */
  precision: "interval" | "range" | "unknown";
  caveats: string[];
  slot_support: { min_slot: number; max_slot: number; central_slot: number | null };
}

export interface AssetsDetail {
  picks_in?: number;
  picks_out?: number;
  roster_spots_delta?: number;
  picks_priced?: PickValuation[];
  picks_not_priced?: PickValuation[];
  pick_units_net?: number;
  pick_reference?: string;
  payroll_delta?: number;
  payroll_basis?: string;
  payroll_note?: string;
  /** Reported here, scored by the contract component — see `payroll_scored_note`. */
  payroll_scored?: boolean;
  payroll_scored_note?: string;
  precision_note?: string;
  unavailable?: string;
}

export interface RiskDetail {
  /** null when no arriving player has a known availability history. */
  incoming_availability?: number | null;
  incoming_availability_players?: number;
  /** null when no departing player has a known availability history. */
  outgoing_availability?: number | null;
  outgoing_availability_players?: number;
  /** The measured fallback for a side with no priced package: who actually plays those minutes. */
  roster_availability?: number | null;
  roster_availability_players?: number;
  availability_delta?: number;
  baseline_note?: string;
  method?: string;
  /** Reported, never scored — see `scored: false`. */
  legality_verification?: {
    rules_evaluated: number;
    rules_with_a_definite_verdict: number;
    share: number | null;
    scored: boolean;
    note: string;
  };
  unavailable?: string;
}


/* ------------------------------------------------------------- intelligence (Pivot)
 *
 * The read surface the decision workflow starts from: `/intelligence/players/{id}`,
 * `/intelligence/teams/{id}/profile` and `/intelligence/fit`.
 *
 * Every value that can be absent arrives inside a `Measurement`, which carries the reason
 * for the absence alongside the gap. That is the shape the whole family shares, and the
 * reason a client never has to invent copy for a missing number.
 */

/** The rung of the evidence ladder a number sits on. */
export type Evidence = "observed" | "derived" | "inferred";

/** How much weight a claim can bear. */
export type Confidence = "validated" | "measured" | "heuristic" | "unavailable";

/**
 * A value with its provenance, or an explicit absence with its reason.
 *
 * `value` and `reason` are mutually exclusive by construction and both keys are always
 * present, so branching on `available` is always sufficient.
 */
export interface Measurement {
  value: number | null;
  available: boolean;
  evidence: Evidence | null;
  confidence: Confidence;
  method: string;
  source: string;
  limitations: string[];
  reason: string;
}

export interface SkillEntry extends Measurement {
  key: string;
  label: string;
  side: "offense" | "defense" | "physical";
  definition: string;
  /** Alias of `value`, kept because the dimension is a percentile in 0..1. */
  percentile: number | null;
}

export interface ArchetypeMembership {
  key: string;
  label: string;
  family: "guard" | "wing" | "big" | "unclassified" | null;
  definition: string;
  weight: number;
  primary: boolean;
  evidence: Evidence;
  confidence: Confidence;
  method: string;
}

export interface ImpactEntry extends Measurement {
  sigma?: number | null;
  availability?: number | null;
  minutes?: number | null;
}

export interface PlayerIntelligence {
  player: {
    id: string;
    full_name: string;
    position: string | null;
    height_inches: number | null;
  };
  season: string;
  skills: SkillEntry[];
  /** Measured out of declared — the gap is deliberate and is shown, not hidden. */
  skills_measured: number;
  skills_declared: number;
  archetypes: ArchetypeMembership[];
  impact: ImpactEntry;
  coverage_note: string;
}

export interface ProfileNeedRow {
  key: string;
  label: string;
  severity: number;
  percentile: number | null;
  explanation: string;
  /** The player skill that addresses it, or null where Pivot claims none does. */
  addressed_by: string | null;
  unaddressable_reason: string;
}

export interface SkillCoverageEntry extends Measurement {
  key: string;
  label: string;
  side: "offense" | "defense" | "physical";
  rotation_players_measured: number;
}

export interface TeamProfile {
  team: { id: string; abbreviation: string; full_name: string };
  season: string;
  roster_size: number;
  skill_coverage: SkillCoverageEntry[];
  needs: ProfileNeedRow[];
  /** Classified on the server so every client agrees. Disjoint from `strengths`. */
  weaknesses: ProfileNeedRow[];
  strengths: ProfileNeedRow[];
  archetype_distribution: {
    key: string;
    label: string;
    family: string | null;
    count: number;
  }[];
  players_without_impact_estimate: { id: string; name: string }[];
  needs_available: boolean;
  needs_unavailable_reason: string;
  classification_note: string;
}

export interface PlayerTeamFit {
  player: { id: string; full_name: string };
  team: { id: string; abbreviation: string; full_name: string };
  season: string;
  already_on_roster: boolean;
  available: boolean;
  /** null whenever `available` is false — never 0 as a stand-in. */
  score: number | null;
  scale_note: string;
  detail: Record<string, unknown> & { unavailable?: string };
  conditional_note: string;
}

export interface VocabularyEntry {
  key: string;
  label: string;
  definition: string;
}

export interface Vocabulary {
  skills: (VocabularyEntry & {
    side: string;
    available: boolean;
    method: string;
    unavailable_reason: string;
    evidence: Evidence;
    confidence: Confidence;
    limitations: string[];
  })[];
  archetypes: (VocabularyEntry & { family: string; contributes: string[]; role_id: number })[];
  needs: (VocabularyEntry & {
    source: string;
    addressed_by: string | null;
    unaddressable_reason: string;
    proxy_note: string;
  })[];
  evidence_ladder: VocabularyEntry[];
  confidence_levels: VocabularyEntry[];
  thresholds: {
    need_severity: number;
    strength_percentile: number;
    headline_rows: number;
  };
}
