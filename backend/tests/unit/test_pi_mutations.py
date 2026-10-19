"""R9.2 — planted defects: every safeguard must actually fire.

A passing suite proves little if the safeguards it relies on cannot fail. Each case here defines a *guard* (an invariant
check, the leakage audit, the contract, the service) that passes on the real code, plants one defect the engine is meant to
be immune to, and requires the guard to complain. If a later change weakens a safeguard, the matching ``*_is_detected`` test
fails — which is the point. The independent review (docs/r9-2/qa-and-regression-report.md) found five defects that an earlier
version of this file did not catch; each now has a case below."""

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.db.models import CandidateEstimate, ModelVersion, PlayerSeasonStats
from app.modeling import panel as panel_module
from app.modeling import targets as targets_module
from app.modeling.backtest import BacktestConfig, leakage_audit
from app.modeling.contract import Interval, ModelRef, OutputKind, Period, PlayerIntelligenceRecord
from app.modeling.pairs import PairCache
from app.modeling.panel import PanelValidationError, PlayerSeasonPanel
from app.modeling.predictors import PREDICTORS, predictors_for
from app.modeling.registry import train_candidate
from app.modeling.targets import get_target
from app.player_intelligence import measured as measured_module
from app.player_intelligence import rate_models as rm
from app.player_intelligence import service as service_module
from app.player_intelligence.panel import build_skill_panel
from app.player_intelligence.service import PlayerIntelligenceService

from .modeling_support import make_frame
from .pi_support import build_league, make_registry, provider_rows

CUTOFF = "2022-23"


# ------------------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def league(tmp_path_factory):
    info = build_league(tmp_path_factory, seed=9, n_players=90, cutoff=CUTOFF)
    info["panel"] = build_skill_panel(info["db"])
    yield info
    info["db"].close()
    info["engine"].dispose()


def a_player(league, rank: int = -1) -> str:
    frame = league["frame"]
    at = frame[frame["season"] == CUTOFF].sort_values("total_minutes")
    return league["players"][at.iloc[rank]["player_id"]].id


# ------------------------------------------------------------------------------------- guards
# Each guard raises (AssertionError or a validation error) when its invariant is broken.


def guard_no_attempts_is_not_a_rate():
    t = get_target("fg3_pct")
    rows = pd.DataFrame({"fg3m": [0.0, 5.0], "fg3a": [0.0, 20.0]})
    out = t.measure(rows)
    assert np.isnan(out.iloc[0]), "a player with no attempts must have no rate, not 0%"
    assert out.iloc[1] == 0.25


def guard_beta_binomial_never_sees_true_shooting():
    assert PREDICTORS["beta_binomial"].applies_to(get_target("ts_pct")) is False
    assert "beta_binomial" not in [p.name for p in predictors_for(get_target("ts_pct"))]


def guard_thin_samples_are_not_certain():
    params = rm.StateSpaceParams(beta=(0.35, 0.0, 0.0), rho=0.9, tau2=0.0009, phi=1.0, unit_variance=0.22)
    y = np.full((2, 4), np.nan)
    s2 = np.full((2, 4), np.nan)
    y[0, 0], s2[0, 0] = 0.60, 0.6 * 0.4 / 10  # 60% on 10 attempts
    y[1, 0], s2[1, 0] = 0.40, 0.4 * 0.6 / 600
    post = rm.filter_histories(y, s2, np.array([26.0, 26.0]), None, params)
    assert post.var_now[0] > 3 * post.var_now[1], "ten attempts must leave far more uncertainty than six hundred"
    assert abs(post.level_now[0] - 0.35) < 0.02, "ten attempts must barely move the estimate"


def guard_percentages_are_refused_in_fraction_columns():
    frame = make_frame(n_players=40, n_seasons=6, seed=2).assign(TS_PCT=lambda d: d["TS_PCT"] * 100)
    with pytest.raises(PanelValidationError):
        PlayerSeasonPanel.from_frame(frame)


