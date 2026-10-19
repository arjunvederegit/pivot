"""R9.3A — a trade's financial picture and its inspectable legality assessment.

Properties defended:

* a salary difference is stated only when every salary behind it is known (unknown is never $0);
* the legality verdict is `prohibited` only on a failing checked rule, `indeterminate` when anything is unverified,
  and `passes_checked_rules` (never "legal") only when everything implemented passed — with the unchecked CBA
  categories attached to every answer;
* partial payroll may *indicate* a matching failure but never decides one: the engine result is unchanged
  (`unavailable`), so no basketball evaluation is suppressed on the strength of a floor;
* a team that ends at or under the cap needs no salary matching (the false failure this release fixes);
* the deduced "not a two-way contract" lets matching run, in the one direction it proves.
"""

from sqlalchemy.orm import Session

from app.cba.builder import build_trade_context
from app.cba.engine import TradeLegalityEngine
from app.db.models import LeagueCapParameters
from app.services.trade_financials import UNCHECKED_CATEGORIES, trade_financial_analysis
from tests.conftest import make_player, make_team


def _roster(db: Session, team, salaries, offset: int, contract_type="standard"):
    return [
        make_player(db, offset + i, f"R{offset + i:03d}", team, salary=s, contract_type=contract_type)
        for i, s in enumerate(salaries)
    ]


def _trade(db: Session, a_roster, b_roster, a_out: int, b_out: int, *, contract_type="standard"):
    team_a, team_b = make_team(db, 1, "AAA"), make_team(db, 2, "BBB")
    ra = _roster(db, team_a, a_roster, 1000, contract_type)
    rb = _roster(db, team_b, b_roster, 2000, contract_type)
    moves = [
        {"player_id": ra[a_out].id, "from_team_id": team_a.id, "to_team_id": team_b.id},
        {"player_id": rb[b_out].id, "from_team_id": team_b.id, "to_team_id": team_a.id},
    ]
    context = build_trade_context(db, [team_a.id, team_b.id], moves)
    legality = TradeLegalityEngine().evaluate(context)
    return team_a, team_b, context, legality, trade_financial_analysis(context, legality)


# ---------------------------------------------------------------------- salary comparison


class TestSalaryComparison:
    def test_known_salaries_give_a_net_change_and_ratio(self, db: Session, cap_params: LeagueCapParameters) -> None:
        a, b, _, _, fa = _trade(db, [30_000_000] + [5_000_000] * 12, [20_000_000] + [5_000_000] * 12, 0, 0)
        block = fa["teams"][a.id]
        assert block["outgoing"]["cap_hit_total"] == 30_000_000
        assert block["incoming"]["cap_hit_total"] == 20_000_000
        assert block["net_salary_change"] == -10_000_000
        assert block["incoming_to_outgoing_ratio"] == round(20 / 30, 4)
        assert fa["teams"][b.id]["net_salary_change"] == 10_000_000

    def test_an_unknown_incoming_salary_withholds_the_difference_and_is_named(self, db: Session, cap_params) -> None:
        a, _, _, _, fa = _trade(db, [30_000_000] + [5_000_000] * 12, [None] + [5_000_000] * 12, 0, 0)
        block = fa["teams"][a.id]
        assert block["incoming"]["cap_hit_total"] is None
        assert block["incoming"]["known_cap_hit_total"] is None
        assert block["incoming"]["unknown_players"] == ["R2000"]
        assert block["net_salary_change"] is None and block["incoming_to_outgoing_ratio"] is None
        assert "not counted as $0" in block["net_salary_note"]

    def test_the_known_subtotal_is_labelled_a_floor_when_a_roster_player_is_unpriced(self, db: Session, cap_params) -> None:
        a, _, _, _, fa = _trade(db, [30_000_000, None] + [5_000_000] * 11, [20_000_000] + [5_000_000] * 12, 0, 0)
        block = fa["teams"][a.id]
        assert block["subtotal_label"] == "Known salary subtotal"
        assert block["coverage_before"]["complete"] is False

    def test_a_full_roster_is_a_payroll(self, db: Session, cap_params) -> None:
        a, _, _, _, fa = _trade(db, [30_000_000] + [5_000_000] * 12, [20_000_000] + [5_000_000] * 12, 0, 0)
        assert fa["teams"][a.id]["subtotal_label"] == "Payroll"

    def test_every_player_row_names_its_financial_season_and_source(self, db: Session, cap_params) -> None:
        _, _, _, _, fa = _trade(db, [30_000_000] + [5_000_000] * 12, [20_000_000] + [5_000_000] * 12, 0, 0)
        assert fa["financial_season"] == "2026-27"
        for row in fa["players"]:
            assert row["financials"]["financial_season"] == "2026-27"
            assert row["financials"]["source"]["name"] == "test fixture"
        assert fa["thresholds"]["season"] == "2026-27"
        assert "cap hit" in fa["definitions"].lower()

    def test_the_provider_not_on_file_list_is_always_present(self, db: Session, cap_params) -> None:
        _, _, _, _, fa = _trade(db, [30_000_000] + [5_000_000] * 12, [20_000_000] + [5_000_000] * 12, 0, 0)
        assert {"options", "guarantees", "no_trade_clause", "signing_date"} <= set(fa["not_on_file"])


