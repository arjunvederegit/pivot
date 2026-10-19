"""Player-level trade restrictions that depend on contract detail.

Each rule evaluates honestly against available data: with no contract provider these
return `unavailable` for affected players rather than pretending the restriction
doesn't exist."""

from datetime import date, timedelta

from ..context import RuleResult, TradeContext

CBA_REF = "2023 CBA Art. VII (trade restrictions)"


class RecentlySignedRule:
    code = "RECENTLY_SIGNED"
    description = "Recently signed free agents cannot be traded for 3 months / until Dec 15"

    def __init__(self, today: date | None = None):
        self.today = today or date.today()

    def evaluate(self, context: TradeContext) -> list[RuleResult]:
        results = []
        for team in context.teams:
            for player in team.outgoing:
                if player.signed_date is None:
                    if player.salary is None and not context.contract_provider_configured:
                        # Nothing at all is known about this player's contract, and
                        # SALARY_DATA_AVAILABLE already says so. Staying silent avoids
                        # two rules reporting one missing dataset.
                        continue
                    # We hold a contract for this player and it has no signing date — a
                    # different statement from "no contract data", and one the reader
                    # needs, because it is the field that keeps the verdict conditional.
                    results.append(
                        RuleResult(
                            rule_code=self.code,
                            status="unavailable",
                            team_id=team.team_id,
                            message=f"Signing date unknown for {player.name}; the recently-signed "
                            "restriction cannot be checked.",
                            source_reference=CBA_REF,
                        )
                    )
                    continue
                three_months = player.signed_date + timedelta(days=90)
                season_gate = date(player.signed_date.year, 12, 15)
                eligible_on = (
                    max(three_months, season_gate)
                    if player.signed_date.month >= 7
                    else three_months
                )
                restricted = self.today < eligible_on
                results.append(
                    RuleResult(
                        rule_code=self.code,
                        status="fail" if restricted else "pass",
                        team_id=team.team_id,
                        message=(
                            f"{player.name} signed {player.signed_date.isoformat()} and cannot be "
                            f"traded before {eligible_on.isoformat()}."
                            if restricted
                            else f"{player.name} is outside the recently-signed restriction window."
                        ),
                        calculation={
                            "signed_date": player.signed_date.isoformat(),
                            "eligible_on": eligible_on.isoformat(),
                        },
                        source_reference=CBA_REF,
                        confidence="medium",
                    )
                )
        return results


class NoTradeClauseRule:
    code = "NO_TRADE_CLAUSE"
    description = "Players with a no-trade clause must consent — surfaced as a warning"

    def evaluate(self, context: TradeContext) -> list[RuleResult]:
        results = []
        for team in context.teams:
            unknown = [p.name for p in team.outgoing if p.no_trade_clause is None]
            for player in team.outgoing:
                if player.no_trade_clause:
                    results.append(
                        RuleResult(
                            rule_code=self.code,
                            status="warning",
                            team_id=team.team_id,
                            message=f"{player.name} has a no-trade clause and must consent to this trade.",
                            source_reference=CBA_REF,
                        )
                    )
            if unknown:
                # Previously silent: a rule that emits nothing reads as "checked, nothing
                # found", and `rule_results` quietly shrank from 9 rules to 5. Absence of
                # a recorded clause is not absence of a clause.
                results.append(
                    RuleResult(
                        rule_code=self.code,
                        status="unavailable",
                        team_id=team.team_id,
                        message=(
                            "No-trade-clause status is unknown for "
                            f"{', '.join(unknown)}; the configured provider does not report it, "
                            "so consent requirements cannot be checked."
                        ),
                        calculation={"players_with_unknown_clause": unknown},
                        source_reference=CBA_REF,
                    )
                )
        return results


class TwoWayExclusionRule:
    code = "TWO_WAY_EXCLUSION"
    description = "Two-way contracts do not count toward salary matching"

    def evaluate(self, context: TradeContext) -> list[RuleResult]:
        results = []
        for team in context.teams:
            moved = team.outgoing + team.incoming
            unknown = [p.name for p in moved if p.matching_class is None]
            if unknown:
                # The exclusion cannot be applied to a contract whose type is unknown, and
                # the failure is permissive: an unrecognised two-way salary inflates the
                # outgoing sum and the maximum incoming it implies (C9). Say so.
                results.append(
                    RuleResult(
                        rule_code=self.code,
                        status="unavailable",
                        team_id=team.team_id,
                        message=(
                            f"Contract type is unknown for {', '.join(unknown)}, so two-way "
                            "contracts cannot be excluded from salary matching. Treating an "
                            "unknown type as standard would overstate outgoing salary and the "
                            "maximum incoming it permits."
                        ),
                        calculation={"players_with_unknown_type": unknown},
                        source_reference="2023 CBA Art. VII §8 (two-way)",
                    )
                )
                continue
            two_way = [p.name for p in moved if p.matching_class == "two-way"]
            if two_way:
                results.append(
                    RuleResult(
                        rule_code=self.code,
                        status="pass",
                        team_id=team.team_id,
                        message=f"Two-way contracts excluded from salary matching: {', '.join(two_way)}.",
                        calculation={"two_way_players": two_way},
                        source_reference="2023 CBA Art. VII §8 (two-way)",
                    )
                )
        return results
