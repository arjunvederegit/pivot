"""R9.3A — the shared financial service: one read path for contracts, honest about what it knows.

What these tests defend (each is a way the tools could have shown a wrong financial fact):

* a contract's length is *derived* only from seasons loaded for the whole league, and a contract whose last row
  is the last season loaded is "at least", never "ends";
* a player with no priced year is unpriced / no_contract — never $0, and his base salary or cash is never promoted
  to a cap hit;
* a team's "known salary subtotal" is only called payroll when every rostered player is priced;
* "not a two-way contract" is deduced only from a cap hit above the league's two-way salary, in that one direction.
"""

from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.canonical.records import SINGLE_SEASON_CONTRACT_SOURCE
from app.cba import resolver
from app.db.models import Contract, ContractYear, DataSyncRun, LeagueCapParameters
from app.services.financials import (
    horizon,
    player_financials,
    team_financial_summary,
    team_financials,
    two_way_salary,
)
from tests.conftest import make_player, make_team

FIN = "2026-27"
ROSTER = "2025-26"  # the fixtures' roster season (make_player's default)


def _run(db: Session, season: str, *, outcome: str = "complete", failed: list | None = None, unpriced=None) -> None:
    db.add(
        DataSyncRun(
            job_name="sync_contracts",
            status="succeeded",
            finished_at=datetime(2026, 10, 19, 12, 0, tzinfo=UTC),
            detail={
                "season": season,
                "outcome": outcome,
                "teams_failed": failed or [],
                "unpriced_players": unpriced or [],
            },
        )
    )
    db.commit()


def _contract(db: Session, player, seasons: dict[str, int], *, per_season_source: bool = True, **year_kw) -> Contract:
    contract = db.query(Contract).filter_by(player_id=player.id).first()
    if contract is None:
        contract = Contract(
            player_id=player.id,
            contract_type=None,
            source_provider="balldontlie",
            source_name=SINGLE_SEASON_CONTRACT_SOURCE if per_season_source else "file snapshot",
            source_date=date(2026, 10, 19),
        )
        db.add(contract)
        db.flush()
    for season, salary in seasons.items():
        db.add(
            ContractYear(
                contract_id=contract.id,
                season=season,
                salary=salary,
                salary_basis="cap_hit",
                source_provider="balldontlie",
                source_retrieved_at=datetime(2026, 10, 19, 12, 0, tzinfo=UTC),
                **year_kw,
            )
        )
    db.commit()
    return contract


def _all_seasons(db: Session, through: str = "2029-30") -> None:
    for season in ("2026-27", "2027-28", "2028-29", "2029-30", "2030-31"):
        _run(db, season)
        if season == through:
            break


# ---------------------------------------------------------------------------- the horizon


class TestHorizon:
    def test_no_runs_means_no_horizon(self, db: Session) -> None:
        info = horizon(db, FIN)
        assert info.complete is False and info.seasons == ()
        assert "unknown" in info.as_dict()["note"] or "No complete" in info.as_dict()["note"]

    def test_contiguous_complete_seasons_from_the_financial_season(self, db: Session) -> None:
        _all_seasons(db, "2028-29")
        info = horizon(db, FIN)
        assert info.seasons == ("2026-27", "2027-28", "2028-29")
        assert info.last_season == "2028-29"

    def test_a_gap_ends_the_chain(self, db: Session) -> None:
        for season in ("2026-27", "2028-29"):  # 2027-28 never loaded
            _run(db, season)
        assert horizon(db, FIN).seasons == ("2026-27",)

    def test_a_season_without_the_financial_season_is_not_a_horizon(self, db: Session) -> None:
        _run(db, "2027-28")
        assert horizon(db, FIN).seasons == ()

    def test_a_partial_read_is_not_counted(self, db: Session) -> None:
        _run(db, "2026-27")
        _run(db, "2027-28", outcome="partial", failed=[{"team_provider_id": "5", "status": "failed"}])
        info = horizon(db, FIN)
        assert info.seasons == ("2026-27",)
        assert info.partial == ("2027-28",)

    def test_a_failed_run_is_ignored(self, db: Session) -> None:
        _run(db, "2026-27")
        db.add(DataSyncRun(job_name="sync_contracts", status="failed", detail={"season": "2027-28", "outcome": "complete"}))
        db.commit()
        assert horizon(db, FIN).seasons == ("2026-27",)


# ---------------------------------------------------------------------- contract length