def guard_candidates_need_an_explicit_opt_in(league):
    svc = PlayerIntelligenceService(league["db"], make_registry(league["fingerprint"]))
    out = svc.get(a_player(league), ["fg3_pct"], as_of=CUTOFF, kinds=["projected", "inferred"])
    assert out.records == [], "a candidate model's output must not be served without include_experimental"


def guard_a_missing_season_is_reported_not_filled(league):
    svc = PlayerIntelligenceService(league["db"], make_registry(league["fingerprint"]))
    r = svc.get(a_player(league), ["usg_pct"], as_of="2031-32")
    assert r.records == [], "a missing season must be reported, never filled"


def guard_nothing_after_the_cutoff_is_read(league):
    rows = measured_module.load_season_rows(league["db"], a_player(league), "2019-20")
    assert rows and max(rows) <= "2019-20", "measured rows after the information cutoff were read"


def guard_true_shooting_inconsistent_inputs_are_refused_by_the_reconstruction():
    """Layer 1: counts must reproduce the published TS_PCT, or the row is 'unconstrained' (and so never served)."""
    from app.player_intelligence.components import QUALITY_UNCONSTRAINED, reconstruct

    base, adv = provider_rows(gp=70, fgm=480, fga=1010, fg3m=254, fg3a=694, ftm=503, fta=645)
    off = {**adv, "TS_PCT": adv["TS_PCT"] + 0.02}
    assert reconstruct(base, off, 70).quality == QUALITY_UNCONSTRAINED, "counts that cannot reproduce the published TS were accepted"


def guard_true_shooting_disagreement_is_flagged_by_the_measured_path():
    """Layer 2: even if a row reached the measured path with counts and a published TS that disagree, it says so."""
    base, adv = provider_rows(gp=70, fgm=480, fga=1010, fg3m=254, fg3a=694, ftm=503, fta=645)
    rec = measured_module.reconstruct(base, adv, 70)  # counts from the CONSISTENT row...
    row = measured_module.SeasonRow(
        season="2024-25", games=70, minutes_per_game=30.0, team_id=None, team_count=1, base=base,
        advanced={**adv, "TS_PCT": adv["TS_PCT"] + 0.02}, retrieved_at=None, recon=rec,  # ...with a published value that disagrees
    )
    out = measured_module.measured_record("p", "ts_pct", row)
    assert not isinstance(out, measured_module.Unavailable) and any("differs from the published value" in lim for lim in out.limitations), "a TS disagreement was not flagged"


def guard_a_traded_player_is_labelled(league):
    db = league["db"]
    player = a_player(league)
    rows = db.scalars(select(PlayerSeasonStats).where(PlayerSeasonStats.player_id == player, PlayerSeasonStats.season == CUTOFF, PlayerSeasonStats.stat_type == "base")).all()
    try:
        for r in rows:
            r.stats = {**r.stats, "TEAM_COUNT": 2}
        db.flush()
        rec = PlayerIntelligenceService(db, make_registry(league["fingerprint"])).get(player, ["usg_pct"], as_of=CUTOFF).records[0]
        assert any("appeared for 2 teams" in lim for lim in rec.limitations), "a traded player's totals were not labelled"
    finally:
        db.rollback()


def guard_the_credible_interval_is_the_stated_level():
    params = rm.StateSpaceParams(beta=(0.35, 0.0, 0.0), rho=0.9, tau2=0.0009, phi=1.0, unit_variance=0.22)
    y = np.full((1, 3), np.nan)
    s2 = np.full((1, 3), np.nan)
    y[0, 0], s2[0, 0] = 0.4, 0.4 * 0.6 / 200
    post = rm.filter_histories(y, s2, np.array([26.0]), None, params)
    lo, hi = post.credible_interval("now", 0.8)
    assert (hi - lo)[0] / 2 == pytest.approx(1.2815515655446004 * np.sqrt(post.var_now[0]), rel=1e-6), "an 80% interval must use the 80% quantile"


