"""Statistical models for shooting rates: sampling noise, ability, and change in ability kept apart.

Everything here is plain NumPy. Nothing imports the modeling harness, so a model can be tested on
simulated data with a known answer, and the harness, the service and the tests all call the same code.

The observation model
---------------------
A player-season statistic ``y`` (true shooting %, three-point %, free-throw %) is a noisy
measurement of the player's ability ``theta`` in that season:

    y = theta + e,        e ~ N(0, phi * s2)

``s2`` is the **sampling variance** the statistic would have given the attempts actually taken (see
``modeling.targets``): binomial ``p(1-p)/n`` for made/attempted rates, and the delta-method variance
of a *ratio estimator* for true shooting (which is not made/attempted). ``phi`` is a dispersion
multiplier estimated from the data: ``phi = 1`` would mean the independent-attempts model is exactly
right, ``phi > 1`` that attempts are positively dependent or shot quality varies in a way the
approximation misses.

The ability model
-----------------
    theta_t = m(age_t, role_t) + u_t,       u_{t+1} = rho * u_t + eta,   eta ~ N(0, omega2)
    omega2 = tau2 * (1 - rho^2)             # so u is stationary with variance tau2 across players

``m`` is a league-level curve (quadratic in age, plus optional role covariates known at the
cutoff). ``tau2`` is the between-player variance of ability at a given age, ``rho`` the season-to-
season persistence of a player's *true* deviation from the curve (``rho = 1`` would be a fixed
talent; ``rho < 1`` lets ability drift and mean-revert). A Kalman filter over a player's seasons
gives the posterior of ability **now** (``u_hat``, ``P``) and, one step on, the forecast of ability
**next season** (``rho * u_hat``, ``rho^2 * P + omega2``).

Estimation
----------
The parameters ``(beta, rho, tau2, phi)`` are fitted by maximising the **one-step-ahead predictive
likelihood** of the next-season observation over training pairs: each pair's history is filtered,
the filter's predictive distribution for the following season is formed, and the realised value is
scored under it. That is the same distribution used to forecast, scored the way it is used, and it
only ever conditions on information available at the pair's cutoff.

This is an *empirical Bayes* method: the hyperparameters are estimated from data and then treated as
known, so the resulting credible intervals do not include hyperparameter uncertainty. They are
Bayesian credible intervals under the model, not frequentist confidence intervals, and any statement
about their coverage must come from out-of-time validation (see ``docs/r9-2``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import optimize, special, stats

AGE_CENTRE = 27.0
MIN_RHO, MAX_RHO = 0.05, 0.999
MIN_TAU2, MAX_TAU2 = 1e-8, 1.0
MIN_PHI, MAX_PHI = 0.05, 10.0
LOG_2PI = float(np.log(2 * np.pi))


@dataclass(frozen=True)
class StateSpaceParams:
    beta: tuple[float, ...]  # mean curve: intercept, age, age^2, then role covariates
    rho: float
    tau2: float
    phi: float
    n_pairs: int = 0
    loglik: float = float("nan")
    converged: bool = True
    #: Mean of (unit variance = s2 * exposure) in training; the fallback for a player with no history.
    unit_variance: float = float("nan")
    n_covariates: int = 0

    @property
    def omega2(self) -> float:
        return self.tau2 * (1.0 - self.rho**2)

    @property
    def at_bound(self) -> list[str]:
        """Parameters sitting on (or within 0.1% of) a bound of the search. ``converged`` is the optimiser's own flag
        and stays true there because the logistic/log re-parametrisation flattens the gradient; a parameter on a bound
        is *not identified* by the data, and is reported as such."""
        out = []
        if self.rho >= MAX_RHO - 1e-3 * (MAX_RHO - MIN_RHO) or self.rho <= MIN_RHO + 1e-3:
            out.append("rho")
        if self.tau2 >= MAX_TAU2 * 0.999 or self.tau2 <= MIN_TAU2 * 1.001:
            out.append("tau2")
        if self.phi >= MAX_PHI * 0.999 or self.phi <= MIN_PHI * 1.001:
            out.append("phi")
        return out

    def as_dict(self) -> dict:
        return {
            "beta": list(self.beta), "rho": self.rho, "tau2": self.tau2, "tau": float(np.sqrt(self.tau2)),
            "omega2": self.omega2, "phi": self.phi, "n_pairs": self.n_pairs, "loglik": self.loglik,
            "converged": self.converged, "at_bound": self.at_bound, "unit_variance": self.unit_variance,
            "n_covariates": self.n_covariates,
        }


@dataclass(frozen=True)
class Posterior:
    """Per-row posterior of ability and the one-step forecast. All arrays have length ``n``."""

    level_now: np.ndarray  # E[theta_T]
    var_now: np.ndarray  # Var[theta_T | history]
    level_next: np.ndarray  # E[theta_{T+1}]
    var_next: np.ndarray  # Var[theta_{T+1} | history]
    curve_now: np.ndarray  # m(age_T): the league curve this player is shrunk toward
    curve_next: np.ndarray
    n_observed: np.ndarray  # seasons with usable evidence

    def credible_interval(self, which: str, level: float) -> tuple[np.ndarray, np.ndarray]:
        z = float(stats.norm.ppf(0.5 + level / 2))
        centre, var = (self.level_now, self.var_now) if which == "now" else (self.level_next, self.var_next)
        half = z * np.sqrt(var)
        return centre - half, centre + half


def _design(age: np.ndarray, covariates: np.ndarray | None) -> np.ndarray:
    a = age - AGE_CENTRE
    cols = [np.ones_like(a), a, a**2]
    X = np.column_stack(cols)
    if covariates is not None and covariates.shape[1]:
        X = np.column_stack([X, covariates])
    return X


def filter_histories(
    y: np.ndarray,
    s2: np.ndarray,
    age: np.ndarray,
    covariates: np.ndarray | None,
    params: StateSpaceParams,
) -> Posterior:
    """Kalman-filter each row's history. ``y`` and ``s2`` are ``(n, H)``; column ``j`` is ``j``
    seasons before the cutoff (``j = 0`` is the cutoff season). NaN in ``y`` or ``s2`` = no evidence
    that season (the filter still ages the state). ``age`` is the age at the cutoff, length ``n``."""
    n, H = y.shape
    beta = np.asarray(params.beta, dtype=float)
    rho, tau2, phi = params.rho, params.tau2, params.phi
    omega2 = params.omega2
    cov = covariates if covariates is not None else np.zeros((n, 0))
    u = np.zeros(n)
    P = np.full(n, tau2)
    started = np.zeros(n, dtype=bool)
    n_obs = np.zeros(n, dtype=int)
    for j in range(H - 1, -1, -1):
        m_j = _design(age - j, cov) @ beta
        obs = np.isfinite(y[:, j]) & np.isfinite(s2[:, j]) & (s2[:, j] > 0)
        # Age the state one season (skipped before the player's first observation: the stationary
        # prior N(0, tau2) already describes a player we have not yet seen).
        step = started
        u = np.where(step, rho * u, u)
        P = np.where(step, rho**2 * P + omega2, P)
        r = np.where(obs, phi * np.where(obs, s2[:, j], 1.0), 1.0)
        S = P + r
        K = np.where(obs, P / S, 0.0)
        innov = np.where(obs, y[:, j] - m_j - u, 0.0)
        u = u + K * innov
        P = np.where(obs, (1.0 - K) * P, P)
        started = started | obs
        n_obs += obs
    curve_now = _design(age, cov) @ beta
    curve_next = _design(age + 1.0, cov) @ beta
    u_next = np.where(started, rho * u, 0.0)
    P_next = np.where(started, rho**2 * P + omega2, tau2)
    u_now = np.where(started, u, 0.0)
    P_now = np.where(started, P, tau2)
    return Posterior(
        level_now=curve_now + u_now, var_now=P_now,
        level_next=curve_next + u_next, var_next=P_next,
        curve_now=curve_now, curve_next=curve_next, n_observed=n_obs,
    )


def observation_weight(
    y: np.ndarray, s2: np.ndarray, age: np.ndarray, covariates: np.ndarray | None, params: StateSpaceParams
) -> np.ndarray:
    """The model's total weight on a player's own observations: how far the posterior mean moves if every
    observation he has is shifted by one unit (the filter is linear in ``y``). ``1 - P/tau^2`` (the reliability)
    equals it only when the cutoff season itself is observed; with older evidence the state has decayed and the
    two differ."""
    usable = np.isfinite(y) & np.isfinite(s2) & (s2 > 0)
    base = filter_histories(y, s2, age, covariates, params).level_now
    shifted = filter_histories(np.where(usable, y + 1.0, y), s2, age, covariates, params).level_now
    return shifted - base


def _unpack(theta: np.ndarray, n_cov: int) -> StateSpaceParams:
    beta = tuple(float(b) for b in theta[: 3 + n_cov])
    rho = MIN_RHO + (MAX_RHO - MIN_RHO) * float(special.expit(theta[3 + n_cov]))
    tau2 = float(np.clip(np.exp(theta[4 + n_cov]), MIN_TAU2, MAX_TAU2))
    phi = float(np.clip(np.exp(theta[5 + n_cov]), MIN_PHI, MAX_PHI))
    return StateSpaceParams(beta=beta, rho=rho, tau2=tau2, phi=phi, n_covariates=n_cov)


def predictive_loglik(
    post: Posterior, params: StateSpaceParams, y_next: np.ndarray, s2_next: np.ndarray
) -> np.ndarray:
    """Per-row log density of the realised next-season value under the filter's forecast."""
    V = post.var_next + params.phi * s2_next
    return -0.5 * (LOG_2PI + np.log(V) + (y_next - post.level_next) ** 2 / V)


