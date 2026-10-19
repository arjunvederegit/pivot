"""Salary matching and apron rules (2023 CBA, in full effect from 2024-25).

Sources: 2023 NBA-NBPA CBA Article VII; thresholds cross-checked against
cbaguide.com/transactions/trades/tpe and Hoops Rumors' traded-player-exception
glossary for the 2025-26 league year. Expanded-TPE dollar anchors scale with the
salary cap per the CBA; other league years derive by the cap ratio.

Every salary rule returns `unavailable` — never a guess — when contract data is
missing for any involved player."""

from ..context import CapParams, RuleResult, TeamContext, TradeContext

CBA_REF = "2023 CBA Art. VII §6; cbaguide.com/transactions/trades/tpe"


def max_incoming_below_first_apron(outgoing: float, params: CapParams) -> float:
    """Expanded TPE bands. The CBA's dollar boundaries make the three formulas exactly
    continuous at the band edges (2x + allowance meets x + band2_add at band1_max, which
    meets 125% + allowance at band2_max).

    C13: continuity holds only when the allowance scales with the cap alongside the band
    edges. With a fixed $250K against scaled edges the 2026-27 boundaries jump by
    ±$16,673 and band 2 stops being monotone — sending out *more* salary could lower the
    maximum you may take back.
    """
    if outgoing <= params.tpe_band1_max:
        return 2.0 * outgoing + params.scaled_allowance
    if outgoing <= params.tpe_band2_max:
        return outgoing + params.tpe_band2_add
    return 1.25 * outgoing + params.scaled_allowance


def max_incoming_at_or_above_first_apron(outgoing: float, params: CapParams) -> float:
    """Standard TPE only: 100% of outgoing + allowance (scaled with the cap, per C13)."""
    return outgoing + params.scaled_allowance


class SalaryDataAvailabilityRule:
    code = "SALARY_DATA_AVAILABLE"
    description = "Contract data must be present for all traded players to verify salary rules"

    def evaluate(self, context: TradeContext) -> list[RuleResult]:
        results = []
        for team in context.teams:
            moved = team.outgoing + team.incoming
            if not moved:
                # A picks-only side moves no contracts, so "contract data present for all
                # traded players" is vacuously true — and it was being reported as a
                # `pass`, which reads as "salary data verified" on a deal that has no
                # salary data at all. Say nothing rather than something false.
                results.append(
                    RuleResult(
                        rule_code=self.code,
                        status="unavailable",
                        team_id=team.team_id,
                        message=(
                            "No player contracts move for this team, so there is no salary "
                            "data to verify. Picks-only sides are not salary-matched."
                        ),
                        calculation={"players_moved": 0},
                        source_reference=CBA_REF,
                    )
                )
                continue
            missing = [p.name for p in moved if p.salary is None]
            unknown_types = [p.name for p in moved if p.matching_class is None]
            if missing:
                provider_note = (
                    "no contract provider is configured"
                    if not context.contract_provider_configured
                    else "the configured provider lacks these players"
                )
                results.append(
                    RuleResult(
                        rule_code=self.code,
                        status="unavailable",
                        team_id=team.team_id,
                        message=(
                            f"Contract data unavailable for {', '.join(missing)} ({provider_note}). "
                            "Salary matching cannot be verified."
                        ),
                        calculation={"missing_players": missing},
                        source_reference=CBA_REF,
                        confidence="high",
                    )
                )
            elif unknown_types:
                # Salaries are on file but contract types are not. Two-way contracts are
                # excluded from matching, so the salary data present is not sufficient to
                # verify matching — reporting `pass` here would overstate what is held.
                results.append(
                    RuleResult(
                        rule_code=self.code,
                        status="unavailable",
                        team_id=team.team_id,
                        message=(
                            f"Salaries are on file for all {len(moved)} traded players, but the "
                            f"contract type is unknown for {', '.join(unknown_types)}. Two-way "
                            "contracts do not count toward salary matching, so matching cannot "
                            "be verified from salary alone."
                        ),
                        calculation={
                            "salaries_present": True,
                            "contract_types_unknown": unknown_types,
                        },
                        source_reference=CBA_REF,
                        confidence="high",
                    )
                )
            else:
                inferred = [p for p in moved if p.contract_type is None]
                results.append(
                    RuleResult(
                        rule_code=self.code,
                        status="pass",
                        team_id=team.team_id,
                        message=(
                            "Contract salary and type present for all traded players."
                            if not inferred
                            else "Contract salary present for all traded players; for "
                            + ", ".join(p.name for p in inferred)
                            + " the type is not stated but the cap hit exceeds the two-way salary, "
                            "so the contract is not a two-way deal (a deduction, not a source field)."
                        ),
                        calculation=(
                            {"contract_type_deduced_not_two_way": [p.name for p in inferred]}
                            if inferred
                            else {}
                        ),
                        source_reference=CBA_REF,
                        confidence="high" if not inferred else "medium",
                    )
                )
        return results