def _a_bounded_row():
    """A season whose provider rounding genuinely leaves the free-throw attempt count undetermined."""
    for fta in range(6, 200):
        base, adv = provider_rows(gp=82, fgm=300, fga=700, fg3m=1, fg3a=40, ftm=int(0.75 * fta), fta=fta)
        rec = measured_module.reconstruct(base, adv, 82)
        if rec.quality == "bounded" and rec.fta_max > rec.fta_min and rec.fta_min > 0 and rec.fta > 0:  # a rate is givable: zero attempts is ruled out
            row = measured_module.SeasonRow(season="2024-25", games=82, minutes_per_game=20.0, team_id=None, team_count=1, base=base, advanced=adv, retrieved_at=None, recon=rec)
            return row, rec
    raise AssertionError("no ambiguous example found: the planted-defect test would be vacuous")


def guard_rounding_ambiguity_is_disclosed():
    row, rec = _a_bounded_row()
    out = measured_module.measured_record("p", "ft_pct", row)
    assert not isinstance(out, measured_module.Unavailable)
    assert any("undetermined" in lim for lim in out.limitations), "reconstruction ambiguity was not disclosed"
    assert out.sample.attempts_min == rec.fta_min and out.sample.attempts_max == rec.fta_max, "the attempt range was not served"


# ----------------------------------------------------------- the guards pass on correct code


def test_every_guard_passes_on_the_real_implementation(league):
    guard_no_attempts_is_not_a_rate()
    guard_beta_binomial_never_sees_true_shooting()
    guard_thin_samples_are_not_certain()
    guard_percentages_are_refused_in_fraction_columns()
    guard_candidates_need_an_explicit_opt_in(league)
    guard_a_missing_season_is_reported_not_filled(league)
    guard_nothing_after_the_cutoff_is_read(league)
    guard_true_shooting_inconsistent_inputs_are_refused_by_the_reconstruction()
    guard_true_shooting_disagreement_is_flagged_by_the_measured_path()
    guard_a_traded_player_is_labelled(league)
    guard_the_credible_interval_is_the_stated_level()


# ----------------------------------------------------------------------- the planted defects


def test_zero_attempts_as_zero_percent_is_detected(monkeypatch):
    def broken(rows):
        m = pd.to_numeric(rows["fg3m"], errors="coerce").astype(float)
        a = pd.to_numeric(rows["fg3a"], errors="coerce").astype(float)
        return (m / a.replace(0, np.nan)).fillna(0.0)  # the planted defect

    spec = get_target("fg3_pct")
    monkeypatch.setitem(targets_module.TARGETS, "fg3_pct", type(spec)(**{**spec.__dict__, "measure": broken}))
    with pytest.raises(AssertionError, match="no rate, not 0%"):
        guard_no_attempts_is_not_a_rate()


def test_beta_binomial_on_true_shooting_is_detected(monkeypatch):
    monkeypatch.setattr(type(PREDICTORS["beta_binomial"]), "applies_to", lambda self, target: True)
    with pytest.raises(AssertionError):
        guard_beta_binomial_never_sees_true_shooting()


def test_ignoring_sampling_variance_gives_false_certainty_and_is_detected(monkeypatch):
    real = rm.filter_histories

    def overconfident(y, s2, age, cov, params):
        return real(y, np.where(np.isfinite(s2), 1e-6, s2), age, cov, params)  # treats every season as measured exactly

    monkeypatch.setattr(rm, "filter_histories", overconfident)
    with pytest.raises(AssertionError):
        guard_thin_samples_are_not_certain()


def test_removing_the_unit_check_is_detected(monkeypatch):
    monkeypatch.setattr(panel_module, "FRACTION_COLUMNS", {})  # the planted defect: no column is checked for percentage points
    with pytest.raises(pytest.fail.Exception):  # the guard's `pytest.raises(PanelValidationError)` no longer sees an error
        guard_percentages_are_refused_in_fraction_columns()


def test_a_future_season_in_the_features_is_detected_by_the_leakage_audit(monkeypatch, league):
    panel = league["panel"]
    cfg = BacktestConfig(target="fg3_pct", models=("persistence", "state_space"), bootstrap_draws=20)
    assert leakage_audit(panel, cfg, "2019-20")["passed"] is True  # clean code passes

    real = PairCache.features

    def leaky(self, origin, target):
        out = real(self, origin, target).copy()  # the planted defect: next season's statistic read from the full panel
        nxt = self.panel.season_rows(f"{int(origin[:4]) + 1}-{(int(origin[:4]) + 2) % 100:02d}")
        lookup = dict(zip(nxt["player_id"], target.measure(nxt), strict=True))
        out["x_t"] = out["player_id"].map(lookup).fillna(out["x_t"])
        return out

    monkeypatch.setattr(PairCache, "features", leaky)
    assert leakage_audit(panel, cfg, "2019-20")["passed"] is False