def fit_state_space(
    y: np.ndarray,
    s2: np.ndarray,
    exposure: np.ndarray,
    age: np.ndarray,
    covariates: np.ndarray | None,
    y_next: np.ndarray,
    s2_next: np.ndarray,
    *,
    ridge_beta: float = 1e-6,
) -> StateSpaceParams:
    """Maximum predictive likelihood over training pairs. Deterministic (fixed starts, no sampling)."""
    ok = np.isfinite(y_next) & np.isfinite(s2_next) & (s2_next > 0) & np.isfinite(age)
    if int(ok.sum()) < 40:
        raise ValueError(f"{int(ok.sum())} usable training pairs; at least 40 are needed")
    y, s2, age, y_next, s2_next = y[ok], s2[ok], age[ok], y_next[ok], s2_next[ok]
    cov = covariates[ok] if covariates is not None else None
    n_cov = 0 if cov is None else cov.shape[1]
    X = _design(age + 1.0, cov)
    beta0 = np.linalg.lstsq(X, y_next, rcond=None)[0]
    resid_var = float(np.var(y_next - X @ beta0))
    mean_s2 = float(np.nanmean(s2_next))

    def objective(theta: np.ndarray) -> float:
        params = _unpack(theta, n_cov)
        post = filter_histories(y, s2, age, cov, params)
        ll = predictive_loglik(post, params, y_next, s2_next)
        beta = np.asarray(params.beta)
        penalty = ridge_beta * float(beta[1:] @ beta[1:]) if len(beta) > 1 else 0.0
        return -float(ll.sum()) + penalty

    best = None
    for rho0 in (0.6, 0.9):
        tau2_0 = max(resid_var - mean_s2, resid_var * 0.2, 1e-5)
        start = np.concatenate([beta0, [special.logit((rho0 - MIN_RHO) / (MAX_RHO - MIN_RHO)), np.log(tau2_0), 0.0]])
        res = optimize.minimize(objective, start, method="L-BFGS-B", options={"maxiter": 400, "maxfun": 20000})
        if best is None or res.fun < best.fun:
            best = res
    assert best is not None
    params = _unpack(best.x, n_cov)
    unit = np.nanmean(s2[:, 0] * exposure[:, 0]) if np.isfinite(s2[:, 0] * exposure[:, 0]).any() else float(np.nanmean(s2_next))
    return StateSpaceParams(
        beta=params.beta, rho=params.rho, tau2=params.tau2, phi=params.phi, n_pairs=int(ok.sum()),
        loglik=-float(best.fun), converged=bool(best.success), unit_variance=float(unit), n_covariates=n_cov,
    )


