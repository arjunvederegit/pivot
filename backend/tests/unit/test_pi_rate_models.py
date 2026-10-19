"""R9.2 — the rate models recover a planted answer, and refuse to invent evidence.

The synthetic league plants a known between-player spread, persistence and sampling process, so a
test can assert that the fitted model finds them, that its predictive distribution is calibrated, and
that it behaves sensibly on the cases that matter: few attempts, none at all, a long record."""

import numpy as np
import pytest

from app.player_intelligence import rate_models as rm


def simulate(n=2500, T=8, rho=0.85, tau=0.035, mu=0.36, seed=3, H=6):
    rng = np.random.default_rng(seed)
    ages = rng.integers(20, 34, n).astype(float)
    omega = tau * np.sqrt(1 - rho**2)
    u = rng.normal(0, tau, n)
    ys = np.full((n, T), np.nan)
    s2 = np.full((n, T), np.nan)
    ex = np.zeros((n, T))
    for t in range(T):
        if t > 0:
            u = rho * u + rng.normal(0, omega, n)
        a = rng.integers(20, 450, n).astype(float)
        p = np.clip(mu + u - 0.0004 * ((ages + t - 27) ** 2), 0.05, 0.9)
        m = rng.binomial(a.astype(int), p)
        ys[:, t], ex[:, t] = m / a, a
        pt = (m + 0.5) / (a + 1)
        s2[:, t] = pt * (1 - pt) / a
    parts = {k: [] for k in ("Y", "S", "E", "A", "yn", "sn")}
    for t in range(1, T - 1):
        def lag(arr, fill):
            return np.stack([arr[:, t - j] if t - j >= 0 else np.full(n, fill) for j in range(H)], axis=1)
        parts["Y"].append(lag(ys, np.nan)); parts["S"].append(lag(s2, np.nan)); parts["E"].append(lag(ex, 0.0))
        parts["A"].append(ages + t); parts["yn"].append(ys[:, t + 1]); parts["sn"].append(s2[:, t + 1])
    return {k: (np.vstack(v) if k in ("Y", "S", "E") else np.concatenate(v)) for k, v in parts.items()}


@pytest.fixture(scope="module")
def fitted():
    d = simulate()
    params = rm.fit_state_space(d["Y"], d["S"], d["E"], d["A"], None, d["yn"], d["sn"])
    return d, params


def test_the_planted_variance_components_are_recovered(fitted):
    _, p = fitted
    assert p.converged
    assert np.sqrt(p.tau2) == pytest.approx(0.035, rel=0.1)
    assert p.rho == pytest.approx(0.85, abs=0.06)
    assert p.phi == pytest.approx(1.0, abs=0.08)  # independent binomial attempts: no extra dispersion


def test_the_predictive_distribution_is_calibrated_out_of_sample(fitted):
    d, p = fitted
    fresh = simulate(seed=11)  # a different league: nothing was fitted on it
    post = rm.filter_histories(fresh["Y"], fresh["S"], fresh["A"], None, p)
    z = (fresh["yn"] - post.level_next) / np.sqrt(post.var_next + p.phi * fresh["sn"])
    assert np.mean(np.abs(z) < 1.2816) == pytest.approx(0.80, abs=0.03)


def test_few_attempts_means_more_shrinkage_than_many(fitted):
    _, p = fitted
    H = 6
    y = np.full((2, H), np.nan)
    s2 = np.full((2, H), np.nan)
    # Player 0: 42% on 30 attempts. Player 1: 39% on 500 attempts. Same age, one season each.
    y[0, 0], s2[0, 0] = 0.42, (0.42 * 0.58) / 30
    y[1, 0], s2[1, 0] = 0.39, (0.39 * 0.61) / 500
    post = rm.filter_histories(y, s2, np.array([27.0, 27.0]), None, p)
    assert post.level_now[1] > post.level_now[0], "500 attempts at 39% must outrank 30 attempts at 42%"
    assert post.var_now[0] > post.var_now[1]
    rel = rm.reliability(post.var_now, p.tau2)
    assert rel[1] > rel[0]
    # the thin-sample player is pulled most of the way to the league curve
    curve = post.curve_now[0]
    assert abs(post.level_now[0] - curve) < 0.5 * abs(0.42 - curve)


def test_a_player_with_no_evidence_gets_the_league_curve_and_no_certainty(fitted):
    _, p = fitted
    y = np.full((1, 6), np.nan)
    s2 = np.full((1, 6), np.nan)
    post = rm.filter_histories(y, s2, np.array([24.0]), None, p)
    assert post.level_now[0] == post.curve_now[0]
    assert post.var_now[0] == pytest.approx(p.tau2)  # prior variance: nothing learned
    assert rm.reliability(post.var_now, p.tau2)[0] == pytest.approx(0.0)
    assert post.n_observed[0] == 0


