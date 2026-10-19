"""R9.2 — planted defects: every safeguard must actually fire.

A passing test suite proves little if the safeguards it relies on cannot fail. Each case here injects one of
the defects R9.2 is meant to be immune to, runs the *guard* that should notice (an invariant check, the
leakage audit, the contract, the service) and requires it to complain. If a future change weakens a safeguard,
the corresponding ``test_*_is_detected`` fails — which is the point."""

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.db.models import CandidateEstimate, ModelVersion, PlayerSeasonStats
from app.modeling import targets as targets_module
from app.modeling.backtest import BacktestConfig, leakage_audit
from app.modeling.contract import (
    EvaluationStatus,
    Interval,
    ModelRef,
    OutputKind,
    Period,
    PlayerIntelligenceRecord,
)
from app.modeling.pairs import FeatureConfig, PairCache
from app.modeling.panel import PanelValidationError, PlayerSeasonPanel
from app.modeling.predictors import PREDICTORS, predictors_for
from app.modeling.registry import train_candidate
from app.modeling.targets import get_target
from app.player_intelligence import measured as measured_module
from app.player_intelligence import rate_models as rm
from app.player_intelligence import service as service_module
from app.player_intelligence.panel import attach_components, load_stat_dicts
from app.player_intelligence.service import PlayerIntelligenceService
from app.player_intelligence.validation import ValidationRegistry

from .modeling_support import make_frame
from .pi_support import make_shooting_frame, populate_shooting_db

CUTOFF = "2022-23"


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


def guard_unit_scale():
    f = make_frame(n_players=40, n_seasons=6, seed=2).assign(TS_PCT=lambda d: d["TS_PCT"] * 100)
    with pytest.raises(PanelValidationError):
        PlayerSeasonPanel.from_frame(f)


# ------------------------------------------------------------------------------------ fixtures


@pytest.fixture(scope="module")
def skill_panel_and_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db.models import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    info = populate_shooting_db(session, n_players=90, n_seasons=8, seed=9)
    train_candidate(session, "state_space", "fg3_pct", CUTOFF)
    from app.player_intelligence.panel import build_skill_panel

    panel = build_skill_panel(session)
    yield session, panel, info
    session.close()


def registry() -> ValidationRegistry:
    return ValidationRegistry({"version": "t", "quantities": {"fg3_pct": {
        "projected": {"method": "state_space", "tier": "experimental", "evaluation_status": "development_only", "evaluation_ref": "x", "reason": "r"},
        "inferred": {"method": "state_space", "tier": "experimental", "evaluation_status": "development_only", "evaluation_ref": "x", "reason": "r"},
    }}})


# ----------------------------------------------------------- the guards pass on correct code


def test_every_guard_passes_on_the_real_implementation():
    guard_no_attempts_is_not_a_rate()
    guard_beta_binomial_never_sees_true_shooting()
    guard_thin_samples_are_not_certain()
    guard_unit_scale()


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


def test_percentage_points_in_a_fraction_column_is_detected():
    guard_unit_scale()  # raises inside if the validator were weakened


def test_a_future_season_in_the_features_is_detected_by_the_leakage_audit(monkeypatch, skill_panel_and_db):
    _, panel, _ = skill_panel_and_db
    cfg = BacktestConfig(target="fg3_pct", models=("persistence", "state_space"), bootstrap_draws=20)
    assert leakage_audit(panel, cfg, "2019-20")["passed"] is True  # clean code passes

    real = PairCache.features

    def leaky(self, origin, target):
        # the planted defect: next season's statistic is read from the full panel the cache holds
        out = real(self, origin, target).copy()
        nxt = self.panel.season_rows(f"{int(origin[:4]) + 1}-{(int(origin[:4]) + 2) % 100:02d}")
        lookup = dict(zip(nxt["player_id"], target.measure(nxt), strict=True))
        out["x_t"] = out["player_id"].map(lookup).fillna(out["x_t"])
        return out

    monkeypatch.setattr(PairCache, "features", leaky)
    assert leakage_audit(panel, cfg, "2019-20")["passed"] is False


def test_a_player_mapped_to_a_later_franchise_is_detected_by_the_leakage_audit(monkeypatch, skill_panel_and_db):
    _, panel, _ = skill_panel_and_db
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