# ------------------------------------------------------------------------ reliability statements


def reliability(var_now: np.ndarray, tau2: float) -> np.ndarray:
    """Share of between-player ability variance that the evidence has resolved: ``1 - P/tau2``.

    0 = nothing known beyond the league curve; 1 = ability known exactly. This is a model quantity
    (a property of the fitted variance components and this player's evidence), not a measured
    test-retest correlation; the measured analogue is reported in the validation."""
    return np.clip(1.0 - var_now / tau2, 0.0, 1.0)


def effective_attempts(var_now: np.ndarray, tau2: float, phi: float, unit_variance: float) -> np.ndarray:
    """The attempts of current-season-quality evidence that would give the same posterior variance.

    ``1/P = 1/tau2 + n_eff / (phi * v)``, with ``v`` the per-attempt variance of the statistic
    (``unit_variance``), so ``n_eff = phi * v * (1/P - 1/tau2)``."""
    return np.maximum(phi * unit_variance * (1.0 / var_now - 1.0 / tau2), 0.0)


# ------------------------------------------------------------------ discounted beta-binomial


@dataclass(frozen=True)
class BetaBinomialParams:
    beta: tuple[float, ...]  # logit-scale mean curve: intercept, age, age^2, covariates
    kappa: float  # prior strength in attempts (alpha + beta)
    discount: float  # weight of a season relative to the one after it
    n_pairs: int = 0
    loglik: float = float("nan")
    converged: bool = True
    n_covariates: int = 0

    def as_dict(self) -> dict:
        return {
            "beta_logit": list(self.beta), "kappa": self.kappa, "discount": self.discount,
            "n_pairs": self.n_pairs, "loglik": self.loglik, "converged": self.converged,
            "n_covariates": self.n_covariates,
        }


