"""Trade context and rule result types.

The honesty standard (docs/cba-rule-coverage.md):
- verified_legal      — every implemented required rule passed with current data
- verified_illegal    — at least one implemented rule failed
- conditionally_valid — implemented rules passed but required data was unavailable
- not_evaluated       — insufficient data to run any meaningful rule

A trade is NEVER labeled legal when only partial validation was possible."""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Protocol

RuleStatus = Literal["pass", "fail", "warning", "unavailable"]
LegalityStatus = Literal[
    "verified_legal", "verified_illegal", "conditionally_valid", "not_evaluated"
]


@dataclass
class PlayerAsset:
    player_id: str
    name: str
    from_team_id: str
    to_team_id: str
    salary: int | None = None  # None = contract data unavailable (never guessed)
    contract_type: str | None = None
    signed_date: date | None = None
    no_trade_clause: bool | None = None
    #: R9.3A. ``True`` when the contract is *deducibly* not a two-way contract: its cap hit exceeds the
    #: league year's two-way salary, which is a fixed league parameter. It stays ``None`` (unknown), never
    #: ``False``, when that cannot be shown. ``contract_type`` is still only ever what a source stated.
    not_two_way: bool | None = None
    not_two_way_basis: str | None = None
    #: Financial facts for display and for the trade's financial analysis (a ``PlayerFinancials``).
    financials: object | None = None

    @property
    def matching_class(self) -> str | None:
        """How the contract is treated by salary matching: ``standard``, ``two-way`` or unknown.

        A stated type wins. Otherwise only the sourced deduction above may stand in for a type, and
        only in the direction it proves (not two-way => counts); an unknown stays unknown, because
        counting a two-way salary as standard is the permissive error C9 documents.
        """
        if self.contract_type is not None:
            return "two-way" if self.contract_type == "two-way" else "standard"
        if self.not_two_way:
            return "standard"
        return None


@dataclass
class PickAsset:
    from_team_id: str
    to_team_id: str
    draft_year: int
    round_number: int
    protections: str | None
    is_hypothetical: bool


@dataclass(frozen=True)
class PayrollCoverage:
    """What a payroll figure was actually built from (R2c).

    `known` is the sum of the salaries we hold. It is a **lower bound** on the team's
    real payroll, never an estimate of it: an unknown salary is a non-negative amount we
    do not have, so the true payroll is `known + (something ≥ 0)`. Nothing is imputed,
    and no missing salary is replaced by a minimum, a median or a league average.

    The asymmetry this buys is the whole point. A lower bound can *refute* — a team whose
    known salaries alone clear the second apron is above the second apron whatever the
    missing rows say — but it can never *verify*, because the unknown remainder can
    always push a team over a line it currently sits under. Rules therefore read
    `payroll_before`/`payroll_after`, which stay `None` until coverage is complete;
    only disclosure reads `known`.
    """

    known: int
    players_known: int
    players_total: int

    @property
    def players_unknown(self) -> int:
        return self.players_total - self.players_known

    @property
    def complete(self) -> bool:
        return self.players_total > 0 and self.players_known == self.players_total

    @property
    def share(self) -> float | None:
        if self.players_total <= 0:
            return None
        return self.players_known / self.players_total

    @property
    def verified(self) -> int | None:
        """The payroll only when every player is priced — what the rules may use."""
        return self.known if self.complete else None

    def as_dict(self) -> dict:
        return {
            "known": self.known,
            "players_known": self.players_known,
            "players_total": self.players_total,
            "players_unknown": self.players_unknown,
            "share": round(self.share, 4) if self.share is not None else None,
            "complete": self.complete,
            "is_lower_bound": not self.complete,
        }

    def disclosure(self) -> str:
        """The sentence that must accompany the number wherever it is rendered."""
        if self.players_total <= 0:
            return "No players on this roster."
        if self.complete:
            return f"Computed from all {self.players_total} contracts on this roster."
        missing = self.players_unknown
        noun = "salary" if missing == 1 else "salaries"
        return (
            f"Computed from {self.players_known} of {self.players_total} contracts; "
            f"{missing} unknown. The true payroll is higher by {missing} {noun} that "
            "are not on file and are not estimated."
        )