class TestContractLength:
    def test_a_contract_ending_before_the_horizon_ends_is_exact(self, db: Session) -> None:
        team = make_team(db, 1, "AAA")
        player = make_player(db, 10, "Short Deal", team)
        _contract(db, player, {"2026-27": 10_000_000, "2027-28": 10_000_000})
        _all_seasons(db, "2030-31")
        fin = player_financials(db, {player.id: player.full_name}, FIN)[player.id]
        assert fin.end_status == "ends" and fin.final_season == "2027-28"
        assert fin.years_remaining == 2 and fin.years_remaining_is_lower_bound is False

    def test_a_contract_through_the_last_season_loaded_is_a_lower_bound(self, db: Session) -> None:
        team = make_team(db, 1, "AAA")
        player = make_player(db, 10, "Long Deal", team)
        _contract(db, player, dict.fromkeys(("2026-27", "2027-28", "2028-29"), 20000000))
        _all_seasons(db, "2028-29")
        fin = player_financials(db, {player.id: player.full_name}, FIN)[player.id]
        assert fin.end_status == "through_horizon"
        assert fin.years_remaining == 3 and fin.years_remaining_is_lower_bound is True

    def test_one_loaded_season_says_nothing_about_length(self, db: Session) -> None:
        """Every priced contract has a row in the last (only) season loaded — that is not evidence of length."""
        team = make_team(db, 1, "AAA")
        player = make_player(db, 10, "Unknown Length", team)
        _contract(db, player, {"2026-27": 10_000_000})
        _run(db, "2026-27")
        fin = player_financials(db, {player.id: player.full_name}, FIN)[player.id]
        assert fin.end_status == "unknown" and fin.years_remaining is None

    def test_no_horizon_at_all_is_unknown(self, db: Session) -> None:
        team = make_team(db, 1, "AAA")
        player = make_player(db, 10, "No Runs", team)
        _contract(db, player, {"2026-27": 10_000_000, "2027-28": 10_000_000})
        fin = player_financials(db, {player.id: player.full_name}, FIN)[player.id]
        assert fin.end_status == "unknown" and fin.years_remaining is None

    def test_a_multi_season_source_is_counted_as_it_states(self, db: Session) -> None:
        team = make_team(db, 1, "AAA")
        player = make_player(db, 10, "Snapshot Deal", team)
        _contract(db, player, {"2026-27": 5_000_000, "2027-28": 5_000_000}, per_season_source=False)
        fin = player_financials(db, {player.id: player.full_name}, FIN)[player.id]
        assert fin.end_status == "ends" and fin.years_remaining == 2

    def test_a_gap_between_seasons_is_flagged(self, db: Session) -> None:
        team = make_team(db, 1, "AAA")
        player = make_player(db, 10, "Gappy", team)
        _contract(db, player, {"2026-27": 5_000_000, "2028-29": 5_000_000})
        _all_seasons(db, "2030-31")
        fin = player_financials(db, {player.id: player.full_name}, FIN)[player.id]
        assert fin.has_gap is True

    def test_past_seasons_are_not_counted_as_remaining(self, db: Session) -> None:
        team = make_team(db, 1, "AAA")
        player = make_player(db, 10, "Veteran", team)
        _contract(db, player, {"2025-26": 9_000_000, "2026-27": 9_000_000, "2027-28": 9_000_000})
        _all_seasons(db, "2030-31")
        fin = player_financials(db, {player.id: player.full_name}, FIN)[player.id]
        assert [s.season for s in fin.seasons] == ["2026-27", "2027-28"]
        assert fin.years_remaining == 2


# ------------------------------------------------------------------- unknown is not zero


