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
* ``ols_history``                — least squares on last season, the windowed history and age: the simple
                                   baseline with the candidates' own information.
* ``incumbent_tei``              — the production TEI, as shipped (impact_proxy only).
* ``incumbent_tei_recalibrated`` — the same ranking information put on the target's scale by OLS
                                   (every target: what does the existing Pivot estimate know about it?).

Candidates (answer "does anything beat the baselines?"):

* ``state_space``       — R9.2. Kalman filter over the player's whole history: separates the sampling
  noise of each season's statistic from the player's ability and from drift in that ability (see
  ``app.player_intelligence.rate_models``). Targets with a sampling-variance model only.
* ``state_space_role``  — the same with role covariates (rebounding, three-point and free-throw rate,
  usage at the cutoff) in the league curve.
* ``beta_binomial``     — R9.2. Discounted conjugate beta-binomial on makes and attempts (three-point and
  free-throw accuracy only).

* ``eb_shrinkage``  — exposure-aware empirical-Bayes shrinkage of the player's windowed history
  toward an age-adjusted league mean, with a persistence slope.
* ``ridge_history`` — ridge on the player's own history (lags, windowed mean, exposure, age).
* ``ridge_boxscore``— ridge on that history plus the incumbent's box-score z-features.
"""

from __future__ import annotations

import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analytics.impact import TEI_SCALE
from app.player_intelligence import rate_models as rm

from .pairs import Z_FEATURES
from .targets import TargetSpec

RIDGE_GRID = (0.1, 1.0, 10.0, 30.0, 100.0, 300.0, 1000.0)
EB_K_MULTIPLIERS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
AGE_CENTRE = 27.0


class NotApplicable(ValueError):
    """A predictor cannot run on this panel (e.g. it needs shot counts the panel does not carry).
    The backtest skips it and says so; it is never silently replaced by something else."""


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
    #: The frame columns this predictor reads — recorded on a candidate as its feature list, so the
    #: record states what the model actually used and not a guess from column names.
    inputs: tuple[str, ...] = ()

    def applies_to(self, target: TargetSpec) -> bool:
        return True

    def spec(self) -> dict:
        return {
            "name": self.name,
            "family": self.family,
            "description": self.description,
            "inputs": list(self.inputs),
        }

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
    inputs = ()
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
    inputs = ("x_t", "x_bar")
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
    inputs: tuple[str, ...] = ("x_t", "x_bar")
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
    inputs = ("x_t", "x_bar", "age")
    with_age = True


class HistoryOLS(Predictor):
    """The strongest *simple* baseline: unpenalised least squares on the candidates' own information.

    Added after the methodology review (amendment 1): the baselines used only last season and age,
    while the candidates also read the windowed history and the evidence behind it, so 'beats the
    best simple baseline' could mean no more than 'uses more than last season'. Four parameters
    and no tuning: anything that beats this has found something beyond additional history."""

    name = "ols_history"
    family = "baseline"
    inputs = ("x_t", "x_bar", "age")
    description = (
        "OLS y = a + b*last_season + c*windowed_history + d*age + e*age^2: simple regression with the same "
        "history the candidates see"
    )

    def fit(self, train: pd.DataFrame, ctx: FitContext) -> Fitted:
        fill_last = float(np.nanmean(train["x_t"].astype(float)))
        fill_bar = float(np.nanmean(train["x_bar"].astype(float)))

        def design(frame: pd.DataFrame) -> np.ndarray:
            last = _last_value(frame, fill_last)
            bar = frame["x_bar"].astype(float).fillna(pd.Series(last, index=frame.index)).fillna(fill_bar).to_numpy()
            age, age2 = _age_terms(frame)
            return np.column_stack([np.ones(len(frame)), last, bar, age, age2])

        beta = ols(design(train), _y(train))
        names = ["intercept", "last_season", "windowed_history", "age", "age_sq"]
        return _Linear(beta, design, {n: float(b) for n, b in zip(names, beta, strict=True)})


class HistoryOLSLong(Predictor):
    """``ols_history`` given the SAME amount of history the state-space models see (up to ``history`` seasons).

    Added after the R9.2 review (amendment 4): ``ols_history`` reads a three-season window while the state-space
    candidates read up to ten seasons, so a gain over it could be 'more history', not 'attempts-aware weighting'. This
    baseline closes that gap: OLS on last season, an exposure-weighted decayed mean over the *whole* available history
    (weights ``0.7**lag * exposure``, the same decay TEI and ``x_bar`` use), the windowed mean, and the age curve."""

    name = "ols_history_long"
    family = "baseline"
    inputs = ("x_t", "x_lag*", "exposure_t", "exposure_lag*", "age")
    description = (
        "OLS y = a + b*last_season + c*decayed_full_history_mean + d*windowed_history + e*age + f*age^2: the strongest simple "
        "baseline given the full per-player history the state-space candidates see"
    )

    def applies_to(self, target: TargetSpec) -> bool:
        return target.sampling_variance is not None

    @staticmethod
    def _long_mean(frame: pd.DataFrame, decay: float = 0.7) -> np.ndarray:
        h = history_length(frame)
        y = _stack(frame, "x_t", "x_lag", h)
        e = _stack(frame, "exposure_t", "exposure_lag", h)
        w = (decay ** np.arange(h))[None, :] * np.where(np.isfinite(e) & np.isfinite(y) & (e > 0), e, 0.0)
        num = (w * np.nan_to_num(y)).sum(axis=1)
        den = w.sum(axis=1)
        with np.errstate(all="ignore"):
            return np.where(den > 0, num / den, np.nan)

    def fit(self, train: pd.DataFrame, ctx: FitContext) -> Fitted:
        if "exposure_lag1" not in train.columns:
            raise NotApplicable("this panel carries no history columns")
        fill_last = float(np.nanmean(train["x_t"].astype(float)))
        fill_bar = float(np.nanmean(train["x_bar"].astype(float)))
        fill_long = float(np.nanmean(self._long_mean(train)))

        def design(frame: pd.DataFrame) -> np.ndarray:
            last = _last_value(frame, fill_last)
            bar = frame["x_bar"].astype(float).fillna(pd.Series(last, index=frame.index)).fillna(fill_bar).to_numpy()
            long = np.where(np.isfinite(self._long_mean(frame)), self._long_mean(frame), bar)
            long = np.where(np.isfinite(long), long, fill_long)
            age, age2 = _age_terms(frame)
            return np.column_stack([np.ones(len(frame)), last, long, bar, age, age2])

        beta = ols(design(train), _y(train))
        names = ["intercept", "last_season", "decayed_full_history", "windowed_history", "age", "age_sq"]
        return _Linear(beta, design, {n: float(b) for n, b in zip(names, beta, strict=True)})


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
    inputs = ("tei_window",)
    description = "The production TEI at the cutoff, as shipped (TEI / 2.5, the scale its own validation used)"

    def applies_to(self, target: TargetSpec) -> bool:
        return target.name == "impact_proxy"

    def fit(self, train: pd.DataFrame, ctx: FitContext) -> Fitted:
        return _Incumbent()


class IncumbentTEIRecalibrated(Predictor):
    name = "incumbent_tei_recalibrated"
    family = "baseline"
    inputs = ("tei_window",)
    description = (
        "OLS y = a + b*TEI: the incumbent's ranking information put on the target's own scale "
        "(applies to every target: it asks what the existing Pivot estimate knows about this quantity)"
    )

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
    inputs = ("x_bar", "window_exposure", "age")
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
    inputs = ("x_t", "x_lag1", "x_lag2", "x_bar", "window_exposure", "age")


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
    inputs = (
        "x_t", "x_lag1", "x_lag2", "x_bar", "window_exposure", "age",
        *[f"z_{c}" for c in Z_FEATURES],
    )


# ------------------------------------------------------------------------------- R9.2 candidates

#: Role covariates (z-scores at the cutoff, mean-filled with 0 = league average when absent).
ROLE_COLUMNS = ("z_REB_PCT", "z_fg3a_rate", "z_fta_rate", "z_USG_PCT")
Z_HALF = 1.2815515655446004  # 80% two-sided normal quantile


def history_length(frame: pd.DataFrame) -> int:
    h = 1
    while f"x_lag{h}" in frame.columns:
        h += 1
    return h


def _stack(frame: pd.DataFrame, stem_t: str, stem_lag: str, h: int) -> np.ndarray:
    cols = [frame[stem_t] if stem_t in frame else pd.Series(np.nan, index=frame.index)]
    for j in range(1, h):
        name = f"{stem_lag}{j}"
        cols.append(frame[name] if name in frame else pd.Series(np.nan, index=frame.index))
    return np.column_stack([pd.to_numeric(c, errors="coerce").to_numpy(dtype=float) for c in cols])


def history_arrays(frame: pd.DataFrame, role: bool, max_history: int | None = None) -> rm.HistoryArrays:
    """Stack a feature frame's lag columns into ``(n, H)`` arrays for the rate models.

    ``max_history`` keeps only the most recent seasons (the history-ablation diagnostic: does the gain come from
    attempts-aware weighting or merely from seeing more seasons than the baseline?)."""
    h = history_length(frame)
    if max_history is not None:
        h = min(h, max_history)
    y = _stack(frame, "x_t", "x_lag", h)
    s2 = _stack(frame, "sv_t", "sv_lag", h)
    e = _stack(frame, "exposure_t", "exposure_lag", h)
    age = frame["age"].astype(float).fillna(AGE_CENTRE).to_numpy()
    cov = None
    if role:
        cov = np.column_stack(
            [pd.to_numeric(frame[c], errors="coerce").fillna(0.0).to_numpy(dtype=float) if c in frame else np.zeros(len(frame)) for c in ROLE_COLUMNS]
        )
    return rm.HistoryArrays(y=y, s2=s2, exposure=e, age=age, covariates=cov)


class _StateSpaceFitted(Fitted):
    def __init__(self, params: rm.StateSpaceParams, role: bool, min_exposure: float, max_history: int | None = None) -> None:
        self.p = params
        self.role = role
        self.min_exposure = min_exposure
        self.max_history = max_history
        self.params = params.as_dict()

    def posterior(self, frame: pd.DataFrame) -> rm.Posterior:
        h = history_arrays(frame, self.role, self.max_history)
        return rm.filter_histories(h.y, h.s2, h.age, h.covariates, self.p)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return self.posterior(frame).level_next

    def ability_extras(self, frame: pd.DataFrame) -> dict[str, np.ndarray]:
        """What an ability record needs beyond the posterior: the model's true weight on the player's own record,
        his own per-attempt variance, and the attempts across all the seasons the filter used."""
        h = history_arrays(frame, self.role, self.max_history)
        usable = np.isfinite(h.y) & np.isfinite(h.s2) & (h.s2 > 0) & np.isfinite(h.exposure)
        with warnings.catch_warnings(), np.errstate(all="ignore"):
            warnings.simplefilter("ignore", RuntimeWarning)
            own = np.nanmean(np.where(usable, h.s2 * h.exposure, np.nan), axis=1)
        return {
            "weight": rm.observation_weight(h.y, h.s2, h.age, h.covariates, self.p),
            "unit_variance": np.where(np.isfinite(own), own, self.p.unit_variance),
            "history_exposure": np.where(usable, h.exposure, 0.0).sum(axis=1),
        }

    def expected_sampling_variance(self, frame: pd.DataFrame) -> np.ndarray:
        """Sampling variance of next season's statistic if the player takes about as many attempts as lately."""
        h = history_arrays(frame, self.role, self.max_history)
        with warnings.catch_warnings(), np.errstate(all="ignore"):
            warnings.simplefilter("ignore", RuntimeWarning)  # "mean of empty slice" = no history, handled below
            unit = np.nanmean(np.where(np.isfinite(h.s2) & np.isfinite(h.exposure), h.s2 * h.exposure, np.nan), axis=1)
            recent = h.exposure[:, :3]
            attempts = np.nanmean(np.where(recent > 0, recent, np.nan), axis=1)
        unit = np.where(np.isfinite(unit), unit, self.p.unit_variance)
        floor = max(self.min_exposure, 1.0)
        attempts = np.where(np.isfinite(attempts), np.maximum(attempts, floor), floor)
        return unit / attempts

    def native_interval(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        post = self.posterior(frame)
        half = Z_HALF * np.sqrt(post.var_next + self.p.phi * self.expected_sampling_variance(frame))
        return post.level_next - half, post.level_next + half


class StateSpaceRate(Predictor):
    name = "state_space"
    family = "candidate"
    inputs: tuple[str, ...] = ("x_t", "x_lag*", "sv_t", "sv_lag*", "exposure_t", "exposure_lag*", "age")
    description = (
        "Gaussian state-space (Kalman) model: observed rate = ability + sampling noise with a known "
        "attempts-based variance; ability = age curve + AR(1) deviation. Hyperparameters by one-step-ahead "
        "predictive likelihood; forecast = posterior mean of next season's ability"
    )
    role = False
    max_history: int | None = None

    def applies_to(self, target: TargetSpec) -> bool:
        return target.sampling_variance is not None

    def fit(self, train: pd.DataFrame, ctx: FitContext) -> Fitted:
        h = history_arrays(train, self.role, self.max_history)
        if not np.isfinite(h.s2[:, 0]).any() and not np.isfinite(h.s2).any():
            raise NotApplicable("this panel has no shot-count columns; build it with build_skill_panel")
        params = rm.fit_state_space(
            h.y, h.s2, h.exposure, h.age, h.covariates,
            train["y"].to_numpy(dtype=float), train["y_sv"].to_numpy(dtype=float),
        )
        return _StateSpaceFitted(params, self.role, ctx.target.min_exposure, self.max_history)


class StateSpaceRateRole(StateSpaceRate):
    name = "state_space_role"
    role = True
    inputs: tuple[str, ...] = (*StateSpaceRate.inputs, *ROLE_COLUMNS)
    description = StateSpaceRate.description + "; the league curve also depends on rebounding, three-point rate, free-throw rate and usage at the cutoff"


class StateSpaceHistory3(StateSpaceRate):
    """DIAGNOSTIC, not a candidate: ``state_space`` limited to the three seasons ``ols_history`` can see."""

    name = "state_space_h3"
    max_history = 3
    description = StateSpaceRate.description + "; restricted to the same three seasons of history the baselines see (ablation)"


class _BetaBinomialFitted(Fitted):
    def __init__(self, params: rm.BetaBinomialParams, role: bool) -> None:
        self.p = params
        self.role = role
        self.params = params.as_dict()

    def _arrays(self, frame: pd.DataFrame):
        h = history_arrays(frame, self.role)
        attempts = np.where(np.isfinite(h.exposure) & np.isfinite(h.y), h.exposure, 0.0)
        makes = np.where(attempts > 0, np.nan_to_num(h.y) * attempts, 0.0)
        return makes, attempts, h

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        makes, attempts, h = self._arrays(frame)
        alpha, beta = rm.beta_binomial_posterior(makes, attempts, h.age, h.covariates, self.p, ahead=1)
        return alpha / (alpha + beta)


class BetaBinomialRate(Predictor):
    name = "beta_binomial"
    family = "candidate"
    inputs = ("x_t", "x_lag*", "exposure_t", "exposure_lag*", "age")
    description = (
        "Discounted beta-binomial: Beta prior (mean = age curve, strength kappa) updated with the player's "
        "makes and attempts, each older season counted at a fitted fraction of the next; forecast = posterior mean"
    )
    role = False

    def applies_to(self, target: TargetSpec) -> bool:
        return target.name in ("fg3_pct", "ft_pct")

    def fit(self, train: pd.DataFrame, ctx: FitContext) -> Fitted:
        h = history_arrays(train, self.role)
        attempts = np.where(np.isfinite(h.exposure) & np.isfinite(h.y), h.exposure, 0.0)
        makes = np.where(attempts > 0, np.nan_to_num(h.y) * attempts, 0.0)
        ny = train["y_exposure"].to_numpy(dtype=float)
        params = rm.fit_beta_binomial(
            makes, attempts, h.age, h.covariates,
            train["y"].to_numpy(dtype=float) * ny, ny,
        )
        return _BetaBinomialFitted(params, self.role)


# ------------------------------------------------------------------------------- R9.2 diagnostics (opt-in)


def _log_attempts(frame: pd.DataFrame, column: str) -> np.ndarray:
    return np.log1p(frame[column].astype(float).fillna(0.0).clip(lower=0.0).to_numpy())


def _role_matrix(frame: pd.DataFrame) -> np.ndarray:
    return np.column_stack([pd.to_numeric(frame[c], errors="coerce").fillna(0.0).to_numpy(dtype=float) if c in frame else np.zeros(len(frame)) for c in ROLE_COLUMNS])


class OLSAttempts(Predictor):
    """DIAGNOSTIC baseline: ``ols_history`` that also *knows how much evidence each player has*.

    Added after the R9.2 statistical review: ``ols_history`` applies one slope to a 30-attempt and a 500-attempt player,
    so it is not the strongest simple baseline for a made/attempted rate. This adds log attempts and the interaction of
    each history input with its own attempts (nine parameters, no tuning) — the simple regression the pre-registered
    hypothesis names. ``*_role`` also carries the four role covariates the ``state_space_role`` candidates use."""

    name = "ols_attempts"
    family = "baseline"
    inputs: tuple[str, ...] = ("x_t", "x_bar", "exposure_t", "window_exposure", "age")
    description = (
        "OLS y = a + b*last + c*windowed_history + d*age + e*age^2 + f*log(window attempts) + g*log(last-season attempts) "
        "+ h*last*log(last attempts) + i*history*log(window attempts): ols_history given the evidence behind each input"
    )
    role = False

    def applies_to(self, target: TargetSpec) -> bool:
        return target.sampling_variance is not None

    def fit(self, train: pd.DataFrame, ctx: FitContext) -> Fitted:
        fill_last = float(np.nanmean(train["x_t"].astype(float)))
        fill_bar = float(np.nanmean(train["x_bar"].astype(float)))
        role = self.role

        def design(frame: pd.DataFrame) -> np.ndarray:
            last = _last_value(frame, fill_last)
            bar = frame["x_bar"].astype(float).fillna(pd.Series(last, index=frame.index)).fillna(fill_bar).to_numpy()
            age, age2 = _age_terms(frame)
            lw, lt = _log_attempts(frame, "window_exposure"), _log_attempts(frame, "exposure_t")
            cols = [np.ones(len(frame)), last, bar, age, age2, lw, lt, (last - fill_last) * lt, (bar - fill_bar) * lw]
            if role:
                return np.column_stack([*cols, _role_matrix(frame)])
            return np.column_stack(cols)

        beta = ols(design(train), _y(train))
        return _Linear(beta, design, {"beta": [float(b) for b in beta]})


class OLSAttemptsRole(OLSAttempts):
    name = "ols_attempts_role"
    role = True
    inputs = (*OLSAttempts.inputs, *ROLE_COLUMNS)


class OLSShrunk(Predictor):
    """DIAGNOSTIC baseline: OLS on empirical-Bayes-shrunk inputs (each input pulled toward the training mean by
    ``E / (E + k)``; ``k`` by leave-one-transition-out). The R9.1 ``eb_shrinkage`` idea inside a regression."""

    name = "ols_eb"
    family = "baseline"
    inputs: tuple[str, ...] = ("x_t", "x_bar", "exposure_t", "window_exposure", "age")
    description = "OLS on shrunk last season and shrunk windowed mean, age and log attempts; the shrinkage constant is tuned out of sample"
    role = False

    def applies_to(self, target: TargetSpec) -> bool:
        return target.sampling_variance is not None

    def fit(self, train: pd.DataFrame, ctx: FitContext) -> Fitted:
        role = self.role
        scale = float(np.nanmedian(train["window_exposure"].astype(float))) or 1.0
        grid = tuple(m * scale for m in (0.05, 0.1, 0.25, 0.5, 1.0, 2.0))

        def build(tr: pd.DataFrame, k: float) -> _Linear:
            centre = float(np.nanmean(tr["y"].astype(float)))

            def design(frame: pd.DataFrame) -> np.ndarray:
                e_t = frame["exposure_t"].astype(float).fillna(0.0).clip(lower=0.0).to_numpy()
                e_w = frame["window_exposure"].astype(float).fillna(0.0).to_numpy()
                x_t, x_b = frame["x_t"].astype(float).to_numpy(), frame["x_bar"].astype(float).to_numpy()
                shrunk_t = np.where(np.isfinite(x_t), centre + e_t / (e_t + k / 3.0) * (np.nan_to_num(x_t) - centre), centre)
                shrunk_b = np.where(np.isfinite(x_b), centre + e_w / (e_w + k) * (np.nan_to_num(x_b) - centre), centre)
                age, age2 = _age_terms(frame)
                cols = [np.ones(len(frame)), shrunk_t, shrunk_b, age, age2, np.log1p(e_w)]
                if role:
                    return np.column_stack([*cols, _role_matrix(frame)])
                return np.column_stack(cols)

            beta = ols(design(tr), _y(tr))
            return _Linear(beta, design, {"shrinkage_constant_k": k, "beta": [float(b) for b in beta]})

        k, _scores = loto_select(train, grid, lambda inner, val, kk: build(inner, kk).predict(val))
        return build(train, k)


class OLSShrunkRole(OLSShrunk):
    name = "ols_eb_role"
    role = True
    inputs = (*OLSShrunk.inputs, *ROLE_COLUMNS)


def fixed_variance_frame(frame: pd.DataFrame, p0: float) -> pd.DataFrame:
    """Replace every per-season sampling variance with ``p0 (1 - p0) / attempts`` — a variance that does not depend on
    the season's own observed rate (the plug-in variance of the main candidates does, which weights a lucky season more
    for a high-rate statistic and biases levels upward; see the R9.2 review, finding 4)."""
    f = frame.copy()
    h = history_length(f)
    for j in range(h):
        e = f["exposure_t" if j == 0 else f"exposure_lag{j}"].astype(float)
        x = f["x_t" if j == 0 else f"x_lag{j}"]
        f["sv_t" if j == 0 else f"sv_lag{j}"] = np.where((e > 0) & x.notna(), p0 * (1 - p0) / e.where(e > 0), np.nan)
    if "y_exposure" in f.columns:
        f["y_sv"] = np.where(f["y_exposure"].astype(float) > 0, p0 * (1 - p0) / f["y_exposure"].astype(float), np.nan)
    return f


class _FixedVarianceFitted(Fitted):
    def __init__(self, inner: _StateSpaceFitted, p0: float) -> None:
        self.inner, self.p0 = inner, p0
        self.p = inner.p
        self.params = {**inner.params, "fixed_rate_p0": p0}

    def posterior(self, frame: pd.DataFrame) -> rm.Posterior:
        return self.inner.posterior(fixed_variance_frame(frame, self.p0))

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return self.inner.predict(fixed_variance_frame(frame, self.p0))

    def native_interval(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        return self.inner.native_interval(fixed_variance_frame(frame, self.p0))


class StateSpaceFixedVariance(StateSpaceRate):
    """DIAGNOSTIC: ``state_space`` with a sampling variance that does not depend on the observed rate."""

    name = "state_space_fixedvar"
    max_history = None

    def applies_to(self, target: TargetSpec) -> bool:
        return target.name in ("fg3_pct", "ft_pct")

    def fit(self, train: pd.DataFrame, ctx: FitContext) -> Fitted:
        e = train["y_exposure"].astype(float)
        p0 = float(np.nansum(train["y"].astype(float) * e) / np.nansum(e))
        inner = super().fit(fixed_variance_frame(train, p0), ctx)
        return _FixedVarianceFitted(inner, p0)  # type: ignore[arg-type]


class StateSpaceRoleFixedVariance(StateSpaceFixedVariance):
    name = "state_space_role_fixedvar"
    role = True
    inputs = (*StateSpaceRate.inputs, *ROLE_COLUMNS)


ALL_PREDICTORS: tuple[Predictor, ...] = (
    LeagueMean(),
    Persistence(),
    RegressedPersistence(),
    RegressedPersistenceAge(),
    HistoryOLS(),
    IncumbentTEI(),
    IncumbentTEIRecalibrated(),
    EmpiricalBayesShrinkage(),
    RidgeHistory(),
    RidgeBoxscore(),
    StateSpaceRate(),
    StateSpaceRateRole(),
    BetaBinomialRate(),
)
#: Selectable by name but never part of the default sets, so the committed experiments stay reproducible: the two
#: R9.2 diagnostics: the history ablation and, after the statistical review, attempts-aware baselines and a
#: fixed-variance state-space variant (see docs/r9-2/shooting-validation-results.md §3).
DIAGNOSTIC_PREDICTORS: tuple[Predictor, ...] = (
    HistoryOLSLong(), StateSpaceHistory3(), OLSAttempts(), OLSAttemptsRole(), OLSShrunk(), OLSShrunkRole(),
    StateSpaceFixedVariance(), StateSpaceRoleFixedVariance(),
)
PREDICTORS: dict[str, Predictor] = {p.name: p for p in (*ALL_PREDICTORS, *DIAGNOSTIC_PREDICTORS)}


def predictors_for(target: TargetSpec, names: list[str] | None = None) -> list[Predictor]:
    chosen = [PREDICTORS[n] for n in names] if names else list(ALL_PREDICTORS)
    unknown = [n for n in (names or []) if n not in PREDICTORS]
    if unknown:
        raise KeyError(f"unknown model(s) {unknown}; registered: {sorted(PREDICTORS)}")
    return [p for p in chosen if p.applies_to(target)]
