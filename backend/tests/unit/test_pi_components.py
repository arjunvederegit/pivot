"""R9.2 — integer shot counts recovered from NBA.com's rounded per-game figures and published rates.

Every case here is generated: a known integer truth is rounded exactly as the provider rounds it, and
the reconstruction must recover it (or, when the provider's rounding genuinely leaves it ambiguous,
must say so and must contain the truth in the range it reports)."""

import math

import numpy as np
import pandas as pd
import pytest

from app.player_intelligence.components import (
    QUALITY_BOUNDED,
    QUALITY_EXACT,
    QUALITY_UNCONSTRAINED,
    components_frame,
    reconstruct,
)

from .pi_support import provider_rows


def test_a_typical_season_is_recovered_exactly():
    base, adv = provider_rows(gp=70, fgm=480, fga=1010, fg3m=254, fg3a=694, ftm=503, fta=645)
    r = reconstruct(base, adv, 70)
    assert (r.fg3m, r.fg3a, r.ftm, r.fta) == (254, 694, 503, 645)
    assert r.pts == 2 * 480 + 254 + 503
    assert r.fg3a_min <= 694 <= r.fg3a_max  # the provider's rounding may leave neighbours feasible, never excludes the truth


def test_a_short_sample_is_determined_exactly():
    base, adv = provider_rows(gp=12, fgm=60, fga=130, fg3m=17, fg3a=46, ftm=24, fta=31)
    r = reconstruct(base, adv, 12)
    assert r.quality == QUALITY_EXACT and (r.fg3m, r.fg3a, r.ftm, r.fta) == (17, 46, 24, 31)
    assert r.fg3a_min == r.fg3a_max == 46


def test_truth_is_always_inside_the_reported_range_across_random_seasons():
    rng = np.random.default_rng(0)
    exact = bounded = 0
    for _ in range(400):
        gp = int(rng.integers(5, 83))
        fga = int(rng.integers(20, 20 * gp))
        fg3a = int(rng.integers(0, fga + 1))
        fta = int(rng.integers(0, 12 * gp))
        fgm = int(rng.binomial(fga, 0.46))
        fg3m = int(min(rng.binomial(fg3a, 0.35), fgm))
        ftm = int(rng.binomial(fta, 0.75))
        base, adv = provider_rows(gp, fgm, fga, fg3m, fg3a, ftm, fta)
        r = reconstruct(base, adv, gp)
        assert r.quality != QUALITY_UNCONSTRAINED  # the truth is feasible by construction
        assert r.fg3a_min <= fg3a <= r.fg3a_max
        assert r.fta_min <= fta <= r.fta_max
        if r.quality == QUALITY_EXACT:
            exact += 1
            assert (r.fg3m, r.fg3a, r.ftm, r.fta) == (fg3m, fg3a, ftm, fta)
        else:
            bounded += 1
            assert abs(r.fg3a - fg3a) <= r.fg3a_max - r.fg3a_min
    assert exact > bounded  # most seasons are determined exactly


def test_it_beats_multiplying_a_rounded_average_by_games_played():
    rng = np.random.default_rng(1)
    err_naive, err_recon = [], []
    for _ in range(300):
        gp = int(rng.integers(30, 83))
        fga = int(rng.integers(200, 1400))
        fg3a = int(rng.integers(20, min(fga, 700)))
        fta = int(rng.integers(30, 700))
        fgm, fg3m, ftm = int(rng.binomial(fga, 0.46)), int(rng.binomial(fg3a, 0.36)), int(rng.binomial(fta, 0.77))
        fgm = max(fgm, fg3m)
        base, adv = provider_rows(gp, fgm, fga, fg3m, fg3a, ftm, fta)
        r = reconstruct(base, adv, gp)
        err_naive.append(abs(round(base["FG3A"] * gp) - fg3a))
        err_recon.append(abs(r.fg3a - fg3a))
    assert np.mean(err_recon) < 0.25 * np.mean(err_naive)


def test_no_attempts_gives_no_rate_not_zero():
    base, adv = provider_rows(gp=40, fgm=200, fga=400, fg3m=0, fg3a=0, ftm=60, fta=90)
    rows = pd.DataFrame([{"player_id": "p", "season": "2024-25", "GP": 40, "base_json": base, "advanced_json": adv}])
    out = components_frame(rows).iloc[0]
    assert out["fg3a"] == 0
    assert math.isnan(out["fg3_pct_exact"])  # undefined, not 0%
    assert out["ft_pct_exact"] == pytest.approx(60 / 90)


def test_a_player_with_no_shots_at_all_is_handled():
    base, adv = provider_rows(gp=1, fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0)
    r = reconstruct(base, adv, 1)
    assert r is not None and r.quality == QUALITY_EXACT and r.fga == 0 and r.pts == 0
    rows = pd.DataFrame([{"player_id": "p", "season": "2024-25", "GP": 1, "base_json": base, "advanced_json": adv}])
    out = components_frame(rows).iloc[0]
    assert math.isnan(out["ts_pct_computed"])


def test_inconsistent_inputs_are_flagged_not_trusted():
    base, adv = provider_rows(gp=70, fgm=480, fga=1010, fg3m=254, fg3a=694, ftm=503, fta=645)
    base["FG3_PCT"] = 0.9  # a rate that cannot belong to 254/694
    r = reconstruct(base, adv, 70)
    assert r.quality == QUALITY_UNCONSTRAINED and r.n_feasible == 0


def test_unusable_inputs_return_none():
    base, adv = provider_rows(gp=70, fgm=480, fga=1010, fg3m=254, fg3a=694, ftm=503, fta=645)
    assert reconstruct(base, adv, 0) is None
    assert reconstruct({**base, "FG3A": None}, adv, 70) is None


def test_ambiguity_is_reported_when_the_rounding_cannot_decide():
    # A full season of a rarely-shooting player: many integer counts round to the same figures.
    found_bounded = False
    for fg3a in range(1, 6):
        base, adv = provider_rows(gp=82, fgm=300, fga=700, fg3m=0, fg3a=fg3a, ftm=100, fta=140)
        r = reconstruct(base, adv, 82)
        if r.quality == QUALITY_BOUNDED:
            found_bounded = True
            assert r.fg3a_max > r.fg3a_min
    assert found_bounded