class SalaryMatchingRule:
    code = "SALARY_MATCHING"
    description = "Incoming salary must fit the traded player exception for the team's apron status"

    def _team_result(self, team: TeamContext, context: TradeContext) -> RuleResult:
        params = context.params
        outgoing = team.outgoing_salary
        incoming = team.incoming_salary
        coverage = team.coverage_before
        if outgoing is None or incoming is None:
            detail = f" {coverage.disclosure()}" if coverage and not coverage.complete else ""
            return RuleResult(
                rule_code=self.code,
                status="unavailable",
                team_id=team.team_id,
                message=(
                    "Salary matching not evaluated: a traded salary or contract type is unknown "
                    f"for this team.{detail}"
                ),
                calculation={
                    "payroll_known": team.payroll_known_before,
                    "payroll_coverage": coverage.as_dict() if coverage else None,
                },
                source_reference=CBA_REF,
            )
        if incoming == 0:
            return RuleResult(
                rule_code=self.code,
                status="pass",
                team_id=team.team_id,
                message="No incoming salary; matching not required.",
                calculation={"outgoing_salary": outgoing, "incoming_salary": 0},
                source_reference=CBA_REF,
            )
        if team.payroll_before is None:
            return self._partial_payroll_result(team, context, outgoing, incoming)
        if outgoing == 0 and incoming > 0:
            # Taking in salary with none outgoing requires cap room, which needs full
            # league-wide contract data to verify precisely; we check against the cap.
            payroll_after = team.payroll_before + incoming
            fits_cap = payroll_after <= params.salary_cap
            return RuleResult(
                rule_code=self.code,
                status="pass" if fits_cap else "fail",
                team_id=team.team_id,
                message=(
                    "Absorbing salary without sending any out "
                    + ("fits under the salary cap." if fits_cap else "exceeds cap room.")
                ),
                calculation={
                    "incoming_salary": incoming,
                    "payroll_after": payroll_after,
                    "salary_cap": params.salary_cap,
                },
                source_reference=CBA_REF,
                confidence="medium",
            )

        payroll_after = team.payroll_after or 0
        if payroll_after <= params.salary_cap:
            # R9.3A: a team whose payroll after the trade is at or under the cap needs no matching
            # at all (it is absorbing into cap room). Applying a matching band here would report a
            # false failure for an under-the-cap team taking back more than 200% of what it sends.
            return RuleResult(
                rule_code=self.code,
                status="pass",
                team_id=team.team_id,
                message=(
                    f"Post-trade payroll ${payroll_after:,.0f} is at or under the salary cap "
                    f"${params.salary_cap:,.0f}, so no salary matching is required."
                ),
                calculation={
                    "outgoing_salary": outgoing,
                    "incoming_salary": incoming,
                    "post_trade_payroll": payroll_after,
                    "salary_cap": params.salary_cap,
                    "band": "under the cap after the trade",
                },
                source_reference=CBA_REF,
            )
        status_after = team.apron_status(payroll_after, params)
        if status_after in ("above_first_apron", "above_second_apron"):
            maximum = max_incoming_at_or_above_first_apron(outgoing, params)
            band = "standard TPE (at/above first apron): 100% of outgoing + $250K"
        else:
            maximum = max_incoming_below_first_apron(outgoing, params)
            band = "expanded TPE (below first apron)"
        ok = incoming <= maximum
        return RuleResult(
            rule_code=self.code,
            status="pass" if ok else "fail",
            team_id=team.team_id,
            message=(
                f"Incoming ${incoming:,.0f} vs maximum ${maximum:,.0f} ({band})."
                + ("" if ok else " Trade fails salary matching.")
            ),
            calculation={
                "outgoing_salary": outgoing,
                "incoming_salary": incoming,
                "maximum_incoming": round(maximum),
                "post_trade_payroll": payroll_after,
                "post_trade_apron_status": status_after,
                "band": band,
            },
            source_reference=CBA_REF,
        )

    def _partial_payroll_result(
        self, team: TeamContext, context: TradeContext, outgoing: int, incoming: int
    ) -> RuleResult:
        """Matching when the payroll is a floor: it may *indicate* a failure, it never passes (R2c policy).

        Missing salaries can only raise the true payroll, so ``known_after`` is a lower bound on the
        post-trade payroll. That indicates a failure in exactly two cases:

        * the floor already exceeds the first apron -> the standard TPE binds (100% of outgoing +
          allowance), and incoming is above it;
        * incoming exceeds even the most generous limit (expanded TPE) and the floor already exceeds
          the cap -> cap room is excluded, so the deal fails under every regime.

        The result stays ``unavailable`` either way — the indication is carried in
        ``calculation['indicated_failure']`` and surfaced by the trade's legality assessment — because the
        floor is built from a roster snapshot, and a verdict that gates the basketball evaluation must not
        rest on one. A deal whose
        incoming salary is within the strictest limit, which every regime would accept: that is
        reported in ``calculation['would_pass_under_every_regime']`` and in the message, but not as a
        pass, because "partial data refutes but never passes" is a deliberate standard
        (tests/unit/test_disclosed_coverage_payroll.py). Confidence is ``medium``: the floor is built
        from the current roster snapshot, which in the preseason still holds camp contracts that may
        be waived.
        """
        params = context.params
        coverage = team.coverage_before
        floor_after = team.payroll_known_after
        strictest = max_incoming_at_or_above_first_apron(outgoing, params)
        generous = max_incoming_below_first_apron(outgoing, params) if outgoing > 0 else 0.0
        calc: dict = {
            "outgoing_salary": outgoing,
            "incoming_salary": incoming,
            "strictest_maximum_incoming": round(strictest),
            "most_generous_maximum_incoming": round(generous),
            "payroll_known_after": floor_after,
            "payroll_coverage": coverage.as_dict() if coverage else None,
        }
        disclosure = f" {coverage.disclosure()}" if coverage and not coverage.complete else ""
        within_strictest = outgoing > 0 and incoming <= strictest
        indication: dict | None = None
        if floor_after is not None:
            if outgoing > 0 and floor_after > params.first_apron and incoming > strictest:
                indication = {
                    "kind": "floor_above_first_apron",
                    "limit": round(strictest),
                    "explanation": (
                        f"Incoming ${incoming:,.0f} exceeds ${strictest:,.0f} (standard TPE: 100% of outgoing + "
                        f"allowance), and the known salaries alone leave the team above the first apron "
                        f"(${floor_after:,.0f} > ${params.first_apron:,.0f}), so that limit would bind "
                        "whatever the missing salaries are."
                    ),
                }
            elif incoming > generous and floor_after > params.salary_cap:
                indication = {
                    "kind": "above_most_generous_limit_and_floor_over_cap",
                    "limit": round(generous),
                    "explanation": (
                        f"Incoming ${incoming:,.0f} exceeds even the most generous matching limit "
                        f"${generous:,.0f}, and the known salaries alone leave the team over the cap "
                        f"(${floor_after:,.0f} > ${params.salary_cap:,.0f}), so cap room cannot absorb it."
                    ),
                }
        if indication:
            message = (
                f"Salary matching not verified: payroll is incomplete. {indication['explanation']}{disclosure} "
                "This is an indication from a floor, reported as unverified: it can be wrong only if the roster "
                "snapshot is (the preseason roster still holds camp contracts)."
            )
        elif within_strictest:
            message = (
                f"Salary matching not verified: payroll is incomplete. Incoming ${incoming:,.0f} is within the "
                f"strictest limit ${strictest:,.0f} (100% of outgoing + allowance), which every matching regime "
                f"would accept — but a verdict needs the full payroll.{disclosure}"
            )
        else:
            message = (
                "Salary matching not evaluated: the verdict depends on this team's full payroll "
                f"(incoming ${incoming:,.0f} is above the strictest limit ${strictest:,.0f}).{disclosure}"
            )
        return RuleResult(
            rule_code=self.code,
            status="unavailable",
            team_id=team.team_id,
            message=message,
            calculation={
                "payroll_known": team.payroll_known_before,
                "payroll_coverage": coverage.as_dict() if coverage else None,
                "would_pass_under_every_regime": within_strictest,
                "indicated_failure": indication,
                **calc,
            },
            source_reference=CBA_REF,
            confidence="medium" if indication else "high",
        )

    def evaluate(self, context: TradeContext) -> list[RuleResult]:
        return [self._team_result(team, context) for team in context.teams]


