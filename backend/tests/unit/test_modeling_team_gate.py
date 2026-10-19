"""R9.1 — the team-level check: what it measures, when it refuses to answer, and its interval."""

import numpy as np
import pandas as pd

from app.modeling.team_gate import r2_difference, team_gate, team_table


def _predictions(good_signal: bool, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Three seasons x 30 teams x 8 players. 'good' scores carry the team's (persistent) quality; 'bad' scores are noise."""
    rng = np.random.default_rng(seed)
    quality = rng.normal(0, 1, 30)
    wins = [
        {"team_id": f"T{t}", "season": season, "win_pct": 0.5 + 0.1 * quality[t] + rng.normal(0, 0.02)}
        for t in range(30)
        for season in ("2020-21", "2021-22", "2022-23")
    ]
    rows = []
    for origin, target in (("2020-21", "2021-22"), ("2021-22", "2022-23")):
        for t in range(30):
            for p in range(8):
                rows.append(
                    {
                        "model": "good" if good_signal else "bad", "origin": origin, "target_season": target,
                        "player_id": f"{origin}-{t}-{p}", "team_id": f"T{t}", "y_team": f"T{t}",
                        "target_observed": True, "origin_minutes": 1000.0 + 100 * p,
                        "pred": (quality[t] if good_signal else 0.0) + rng.normal(0, 0.5),
                    }
                )
    return pd.DataFrame(rows), pd.DataFrame(wins)


def test_a_metric_that_tracks_team_quality_has_a_high_r2_and_noise_does_not():
    good, standings = _predictions(True)
    noise, _ = _predictions(False)
    assert team_gate(good, standings, "good")["concurrent"]["r2_pooled"] > 0.6
    assert team_gate(noise, standings, "bad")["concurrent"]["r2_pooled"] < 0.2


def test_the_prospective_view_uses_the_next_seasons_roster_and_win_percentage():
    good, standings = _predictions(True)
    table = team_table(good, standings, "good", "prospective_roster_known")
    assert set(table["origin"]) == {"2020-21", "2021-22"} and len(table) == 60
    assert team_gate(good, standings, "good")["prospective_roster_known"]["n_team_seasons"] == 60


def test_the_bootstrap_difference_separates_a_good_metric_from_noise():
    good, standings = _predictions(True)
    noise, _ = _predictions(False)
    both = pd.concat([good, noise], ignore_index=True)
    diff = r2_difference(both, standings, "good", "bad", draws=500)
    assert diff["delta"] > 0.4 and diff["ci95"][0] > 0
    reverse = r2_difference(both, standings, "bad", "good", draws=500)
    assert reverse["ci95"][1] < 0


def test_identical_models_have_a_difference_interval_around_zero():
    good, standings = _predictions(True)
    twin = good.assign(model="twin")
    both = pd.concat([good, twin], ignore_index=True)
    diff = r2_difference(both, standings, "good", "twin", draws=300)
    assert diff["delta"] == 0 and diff["ci95"][0] <= 0 <= diff["ci95"][1]


def test_too_few_team_seasons_means_no_answer_not_a_pass():
    good, standings = _predictions(True)
    few = good[good["team_id"].isin([f"T{i}" for i in range(5)])]
    twin = few.assign(model="twin")
    assert r2_difference(pd.concat([few, twin], ignore_index=True), standings, "good", "twin", draws=100) is None
    assert r2_difference(good, standings.iloc[0:0], "good", "good", draws=100) is None
