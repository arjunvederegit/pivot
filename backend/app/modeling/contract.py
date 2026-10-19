"""The envelope in which player intelligence is returned.

R9.2 will compute skills and R9.3 will carry them between tools. Both need the same answer to the
same questions about any number: *what kind of statement is this, about whom, over which period,
from which model, built from which data, based on how much evidence, and has it been validated?*

Three kinds of statement are kept apart on purpose, because they are validated differently and a
user must never mistake one for another:

``measured``   what was directly observed in a stated period (data integrity + reproducibility).
``inferred``   an estimate of an underlying ability, after accounting for sampling noise (construct
               validity, stability, uncertainty).
``projected``  an estimate of a specific future observation (out-of-time forecast error).

Two rules are enforced by the types rather than left to convention:

* an interval carries its **method and its nominal level**, and says whether any calibration
  evidence exists; there is no bare "confidence %", and
* a ``projected`` record must name the future period it is about, and a model-produced record must
  name the model version and its evaluation status.

This module defines no endpoint and is not wired into the API. It is the contract R9.2 implements.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OutputKind(StrEnum):
    MEASURED = "measured"
    INFERRED = "inferred"
    PROJECTED = "projected"


class EvaluationStatus(StrEnum):
    UNEVALUATED = "unevaluated"
    DEVELOPMENT_ONLY = "development_only"
    HOLDOUT_CONFIRMED = "holdout_confirmed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"  # measured values are checked for integrity, not accuracy
    #: R9.2. A method whose out-of-time accuracy was measured on the development and holdout folds
    #: and recorded, but which makes no claim to improve on anything: the reference the candidates
    #: had to beat. "Benchmarked" is not "validated better than alternatives".
    BENCHMARKED = "benchmarked"


class Calibration(BaseModel):
    """Evidence that an interval delivers its nominal level — or the absence of it."""

    model_config = ConfigDict(frozen=True)
    folds: int = Field(ge=1)
    n: int = Field(ge=1)
    empirical_coverage: float = Field(ge=0, le=1, allow_inf_nan=False)
    evaluation_ref: str  # experiment id whose artifacts hold the numbers


class Interval(BaseModel):
    model_config = ConfigDict(frozen=True)
    low: float = Field(allow_inf_nan=False)
    high: float = Field(allow_inf_nan=False)
    nominal_level: float = Field(gt=0, lt=1)
    method: str
    #: ``None`` means no calibration evidence: the level is a design target, not a measurement, and
    #: a client must not present it as a probability.
    calibration: Calibration | None = None
    #: R9.2. What kind of interval this is. ``prediction``: a range for a future *observed* value
    #: (empirical coverage can and should be measured). ``credible``: a posterior interval for an
    #: *unobserved* quantity such as ability, under a model whose hyperparameters were estimated from
    #: data; it is not a frequentist confidence interval and no coverage of it is measurable.
    kind: Literal["prediction", "credible"] = "prediction"
    #: A sentence a user can read: what the interval is an interval *of*.
    interpretation: str | None = None

    @model_validator(mode="after")
    def _credible_has_no_coverage(self) -> Interval:
        if self.kind == "credible" and self.calibration is not None:
            raise ValueError(
                "a credible interval for an unobserved ability carries no empirical coverage; "
                "report predictive-check evidence in `interpretation` and the validation record instead"
            )
        return self

    @model_validator(mode="after")
    def _ordered(self) -> Interval:
        if self.low > self.high:
            raise ValueError("interval low exceeds high")
        return self


class Period(BaseModel):
    model_config = ConfigDict(frozen=True)
    season: str = Field(pattern=r"^\d{4}-\d{2}$")
    season_type: Literal["regular", "playoffs", "play_in", "preseason", "ist"] = "regular"
    #: Information cutoff: the last season whose results the value may depend on.
    as_of_season: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")


class Sample(BaseModel):
    model_config = ConfigDict(frozen=True)
    games: int | None = Field(default=None, ge=0)
    minutes: float | None = Field(default=None, ge=0)
    attempts: float | None = Field(default=None, ge=0)
    unit: str | None = None
    #: R9.2. Successes behind a made/attempted rate, and how the integer counts were obtained
    #: (``exact`` / ``bounded`` / ``unconstrained``; see ``player_intelligence.components``).
    makes: float | None = Field(default=None, ge=0)
    counts_quality: str | None = None
    #: Seasons of evidence an inferred or projected value drew on.
    seasons_used: int | None = Field(default=None, ge=0)


class Reliability(BaseModel):
    """How much of an inferred value is the player and how much is the league curve. A model
    quantity, not a probability and not a test-retest correlation (see ``docs/r9-2``)."""

    model_config = ConfigDict(frozen=True)
    #: Share of between-player ability variance resolved by this player's evidence, in [0, 1].
    reliability: float = Field(ge=0, le=1, allow_inf_nan=False)
    #: Attempts of current-season-quality evidence the posterior is equivalent to.
    effective_attempts: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    #: Where the estimate sits between the league curve (0) and the player's own record (1).
    weight_on_player_record: float = Field(ge=0, le=1, allow_inf_nan=False)
    #: The league-curve value this player's estimate is pulled toward.
    prior_mean: float = Field(allow_inf_nan=False)
    interpretation: str


class SourceRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: str
    dataset: str
    retrieved_at: datetime | None = None
    #: Identity-mapping confidence for providers that need a crosswalk (``high`` / ``medium`` / ``manual``).
    identity_confidence: str | None = None


class ModelRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    version: str
    status: Literal["active", "candidate", "retired", "rejected"]
    evaluation_status: EvaluationStatus = EvaluationStatus.UNEVALUATED
    evaluation_ref: str | None = None
    #: R9.2. One sentence on the method, and the inputs it read.
    methodology: str | None = None
    feature_definitions: list[str] = Field(default_factory=list)


class Provenance(BaseModel):
    model_config = ConfigDict(frozen=True)
    sources: list[SourceRef] = Field(default_factory=list)
    data_snapshot_id: str | None = None
    code_commit: str | None = None
    #: R9.2. What is needed to regenerate the number: seeds, training window, fitted hyperparameters.
    reproducibility: dict = Field(default_factory=dict)


class PlayerIntelligenceRecord(BaseModel):
    """One quantity about one player, with everything needed to interpret and reproduce it."""

    model_config = ConfigDict(frozen=True)

    #: Pivot's canonical player id (``players.id``), never a provider's.
    player_id: str
    quantity: str  # e.g. "ts_pct"
    unit: str
    output_kind: OutputKind
    value: float = Field(allow_inf_nan=False)
    period: Period
    #: ``projected`` only: the period the value is a forecast of.
    target_period: Period | None = None
    interval: Interval | None = None
    sample: Sample = Field(default_factory=Sample)
    #: Required for ``inferred`` and ``projected``; a measured value has no model.
    model: ModelRef | None = None
    provenance: Provenance = Field(default_factory=Provenance)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    #: R9.2. ``inferred`` only: how much of the value is the player's own record.
    reliability: Reliability | None = None
    #: R9.2. ``projected`` only, and required: the population the forecast is *conditional on*. A
    #: forecast given that a player takes a rotation role is not a forecast of his total contribution.
    conditioning: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> PlayerIntelligenceRecord:
        kind = self.output_kind
        if kind is OutputKind.PROJECTED:
            if self.target_period is None:
                raise ValueError("a projected record must name the period it forecasts")
            if self.period.as_of_season is None:
                raise ValueError("a projected record must state its information cutoff (period.as_of_season)")
            if self.period.as_of_season >= self.target_period.season:
                raise ValueError("a projection's cutoff must precede the period it forecasts")
            if not self.conditioning:
                raise ValueError(
                    "a projected record must state what it is conditional on (for example, playing a rotation role)"
                )
        elif self.target_period is not None:
            raise ValueError("only a projected record has a target period")
        if kind is not OutputKind.INFERRED and self.reliability is not None:
            raise ValueError("a reliability statement describes an inferred value")
        if kind is not OutputKind.PROJECTED and self.conditioning is not None:
            raise ValueError("only a projected record carries a conditioning statement")
        if kind is OutputKind.MEASURED:
            if self.model is not None:
                raise ValueError("a measured value is an observation, not a model output")
        elif self.model is None:
            raise ValueError(f"a {kind.value} record must name the model version that produced it")
        if kind is OutputKind.MEASURED and self.interval is not None:
            raise ValueError("a measured value has no forecast interval")
        return self

    def reliability_statement(self) -> str:
        """What can honestly be said about the reliability of this number."""
        if self.output_kind is OutputKind.MEASURED:
            return "observed value; reliability is a question of data integrity, not of estimation"
        if self.interval is None:
            return "no uncertainty is attached to this estimate"
        if self.interval.kind == "credible":
            return (
                f"a nominal {self.interval.nominal_level:.0%} credible interval for an unobserved ability under "
                f"{self.interval.method}; hyperparameters are estimated from data and treated as known, and its "
                "frequentist coverage is not claimed"
            )
        if self.interval.calibration is None:
            return (
                f"a {self.interval.nominal_level:.0%} interval from {self.interval.method}; its coverage "
                "has not been measured, so the level is a design target and not a probability"
            )
        c = self.interval.calibration
        return (
            f"a nominal {self.interval.nominal_level:.0%} interval that covered {c.empirical_coverage:.1%} of "
            f"{c.n} out-of-time outcomes across {c.folds} folds ({c.evaluation_ref})"
        )
