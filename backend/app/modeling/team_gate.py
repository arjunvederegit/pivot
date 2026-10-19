"""Team-level validity: does a player metric, summed to a team, track how good the team was?

This is the gate that rejected two earlier impact proposals (the ridge at R² 0.004, the defensive
differential), because player-level error is not the product's outcome — team projections are.
Two views, with the limits of each stated where it is computed:

* **concurrent** — a team's minutes-weighted mean score (players on the team in season ``t``,
  weighted by their season-``t`` minutes) against the same team's season-``t`` win percentage. It
  asks whether a metric *describes* team quality. It is not a forecast and not a causal claim.
* **prospective, roster-known** — season-``t`` scores (and season-``t`` minutes) aggregated over the
  players who were on the team's roster in season ``t+1``, against season ``t+1`` win percentage.
  It asks whether last season's player metrics, applied to next season's roster, forecast that
  season's wins. **It assumes the next season's roster is known at the cutoff** and includes players
  who joined mid-season, so it is optimistic; it is a comparison between models under the same
  optimistic assumption, not an absolute forecast skill.

Win percentage comes from ``standings`` — independent of the player table, unlike on-court net
rating, which is itself built from the same players.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .metrics import r_squared


def _team_scores(frame: pd.DataFrame, team_col: str) -> pd.DataFrame:
    f = frame.dropna(subset=[team_col, "pred"]).copy()
    f["w"] = f["origin_minutes"].astype(float)
    f = f[f["w"] > 0]
    grouped = f.groupby(team_col)
    out = grouped.apply(
        lambda g: pd.Series(
            {"score": np.average(g["pred"], weights=g["w"]), "minutes": g["w"].sum(), "players": len(g)}
        ),
        include_groups=False,
    ).reset_index()
    return out.rename(columns={team_col: "team_id"})


def team_gate(predictions: pd.DataFrame, standings: pd.DataFrame, model: str) -> dict:
    """``predictions``: one model's rows over the full scoring population (all folds)."""
    wp = standings.set_index(["team_id", "season"])["win_pct"]
    concurrent, prospective = [], []
    for origin, group in predictions[predictions["model"] == model].groupby("origin"):
        target_season = group["target_season"].iloc[0]
        scores = _team_scores(group, "team_id")
        scores["win_pct"] = [wp.get((t, origin), np.nan) for t in scores["team_id"]]
        scores["origin"] = origin
        concurrent.append(scores.dropna(subset=["win_pct"]))
        scores_p = _team_scores(group[group["target_observed"].astype(bool)], "y_team")
        scores_p["win_pct"] = [wp.get((t, target_season), np.nan) for t in scores_p["team_id"]]
        scores_p["origin"] = origin
        prospective.append(scores_p.dropna(subset=["win_pct"]))

    def summarise(parts: list[pd.DataFrame]) -> dict:
        if not parts:
            return {"n_team_seasons": 0, "r2_pooled": None, "r2_by_origin": {}}
        pooled = pd.concat(parts, ignore_index=True)
        by_origin = {
            str(o): r_squared(g["score"].to_numpy(), g["win_pct"].to_numpy())
            for o, g in pooled.groupby("origin")
        }
        # Pooled over seasons after removing each season's mean win percentage (it is 0.5 by
        # construction) and each season's mean score, so a level shift between folds is not credited.
        demeaned_x = pooled["score"] - pooled.groupby("origin")["score"].transform("mean")
        demeaned_y = pooled["win_pct"] - pooled.groupby("origin")["win_pct"].transform("mean")
        return {
            "n_team_seasons": int(len(pooled)),
            "r2_pooled": r_squared(demeaned_x.to_numpy(), demeaned_y.to_numpy()),
            "r2_by_origin": by_origin,
        }

    return {"concurrent": summarise(concurrent), "prospective_roster_known": summarise(prospective)}