class TestUnknownIsNotZero:
    def test_a_player_with_no_contract_is_no_contract_not_zero(self, db: Session) -> None:
        team = make_team(db, 1, "AAA")
        player = make_player(db, 10, "Nobody", team)
        fin = player_financials(db, {player.id: player.full_name}, FIN)[player.id]
        assert fin.status == "no_contract" and fin.cap_hit is None
        assert fin.as_dict()["cap_hit"] is None and fin.seasons == ()

    def test_a_contract_with_only_other_seasons_is_unpriced(self, db: Session) -> None:
        team = make_team(db, 1, "AAA")
        player = make_player(db, 10, "Later Only", team)
        _contract(db, player, {"2027-28": 7_000_000})
        fin = player_financials(db, {player.id: player.full_name}, FIN)[player.id]
        assert fin.status == "unpriced" and fin.cap_hit is None
        assert fin.end_status == "unknown"

    def test_recorded_provider_rows_without_a_cap_hit_are_explained_not_priced(self, db: Session, cap_params) -> None:
        cap_params.extras = {"two_way_salary": 678_882}
        db.commit()
        team = make_team(db, 1, "AAA")
        twoway = make_player(db, 10, "Cash Only", team)
        other = make_player(db, 11, "Base Only", team)
        _run(
            db,
            FIN,
            unpriced=[
                {"player_id": twoway.id, "name": "Cash Only", "base_salary": None, "total_cash": 678_882},
                {"player_id": other.id, "name": "Base Only", "base_salary": 2_500_000, "total_cash": None},
            ],
        )
        fins = player_financials(db, {twoway.id: "Cash Only", other.id: "Base Only"}, FIN)
        for fin in fins.values():
            assert fin.status == "unpriced" and fin.cap_hit is None
            assert "not $0" in fin.status_reason
        assert "equals the 2026-27 two-way salary" in fins[twoway.id].status_reason
        assert "unverified" in fins[twoway.id].status_reason
        # a base salary is not promoted to a cap hit, and earns no two-way hint
        assert "two-way" not in fins[other.id].status_reason
        assert fins[other.id].as_dict()["cap_hit"] is None

    def test_cash_equal_to_the_two_way_salary_is_a_hint_only_never_a_type(self, db: Session, cap_params) -> None:
        cap_params.extras = {"two_way_salary": 678_882}
        db.commit()
        team = make_team(db, 1, "AAA")
        p = make_player(db, 10, "Cash Only", team)
        _run(db, FIN, unpriced=[{"player_id": p.id, "base_salary": None, "total_cash": 678_882}])
        fin = player_financials(db, {p.id: p.full_name}, FIN)[p.id]
        assert fin.contract_type is None and fin.not_two_way is None


# ------------------------------------------------------------------- the two-way deduction


class TestNotTwoWayDeduction:
    def _setup(self, db: Session, cap_params, salary: int, *, two_way: int | None = 678_882):
        cap_params.extras = {"two_way_salary": two_way} if two_way else {}
        db.commit()
        team = make_team(db, 1, "AAA")
        player = make_player(db, 10, "Deduced", team, salary=salary, contract_type=None)
        return player

    def test_a_cap_hit_above_the_two_way_salary_rules_out_a_two_way_contract(self, db: Session, cap_params) -> None:
        player = self._setup(db, cap_params, 5_000_000)
        fin = player_financials(db, {player.id: player.full_name}, FIN)[player.id]
        assert fin.not_two_way is True
        assert "two-way salary" in (fin.not_two_way_basis or "")

    def test_a_cap_hit_at_the_two_way_salary_proves_nothing(self, db: Session, cap_params) -> None:
        player = self._setup(db, cap_params, 678_882)
        assert player_financials(db, {player.id: player.full_name}, FIN)[player.id].not_two_way is None

    def test_a_cap_hit_below_the_two_way_salary_proves_nothing(self, db: Session, cap_params) -> None:
        player = self._setup(db, cap_params, 400_000)
        assert player_financials(db, {player.id: player.full_name}, FIN)[player.id].not_two_way is None

    def test_without_a_sourced_two_way_salary_nothing_is_deduced(self, db: Session, cap_params) -> None:
        player = self._setup(db, cap_params, 20_000_000, two_way=None)
        assert two_way_salary(db, FIN) is None
        assert player_financials(db, {player.id: player.full_name}, FIN)[player.id].not_two_way is None

    def test_the_stored_contract_type_is_never_written_by_the_deduction(self, db: Session, cap_params) -> None:
        player = self._setup(db, cap_params, 20_000_000)
        player_financials(db, {player.id: player.full_name}, FIN)
        assert db.query(Contract).filter_by(player_id=player.id).one().contract_type is None


# ---------------------------------------------------------------------------- team view