class SecondApronAggregationRule:
    code = "SECOND_APRON_AGGREGATION"
    description = "Teams above the second apron may not aggregate multiple salaries in one trade"

    def evaluate(self, context: TradeContext) -> list[RuleResult]:
        results = []
        for team in context.teams:
            aggregates = team.aggregates_salaries
            if aggregates is None:
                # 2+ outgoing players with at least one unknown contract type. Two-way
                # deals are not aggregated, so whether this is an aggregation at all is
                # unknown — and "no aggregation" is the permissive answer (C9).
                results.append(
                    RuleResult(
                        rule_code=self.code,
                        status="unavailable",
                        team_id=team.team_id,
                        message=(
                            f"{len(team.outgoing)} players are outgoing, but at least one "
                            "contract type is unknown. Two-way contracts are not aggregated, "
                            "so whether this deal aggregates salaries cannot be determined."
                        ),
                        calculation={
                            "outgoing_players": len(team.outgoing),
                            "contract_types_known": False,
                        },
                        source_reference=CBA_REF,
                    )
                )
                continue
            if not aggregates:
                results.append(
                    RuleResult(
                        rule_code=self.code,
                        status="pass",
                        team_id=team.team_id,
                        message="No salary aggregation in this deal.",
                        source_reference=CBA_REF,
                    )
                )
                continue
            payroll_after = team.payroll_after
            if payroll_after is None:
                # R2c: partial data can still *refute*. The salaries actually on file are
                # a lower bound on the post-trade payroll, so a team already past the
                # second apron on known contracts alone is past it whatever is missing —
                # the missing rows can only add. The converse is not sound and is not
                # claimed: failing to clear the line leaves the rule `unavailable`.
                coverage_after = team.coverage_after
                proven_above = (
                    coverage_after is not None
                    and coverage_after.known > context.params.second_apron
                )
                if proven_above and coverage_after is not None:
                    results.append(
                        RuleResult(
                            rule_code=self.code,
                            status="fail",
                            team_id=team.team_id,
                            message=(
                                "Second-apron team aggregates multiple salaries — prohibited. "
                                f"The {coverage_after.players_known} contracts on file already "
                                f"total ${coverage_after.known:,.0f} after this trade, above the "
                                f"${context.params.second_apron:,.0f} second apron; the "
                                f"{coverage_after.players_unknown} unpriced players can only "
                                "raise it."
                            ),
                            calculation={
                                "post_trade_payroll_known": coverage_after.known,
                                "post_trade_payroll_is_lower_bound": True,
                                "second_apron": context.params.second_apron,
                                "payroll_coverage_after": coverage_after.as_dict(),
                                "outgoing_players": len(team.outgoing),
                            },
                            source_reference=CBA_REF,
                        )
                    )
                    continue
                coverage = team.coverage_before
                detail = f" {coverage.disclosure()}" if coverage and not coverage.complete else ""
                results.append(
                    RuleResult(
                        rule_code=self.code,
                        status="unavailable",
                        team_id=team.team_id,
                        message=(
                            "Team aggregates multiple salaries, but apron status cannot be "
                            f"determined without complete contract data.{detail}"
                        ),
                        calculation={
                            "payroll_known": team.payroll_known_before,
                            "payroll_coverage": coverage.as_dict() if coverage else None,
                        },
                        source_reference=CBA_REF,
                    )
                )
                continue
            above_second = payroll_after > context.params.second_apron
            results.append(
                RuleResult(
                    rule_code=self.code,
                    status="fail" if above_second else "pass",
                    team_id=team.team_id,
                    message=(
                        "Second-apron team aggregates multiple salaries — prohibited."
                        if above_second
                        else "Aggregation permitted below the second apron."
                    ),
                    calculation={
                        "post_trade_payroll": payroll_after,
                        "second_apron": context.params.second_apron,
                        "outgoing_players": len(team.outgoing),
                    },
                    source_reference=CBA_REF,
                )
            )
        return results


class MinimumTeamSalaryRule:
    code = "MINIMUM_TEAM_SALARY"
    description = (
        "Post-trade payroll below 90% of the cap triggers a warning (not illegal mid-season)"
    )

    def evaluate(self, context: TradeContext) -> list[RuleResult]:
        results = []
        for team in context.teams:
            payroll_after = team.payroll_after
            if payroll_after is None:
                continue  # covered by SALARY_DATA_AVAILABLE
            below = payroll_after < context.params.minimum_team_salary
            results.append(
                RuleResult(
                    rule_code=self.code,
                    status="warning" if below else "pass",
                    team_id=team.team_id,
                    message=(
                        f"Post-trade payroll ${payroll_after:,.0f} is below the minimum team "
                        f"salary ${context.params.minimum_team_salary:,.0f} — shortfall is owed "
                        "to players at season end."
                        if below
                        else "Post-trade payroll meets the minimum team salary."
                    ),
                    calculation={
                        "post_trade_payroll": payroll_after,
                        "minimum_team_salary": context.params.minimum_team_salary,
                    },
                    source_reference="2023 CBA Art. VII §2(b)",
                )
            )
        return results