# -------------------------------------------------------------------------- legality view


class TestLegalityAssessment:
    def test_unchecked_categories_are_attached_to_every_assessment(self, db: Session, cap_params) -> None:
        _, _, _, _, fa = _trade(db, [30_000_000] + [5_000_000] * 12, [30_000_000] + [5_000_000] * 12, 0, 0)
        la = fa["legality_assessment"]
        assert [c["category"] for c in la["unchecked_categories"]] == [c["category"] for c in UNCHECKED_CATEGORIES]
        assert "no result here is a statement that the trade is legal" in la["statement"]

    def test_a_clean_deal_with_unverifiable_rules_is_indeterminate_not_legal(self, db: Session, cap_params) -> None:
        _, _, _, legality, fa = _trade(db, [30_000_000] + [5_000_000] * 12, [30_000_000] + [5_000_000] * 12, 0, 0)
        la = fa["legality_assessment"]
        assert la["verdict"] == "indeterminate"  # recently-signed / no-trade-clause data are not on file
        assert la["counts"]["unverified"] > 0
        assert legality["overall_status"] != "verified_legal"
        assert {r["rule_code"] for r in la["unverified"]} >= {"RECENTLY_SIGNED", "NO_TRADE_CLAUSE"}

    def test_a_failing_checked_rule_is_prohibited_and_names_the_rule(self, db: Session, cap_params) -> None:
        # Both teams over the second apron with complete payrolls; AAA takes back far more than it sends.
        a, _, _, legality, fa = _trade(db, [40_000_000] * 7 + [3_000_000] * 6, [100_000_000] + [20_000_000] * 5 + [3_000_000] * 6, 0, 0)
        la = fa["legality_assessment"]
        assert legality["overall_status"] == "verified_illegal"
        assert la["verdict"] == "prohibited"
        assert any(f["rule_code"] == "SALARY_MATCHING" for f in la["failed"])
        assert "Salary matching" in la["headline"]

    def test_passes_checked_rules_is_the_most_the_assessment_ever_says(self) -> None:
        from app.services.trade_financials import legality_assessment

        all_pass = {
            "overall_status": "verified_legal",
            "rule_results": [
                {"rule_code": "SALARY_MATCHING", "status": "pass", "team_id": "t", "message": "ok", "calculation": {}},
            ],
        }
        la = legality_assessment(all_pass, {"t": "AAA"})
        assert la["verdict"] == "passes_checked_rules"
        assert "not a statement that the trade is legal" in la["headline"]
        assert la["unchecked_categories"]

    def test_no_evaluated_rule_is_indeterminate(self) -> None:
        from app.services.trade_financials import legality_assessment

        la = legality_assessment({"overall_status": "not_evaluated", "rule_results": []}, {})
        assert la["verdict"] == "indeterminate"

    def test_a_failure_beats_unverified_rules(self) -> None:
        from app.services.trade_financials import legality_assessment

        results = {
            "overall_status": "verified_illegal",
            "rule_results": [
                {"rule_code": "SALARY_MATCHING", "status": "fail", "team_id": "t", "message": "x", "calculation": {}},
                {"rule_code": "NO_TRADE_CLAUSE", "status": "unavailable", "team_id": "t", "message": "y", "calculation": {}},
            ],
        }
        la = legality_assessment(results, {"t": "AAA"})
        assert la["verdict"] == "prohibited" and la["counts"] == {"checked_passed": 0, "failed": 1, "unverified": 1}