class TestTeamFinancials:
    def _team(self, db: Session, salaries: list[int | None]):
        team = make_team(db, 1, "AAA")
        for i, salary in enumerate(salaries):
            p = make_player(db, 100 + i, f"Player {i:02d}", team, salary=salary, contract_type=None)
            if salary is not None:
                _contract(db, p, {"2027-28": salary})
        _all_seasons(db, "2028-29")
        return team

    def test_a_partial_team_is_a_known_subtotal_never_a_payroll(self, db: Session) -> None:
        team = self._team(db, [10_000_000, 20_000_000, None])
        fin = team_financials(db, team.id, FIN, ROSTER)
        summary = team_financial_summary(fin, None)
        assert summary["subtotal_label"] == "Known salary subtotal"
        assert summary["known_subtotal"] == 30_000_000
        assert summary["payroll"] is None and summary["is_floor"] is True
        assert summary["coverage"] == {"roster_size": 3, "priced": 2, "unpriced": 1, "share": 0.6667, "complete": False}
        assert [u["name"] for u in summary["unpriced_players"]] == ["Player 02"]

    def test_a_complete_team_is_a_payroll_with_a_position(self, db: Session, cap_params) -> None:
        team = self._team(db, [100_000_000, 120_000_000])
        params = {
            "salary_cap": cap_params.salary_cap, "luxury_tax": cap_params.luxury_tax,
            "first_apron": cap_params.first_apron, "second_apron": cap_params.second_apron,
        }
        summary = team_financial_summary(team_financials(db, team.id, FIN, ROSTER), params)
        assert summary["subtotal_label"] == "Payroll"
        assert summary["payroll"] == 220_000_000
        assert summary["position"]["status"] == "above_first_apron"

    def test_an_empty_roster_is_not_complete(self, db: Session) -> None:
        team = make_team(db, 1, "AAA")
        summary = team_financial_summary(team_financials(db, team.id, FIN, ROSTER), None)
        assert summary["coverage"]["complete"] is False
        assert summary["payroll"] is None and summary["known_subtotal"] is None

    def test_a_partial_floor_names_only_thresholds_it_clears_and_withholds_the_rest(self, db: Session, cap_params) -> None:
        team = self._team(db, [100_000_000, 70_000_000, None])
        params = {
            "salary_cap": cap_params.salary_cap, "luxury_tax": cap_params.luxury_tax,
            "first_apron": cap_params.first_apron, "second_apron": cap_params.second_apron,
        }
        summary = team_financial_summary(team_financials(db, team.id, FIN, ROSTER), params)
        assert summary["thresholds_cleared_by_known_subtotal"] == ["Salary cap"]
        assert summary["position"] is None
        withheld = {w["conclusion"] for w in summary["withheld"]}
        assert {"Team payroll", "Cap space", "Apron position", "Luxury-tax bill"} <= withheld

    def test_commitments_are_per_season_known_cap_hits_of_rostered_players(self, db: Session) -> None:
        team = self._team(db, [10_000_000, 20_000_000])
        fin = team_financials(db, team.id, FIN, ROSTER)
        rows = {r["season"]: r for r in fin.commitments()}
        assert rows[FIN]["known_total"] == 30_000_000 and rows[FIN]["players"] == 2
        assert rows["2027-28"]["known_total"] == 30_000_000

    def test_thresholds_are_for_the_financial_season_only(self, db: Session, cap_params) -> None:
        team = self._team(db, [10_000_000])
        params = {
            "salary_cap": cap_params.salary_cap, "luxury_tax": cap_params.luxury_tax,
            "first_apron": cap_params.first_apron, "second_apron": cap_params.second_apron,
        }
        summary = team_financial_summary(team_financials(db, team.id, FIN, ROSTER), params)
        assert summary["thresholds"]["season"] == FIN
        assert any(w["conclusion"] == "Seasons after 2026-27" for w in summary["withheld"])

    def test_the_other_provider_and_the_two_way_salary_row_are_read_from_the_cap_row(self, db: Session, cap_params) -> None:
        # a fresh league-year row without the extra yields no deduction (see TestNotTwoWayDeduction)
        db.add(LeagueCapParameters(league_year="2027-28", salary_cap=1, luxury_tax=1, first_apron=1, second_apron=1, minimum_team_salary=1, source_name="x"))
        db.commit()
        assert two_way_salary(db, "2027-28") is None


# ------------------------------------------------------------------------------ caching


def test_resolver_memoizes_financials_and_league_inputs_per_session(db: Session) -> None:
    team = make_team(db, 1, "AAA")
    player = make_player(db, 10, "Cached", team, salary=9_000_000, contract_type=None)
    first = resolver.financials(db, {player.id: player.full_name}, FIN)
    second = resolver.financials(db, {player.id: player.full_name}, FIN)
    assert first[player.id] is second[player.id]
    # a write inside the session invalidates (the resolver clears on flush/commit)
    db.add(DataSyncRun(job_name="sync_contracts", status="succeeded", detail={"season": FIN, "outcome": "complete"}))
    db.commit()
    third = resolver.financials(db, {player.id: player.full_name}, FIN)
    assert third[player.id] is not first[player.id]
