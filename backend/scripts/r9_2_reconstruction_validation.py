"""Validate the shot-count reconstruction against independent exact totals, and against the published rates.

    python -m scripts.r9_2_reconstruction_validation --db sqlite:///copy.db --out docs/r9-2/results/reconstruction_validation.json

For 2025-26 the database also holds exact season totals from a user-supplied CSV import (``stat_type = 'totals'``);
they are used **only here**, never by a model. Every player-season is also reconciled with NBA.com's published ``TS_PCT``.
Read-only; aggregates only."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models import PlayerSeasonStats
from app.player_intelligence.components import reconstruct


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    with Session(create_engine(args.db)) as db:
        rows = db.execute(
            select(PlayerSeasonStats.player_id, PlayerSeasonStats.season, PlayerSeasonStats.stat_type, PlayerSeasonStats.games_played, PlayerSeasonStats.stats)
            .where(PlayerSeasonStats.season_type == "regular")
        ).all()
    base = {(r.player_id, r.season): r for r in rows if r.stat_type == "base"}
    adv = {(r.player_id, r.season): r for r in rows if r.stat_type == "advanced"}
    tot = {(r.player_id, r.season): r for r in rows if r.stat_type == "totals"}
    recon = {k: reconstruct(b.stats, adv[k].stats if k in adv else {}, int(b.games_played or 0)) for k, b in base.items()}
    out: dict = {"player_seasons": len(base), "by_quality": dict(Counter(r.quality if r else "none" for r in recon.values()))}
    by_season: dict[str, Counter] = {}
    for (_, season), r in recon.items():
        by_season.setdefault(season, Counter())[r.quality if r else "none"] += 1
    out["by_season"] = {s: dict(c) for s, c in sorted(by_season.items())}
    diffs = [abs(adv[k].stats["TS_PCT"] - r.pts / (2 * (r.fga + 0.44 * r.fta))) for k, r in recon.items() if r and k in adv and (r.fga + 0.44 * r.fta) > 0 and adv[k].stats.get("TS_PCT") is not None]
    out["true_shooting_recomputed_vs_published"] = {"n": len(diffs), "max_abs_difference": float(max(diffs)), "share_within_0.0006": float(np.mean(np.array(diffs) <= 0.0006))}
    if tot:
        exact, naive = {n: [] for n in ("fg3a", "fg3m", "fta", "ftm", "pts")}, {n: [] for n in ("fg3a", "fg3m", "fta", "ftm")}
        calls: Counter = Counter()
        for k, t in tot.items():
            r, b, ts = recon.get(k), base.get(k), t.stats
            if r is None or b is None:
                continue
            truth = {"fg3a": ts["FG3A"], "fg3m": ts["FG3M"], "fta": ts["FTA"], "ftm": ts["FTM"], "pts": ts["PTS"]}
            got = {"fg3a": r.fg3a, "fg3m": r.fg3m, "fta": r.fta, "ftm": r.ftm, "pts": r.pts}
            for n in exact:
                exact[n].append(got[n] - truth[n])
            for n, col in (("fg3a", "FG3A"), ("fg3m", "FG3M"), ("fta", "FTA"), ("ftm", "FTM")):
                naive[n].append(round(b.stats[col] * int(b.games_played)) - truth[n])
            all_ok = all(got[n] == truth[n] for n in ("fg3a", "fg3m", "fta", "ftm"))
            calls[(r.quality, "all four counts equal the truth" if all_ok else "at least one count differs")] += 1
            inside = r.fg3a_min <= truth["fg3a"] <= r.fg3a_max and r.fta_min <= truth["fta"] <= r.fta_max
            calls[("range_contains_truth", inside)] += 1
        out["against_exact_totals_2025_26"] = {
            "n": len(exact["fg3a"]),
            "reconstruction": {n: {"exact_share": float(np.mean(np.array(v) == 0)), "mean_abs_error": float(np.mean(np.abs(v))), "max_abs_error": float(np.max(np.abs(v)))} for n, v in exact.items()},
            "naive_rounded_per_game_times_games": {n: {"exact_share": float(np.mean(np.array(v) == 0)), "mean_abs_error": float(np.mean(np.abs(v))), "max_abs_error": float(np.max(np.abs(v)))} for n, v in naive.items()},
            "quality_vs_truth": {f"{a} | {b}": c for (a, b), c in sorted(calls.items(), key=str)},
        }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, sort_keys=True, default=str))
    print(json.dumps(out, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