@dataclass
class CapParams:
    league_year: str
    salary_cap: int
    luxury_tax: int
    first_apron: int
    second_apron: int
    minimum_team_salary: int
    source_name: str = ""
    # Expanded TPE anchors for the 2025-26 league year; the CBA scales these with the
    # cap, so other years derive by the cap ratio (documented in cba-rule-coverage.md).
    tpe_band1_max_2025: int = 8_846_000
    tpe_band2_add_2025: int = 9_096_000
    tpe_band2_max_2025: int = 35_384_000
    salary_cap_2025: int = 154_647_000
    allowance: int = 250_000

    @property
    def cap_ratio(self) -> float:
        return self.salary_cap / self.salary_cap_2025

    @property
    def tpe_band1_max(self) -> float:
        return self.tpe_band1_max_2025 * self.cap_ratio

    @property
    def tpe_band2_add(self) -> float:
        return self.tpe_band2_add_2025 * self.cap_ratio

    @property
    def tpe_band2_max(self) -> float:
        return self.tpe_band2_max_2025 * self.cap_ratio

    @property
    def scaled_allowance(self) -> float:
        """The $250K allowance, scaled with the cap like every other TPE dollar figure.

        C13: the three band formulas are continuous only when *all* their dollar terms
        scale together. Leaving the allowance fixed while the band edges scale by
        `cap_ratio` opens a gap of ±$16,673 at the 2026-27 edges and makes band 2
        non-monotonic — a trade could become illegal by sending out *more* salary.
        """
        return self.allowance * self.cap_ratio


@dataclass
class TeamContext:
    team_id: str
    abbreviation: str
    name: str
    roster_count_before: int
    outgoing: list[PlayerAsset] = field(default_factory=list)
    incoming: list[PlayerAsset] = field(default_factory=list)
    picks_out: list[PickAsset] = field(default_factory=list)
    picks_in: list[PickAsset] = field(default_factory=list)
    coverage_before: PayrollCoverage | None = None
    #: Whether *every* player on the pre-trade roster has a known contract type. The
    #: 15-standard limit is a whole-roster property, so the traded players' types alone
    #: never establish it.
    roster_contract_types_known: bool = False
    #: First-round picks this team verifiably holds, by draft year, **before** the trade.
    #: `None` when no reconciled ownership source is loaded, or when one is loaded and it
    #: leaves this team's picks unresolved — a swap or protection nobody can reduce to an
    #: owner. The Stepien rule needs the difference between "holds none" and "we cannot
    #: say", and a dict of zeros cannot express the second (R5-2).
    first_round_holdings: dict[int, int] | None = None
    #: The source sentences that could not be resolved, so the rule can name them rather
    #: than reporting an unexplained `unavailable`.
    pick_ownership_unresolved: list[dict] = field(default_factory=list)

    @property
    def payroll_before(self) -> int | None:
        """The verified payroll: `None` unless every rostered player is priced.

        Deliberately unchanged by R2c. Disclosed coverage makes the *number* useful, not
        the *verdict* — every rule that gates on this keeps gating on complete data.
        """
        return self.coverage_before.verified if self.coverage_before else None

    @property
    def payroll_players_known(self) -> int:
        return self.coverage_before.players_known if self.coverage_before else 0

    @property
    def payroll_players_total(self) -> int:
        return self.coverage_before.players_total if self.coverage_before else 0

    @property
    def payroll_known_before(self) -> int | None:
        """Lower-bound payroll from the contracts actually on file (R2c)."""
        if self.coverage_before is None or self.coverage_before.players_total <= 0:
            return None
        return self.coverage_before.known

    @property
    def coverage_after(self) -> PayrollCoverage | None:
        """Coverage after the trade.

        The traded players' salaries have to be known for the *delta* to be known, so
        this is `None` the moment any of them is missing — a lower bound built from an
        unknown subtraction is not a lower bound.
        """
        if self.coverage_before is None:
            return None
        out_total, in_total = self.outgoing_salary_total, self.incoming_salary_total
        if out_total is None or in_total is None:
            return None
        # Deliberately the *totals*, not the matching-eligible sums: coverage describes
        # what is on file, so it must not change because a contract type is unknown.
        return PayrollCoverage(
            known=self.coverage_before.known - out_total + in_total,
            players_known=self.coverage_before.players_known
            - len(self.outgoing)
            + len(self.incoming),
            players_total=self.roster_count_after,
        )

    @property
    def payroll_known_after(self) -> int | None:
        coverage = self.coverage_after
        return coverage.known if coverage and coverage.players_total > 0 else None

    @property
    def roster_count_after(self) -> int:
        return self.roster_count_before - len(self.outgoing) + len(self.incoming)

    @property
    def salaries_known(self) -> bool:
        return all(p.salary is not None for p in self.outgoing + self.incoming)

    @property
    def contract_types_known(self) -> bool:
        """Whether every traded player's contract *type* is actually known.

        Not the same question as whether their salary is known, and the difference is the
        R2b honesty defect (C9). A two-way contract does not count toward salary matching,
        so an unknown type makes both the outgoing sum and the maximum incoming it implies
        unknown — in the **permissive** direction: counting a two-way salary as standard
        inflates `outgoing_salary`, inflates `maximum_incoming`, and approves trades the
        engine should reject. `contract_type or "standard"` was exactly that substitution.
        """
        return all(p.matching_class is not None for p in self.outgoing + self.incoming)

    @property
    def outgoing_salary_total(self) -> int | None:
        """Every outgoing salary on file, whatever the contract type.

        The payroll-side sum: what leaves the books. Contract type does not enter it, so
        an unknown type does not withhold it.
        """
        if any(p.salary is None for p in self.outgoing):
            return None
        return sum(p.salary or 0 for p in self.outgoing)

    @property
    def incoming_salary_total(self) -> int | None:
        if any(p.salary is None for p in self.incoming):
            return None
        return sum(p.salary or 0 for p in self.incoming)

    @property
    def outgoing_salary(self) -> int | None:
        """The salary-**matching** sum: two-way contracts excluded, per Art. VII §8.

        `None` when any salary or any contract type is unknown. Withholding on an unknown
        type is the point — see `contract_types_known`.
        """
        if any(p.salary is None for p in self.outgoing) or not self.contract_types_known:
            return None
        return sum(p.salary or 0 for p in self.outgoing if p.matching_class == "standard")

    @property
    def incoming_salary(self) -> int | None:
        if any(p.salary is None for p in self.incoming) or not self.contract_types_known:
            return None
        return sum(p.salary or 0 for p in self.incoming if p.matching_class == "standard")

    @property
    def payroll_after(self) -> int | None:
        coverage = self.coverage_after
        return coverage.verified if coverage else None

    @property
    def aggregates_salaries(self) -> bool | None:
        """Sending 2+ standard contracts combined for matching purposes.

        `None` — not `False` — when contract types are unknown: two-way contracts are not
        aggregated, so whether this deal aggregates at all cannot be determined, and
        `False` would silently clear the second-apron prohibition.
        """
        if not self.contract_types_known:
            return None if len(self.outgoing) >= 2 else False
        return len([p for p in self.outgoing if p.matching_class == "standard"]) >= 2

    def apron_status(self, payroll: int | None, params: CapParams) -> str | None:
        if payroll is None:
            return None
        if payroll > params.second_apron:
            return "above_second_apron"
        if payroll > params.first_apron:
            return "above_first_apron"
        if payroll > params.luxury_tax:
            return "above_tax"
        return "below_tax"

    def apron_status_at_least(
        self, coverage: PayrollCoverage | None, params: CapParams
    ) -> str | None:
        """The highest threshold the *known* salaries alone already clear (R2c).

        Sound in one direction only. Missing salaries can only raise the true payroll, so
        a team whose priced contracts already exceed the second apron is above the second
        apron regardless of what is missing. The converse says nothing: `below_tax` here
        means "not yet proven above the tax", not "below the tax", which is why this is
        never used to pass a rule.
        """
        if coverage is None or coverage.players_total <= 0:
            return None
        if coverage.complete:
            return self.apron_status(coverage.known, params)
        if coverage.known > params.second_apron:
            return "above_second_apron"
        if coverage.known > params.first_apron:
            return "above_first_apron"
        if coverage.known > params.luxury_tax:
            return "above_tax"
        return None  # nothing is proven; do not imply "below_tax" from partial data


