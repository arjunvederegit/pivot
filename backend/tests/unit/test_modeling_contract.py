"""R9.1 — the player-intelligence envelope refuses statements that blur measured, inferred and projected."""

import pytest
from pydantic import ValidationError

from app.modeling.contract import (
    Calibration,
    EvaluationStatus,
    Interval,
    ModelRef,
    OutputKind,
    Period,
    PlayerIntelligenceRecord,
    Provenance,
    Sample,
    SourceRef,
)

MODEL = ModelRef(name="player_forecast_ts_pct", version="cand-x", status="candidate")


def record(**overrides):
    base = {
        "player_id": "9f3c0b3e-0000-4000-8000-000000000001",
        "quantity": "ts_pct",
        "unit": "fraction",
        "output_kind": OutputKind.MEASURED,
        "value": 0.583,
        "period": Period(season="2025-26"),
        "sample": Sample(games=70, attempts=1100.0, unit="true shooting attempts"),
        "provenance": Provenance(sources=[SourceRef(provider="nba_api", dataset="leaguedashplayerstats/advanced")]),
    }
    base.update(overrides)
    return PlayerIntelligenceRecord(**base)


def test_a_measured_value_is_an_observation_with_no_model_and_no_forecast_interval():
    assert record().output_kind is OutputKind.MEASURED
    with pytest.raises(ValidationError, match="not a model output"):
        record(model=MODEL)
    with pytest.raises(ValidationError, match="no forecast interval"):
        record(interval=Interval(low=0.5, high=0.6, nominal_level=0.8, method="x"))


def test_a_projection_names_what_it_forecasts_and_its_information_cutoff():
    ok = record(
        output_kind=OutputKind.PROJECTED, model=MODEL,
        period=Period(season="2025-26", as_of_season="2025-26"),
        target_period=Period(season="2026-27"),
        conditioning="the player plays at least 500 minutes in the target season",
    )
    assert ok.target_period.season == "2026-27"
    # R9.2: a forecast must say what it is conditional on; the participation question is separate.
    with pytest.raises(ValidationError, match="conditional on"):
        record(
            output_kind=OutputKind.PROJECTED, model=MODEL,
            period=Period(season="2025-26", as_of_season="2025-26"), target_period=Period(season="2026-27"),
        )
    with pytest.raises(ValidationError, match="must name the period it forecasts"):
        record(output_kind=OutputKind.PROJECTED, model=MODEL, period=Period(season="2025-26", as_of_season="2025-26"))
    with pytest.raises(ValidationError, match="information cutoff"):
        record(output_kind=OutputKind.PROJECTED, model=MODEL, target_period=Period(season="2026-27"))
    with pytest.raises(ValidationError, match="must precede"):
        record(
            output_kind=OutputKind.PROJECTED, model=MODEL,
            period=Period(season="2025-26", as_of_season="2026-27"), target_period=Period(season="2026-27"),
        )


def test_a_model_output_must_name_its_model_and_a_measurement_cannot_have_a_target_period():
    with pytest.raises(ValidationError, match="must name the model version"):
        record(output_kind=OutputKind.INFERRED)
    with pytest.raises(ValidationError, match="only a projected record"):
        record(target_period=Period(season="2026-27"))
    assert record(output_kind=OutputKind.INFERRED, model=MODEL).model.version == "cand-x"


def test_intervals_must_be_ordered_and_levels_are_proper_probabilities():
    with pytest.raises(ValidationError):
        Interval(low=0.7, high=0.6, nominal_level=0.8, method="x")
    with pytest.raises(ValidationError):
        Interval(low=0.5, high=0.6, nominal_level=80, method="x")


def test_an_uncalibrated_interval_is_never_described_as_a_probability():
    r = record(
        output_kind=OutputKind.INFERRED, model=MODEL,
        interval=Interval(low=0.55, high=0.61, nominal_level=0.8, method="residual quantile"),
    )
    text = r.reliability_statement()
    assert "has not been measured" in text and "not a probability" in text


def test_a_calibrated_interval_cites_its_evidence():
    r = record(
        output_kind=OutputKind.INFERRED, model=MODEL,
        interval=Interval(
            low=0.55, high=0.61, nominal_level=0.8, method="residual quantile",
            calibration=Calibration(folds=5, n=1570, empirical_coverage=0.797, evaluation_ref="exp-development-ts_pct-abc"),
        ),
    )
    text = r.reliability_statement()
    assert "79.7%" in text and "exp-development-ts_pct-abc" in text and "1570" in text


def test_the_model_status_vocabulary_matches_the_database():
    for status in ("active", "candidate", "retired", "rejected"):
        ModelRef(name="m", version="v", status=status, evaluation_status=EvaluationStatus.UNEVALUATED)
    with pytest.raises(ValidationError):
        ModelRef(name="m", version="v", status="promoted")


def test_the_record_is_immutable_so_a_tool_cannot_quietly_relabel_it():
    r = record()
    with pytest.raises(ValidationError):
        r.output_kind = OutputKind.INFERRED


# ----------------------------------------------------------------------------- R9.2 additions


def test_a_credible_interval_cannot_borrow_an_empirical_coverage_claim():
    from app.modeling.contract import Calibration

    cal = Calibration(folds=5, n=100, empirical_coverage=0.8, evaluation_ref="x")
    with pytest.raises(ValidationError, match="credible interval"):
        Interval(low=0.3, high=0.4, nominal_level=0.8, method="posterior", kind="credible", calibration=cal)
    ok = Interval(low=0.3, high=0.4, nominal_level=0.8, method="posterior", kind="credible")
    r = record(output_kind=OutputKind.INFERRED, model=MODEL, interval=ok)
    assert "frequentist coverage is not claimed" in r.reliability_statement()


def test_only_an_inferred_record_carries_reliability_and_only_a_projection_a_condition():
    from app.modeling.contract import Reliability

    rel = Reliability(reliability=0.4, weight_on_player_record=0.4, prior_mean=0.35, interpretation="x")
    assert record(output_kind=OutputKind.INFERRED, model=MODEL, reliability=rel).reliability.reliability == 0.4
    with pytest.raises(ValidationError, match="describes an inferred value"):
        record(reliability=rel)
    with pytest.raises(ValidationError, match="only a projected record carries a conditioning"):
        record(output_kind=OutputKind.INFERRED, model=MODEL, conditioning="x")
    with pytest.raises(ValidationError):
        Reliability(reliability=1.4, weight_on_player_record=0.4, prior_mean=0.3, interpretation="x")


def test_the_benchmarked_status_exists_and_is_not_confirmation():
    assert EvaluationStatus.BENCHMARKED.value == "benchmarked"
    assert EvaluationStatus.BENCHMARKED is not EvaluationStatus.HOLDOUT_CONFIRMED