# -------------------------------------------------------------- partial payroll indications


class TestPartialPayrollIndicationsNeverDecide:
    def test_a_floor_above_the_first_apron_is_an_indication_and_the_engine_stays_unverified(self, db: Session, cap_params) -> None:
        # AAA: 11 players at $20M known (220M floor), 1 unpriced. It sends $20M and takes back $60M.
        a, _, _, legality, fa = _trade(db, [20_000_000] * 11 + [None] * 2, [60_000_000] + [3_000_000] * 12, 0, 0)
        result = next(r for r in legality["rule_results"] if r["rule_code"] == "SALARY_MATCHING" and r["team_id"] == a.id)
        assert result["status"] == "unavailable"
        assert result["calculation"]["indicated_failure"]["kind"] == "floor_above_first_apron"
        assert result["confidence"] == "medium"
        assert legality["overall_status"] == "conditionally_valid"  # no evaluation is suppressed on a floor
        la = fa["legality_assessment"]
        assert la["verdict"] == "indeterminate"
        assert la["indications"] and la["indications"][0]["team"] == "AAA"
        assert "indicate" in la["headline"]

    def test_incoming_within_the_strictest_limit_is_reported_but_never_a_pass(self, db: Session, cap_params) -> None:
        a, _, _, legality, _ = _trade(db, [20_000_000] * 3 + [None] * 2, [20_000_000] + [3_000_000] * 12, 0, 0)
        result = next(r for r in legality["rule_results"] if r["rule_code"] == "SALARY_MATCHING" and r["team_id"] == a.id)
        assert result["status"] == "unavailable"
        assert result["calculation"]["would_pass_under_every_regime"] is True
        assert "every matching regime" in result["message"]

    def test_a_floor_below_every_line_decides_nothing(self, db: Session, cap_params) -> None:
        a, _, _, legality, _ = _trade(db, [10_000_000] * 3 + [None] * 2, [60_000_000] + [3_000_000] * 12, 0, 0)
        result = next(r for r in legality["rule_results"] if r["rule_code"] == "SALARY_MATCHING" and r["team_id"] == a.id)
        assert result["status"] == "unavailable"
        assert result["calculation"]["indicated_failure"] is None


# ------------------------------------------------------------------- the cap-room fix


class TestUnderTheCapNeedsNoMatching:
    def test_a_team_ending_under_the_cap_passes_even_taking_back_more_than_twice_what_it_sends(self, db: Session, cap_params) -> None:
        # AAA payroll 3*? -> well under the cap; sends $5M, takes $30M: post-trade payroll still under $164.9M.
        a, _, _, legality, _ = _trade(db, [5_000_000] * 6 + [3_000_000] * 5, [30_000_000] + [5_000_000] * 12, 0, 0)
        result = next(r for r in legality["rule_results"] if r["rule_code"] == "SALARY_MATCHING" and r["team_id"] == a.id)
        assert result["status"] == "pass"
        assert "no salary matching is required" in result["message"]

    def test_the_same_deal_over_the_cap_still_fails_matching(self, db: Session, cap_params) -> None:
        a, _, _, legality, _ = _trade(db, [20_000_000] * 8 + [3_000_000] * 4, [60_000_000] + [5_000_000] * 12, 0, 0)
        result = next(r for r in legality["rule_results"] if r["rule_code"] == "SALARY_MATCHING" and r["team_id"] == a.id)
        assert result["status"] == "fail"


# ------------------------------------------------------------------ the two-way deduction