def test_zero_attempts_in_a_season_is_missing_evidence_not_a_zero_rate(fitted):
    _, p = fitted
    y = np.full((2, 6), np.nan)
    s2 = np.full((2, 6), np.nan)
    y[0, 1], s2[0, 1] = 0.38, 0.38 * 0.62 / 200
    y[1, 1], s2[1, 1] = 0.38, 0.38 * 0.62 / 200
    y[1, 0], s2[1, 0] = 0.0, np.nan  # a season with no attempts: rate undefined, variance undefined
    post = rm.filter_histories(y, s2, np.array([27.0, 27.0]), None, p)
    assert post.level_now[0] > 0.2 and post.level_now[1] > 0.2
    assert post.n_observed.tolist() == [1, 1]


def test_a_longer_record_is_more_reliable_than_a_single_season(fitted):
    _, p = fitted
    y = np.full((2, 6), np.nan)
    s2 = np.full((2, 6), np.nan)
    y[0, 0], s2[0, 0] = 0.38, 0.38 * 0.62 / 150
    for j in range(5):
        y[1, j], s2[1, j] = 0.38, 0.38 * 0.62 / 150
    post = rm.filter_histories(y, s2, np.array([27.0, 27.0]), None, p)
    assert post.var_now[1] < post.var_now[0]
    ne = rm.effective_attempts(post.var_now, p.tau2, p.phi, p.unit_variance)
    assert ne[1] > ne[0] > 0


def test_extreme_observed_rates_do_not_break_the_filter(fitted):
    _, p = fitted
    y = np.array([[1.0, np.nan, np.nan, np.nan, np.nan, np.nan], [0.0, np.nan, np.nan, np.nan, np.nan, np.nan]])
    pt = np.array([(3 + .5) / 4, (0 + .5) / 4])  # 3/3 and 0/3 attempts, half-count smoothed
    s2 = np.full((2, 6), np.nan)
    s2[:, 0] = pt * (1 - pt) / 3
    post = rm.filter_histories(y, s2, np.array([25.0, 25.0]), None, p)
    assert np.isfinite(post.level_now).all() and np.isfinite(post.var_now).all()
    assert (np.abs(post.level_now - post.curve_now) < 0.05).all()  # three attempts barely move it


def test_credible_intervals_widen_with_less_evidence(fitted):
    _, p = fitted
    y = np.full((2, 6), np.nan)
    s2 = np.full((2, 6), np.nan)
    y[0, 0], s2[0, 0] = 0.4, 0.4 * 0.6 / 40
    y[1, 0], s2[1, 0] = 0.4, 0.4 * 0.6 / 600
    lo, hi = rm.filter_histories(y, s2, np.array([27.0, 27.0]), None, p).credible_interval("now", 0.8)
    assert (hi - lo)[0] > (hi - lo)[1]


def test_beta_binomial_posterior_is_the_conjugate_update():
    p = rm.BetaBinomialParams(beta=(0.0, 0.0, 0.0), kappa=100.0, discount=0.5)
    makes = np.array([[30.0, 20.0]])
    attempts = np.array([[100.0, 100.0]])
    alpha, beta = rm.beta_binomial_posterior(makes, attempts, np.array([27.0]), None, p)
    # prior mean sigmoid(0) = 0.5 -> Beta(50, 50); counts: 30 + 0.5*20 makes, 100 + 50 attempts
    assert alpha[0] == pytest.approx(50 + 30 + 10)
    assert beta[0] == pytest.approx(50 + (100 - 30) + 0.5 * (100 - 20))


def test_beta_binomial_fit_recovers_the_prior_strength():
    rng = np.random.default_rng(5)
    n, kappa, mu = 4000, 120.0, 0.35
    p_true = rng.beta(mu * kappa, (1 - mu) * kappa, n)
    a1, a2 = rng.integers(30, 400, n).astype(float), rng.integers(30, 400, n).astype(float)
    m1, m2 = rng.binomial(a1.astype(int), p_true).astype(float), rng.binomial(a2.astype(int), p_true).astype(float)
    makes = np.column_stack([m1, np.zeros(n)])
    attempts = np.column_stack([a1, np.zeros(n)])
    fit = rm.fit_beta_binomial(makes, attempts, np.full(n, 27.0), None, m2, a2)
    assert fit.kappa == pytest.approx(kappa, rel=0.25)
    assert fit.discount > 0.7  # unchanged ability: old evidence should not be discounted away


def test_too_little_training_data_is_refused():
    d = simulate(n=10, T=4)
    with pytest.raises(ValueError, match="at least 40"):
        rm.fit_state_space(d["Y"], d["S"], d["E"], d["A"], None, d["yn"], d["sn"])