def test_a_player_mapped_to_a_later_franchise_is_detected_by_the_leakage_audit(monkeypatch, league):
    panel = league["panel"]
    cfg = BacktestConfig(target="fg3_pct", models=("persistence",), bootstrap_draws=20)
    real = PairCache.features

    def wrong_franchise(self, origin, target):
        out = real(self, origin, target).copy()
        latest_team = self.panel.frame.sort_values("season").groupby("player_id")["team_id"].last()  # includes the future
        out["team_id"] = out["player_id"].map(latest_team)  # the planted defect: the team he plays for *later*
        return out

    monkeypatch.setattr(PairCache, "features", wrong_franchise)
    assert leakage_audit(panel, cfg, "2019-20")["passed"] is False


def test_an_inferred_value_labelled_measured_is_refused():
    with pytest.raises(ValidationError):
        PlayerIntelligenceRecord(
            player_id="p", quantity="fg3_pct", unit="fraction of 1", output_kind=OutputKind.MEASURED, value=0.36,
            period=Period(season="2024-25"), model=ModelRef(name="m", version="v", status="candidate"),
        )
    with pytest.raises(ValidationError):
        PlayerIntelligenceRecord(  # a model output that hides its model
            player_id="p", quantity="fg3_pct", unit="fraction of 1", output_kind=OutputKind.INFERRED, value=0.36,
            period=Period(season="2024-25"),
        )


def test_serving_a_candidate_without_the_opt_in_is_detected(monkeypatch, league):
    guard_candidates_need_an_explicit_opt_in(league)  # the safeguard works...
    monkeypatch.setattr(service_module, "serving_decision", lambda *a, **k: (True, "validated"))  # ...the planted defect
    with pytest.raises(AssertionError, match="without include_experimental"):
        guard_candidates_need_an_explicit_opt_in(league)


def test_a_missing_season_becoming_zero_is_detected(monkeypatch, league):
    def filler(player_id, quantities, rows, season):  # the planted defect: report a zero record for the missing season
        from app.modeling.contract import Provenance

        spec = measured_module.get_quantity(quantities[0])
        rec = PlayerIntelligenceRecord(player_id=player_id, quantity=quantities[0], unit=spec.unit, output_kind=OutputKind.MEASURED, value=0.0, period=Period(season=season), provenance=Provenance())
        return [rec], []

    monkeypatch.setattr(service_module, "measured_for", filler)
    with pytest.raises(AssertionError, match="never filled"):
        guard_a_missing_season_is_reported_not_filled(league)


def test_reading_rows_after_the_cutoff_is_detected(monkeypatch, league):
    real = measured_module.load_season_rows

    def ignores_the_cutoff(db, player_id, as_of=None):
        return real(db, player_id, None)  # the planted defect

    monkeypatch.setattr(measured_module, "load_season_rows", ignores_the_cutoff)
    with pytest.raises(AssertionError, match="after the information cutoff"):
        guard_nothing_after_the_cutoff_is_read(league)


def test_disabling_the_true_shooting_constraint_is_detected(monkeypatch):
    from app.player_intelligence import components as components_module

    monkeypatch.setattr(components_module, "PCT_HALF_ULP", 1.0)  # the planted defect: any published rate is "consistent"
    with pytest.raises(AssertionError, match="cannot reproduce the published TS"):
        guard_true_shooting_inconsistent_inputs_are_refused_by_the_reconstruction()


