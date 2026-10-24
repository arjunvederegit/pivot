"""R9.2 — what the one ingested BALLDONTLIE season can and cannot tell us, cross-sectionally.

    python -m scripts.r9_2_goat_assessment --db sqlite:///copy.db --out docs/r9-2/results/goat_assessment.json

Read-only. Aggregates only (no per-player rows are written). A single season cannot show stability or
predictive value, so this asks only questions one season *can* answer:

1. **Identity coverage** of the trusted (high-confidence) population, and how the medium-confidence links
   corroborate against NBA.com (games and minutes) — the evidence for an explicit promotion rule.
2. **Is shooting one skill or several?** Three-point accuracy split by zone (corner vs above the break) gives
   two *independent* binomial measurements of one player-season, so the covariance of the observed rates is the
   covariance of the true rates (sampling errors are independent); subtracting the expected sampling variance from
   each variance gives the noise-corrected correlation. The same is done against free-throw accuracy (NBA.com
   counts) and mid-range accuracy. This is information about *measurement structure*, not a validated skill.
3. **Redundancy of tracking and hustle with the box score** is R9.1's result (docs/r9-1/goat-feature-readiness.md);
   it is not recomputed here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models import PlayerSeasonStats, ProviderEntityId, ProviderStatSnapshot
from app.player_intelligence.panel import build_skill_panel

SEASON = "2025-26"
MIN_ZONE_ATTEMPTS = 40
ZONES = ("corner_3", "above_the_break_3", "mid-range", "restricted_area", "in_the_paint_(non-ra)")


def zone_frame(db: Session, panel_frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    links = {
        r.internal_id: r.confidence
        for r in db.scalars(select(ProviderEntityId).where(ProviderEntityId.provider == "balldontlie", ProviderEntityId.entity_type == "player"))
    }
    rows = db.scalars(
        select(ProviderStatSnapshot).where(
            ProviderStatSnapshot.provider == "balldontlie", ProviderStatSnapshot.season == SEASON,
            ProviderStatSnapshot.stat_group == "shooting/by_zone", ProviderStatSnapshot.season_type == "regular",
        )
    ).all()
    recs = []
    for r in rows:
        v = r.values or {}
        rec = {"player_id": r.internal_id, "link": links.get(r.internal_id, "none")}
        for z in ZONES:
            rec[f"{z}_pg_a"] = float(v.get(f"{z}_fga", np.nan))
            rec[f"{z}_pg_m"] = float(v.get(f"{z}_fgm", np.nan))
            rec[f"{z}_pct"] = float(v.get(f"{z}_fg_pct", np.nan))  # published to 3 decimals: no per-game rounding in the rate
        recs.append(rec)
    z = pd.DataFrame(recs)
    coverage = {
        "zone_rows": int(len(z)),
        "by_link_confidence": z["link"].value_counts().to_dict() if len(z) else {},
    }
    merged = z.merge(
        panel_frame.loc[panel_frame["season"] == SEASON, ["player_id", "GP", "ftm", "fta", "fg3m", "fg3a", "total_minutes"]],
        on="player_id", how="inner",
    )
    # The zone table publishes per-game averages and no games played; the games come from NBA.com's row
    # for the same canonical player (the providers agree on games: see the corroboration check).
    for zone in ZONES:
        merged[f"{zone}_a"] = merged[f"{zone}_pg_a"] * merged["GP"]
        merged[f"{zone}_m"] = merged[f"{zone}_pg_m"] * merged["GP"]
    return merged, coverage


def _rate_and_var(m: pd.Series, a: pd.Series, published: pd.Series | None = None) -> tuple[pd.Series, pd.Series]:
    """The rate and its binomial sampling variance. The rate is the *published* percentage when given (it
    carries no per-game rounding); the attempts, known only to within 0.05*GP, enter the variance only."""
    p = (m + 0.5) / (a + 1.0)
    return (published if published is not None else m / a), p * (1 - p) / a


def _corrected(x: np.ndarray, vx: np.ndarray, y: np.ndarray, vy: np.ndarray) -> float | None:
    cov = float(np.cov(x, y, ddof=1)[0, 1])
    true_vx, true_vy = float(np.var(x, ddof=1) - vx.mean()), float(np.var(y, ddof=1) - vy.mean())
    return float(cov / np.sqrt(true_vx * true_vy)) if true_vx > 0 and true_vy > 0 else None


def corrected_correlation(x: pd.Series, vx: pd.Series, y: pd.Series, vy: pd.Series, draws: int = 2000, seed: int = 11) -> dict:
    """Correlation of the *true* rates, assuming the two observation errors are independent.

    The interval is a percentile bootstrap over players. Draws in which a true variance is not positive
    (the sampling noise swamps the spread) are discarded and counted: a high discard share means the
    estimate is not identified at this sample size."""
    cov = float(np.cov(x, y, ddof=1)[0, 1])
    true_vx = float(np.var(x, ddof=1) - vx.mean())
    true_vy = float(np.var(y, ddof=1) - vy.mean())
    raw = float(np.corrcoef(x, y)[0, 1])
    rng = np.random.default_rng(seed)
    xa, vxa, ya, vya = (np.asarray(v, dtype=float) for v in (x, vx, y, vy))
    boots = []
    for _ in range(draws):
        i = rng.integers(0, len(xa), len(xa))
        r = _corrected(xa[i], vxa[i], ya[i], vya[i])
        if r is not None:
            boots.append(r)
    out = {"n": int(len(x)), "observed_correlation": raw, "true_sd_x": float(np.sqrt(max(true_vx, 0))), "true_sd_y": float(np.sqrt(max(true_vy, 0)))}
    if true_vx > 0 and true_vy > 0:
        out["noise_corrected_correlation"] = float(cov / np.sqrt(true_vx * true_vy))
        out["bootstrap_ci95"] = [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))] if len(boots) > 200 else None
        out["bootstrap_draws_usable"] = len(boots)
        out["reliability_x"] = true_vx / float(np.var(x, ddof=1))
        out["reliability_y"] = true_vy / float(np.var(y, ddof=1))
    else:
        out["noise_corrected_correlation"] = None
        out["note"] = "the observed variance is not larger than the sampling variance: no detectable true spread at this sample size"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    with Session(create_engine(args.db)) as db:
        panel = build_skill_panel(db).frame
        z, coverage = zone_frame(db, panel)
        medium = db.scalars(select(ProviderEntityId).where(ProviderEntityId.provider == "balldontlie", ProviderEntityId.confidence == "medium")).all()
        corroboration = []
        base = {(r.player_id): r for r in db.scalars(select(PlayerSeasonStats).where(PlayerSeasonStats.season == SEASON, PlayerSeasonStats.stat_type == "base", PlayerSeasonStats.season_type == "regular"))}
        snaps = {r.internal_id: r for r in db.scalars(select(ProviderStatSnapshot).where(ProviderStatSnapshot.provider == "balldontlie", ProviderStatSnapshot.season == SEASON, ProviderStatSnapshot.stat_group == "general/base"))}
        for link in medium:
            n, s = base.get(link.internal_id), snaps.get(link.internal_id)
            if n is None or s is None:
                corroboration.append({"corroborated": False, "reason": "no season rows to compare"})
                continue
            gp_ok = n.games_played == s.games_played
            min_ok = abs(float(n.minutes or 0) - float((s.values or {}).get("min", -1))) < 0.051
            corroboration.append({"corroborated": bool(gp_ok and min_ok), "games_agree": bool(gp_ok), "minutes_agree": bool(min_ok)})
    trusted = z[z["link"] == "high"].copy()
    out: dict = {
        "season": SEASON, "zone_coverage": coverage, "trusted_population": int(len(trusted)),
        "medium_link_corroboration": {
            "links": len(corroboration), "corroborated": sum(c["corroborated"] for c in corroboration),
            "rule": "promote a medium link to 'verified' only if games played agree exactly and minutes per game agree to the provider's rounding (0.05) in a season where both providers carry the player",
            "applied": False, "note": "reported, not applied: promotion is an explicit, owner-reviewed write",
        },
        "attempt_totals_note": "zone totals are per-game averages (one decimal) times games played: each is within 0.05*GP of the true count",
    }
    c, a = trusted["corner_3_a"], trusted["above_the_break_3_a"]
    t3 = trusted[(c >= MIN_ZONE_ATTEMPTS) & (a >= MIN_ZONE_ATTEMPTS)]
    xc, vc = _rate_and_var(t3["corner_3_m"], t3["corner_3_a"], t3["corner_3_pct"])
    xa, va = _rate_and_var(t3["above_the_break_3_m"], t3["above_the_break_3_a"], t3["above_the_break_3_pct"])
    out["corner_vs_above_break_three_point_accuracy"] = {"min_attempts_each_zone": MIN_ZONE_ATTEMPTS, **corrected_correlation(xc, vc, xa, va)}
    both = trusted[(trusted["fta"] >= 100) & (trusted["above_the_break_3_a"] >= 100)]
    xf, vf = _rate_and_var(both["ftm"], both["fta"])
    x3, v3 = _rate_and_var(both["above_the_break_3_m"], both["above_the_break_3_a"], both["above_the_break_3_pct"])
    out["free_throw_vs_above_break_three_point_accuracy"] = {"min_attempts": 100, **corrected_correlation(xf, vf, x3, v3)}
    mid = trusted[(trusted["mid-range_a"] >= MIN_ZONE_ATTEMPTS) & (trusted["fta"] >= 100)]
    xm, vm = _rate_and_var(mid["mid-range_m"], mid["mid-range_a"], mid["mid-range_pct"])
    xf2, vf2 = _rate_and_var(mid["ftm"], mid["fta"])
    out["free_throw_vs_mid_range_accuracy"] = {"min_attempts_mid_range": MIN_ZONE_ATTEMPTS, **corrected_correlation(xf2, vf2, xm, vm)}
    ra = trusted[trusted["restricted_area_a"] >= 100]
    xr, vr = _rate_and_var(ra["restricted_area_m"], ra["restricted_area_a"], ra["restricted_area_pct"])
    out["restricted_area_accuracy_spread"] = {
        "n": int(len(ra)), "observed_sd": float(xr.std(ddof=1)), "expected_sampling_sd": float(np.sqrt(vr.mean())),
        "true_sd": float(np.sqrt(max(xr.var(ddof=1) - vr.mean(), 0))),
    }
    median_att = {z_: float(np.nanmedian(trusted[f"{z_}_a"])) for z_ in ZONES}
    out["median_season_attempts_by_zone"] = median_att
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, sort_keys=True))
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