def beta_binomial_posterior(
    makes: np.ndarray, attempts: np.ndarray, age: np.ndarray, covariates: np.ndarray | None,
    params: BetaBinomialParams, ahead: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Posterior (alpha, beta) of a player's rate: the prior Beta(mu*kappa, (1-mu)*kappa) updated with
    discounted counts. Column ``j`` of ``makes`` / ``attempts`` is ``j`` seasons before the cutoff;
    ``ahead`` is how many seasons beyond the cutoff the prior mean's age refers to."""
    n, H = attempts.shape
    cov = covariates if covariates is not None else np.zeros((n, 0))
    mu = special.expit(_design(age + ahead, cov) @ np.asarray(params.beta))
    w = params.discount ** np.arange(H, dtype=float)  # j = 0 (the cutoff season) has weight 1
    m = np.nan_to_num(makes, nan=0.0) * w
    a = np.nan_to_num(attempts, nan=0.0) * w
    alpha = mu * params.kappa + m.sum(axis=1)
    beta = (1.0 - mu) * params.kappa + (a - m).sum(axis=1)
    return alpha, beta


def fit_beta_binomial(
    makes: np.ndarray, attempts: np.ndarray, age: np.ndarray, covariates: np.ndarray | None,
    next_makes: np.ndarray, next_attempts: np.ndarray,
) -> BetaBinomialParams:
    """Maximise the beta-binomial predictive likelihood of next season's makes given its attempts."""
    ok = np.isfinite(next_makes) & np.isfinite(next_attempts) & (next_attempts > 0) & np.isfinite(age)
    if int(ok.sum()) < 40:
        raise ValueError(f"{int(ok.sum())} usable training pairs; at least 40 are needed")
    makes, attempts, age = makes[ok], attempts[ok], age[ok]
    nm, na = next_makes[ok], next_attempts[ok]
    cov = covariates[ok] if covariates is not None else None
    n_cov = 0 if cov is None else cov.shape[1]

    def unpack(theta: np.ndarray) -> BetaBinomialParams:
        beta = tuple(float(b) for b in theta[: 3 + n_cov])
        kappa = float(np.clip(np.exp(theta[3 + n_cov]), 1.0, 1e5))
        disc = 0.05 + 0.95 * float(special.expit(theta[4 + n_cov]))
        return BetaBinomialParams(beta=beta, kappa=kappa, discount=disc, n_covariates=n_cov)

    def objective(theta: np.ndarray) -> float:
        p = unpack(theta)
        al, be = beta_binomial_posterior(makes, attempts, age, cov, p, ahead=1)
        ll = (
            special.gammaln(na + 1) - special.gammaln(nm + 1) - special.gammaln(na - nm + 1)
            + special.betaln(nm + al, na - nm + be) - special.betaln(al, be)
        )
        return -float(ll.sum())

    base_rate = float(np.sum(nm) / np.sum(na))
    best = None
    for kappa0, disc0 in ((60.0, 0.7), (150.0, 0.9)):
        start = np.concatenate([[special.logit(base_rate), 0.0, 0.0], np.zeros(n_cov), [np.log(kappa0), special.logit((disc0 - 0.05) / 0.95)]])
        res = optimize.minimize(objective, start, method="L-BFGS-B", options={"maxiter": 400, "maxfun": 20000})
        if best is None or res.fun < best.fun:
            best = res
    assert best is not None
    p = unpack(best.x)
    return BetaBinomialParams(
        beta=p.beta, kappa=p.kappa, discount=p.discount, n_pairs=int(ok.sum()), loglik=-float(best.fun),
        converged=bool(best.success), n_covariates=n_cov,
    )


@dataclass
class HistoryArrays:
    """The columns of a harness feature frame, stacked into ``(n, H)`` arrays (lag 0 = cutoff)."""

    y: np.ndarray
    s2: np.ndarray
    exposure: np.ndarray
    age: np.ndarray
    covariates: np.ndarray | None
    extras: dict = field(default_factory=dict)