class TestDeducedMatchingClass:
    def test_a_deduced_type_lets_the_type_rules_run(self, db: Session, cap_params) -> None:
        cap_params.extras = {"two_way_salary": 678_882}
        db.commit()
        _, _, _, legality, _ = _trade(db, [30_000_000] + [5_000_000] * 12, [30_000_000] + [5_000_000] * 12, 0, 0, contract_type=None)
        data_rules = [r for r in legality["rule_results"] if r["rule_code"] == "SALARY_DATA_AVAILABLE"]
        assert {r["status"] for r in data_rules} == {"pass"}
        assert all(r["confidence"] == "medium" for r in data_rules)
        assert all("a deduction, not a source field" in r["message"] for r in data_rules)

    def test_without_the_two_way_salary_the_type_stays_unknown(self, db: Session, cap_params) -> None:
        _, _, _, legality, _ = _trade(db, [30_000_000] + [5_000_000] * 12, [30_000_000] + [5_000_000] * 12, 0, 0, contract_type=None)
        data_rules = [r for r in legality["rule_results"] if r["rule_code"] == "SALARY_DATA_AVAILABLE"]
        assert {r["status"] for r in data_rules} == {"unavailable"}


# ------------------------------------------------------------ provisional roster counts


class TestProvisionalRosterCountIsQualifiedNotChanged:
    """The roster-count rule gates the basketball evaluation, so this release does not change its outcome; the
    assessment says when the failing count was measured on a provisional snapshot and lowers its confidence."""

    def _ceiling_trade(self, db: Session, provisional: bool):
        team_a, team_b = make_team(db, 1, "AAA"), make_team(db, 2, "BBB")
        _roster(db, team_a, [5_000_000] * 18, 1000)
        rb = _roster(db, team_b, [5_000_000] * 14, 2000)
        moves = [{"player_id": rb[0].id, "from_team_id": team_b.id, "to_team_id": team_a.id}]
        context = build_trade_context(db, [team_a.id, team_b.id], moves)
        context.roster_provisional = provisional
        legality = TradeLegalityEngine().evaluate(context)
        return legality, trade_financial_analysis(context, legality)["legality_assessment"]

    def test_the_engine_outcome_is_the_same_whether_or_not_the_roster_is_provisional(self, db: Session, cap_params) -> None:
        a, _ = self._ceiling_trade(db, provisional=False)
        assert a["overall_status"] == "verified_illegal"

    def test_a_settled_roster_failure_keeps_its_confidence(self, db: Session, cap_params) -> None:
        _, la = self._ceiling_trade(db, provisional=False)
        row = next(f for f in la["failed"] if f["rule_code"] == "ROSTER_SIZE")
        assert row["confidence"] != "low" and "provisional" not in row["message"]

    def test_a_provisional_roster_failure_says_so_and_lowers_its_confidence(self, db: Session, cap_params) -> None:
        legality, la = self._ceiling_trade(db, provisional=True)
        assert legality["overall_status"] == "verified_illegal"  # unchanged: the evaluation gate is not touched
        row = next(f for f in la["failed"] if f["rule_code"] == "ROSTER_SIZE")
        assert row["confidence"] == "low" and "provisional preseason roster snapshot" in row["message"]
        assert la["verdict"] == "prohibited" and "Confidence is reduced" in la["headline"]


# --------------------------------------------- per-season commitments: ended is known zero, unknown is unknown


