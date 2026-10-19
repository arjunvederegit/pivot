"""Baselines and candidates — all small, all interpretable, all fitted on training pairs only.

A ``Predictor`` is a recipe; ``fit`` returns a ``Fitted`` that has memorised nothing but its
parameters. ``fit`` receives only the **training** frame (rows whose outcome season is at or before
the fold's cutoff). Hyper-parameters (ridge penalty, shrinkage constant) are chosen *inside* ``fit``
by leave-one-transition-out error over the training origins, so the held-out fold never influences
them.

Baselines (answer "is the candidate better than something trivial?"):

* ``league_mean``                — the training mean of the target.
* ``persistence``                — the player's own value last season, uncorrected.
* ``regressed_persistence``      — OLS ``a + b * last_season``: regression toward the mean.
* ``regressed_persistence_age``  — the same plus an age curve (``a + b*x + c*age + d*age^2``).
* ``incumbent_tei``              — the production TEI, as shipped (impact_proxy only).
* ``incumbent_tei_recalibrated`` — the same ranking information put on the target's scale by OLS.

Candidates (answer "does anything beat the baselines?"):

* ``eb_shrinkage``  — exposure-aware empirical-Bayes shrinkage of the player's windowed history
  toward an age-adjusted league mean, with a persistence slope.
* ``ridge_history`` — ridge on the player's own history (lags, windowed mean, exposure, age).
* ``ridge_boxscore``— ridge on that history plus the incumbent's box-score z-features.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analytics.impact import TEI_SCALE

from .pairs import Z_FEATURES
from .targets import TargetSpec

RIDGE_GRID = (0.1, 1.0, 10.0, 30.0, 100.0, 300.0, 1000.0)
EB_K_MULTIPLIERS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
AGE_CENTRE = 27.0


@dataclass(frozen=True)
class FitContext:
    target: TargetSpec
    seed: int = 20260719


class Fitted(ABC):
    params: dict

    @abstractmethod
    def predict(self, frame: pd.DataFrame) -> np.ndarray: ...

    def native_interval(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray] | None:
        return None


class Predictor(ABC):
    name: str
    family: str  # "baseline" | "candidate"
    description: str

    def applies_to(self, target: TargetSpec) -> bool:
        return True

    def spec(self) -> dict:
        return {"name": self.name, "family": self.family, "description": self.description}

    @abstractmethod
    def fit(self, train: pd.DataFrame, ctx: FitContext) -> Fitted: ...


# ------------------------------------------------------------------------------- helpers


def _y(train: pd.DataFrame) -> np.ndarray:
    return train["y"].to_numpy(dtype=float)


def _last_value(frame: pd.DataFrame, fallback: float) -> np.ndarray:
    """The player's most recent evidence: last season's value; failing that (a returner who missed
    the season) the windowed mean of the seasons they did play; failing that the training mean."""
    return frame["x_t"].astype(float).fillna(frame["x_bar"].astype(float)).fillna(fallback).to_numpy()


def _age_terms(frame: pd.DataFrame, fill: float = AGE_CENTRE) -> tuple[np.ndarray, np.ndarray]:
    age = frame["age"].astype(float).fillna(fill).to_numpy() - AGE_CENTRE
    return age, age**2


def ols(X: np.ndarray, y: np.ndarray, ridge: float = 1e-9) -> np.ndarray:
    """Least squares with an unpenalised first column (the intercept)."""
    penalty = np.eye(X.shape[1]) * ridge
    penalty[0, 0] = 0.0
    return np.linalg.solve(X.T @ X + penalty, X.T @ y)


def _origins(train: pd.DataFrame) -> list[str]:
    return sorted(train["origin"].unique())


def loto_select(train: pd.DataFrame, grid, fit_predict, y_col: str = "y"):
    """Pick the grid value with the lowest leave-one-transition-out MAE.

    ``fit_predict(inner_train, validation, value) -> predictions``. With a single training origin
    there is nothing to hold out, so the grid's middle value is returned (and the caller records
    that nothing was tuned)."""
    origins = _origins(train)
    if len(origins) < 2:
        return grid[len(grid) // 2], {}
    scores: dict = {}
    for value in grid:
        errors = []
        for held in origins:
            inner = train[train["origin"] != held]
            val = train[train["origin"] == held]
            pred = fit_predict(inner, val, value)
            errors.append(np.abs(pred - val[y_col].to_numpy(dtype=float)))
        scores[value] = float(np.mean(np.concatenate(errors)))
    best = min(scores, key=lambda v: (scores[v], v))
    return best, scores


# ------------------------------------------------------------------------------- baselines


class _Constant(Fitted):
    def __init__(self, value: float) -> None:
        self.value = float(value)
        self.params = {"mean": self.value}

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.full(len(frame), self.value)


class LeagueMean(Predictor):
    name = "league_mean"
    family = "baseline"
    description = "Training-period mean of the target among eligible players"

    def fit(self, train: pd.DataFrame, ctx: FitContext) -> Fitted:
        return _Constant(np.nanmean(_y(train)))


class _Persistence(Fitted):
    def __init__(self, fallback: float) -> None:
        self.params = {"fallback_mean": float(fallback)}
        self._fallback = float(fallback)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return _last_value(frame, self._fallback)


class Persistence(Predictor):
    name = "persistence"
    family = "baseline"
    description = (
        "Last season's value, uncorrected (a player who missed the season uses the mean of the seasons "
        "they played; with no evidence at all, the training mean)"
    )

    def fit(self, train: pd.DataFrame, ctx: FitContext) -> Fitted:
        return _Persistence(np.nanmean(_y(train)))


class _Linear(Fitted):
    """y = beta . design(frame), design built by ``builder``; missing inputs use ``fills``."""

    def __init__(self, beta: np.ndarray, builder, params: dict) -> None:
        self.beta = beta
        self._builder = builder
        self.params = params

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return self._builder(frame) @ self.beta


class RegressedPersistence(Predictor):
    name = "regressed_persistence"
    family = "baseline"
    description = "OLS y = a + b*last_season: regression toward the mean"
    with_age = False

    def fit(self, train: pd.DataFrame, ctx: FitContext) -> Fitted:
        fill = float(np.nanmean(train["x_t"].astype(float)))
        with_age = self.with_age

        def design(frame: pd.DataFrame) -> np.ndarray:
            cols = [np.ones(len(frame)), _last_value(frame, fill)]
            if with_age:
                cols.extend(_age_terms(frame))
            return np.column_stack(cols)

        beta = ols(design(train), _y(train))
        names = ["intercept", "slope_last_season"] + (["age", "age_sq"] if with_age else [])
        return _Linear(beta, design, {n: float(b) for n, b in zip(names, beta, strict=True)})


class RegressedPersistenceAge(RegressedPersistence):
    name = "regressed_persistence_age"
    description = "OLS y = a + b*last_season + c*age + d*age^2: persistence with an age curve"
    with_age = True


class _Incumbent(Fitted):
    def __init__(self) -> None:
        self.params = {"scale": TEI_SCALE, "note": "tei_window / TEI_SCALE, no fitting"}

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return (frame["tei_window"].astype(float) / TEI_SCALE).to_numpy()

    def native_interval(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        return (
            (frame["tei_low"].astype(float) / TEI_SCALE).to_numpy(),
            (frame["tei_high"].astype(float) / TEI_SCALE).to_numpy(),
        )


class IncumbentTEI(Predictor):
    name = "incumbent_tei"
    family = "baseline"
    description = "The production TEI at the cutoff, as shipped (TEI / 2.5, the scale its own validation used)"

    def applies_to(self, target: TargetSpec) -> bool:
        return target.name == "impact_proxy"

    def fit(self, train: pd.DataFrame, ctx: FitContext) -> Fitted:
        return _Incumbent()


class IncumbentTEIRecalibrated(Predictor):
    name = "incumbent_tei_recalibrated"
    family = "baseline"
    description = "OLS y = a + b*TEI: the incumbent's ranking information on the target's own scale"

    def applies_to(self, target: TargetSpec) -> bool:
        return target.name == "impact_proxy"

    def fit(self, train: pd.DataFrame, ctx: FitContext) -> Fitted:
        fill = float(np.nanmean(train["tei_window"].astype(float)))

        def design(frame: pd.DataFrame) -> np.ndarray:
            return np.column_stack(
                [np.ones(len(frame)), frame["tei_window"].astype(float).fillna(fill).to_numpy()]
            )

        beta = ols(design(train), _y(train))
        return _Linear(beta, design, {"intercept": float(beta[0]), "slope_per_tei_point": float(beta[1])})


# ------------------------------------------------------------------------------- candidates


class _EB(Fitted):
    def __init__(self, age_beta: np.ndarray, k: float, slope: float, tuning: dict) -> None:
        self.age_beta = age_beta
        self.k = k
        self.slope = slope
        self.params = {
            "age_curve": [float(b) for b in age_beta],
            "shrinkage_constant_k": float(k),
            "persistence_slope": float(slope),
            "k_grid_loto_mae": {str(key): float(v) for key, v in tuning.items()},
        }

    def prior(self, frame: pd.DataFrame) -> np.ndarray:
        a, a2 = _age_terms(frame)
        return np.column_stack([np.ones(len(frame)), a, a2]) @ self.age_beta

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        prior = self.prior(frame)
        exposure = frame["window_exposure"].astype(float).fillna(0.0).to_numpy()
        xbar = frame["x_bar"].astype(float).to_numpy()
        weight = exposure / (exposure + self.k)
        deviation = np.where(np.isnan(xbar), 0.0, xbar - prior)
        return prior + self.slope * weight * deviation


def _eb_fit(train: pd.DataFrame, k: float) -> tuple[np.ndarray, float]:
    a, a2 = _age_terms(train)
    design = np.column_stack([np.ones(len(train)), a, a2])
    age_beta = ols(design, _y(train))
    prior = design @ age_beta
    exposure = train["window_exposure"].astype(float).fillna(0.0).to_numpy()
    xbar = train["x_bar"].astype(float).to_numpy()
    z = exposure / (exposure + k) * np.where(np.isnan(xbar), 0.0, xbar - prior)
    denom = float(z @ z)
    slope = float(z @ (_y(train) - prior) / denom) if denom > 0 else 0.0
    return age_beta, slope


class EmpiricalBayesShrinkage(Predictor):
    name = "eb_shrinkage"
    family = "candidate"
    description = (
        "Prior = age-adjusted league mean; prediction = prior + b * E/(E+k) * (windowed history - prior), "
        "where E is the evidence (minutes or attempts) behind the history and k is chosen by "
        "leave-one-transition-out error"
    )

    def fit(self, train: pd.DataFrame, ctx: FitContext) -> Fitted:
        scale = float(np.nanmedian(train["window_exposure"].astype(float))) or 1.0
        grid = tuple(m * scale for m in EB_K_MULTIPLIERS)

        def fit_predict(inner: pd.DataFrame, val: pd.DataFrame, k: float) -> np.ndarray:
            age_beta, slope = _eb_fit(inner, k)
            return _EB(age_beta, k, slope, {}).predict(val)

        k, scores = loto_select(train, grid, fit_predict)
        age_beta, slope = _eb_fit(train, k)
        return _EB(age_beta, k, slope, scores)


class _Design:
    """Standardised feature matrix whose statistics come from the training frame only."""

    def __init__(self, columns: list[str], lag_columns: list[str]) -> None:
        self.columns = columns
        self.lag_columns = lag_columns
        self.mean: dict[str, float] = {}
        self.std: dict[str, float] = {}

    def _raw(self, frame: pd.DataFrame) -> pd.DataFrame:
        cols: dict[str, np.ndarray] = {}
        age, age2 = _age_terms(frame)
        for col in self.columns:
            if col == "age":
                cols[col] = age
            elif col == "age_sq":
                cols[col] = age2
            elif col == "log_window_exposure":
                cols[col] = np.log1p(frame["window_exposure"].astype(float).fillna(0.0).to_numpy())
            else:
                cols[col] = pd.to_numeric(frame[col], errors="coerce").to_numpy(dtype=float)
        for col in self.lag_columns:
            cols[f"missing_{col}"] = frame[col].isna().to_numpy(dtype=float)
        return pd.DataFrame(cols, index=frame.index)

    def fit(self, frame: pd.DataFrame) -> _Design:
        raw = self._raw(frame)
        for col in raw.columns:
            values = raw[col].dropna()
            self.mean[col] = float(values.mean()) if len(values) else 0.0
            sd = float(values.std(ddof=0)) if len(values) else 0.0
            self.std[col] = sd if sd > 1e-12 else 1.0
        return self

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        raw = self._raw(frame)
        cols = []
        for col in raw.columns:
            # A missing value becomes the training mean (0 after standardising), and an explicit
            # indicator column lets the model move away from that default.
            filled = raw[col].fillna(self.mean[col])
            cols.append(((filled - self.mean[col]) / self.std[col]).to_numpy())
        return np.column_stack(cols) if cols else np.zeros((len(frame), 0))


def _ridge_solve(X: np.ndarray, y: np.ndarray, alpha: float) -> tuple[float, np.ndarray]:
    mu = float(y.mean())
    gram = X.T @ X + alpha * np.eye(X.shape[1])
    return mu, np.linalg.solve(gram, X.T @ (y - mu))


class _Ridge(Fitted):
    def __init__(self, design: _Design, mu: float, w: np.ndarray, alpha: float, tuning: dict) -> None:
        self.design = design
        self.mu = mu
        self.w = w
        names = list(design.columns) + [f"missing_{c}" for c in design.lag_columns]
        self.params = {
            "alpha": float(alpha),
            "intercept": float(mu),
            "standardised_coefficients": {n: float(c) for n, c in zip(names, w, strict=True)},
            "alpha_grid_loto_mae": {str(k): float(v) for k, v in tuning.items()},
        }

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return self.mu + self.design.transform(frame) @ self.w


class RidgeModel(Predictor):
    family = "candidate"
    columns: list[str]
    #: Columns that can be missing for a returner or a newcomer; each gets an explicit indicator.
    lag_columns = ["x_t", "x_lag1", "x_lag2"]

    def fit(self, train: pd.DataFrame, ctx: FitContext) -> Fitted:
        def fit_predict(inner: pd.DataFrame, val: pd.DataFrame, alpha: float) -> np.ndarray:
            design = _Design(self.columns, self.lag_columns).fit(inner)
            mu, w = _ridge_solve(design.transform(inner), _y(inner), alpha)
            return mu + design.transform(val) @ w

        alpha, scores = loto_select(train, RIDGE_GRID, fit_predict)
        design = _Design(self.columns, self.lag_columns).fit(train)
        mu, w = _ridge_solve(design.transform(train), _y(train), alpha)
        return _Ridge(design, mu, w, alpha, scores)


class RidgeHistory(RidgeModel):
    name = "ridge_history"
    description = "Ridge on the player's own history only: last 3 values, windowed mean, evidence, age curve"
    columns = ["x_t", "x_lag1", "x_lag2", "x_bar", "log_window_exposure", "age", "age_sq"]


class RidgeBoxscore(RidgeModel):
    name = "ridge_boxscore"
    description = (
        "Ridge on that history plus the incumbent's box-score z-features (does other box-score "
        "information improve the forecast of this target?)"
    )
    columns = [
        "x_t", "x_lag1", "x_lag2", "x_bar", "log_window_exposure", "age", "age_sq",
        *[f"z_{c}" for c in Z_FEATURES],
    ]


ALL_PREDICTORS: tuple[Predictor, ...] = (
    LeagueMean(),
    Persistence(),
    RegressedPersistence(),
    RegressedPersistenceAge(),
    IncumbentTEI(),
    IncumbentTEIRecalibrated(),
    EmpiricalBayesShrinkage(),
    RidgeHistory(),
    RidgeBoxscore(),
)
PREDICTORS: dict[str, Predictor] = {p.name: p for p in ALL_PREDICTORS}


def predictors_for(target: TargetSpec, names: list[str] | None = None) -> list[Predictor]:
    chosen = [PREDICTORS[n] for n in names] if names else list(ALL_PREDICTORS)
    unknown = [n for n in (names or []) if n not in PREDICTORS]
    if unknown:
        raise KeyError(f"unknown model(s) {unknown}; registered: {sorted(PREDICTORS)}")
    return [p for p in chosen if p.applies_to(target)]
