"""Roster size limits around a trade.

NBA.com roster snapshots include two-way players (up to 18 rows: 15 standard + 3
two-way), and two-way status is only knowable with contract data. The rule therefore
uses precise standard-contract counts when contract types are available and honest
wider bounds (fail only above the 18-spot ceiling) when they are not, saying so."""

from ..context import RuleResult, TradeContext

MAX_STANDARD = 15
MIN_STANDARD = 14
MAX_WITH_TWO_WAYS = 18
HARD_MIN = 12


class RosterSizeRule:
    code = "ROSTER_SIZE"
    description = "Post-trade roster must fit within roster limits (15 standard + 3 two-way spots)"

    def evaluate(self, context: TradeContext) -> list[RuleResult]:
        results = []
        for team in context.teams:
            after = team.roster_count_after
            # The 15-standard limit counts the whole post-trade roster, so it needs the
            # contract type of every player on it — the ones staying as well as the ones
            # moving. Gating on the traded players alone (C9) let one curated row promote
            # a whole roster from (warning, medium) to (pass, high); gating additionally
            # on `contract_provider_configured` asked about the environment rather than
            # the data, and a provider that reports no types satisfied it for all 30 teams.
            types_known = team.roster_contract_types_known and team.contract_types_known

            if types_known:
                if after > MAX_STANDARD + 3:
                    status, message = (
                        "fail",
                        (
                            f"Roster would have {after} players — above the {MAX_STANDARD} standard "
                            "+ 3 two-way ceiling; a corresponding move would be required first."
                        ),
                    )
                elif after < HARD_MIN:
                    status, message = (
                        "fail",
                        (
                            f"Roster would drop to {after} players — below the league minimum of {HARD_MIN}."
                        ),
                    )
                elif after < MIN_STANDARD:
                    status, message = (
                        "warning",
                        (
                            f"Roster would have {after} players — below {MIN_STANDARD} is allowed only "
                            "for up to 14 consecutive days (28 total per season)."
                        ),
                    )
                else:
                    status, message = (
                        "pass",
                        f"Post-trade roster of {after} players is within limits.",
                    )
                confidence = "high"
            else:
                # Two-way status unknown: only the 18-spot ceiling is a certain violation.
                if after > MAX_WITH_TWO_WAYS:
                    status, message = (
                        "fail",
                        (
                            f"Roster would have {after} players — above the 18-spot ceiling "
                            "(15 standard + 3 two-way) regardless of contract types."
                        ),
                    )
                elif after < HARD_MIN:
                    status, message = (
                        "fail",
                        (
                            f"Roster would drop to {after} players — below the league minimum of {HARD_MIN}."
                        ),
                    )
                elif after > MAX_STANDARD:
                    status, message = (
                        "warning",
                        (
                            f"Roster would have {after} players; the snapshot mixes standard and "
                            "two-way contracts, so the 15-man standard limit cannot be verified "
                            "without contract data."
                        ),
                    )
                else:
                    status, message = (
                        "pass",
                        f"Post-trade roster of {after} players is within limits.",
                    )
                confidence = "medium"

            results.append(
                RuleResult(
                    rule_code=self.code,
                    status=status,  # type: ignore[arg-type]
                    team_id=team.team_id,
                    message=message,
                    calculation={
                        "roster_before": team.roster_count_before,
                        "roster_after": after,
                        "contract_types_known": types_known,
                    },
                    source_reference="2023 CBA Art. X (roster limits)",
                    confidence=confidence,  # type: ignore[arg-type]
                )
            )
        return results