class TestCommitmentsBySeason:
    def _two_player_deal(self, db: Session, *, give_horizon: bool):
        from datetime import UTC, datetime

        from app.canonical.records import SINGLE_SEASON_CONTRACT_SOURCE
        from app.db.models import Contract, ContractYear, DataSyncRun

        team_a, team_b = make_team(db, 1, "AAA"), make_team(db, 2, "BBB")
        ra = _roster(db, team_a, [20_000_000] * 13, 1000, contract_type=None)
        rb = _roster(db, team_b, [10_000_000] * 13, 2000, contract_type=None)
        # AAA's outgoing player: one-year deal (2026-27 only); BBB's: three years.
        c_out = db.query(Contract).filter_by(player_id=ra[0].id).one()
        c_in = db.query(Contract).filter_by(player_id=rb[0].id).one()
        for c in (c_out, c_in):
            c.source_name = SINGLE_SEASON_CONTRACT_SOURCE
        for season in ("2027-28", "2028-29"):
            db.add(ContractYear(contract_id=c_in.id, season=season, salary=10_000_000, salary_basis="cap_hit"))
        db.commit()
        if give_horizon:
            for season in ("2026-27", "2027-28", "2028-29"):
                db.add(DataSyncRun(job_name="sync_contracts", status="succeeded", finished_at=datetime.now(UTC),
                                   detail={"season": season, "outcome": "complete", "teams_failed": []}))
            db.commit()
        moves = [
            {"player_id": ra[0].id, "from_team_id": team_a.id, "to_team_id": team_b.id},
            {"player_id": rb[0].id, "from_team_id": team_b.id, "to_team_id": team_a.id},
        ]
        context = build_trade_context(db, [team_a.id, team_b.id], moves)
        analysis = trade_financial_analysis(context, TradeLegalityEngine().evaluate(context))
        return team_a, analysis

    def test_a_contract_that_has_ended_contributes_a_known_zero_so_the_net_is_stated(self, db: Session, cap_params) -> None:
        a, analysis = self._two_player_deal(db, give_horizon=True)
        seasons = {c["season"]: c for c in analysis["teams"][a.id]["commitments_by_season"]}
        later = seasons["2027-28"]
        assert later["outgoing"] == {"known_total": 0, "unknown_players": [], "complete": True}
        assert later["incoming"]["known_total"] == 10_000_000 and later["net_known"] == 10_000_000
        assert seasons["2026-27"]["net_known"] == 10_000_000 - 20_000_000

    def test_without_a_horizon_later_seasons_are_not_stated_never_zero(self, db: Session, cap_params) -> None:
        a, analysis = self._two_player_deal(db, give_horizon=False)
        later = {c["season"]: c for c in analysis["teams"][a.id]["commitments_by_season"]}["2027-28"]
        assert later["outgoing"]["complete"] is False and later["outgoing"]["unknown_players"]
        assert later["net_known"] is None


# ------------------------------------------ deduced types never gate the basketball evaluation (round 2, F1)


class TestADeducedTypeNeverCreatesAVerifiedFailure:
    """A failure that rests on a contract type deduced from the cap hit is an indication, not a verified failure:
    the engine's verified-illegal gate (which suppresses the basketball evaluation) must stay exactly where the
    previous engine had it, for every deal the previous engine could not classify."""

    def _second_apron_aggregation(self, db: Session, contract_type, two_way: bool):
        # AAA is far above the second apron on known salaries and sends two salaries in one deal.
        team_a, team_b = make_team(db, 1, "AAA"), make_team(db, 2, "BBB")
        ra = _roster(db, team_a, [40_000_000] * 7 + [3_000_000] * 6, 1000, contract_type=contract_type)
        rb = _roster(db, team_b, [60_000_000] + [5_000_000] * 12, 2000, contract_type=contract_type)
        moves = [
            {"player_id": ra[0].id, "from_team_id": team_a.id, "to_team_id": team_b.id},
            {"player_id": ra[1].id, "from_team_id": team_a.id, "to_team_id": team_b.id},
            {"player_id": rb[0].id, "from_team_id": team_b.id, "to_team_id": team_a.id},
        ]
        context = build_trade_context(db, [team_a.id, team_b.id], moves)
        legality = TradeLegalityEngine().evaluate(context)
        row = next(r for r in legality["rule_results"] if r["rule_code"] == "SECOND_APRON_AGGREGATION" and r["team_id"] == team_a.id)
        return legality, row, trade_financial_analysis(context, legality)

    def test_stated_types_still_fail_exactly_as_before(self, db: Session, cap_params) -> None:
        legality, row, _ = self._second_apron_aggregation(db, "standard", two_way=False)
        assert row["status"] == "fail" and legality["overall_status"] == "verified_illegal"

    def test_deduced_types_are_an_indication_and_do_not_suppress_the_evaluation(self, db: Session, cap_params) -> None:
        cap_params.extras = {"two_way_salary": 678_882}
        db.commit()
        legality, row, analysis = self._second_apron_aggregation(db, None, two_way=False)
        assert row["status"] == "unavailable" and row["confidence"] == "medium"
        assert row["calculation"]["indicated_failure"]["kind"] == "second_apron_aggregation"
        assert row["calculation"]["indicated_failure"]["rests_on_deduced_type"]
        assert legality["overall_status"] != "verified_illegal"
        la = analysis["legality_assessment"]
        assert la["verdict"] == "indeterminate" and any(i["rule_code"] == "SECOND_APRON_AGGREGATION" for i in la["indications"])
