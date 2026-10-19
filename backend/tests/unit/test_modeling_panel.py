"""R9.1 — the as-of panel: leakage is structurally impossible, and bad data is refused loudly."""

import numpy as np
import pandas as pd
import pytest

from app.modeling.panel import (
    AsOfPanel,
    LeakageError,
    PanelValidationError,
    PlayerSeasonPanel,
    replace_future,
    season_key,
    validate_panel,
)

from .modeling_support import make_frame, make_panel


def test_a_clean_panel_validates():
    validate_panel(make_frame())


def test_as_of_view_contains_nothing_after_the_cutoff():
    panel = make_panel()
    view = panel.as_of("2019-20")
    assert max(season_key(s) for s in view.frame["season"]) == season_key("2019-20")
    assert len(view.frame) < len(panel.frame)


def test_an_as_of_view_cannot_be_built_with_a_future_season():
    panel = make_panel()
    with pytest.raises(LeakageError, match="contains season"):
        AsOfPanel(origin="2018-19", frame=panel.frame)


def test_present_state_columns_never_reach_a_historical_view():
    frame = make_frame()
    frame["is_active"] = True
    frame["position"] = "G"
    frame["height_inches"] = 78
    panel = PlayerSeasonPanel.from_frame(frame)
    for column in ("is_active", "position", "height_inches"):
        assert column not in panel.frame.columns
        assert column not in panel.as_of("2020-21").frame.columns
    with pytest.raises(LeakageError, match="present-state"):
        AsOfPanel(origin="2020-21", frame=frame[frame["season"] <= "2020-21"])


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: pd.concat([d, d.iloc[[0]]]), "duplicate a \\(player, season\\)"),
        (lambda d: d.assign(player_id=d["player_id"].where(d.index != 3, None)), "no player identity"),
        (lambda d: d.assign(season=d["season"].where(d.index != 2, "2019")), "unparseable season"),
        (lambda d: d.assign(TS_PCT=d["TS_PCT"] * 100), "percentages where fractions"),
        (lambda d: d.assign(TM_TOV_PCT=d["TM_TOV_PCT"] / 100), "percentage"),
        (lambda d: d.assign(MIN=d["MIN"] * 82), "MIN"),
        (lambda d: d.assign(total_minutes=d["total_minutes"] * 2), "total_minutes != GP \\* MIN"),
        (lambda d: d.assign(GP=d["GP"] + 200), "GP outside"),
    ],
)
def test_injected_data_defects_are_refused(mutate, message):
    broken = mutate(make_frame().reset_index(drop=True)).reset_index(drop=True)
    with pytest.raises(PanelValidationError, match=message):
        validate_panel(broken)


def test_a_missing_required_column_is_refused():
    with pytest.raises(PanelValidationError, match="missing required columns"):
        validate_panel(make_frame().drop(columns=["AGE"]))


def test_a_fringe_player_with_an_extreme_rate_is_not_mistaken_for_a_unit_error():
    """One made three on one attempt is a 1.5 true-shooting percentage — a legitimate row."""
    frame = make_frame().reset_index(drop=True)
    frame.loc[0, ["TS_PCT", "GP", "MIN"]] = [1.5, 1, 2.0]
    frame.loc[0, "total_minutes"] = 2.0
    validate_panel(frame)


def test_scrambling_the_future_leaves_the_past_untouched():
    panel = make_panel()
    scrambled = replace_future(panel, "2019-20", np.random.default_rng(0))
    past = panel.frame["season"].map(season_key) <= season_key("2019-20")
    pd.testing.assert_frame_equal(
        panel.frame[past].reset_index(drop=True),
        scrambled.frame[past].reset_index(drop=True),
        check_dtype=False,
    )
    assert not np.allclose(panel.frame.loc[~past, "PIE"], scrambled.frame.loc[~past, "PIE"])