def test_disabling_the_measured_true_shooting_reconciliation_is_detected(monkeypatch):
    guard_true_shooting_disagreement_is_flagged_by_the_measured_path()
    monkeypatch.setattr(measured_module, "TS_RECONCILIATION_TOLERANCE", 10.0)  # the planted defect: nothing ever disagrees
    with pytest.raises(AssertionError, match="not flagged"):
        guard_true_shooting_disagreement_is_flagged_by_the_measured_path()


def test_dropping_the_traded_player_note_is_detected(monkeypatch, league):
    monkeypatch.setattr(measured_module, "_common_limitations", lambda row: [])
    with pytest.raises(AssertionError, match="not labelled"):
        guard_a_traded_player_is_labelled(league)


def test_suppressing_the_rounding_disclosure_is_detected(monkeypatch):
    guard_rounding_ambiguity_is_disclosed()
    monkeypatch.setattr(measured_module, "_ambiguity_limitation", lambda *a: "")  # the planted defect: the note can never fire
    with pytest.raises(AssertionError, match="was not disclosed"):
        guard_rounding_ambiguity_is_disclosed()


def test_an_80_percent_interval_computed_at_50_percent_is_detected(monkeypatch):
    from scipy import stats as scipy_stats

    z50 = float(scipy_stats.norm.ppf(0.75))  # captured BEFORE patching: the planted defect uses the 50% quantile for any level
    monkeypatch.setattr(rm.stats, "norm", type("N", (), {"ppf": staticmethod(lambda q: z50)}))
    with pytest.raises(AssertionError, match="80% quantile"):
        guard_the_credible_interval_is_the_stated_level()


def test_an_interval_advertised_with_unsupported_coverage_is_detected():
    from app.modeling.contract import Calibration

    cal = Calibration(folds=3, n=50, empirical_coverage=0.8, evaluation_ref="x")
    with pytest.raises(ValidationError):  # a credible interval borrowing an empirical-coverage claim
        Interval(low=0.3, high=0.4, nominal_level=0.8, method="posterior", kind="credible", calibration=cal)
    uncalibrated = Interval(low=0.3, high=0.4, nominal_level=0.8, method="x")
    r = PlayerIntelligenceRecord(
        player_id="p", quantity="fg3_pct", unit="u", output_kind=OutputKind.INFERRED, value=0.35, period=Period(season="2024-25"),
        model=ModelRef(name="m", version="v", status="candidate"), interval=uncalibrated,
    )
    assert "has not been measured" in r.reliability_statement()  # never presented as a probability


def test_a_provider_that_changes_scale_is_refused_by_the_measured_path(league):
    db = league["db"]
    player = a_player(league)
    row = db.scalars(select(PlayerSeasonStats).where(PlayerSeasonStats.player_id == player, PlayerSeasonStats.season == CUTOFF, PlayerSeasonStats.stat_type == "advanced")).one()
    try:
        row.stats = {**row.stats, "REB_PCT": 9.4}
        db.flush()
        out = PlayerIntelligenceService(db, make_registry(league["fingerprint"])).get(player, ["reb_pct"], as_of=CUTOFF)
        assert out.records == [] and "not a fraction of 1" in out.unavailable[0].reason
    finally:
        db.rollback()


def test_a_candidate_cannot_overwrite_an_active_model(league):
    db = league["db"]
    active = ModelVersion(model_name="player_impact", version="legacy-active", algorithm="fixture", is_active=True, status="active")
    db.add(active)
    db.commit()
    before = db.get(ModelVersion, active.id).validation_metrics
    train_candidate(db, "ols_history", "fg3_pct", CUTOFF)
    again = db.get(ModelVersion, active.id)
    assert again.is_active and again.status == "active" and again.validation_metrics == before
    assert db.scalar(select(CandidateEstimate).where(CandidateEstimate.model_version_id == active.id)) is None


def test_the_reconstruction_notices_inputs_that_cannot_be_right():
    from app.player_intelligence.components import QUALITY_UNCONSTRAINED, reconstruct

    base, adv = provider_rows(gp=70, fgm=480, fga=1010, fg3m=254, fg3a=694, ftm=503, fta=645)
    base["FG3_PCT"] = 35.6  # percentage points where a fraction belongs
    assert reconstruct(base, adv, 70).quality == QUALITY_UNCONSTRAINED
