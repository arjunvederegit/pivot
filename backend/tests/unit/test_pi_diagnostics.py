"""R9.2 — the history-ablation diagnostics stay opt-in, and ``state_space_h3`` really cannot see past three seasons."""

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.modeling.pairs import FeatureConfig, PairCache
from app.modeling.predictors import ALL_PREDICTORS, PREDICTORS, FitContext, predictors_for
from app.modeling.targets import get_target
from app.player_intelligence.panel import build_skill_panel

from .pi_support import populate_shooting_db


@pytest.fixture(scope="module")
def panel():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    populate_shooting_db(session, n_players=100, n_seasons=9, seed=12)
    p = build_skill_panel(session)
    session.close()
    return p


def test_diagnostics_are_selectable_but_never_in_the_default_sets():
    names = {p.name for p in ALL_PREDICTORS}
    assert {"ols_history_long", "state_space_h3"}.isdisjoint(names)
    assert {"ols_history_long", "state_space_h3"} <= set(PREDICTORS)
    default = {p.name for p in predictors_for(get_target("fg3_pct"))}
    assert {"ols_history_long", "state_space_h3"}.isdisjoint(default)
    assert {p.name for p in predictors_for(get_target("fg3_pct"), ["ols_history_long"])} == {"ols_history_long"}


def test_the_three_season_diagnostic_ignores_older_seasons_and_the_full_model_does_not(panel):
    target = get_target("fg3_pct")
    cache = PairCache(panel, FeatureConfig())
    origins = [s for s in panel.seasons if s < "2023-24"]
    train = cache.pairs(origins[-1], target)
    train = train[train["target_eligible"]]
    assert {"x_lag3", "exposure_lag3", "sv_lag3"} <= set(train.columns), "the frame must carry history beyond the window"
    ctx = FitContext(target=target)
    h3 = PREDICTORS["state_space_h3"].fit(train, ctx)
    full = PREDICTORS["state_space"].fit(train, ctx)
    test = cache.features(origins[-1], target)
    altered = test.copy()
    for j in range(3, 10):
        for stem, value in (("x_lag", 0.9), ("exposure_lag", 400.0), ("sv_lag", 1e-4)):
            if f"{stem}{j}" in altered:
                altered[f"{stem}{j}"] = value
    assert np.array_equal(h3.predict(test), h3.predict(altered)), "h3 must not read lags beyond three seasons"
    assert not np.allclose(full.predict(test), full.predict(altered)), "the full model does read them"


def test_ols_history_long_reads_the_whole_history(panel):
    target = get_target("ft_pct")
    cache = PairCache(panel, FeatureConfig())
    last = [s for s in panel.seasons if s < "2023-24"][-1]
    pairs = cache.pairs(last, target)
    train = pairs[pairs["target_eligible"]]
    fitted = PREDICTORS["ols_history_long"].fit(train, FitContext(target=target))
    test = cache.features(last, target)
    altered = test.copy()
    for j in range(3, 10):
        altered[f"x_lag{j}"] = 0.2
        altered[f"exposure_lag{j}"] = 500.0
    assert not np.allclose(fitted.predict(test), fitted.predict(altered))
