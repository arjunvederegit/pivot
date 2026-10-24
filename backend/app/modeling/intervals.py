"""Prediction intervals, calibrated on held-out *training* transitions only.

The method is deliberately plain: take the residuals a model makes on *held-out training
transitions* (leave-one-transition-out: each residual is out-of-sample for the model that made it,
but this is cross-validation, not forward chaining — a held-out transition can be earlier than some
of the data its model was fitted on), model their spread as a function of the evidence behind the
estimate,

    s^2(E) = a + b / E            (E = window exposure; a, b >= 0, least squares on squared residuals)

and set the half-width to the empirical ``level`` quantile of ``|residual| / s(E)`` times ``s(E)``.
No distributional assumption is made beyond the spread-by-evidence shape.

What the interval is, and is not:

* It is a **forecast interval for the next-season observed value**, so it includes the player's
  true year-to-year change *and* the noise in a single season's statistic.
* The ``level`` is **nominal**. Whether it is delivered is an empirical question answered by the
  backtest on the held-out *test* folds (coverage per fold and per subgroup) — that, not the
  calibration, is the out-of-time evidence. Every model shares this one calibrator, so pooled
  coverage near the level is expected of all of them and says little about a particular model;
  mean width at that coverage is what distinguishes them.
* It describes the **evaluated population** only (players who played at least the minimum next
  season); it makes no statement about players who do not.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .pairs import features_only
from .predictors import FitContext, Predictor


@dataclass(frozen=True)
class IntervalModel:
    a: float
    b: float
    quantile: float
    level: float
    residuals_used: int
    exposure_floor: float

    def half_width(self, exposure: np.ndarray) -> np.ndarray:
        e = np.maximum(np.nan_to_num(exposure, nan=self.exposure_floor), self.exposure_floor)
        return self.quantile * np.sqrt(np.maximum(self.a + self.b / e, 1e-12))

    def apply(self, pred: np.ndarray, exposure: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h = self.half_width(exposure)
        return pred - h, pred + h

    def params(self) -> dict:
        return {
            "method": "leave-one-transition-out residuals; spread a + b/E; empirical quantile of |r|/s",
            "a": self.a,
            "b": self.b,
            "quantile_multiplier": self.quantile,
            "nominal_level": self.level,
            "residuals_used": self.residuals_used,
        }


def loto_residuals(predictor: Predictor, train: pd.DataFrame, ctx: FitContext) -> pd.DataFrame:
    """Out-of-sample residuals: each training origin predicted by a model fitted on the others."""
    origins = sorted(train["origin"].unique())
    parts = []
    if len(origins) < 2:
        fitted = predictor.fit(train, ctx)
        pred = fitted.predict(features_only(train))
        return pd.DataFrame(
            {"resid": train["y"].to_numpy(dtype=float) - pred, "exposure": train["window_exposure"].to_numpy(dtype=float)}
        )
    for held in origins:
        inner = train[train["origin"] != held]
        val = train[train["origin"] == held]
        pred = predictor.fit(inner, ctx).predict(features_only(val))
        parts.append(
            pd.DataFrame(
                {
                    "resid": val["y"].to_numpy(dtype=float) - pred,
                    "exposure": val["window_exposure"].to_numpy(dtype=float),
                }
            )
        )
    return pd.concat(parts, ignore_index=True)


def fit_interval(residuals: pd.DataFrame, level: float = 0.8) -> IntervalModel:
    r = residuals["resid"].to_numpy(dtype=float)
    e = residuals["exposure"].to_numpy(dtype=float)
    ok = np.isfinite(r) & np.isfinite(e) & (e > 0)
    r, e = r[ok], e[ok]
    if len(r) == 0:
        raise ValueError("no finite out-of-sample residuals to calibrate on")
    floor = float(np.percentile(e, 5)) if len(e) else 1.0
    e = np.maximum(e, floor)
    design = np.column_stack([np.ones_like(e), 1.0 / e])
    coef, *_ = np.linalg.lstsq(design, r**2, rcond=None)
    a, b = max(float(coef[0]), 0.0), max(float(coef[1]), 0.0)
    if a + b == 0.0:
        a = float(np.mean(r**2))
    s = np.sqrt(np.maximum(a + b / e, 1e-12))
    q = float(np.quantile(np.abs(r) / s, level))
    return IntervalModel(a=a, b=b, quantile=q, level=level, residuals_used=int(len(r)), exposure_floor=floor)
