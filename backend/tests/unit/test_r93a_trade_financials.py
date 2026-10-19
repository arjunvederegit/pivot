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


class TestProvisionalRosterCountIsNotAVerifiedFailure:
    def _ceiling_trade(self, db: Session, provisional: bool):
        team_a, team_b = make_team(db, 1, "AAA"), make_team(db, 2, "BBB")
        ra = _roster(db, team_a, [5_000_000] * 18, 1000)
        rb = _roster(db, team_b, [5_000_000] * 14, 2000)
        moves = [{"player_id": rb[0].id, "from_team_id": team_b.id, "to_team_id": team_a.id}]
        context = build_trade_context(db, [team_a.id, team_b.id], moves)
        context.roster_provisional = provisional
        legality = TradeLegalityEngine().evaluate(context)
        row = next(r for r in legality["rule_results"] if r["rule_code"] == "ROSTER_SIZE" and r["team_id"] == team_a.id)
        return row, legality, ra

    def test_a_settled_roster_over_the_ceiling_still_fails(self, db: Session, cap_params) -> None:
        row, legality, _ = self._ceiling_trade(db, provisional=False)
        assert row["status"] == "fail"
        assert legality["overall_status"] == "verified_illegal"

    def test_a_provisional_roster_over_the_ceiling_is_a_low_confidence_warning(self, db: Session, cap_params) -> None:
        row, legality, _ = self._ceiling_trade(db, provisional=True)
        assert row["status"] == "warning" and row["confidence"] == "low"
        assert "provisional preseason snapshot" in row["message"]
        assert legality["overall_status"] != "verified_illegal"
