"""Team-level validity: does a player metric, summed to a team, track how good the team was?

This is the gate that rejected two earlier impact proposals (the ridge at R² 0.004, the defensive
differential), because player-level error is not the product's outcome — team projections are.
Two views, with the limits of each stated where it is computed:

* **concurrent** — a team's minutes-weighted mean score (players on the team in season ``t``,
  weighted by their season-``t`` minutes) against the same team's season-``t`` win percentage. It
  asks whether a metric *describes* team quality. **It is not a test of a forecast**: a forecast of
  season ``t+1`` that regresses toward the mean or applies an age curve is expected to correlate
  *less* with season-``t`` wins than the raw season-``t`` value, so this view is reported but never
  used to judge a forecasting model.
* **prospective, roster-known** — season-``t`` scores (weighted by season-``t`` minutes) aggregated
  over the players who were on the team's roster in season ``t+1``, against season ``t+1`` win
  percentage. It asks whether last season's player metrics, applied to next season's roster,
  forecast that season's wins. **It assumes the next season's roster is known at the cutoff** and
  includes players who joined mid-season, so it is optimistic; it is a comparison between models
  under the same assumption, not an absolute forecast skill. Multi-team players carry one row (their
  end-of-season team), so their minutes are attributed to that team.

Win percentage comes from ``standings`` — independent of the player table, unlike on-court net
rating, which is itself built from the same players. **For ``impact_proxy`` the comparison is
partly circular** (the proxy contains on-court net rating, itself a team result), so a model that
carries last season's value will correlate with wins by construction; the fair comparison is then
between models that all carry it, which is why the pre-registered check compares a candidate with
the *reference baseline*, with a bootstrap interval over team-seasons, and never with the TEI.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .metrics import r_squared

VIEWS = ("concurrent", "prospective_roster_known")


def _team_scores(frame: pd.DataFrame, team_col: str) -> pd.DataFrame:
    f = frame.dropna(subset=[team_col, "pred"]).copy()
    f["w"] = f["origin_minutes"].astype(float)
    f = f[f["w"] > 0]
    out = f.groupby(team_col).apply(
        lambda g: pd.Series({"score": np.average(g["pred"], weights=g["w"]), "players": len(g)}),
        include_groups=False,
    ).reset_index()
    return out.rename(columns={team_col: "team_id"})


def team_table(predictions: pd.DataFrame, standings: pd.DataFrame, model: str, view: str) -> pd.DataFrame:
    """``origin, team_id, score, win_pct`` — one row per team-season for one model and one view."""
    if view not in VIEWS:
        raise ValueError(f"unknown view {view!r}")
    wp = standings.set_index(["team_id", "season"])["win_pct"]
    parts = []
    for origin, group in predictions[predictions["model"] == model].groupby("origin"):
        target_season = group["target_season"].iloc[0]
        if view == "concurrent":
            scores, season = _team_scores(group, "team_id"), origin
        else:
            scores = _team_scores(group[group["target_observed"].astype(bool)], "y_team")
            season = target_season
        scores["win_pct"] = [wp.get((t, season), np.nan) for t in scores["team_id"]]
        scores["origin"] = origin
        parts.append(scores.dropna(subset=["win_pct"]))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=["team_id", "score", "players", "win_pct", "origin"]
    )


def _demeaned(table: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Score and win % with each season's mean removed, so a level shift between folds is not credited."""
    x = table["score"] - table.groupby("origin")["score"].transform("mean")
    y = table["win_pct"] - table.groupby("origin")["win_pct"].transform("mean")
    return x.to_numpy(), y.to_numpy()


def team_gate(predictions: pd.DataFrame, standings: pd.DataFrame, model: str) -> dict:
    """Pooled and per-season R² for one model, both views (descriptive)."""
    out: dict[str, dict] = {}
    for view in VIEWS:
        table = team_table(predictions, standings, model, view)
        if table.empty:
            out[view] = {"n_team_seasons": 0, "r2_pooled": None, "r2_by_origin": {}}
            continue
        x, y = _demeaned(table)
        out[view] = {
            "n_team_seasons": int(len(table)),
            "r2_pooled": r_squared(x, y),
            "r2_by_origin": {
                str(o): r_squared(g["score"].to_numpy(), g["win_pct"].to_numpy()) for o, g in table.groupby("origin")
            },
        }
    return out


def r2_difference(
    predictions: pd.DataFrame,
    standings: pd.DataFrame,
    model: str,
    reference: str,
    view: str = "prospective_roster_known",
    draws: int = 2000,
    seed: int = 20260719,
) -> dict | None:
    """R²(model) − R²(reference) over the same team-seasons, with a team-season bootstrap interval.

    ``None`` when the comparison cannot be computed (no standings, too few team-seasons) — callers
    must treat that as a failed check, not a pass."""
    a = team_table(predictions, standings, model, view)
    b = team_table(predictions, standings, reference, view)
    if a.empty or b.empty:
        return None
    merged = a.merge(b, on=["origin", "team_id"], suffixes=("_a", "_b"))
    if len(merged) < 30:
        return None
    ma = merged.rename(columns={"score_a": "score", "win_pct_a": "win_pct"})[["origin", "score", "win_pct"]]
    mb = merged.rename(columns={"score_b": "score", "win_pct_b": "win_pct"})[["origin", "score", "win_pct"]]
    xa, ya = _demeaned(ma)
    xb, yb = _demeaned(mb)
    ra, rb = r_squared(xa, ya), r_squared(xb, yb)
    if ra is None or rb is None:
        return None
    rng = np.random.default_rng(seed)
    n = len(merged)
    idx = rng.integers(0, n, size=(draws, n))

    def r2_rows(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        xs, ys = x[idx], y[idx]
        xs = xs - xs.mean(axis=1, keepdims=True)
        ys = ys - ys.mean(axis=1, keepdims=True)
        denom = (xs**2).sum(axis=1) * (ys**2).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(denom > 0, (xs * ys).sum(axis=1) ** 2 / denom, np.nan)

    diff = r2_rows(xa, ya) - r2_rows(xb, yb)
    lo, hi = np.nanpercentile(diff, [2.5, 97.5])
    return {
        "view": view,
        "r2_model": ra,
        "r2_reference": rb,
        "delta": ra - rb,
        "ci95": [float(lo), float(hi)],
        "n_team_seasons": int(n),
        "model": model,
        "reference": reference,
    }