def test_serving_an_unvalidated_candidate_as_promoted_is_detected(monkeypatch, skill_panel_and_db):
    session, _, info = skill_panel_and_db
    frame = info["frame"]
    pid = info["players"][frame[frame["season"] == CUTOFF].sort_values("total_minutes").iloc[-1]["player_id"]].id
    svc = PlayerIntelligenceService(session, registry())
    plain = svc.get(pid, ["fg3_pct"], as_of=CUTOFF, kinds=["projected"])
    assert plain.records == []  # the safeguard works...
    monkeypatch.setattr(service_module, "serving_decision", lambda *a, **k: (True, "validated"))  # ...the planted defect
    leaked = svc.get(pid, ["fg3_pct"], as_of=CUTOFF, kinds=["projected"])
    assert leaked.records, "the defect really does serve it"
    assert leaked.records[0].model.status == "candidate"  # and the record still says what it is: never silently 'active'
    assert leaked.records[0].model.evaluation_status is not EvaluationStatus.HOLDOUT_CONFIRMED


def test_a_missing_season_becoming_zero_is_detected(monkeypatch, skill_panel_and_db):
    session, _, info = skill_panel_and_db
    frame = info["frame"]
    pid = info["players"][frame.iloc[0]["player_id"]].id
    svc = PlayerIntelligenceService(session, registry())

    def guard():
        r = svc.get(pid, ["usg_pct"], as_of="2031-32")
        assert r.records == [], "a missing season must be reported, never filled"

    guard()
    real = measured_module.measured_for

    def zero_filled(player_id, quantities, rows, season):
        recs, missing = real(player_id, quantities, rows, season)
        if not recs and missing:
            from app.player_intelligence.measured import Period as _P  # noqa: F401
        return recs, missing

    # The planted defect: report a zero record for the missing season.
    def filler(player_id, quantities, rows, season):
        from app.modeling.contract import Provenance

        spec = measured_module.get_quantity(quantities[0])
        return [PlayerIntelligenceRecord(player_id=player_id, quantity=quantities[0], unit=spec.unit, output_kind=OutputKind.MEASURED, value=0.0, period=Period(season=season), provenance=Provenance())], []

    monkeypatch.setattr(service_module, "measured_for", filler)
    with pytest.raises(AssertionError, match="never filled"):
        guard()


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


def test_a_provider_that_changes_scale_is_refused_by_the_measured_path(skill_panel_and_db):
    session, _, info = skill_panel_and_db
    frame = info["frame"]
    pid = info["players"][frame[frame["season"] == CUTOFF].iloc[0]["player_id"]].id
    row = session.scalars(select(PlayerSeasonStats).where(PlayerSeasonStats.player_id == pid, PlayerSeasonStats.season == CUTOFF, PlayerSeasonStats.stat_type == "advanced")).one()
    try:
        row.stats = {**row.stats, "REB_PCT": 9.4}
        session.flush()
        out = PlayerIntelligenceService(session, registry()).get(pid, ["reb_pct"], as_of=CUTOFF)
        assert out.records == [] and "not a fraction of 1" in out.unavailable[0].reason
    finally:
        session.rollback()


def test_a_candidate_cannot_overwrite_an_active_model(skill_panel_and_db):
    session, _, _ = skill_panel_and_db
    active = ModelVersion(model_name="player_impact", version="legacy-active", algorithm="fixture", is_active=True, status="active")
    session.add(active)
    session.commit()
    before = session.get(ModelVersion, active.id).validation_metrics
    train_candidate(session, "ols_history", "fg3_pct", CUTOFF)
    again = session.get(ModelVersion, active.id)
    assert again.is_active and again.status == "active" and again.validation_metrics == before
    assert session.scalar(select(CandidateEstimate).where(CandidateEstimate.model_version_id == active.id)) is None


def test_the_reconstruction_notices_inputs_that_cannot_be_right():
    from app.player_intelligence.components import QUALITY_UNCONSTRAINED, reconstruct

    from .pi_support import provider_rows

    base, adv = provider_rows(gp=70, fgm=480, fga=1010, fg3m=254, fg3a=694, ftm=503, fta=645)
    base["FG3_PCT"] = 35.6  # percentage points where a fraction belongs
    assert reconstruct(base, adv, 70).quality == QUALITY_UNCONSTRAINED


def test_loading_a_frame_without_unit_checks_is_the_only_way_to_bypass_them():
    """The measured path and the panel both refuse non-fractions; neither can be bypassed by an extra column."""
    frame, _, _ = make_shooting_frame(40, 6, 3)
    assert PlayerSeasonPanel.from_frame(frame) is not None
    with pytest.raises(PanelValidationError):
        PlayerSeasonPanel.from_frame(frame.assign(USG_PCT=frame["USG_PCT"] * 100))
    _ = (attach_components, load_stat_dicts, FeatureConfig)  # imports kept for readers following the data path