@dataclass
class TradeContext:
    league_year: str
    params: CapParams
    teams: list[TeamContext]
    contract_provider_configured: bool = False
    #: R9.3A. The roster snapshot is a provisional preseason / offseason one (camp invitees, Exhibit 10 and two-way
    #: players are not distinguishable from the final 15), so a roster-*count* conclusion drawn from it is not final.
    roster_provisional: bool = False

    def team(self, team_id: str) -> TeamContext:
        for t in self.teams:
            if t.team_id == team_id:
                return t
        raise KeyError(team_id)


@dataclass
class RuleResult:
    rule_code: str
    status: RuleStatus
    team_id: str | None
    message: str
    calculation: dict = field(default_factory=dict)
    source_reference: str = ""
    confidence: Literal["high", "medium", "low"] = "high"


class TradeRule(Protocol):
    code: str
    description: str

    def evaluate(self, context: TradeContext) -> list[RuleResult]: ...


def overall_status(results: list[RuleResult]) -> LegalityStatus:
    evaluated = [r for r in results if r.status in ("pass", "fail", "warning")]
    if any(r.status == "fail" for r in results):
        return "verified_illegal"
    if not evaluated:
        return "not_evaluated"
    if any(r.status == "unavailable" for r in results):
        return "conditionally_valid"
    return "verified_legal"
