"""R9.1 — GOAT feature readiness: coverage, missingness, identity confidence and redundancy.

Reads a DATABASE COPY (never the owner's database) that holds R8.3's `provider_stat_snapshots`, and
prints **aggregates only**. No per-player value is written anywhere: the snapshots are licensed
provider data and are not committed.

    python scripts/r9_1_goat_readiness.py /path/to/scratch-copy.db > goat_readiness.json

What it can and cannot establish is stated in docs/r9-1/goat-feature-readiness.md. In short: it
measures coverage, completeness, units, identity confidence and cross-sectional redundancy for the
ONE season R8.3 ingested (2025-26 regular season). It cannot measure reliability, predictive value or
anything over time, because there is exactly one retrieval of each scope.
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
from scipy import stats
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models import ProviderEntityId, ProviderStatSnapshot
from app.modeling.panel import PlayerSeasonPanel

SEASON = "2025-26"
REFERENCE_MINUTES = 500.0
HIGH_CORR = 0.9
# Per-game counting fields are volume, not skill, unless divided by playing time.
COUNT_FIELDS_PER_GAME = {
    "hustle": [
        "box_outs", "def_boxouts", "off_boxouts", "deflections", "charges_drawn", "screen_assists",
        "contested_shots", "contested_shots_2pt", "contested_shots_3pt", "loose_balls_recovered",
    ],
}


def load(db: Session) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """One frame per stat group, columns = that group's own keys only (a union across groups would
    make every field look 'missing' in the groups that never had it)."""
    snaps = db.scalars(
        select(ProviderStatSnapshot).where(
            ProviderStatSnapshot.entity_type == "player",
            ProviderStatSnapshot.season == SEASON,
            ProviderStatSnapshot.season_type == "regular",
        )
    ).all()
    by_group: dict[str, list[dict]] = {}
    for s in snaps:
        by_group.setdefault(s.stat_group, []).append(
            {"player_id": s.internal_id, "games_played": s.games_played, **{f"v:{k}": v for k, v in (s.values or {}).items()}}
        )
    frames = {g: pd.DataFrame(rows) for g, rows in by_group.items()}
    links = db.scalars(select(ProviderEntityId).where(ProviderEntityId.entity_type == "player")).all()
    link_df = pd.DataFrame([{"player_id": l.internal_id, "confidence": l.confidence, "method": l.match_method} for l in links])
    return frames, link_df


def main(path: str) -> dict:
    engine = create_engine(f"sqlite:///file:{path}?mode=ro&uri=true")
    with Session(engine) as db:
        frames, links = load(db)
        panel = PlayerSeasonPanel.from_db(db)
    ref = panel.season_rows(SEASON)
    ref = ref[ref["total_minutes"] >= REFERENCE_MINUTES].set_index("player_id")
    confidence = links.drop_duplicates("player_id").set_index("player_id")["confidence"]

    report: dict = {
        "season": SEASON,
        "reference_population": {"definition": f"NBA.com players with >= {REFERENCE_MINUTES:.0f} minutes in {SEASON}", "n": int(len(ref))},
        "groups": {},
    }
    for group, g in frames.items():
        g = g.drop_duplicates("player_id").set_index("player_id")
        fields = [c for c in g.columns if c.startswith("v:")]
        numeric = g[fields].apply(pd.to_numeric, errors="coerce")
        present = ref.index.intersection(g.index)
        conf = confidence.reindex(present)
        high_only = present[conf.reindex(present).eq("high").to_numpy()]
        minutes_missing = ref.loc[ref.index.difference(present), "total_minutes"].sum()
        nulls = numeric.isna().mean()
        entry = {
            "player_rows": int(len(g)),
            "fields": int(len(fields)),
            "fields_over_5pct_null": {f[2:]: round(float(v), 3) for f, v in nulls.items() if v > 0.05},
            "constant_fields": [f[2:] for f in fields if numeric[f].nunique(dropna=True) <= 1],
            "reference_players_with_row": int(len(present)),
            "reference_coverage": round(len(present) / len(ref), 4),
            "reference_minutes_share_without_row": round(float(minutes_missing / ref["total_minutes"].sum()), 4),
            "identity": {
                "high": int((conf == "high").sum()),
                "medium": int((conf == "medium").sum()),
                "manual": int((conf == "manual").sum()),
                "no_link_row": int(conf.isna().sum()),
                "coverage_if_high_only": round(len(high_only) / len(ref), 4),
            },
        }
        if "v:gp" in g or "v:g" in g:
            gp = numeric["v:gp" if "v:gp" in g else "v:g"]
            entry["games_played_distribution_reference"] = {
                "p10": float(gp.reindex(present).quantile(0.1)), "median": float(gp.reindex(present).median()),
                "share_under_10": round(float((gp.reindex(present) < 10).mean()), 3),
            }
        # Redundancy: cluster fields whose rank correlation is >= HIGH_CORR among reference players.
        sub = numeric.reindex(present).dropna(axis=1, how="any")
        sub = sub.loc[:, sub.nunique() > 3]
        if sub.shape[1] > 2 and len(sub) > 30:
            corr = pd.DataFrame(stats.spearmanr(sub.to_numpy()).statistic, index=sub.columns, columns=sub.columns)
            seen, clusters = set(), []
            for col in sub.columns:
                if col in seen:
                    continue
                members = [c for c in sub.columns if abs(corr.loc[col, c]) >= HIGH_CORR]
                seen.update(members)
                if len(members) > 1:
                    clusters.append([m[2:] for m in members])
            entry["redundant_clusters_abs_spearman_ge_0.9"] = clusters
            entry["independent_dimensions_after_clustering"] = int(sub.shape[1] - sum(len(c) - 1 for c in clusters))
        # Volume vs skill: how much of a per-game counting field is just minutes?
        if "v:min" in g and group in COUNT_FIELDS_PER_GAME:
            minutes = numeric["v:min"].reindex(present)
            entry["spearman_with_minutes_per_game"] = {
                f: round(float(stats.spearmanr(numeric[f"v:{f}"].reindex(present), minutes, nan_policy="omit").statistic), 3)
                for f in COUNT_FIELDS_PER_GAME[group] if f"v:{f}" in numeric
            }
        report["groups"][group] = entry

    # Does the crosswalk link the right person? GOAT's own games and minutes vs NBA.com's, by link
    # confidence. A link that agrees on both for every player is corroborated by the data itself;
    # a disagreement is a wrong link or a data discrepancy, and either way the row is not trainable.
    base = frames["general/base"].drop_duplicates("player_id").set_index("player_id")
    nba = panel.season_rows(SEASON).set_index("player_id")
    both = base.index.intersection(nba.index)
    check = pd.DataFrame(
        {
            "confidence": confidence.reindex(both),
            "gp_equal": (pd.to_numeric(base.loc[both, "v:gp"]) == nba.loc[both, "GP"]),
            "mpg_equal": (pd.to_numeric(base.loc[both, "v:min"]) - nba.loc[both, "MIN"]).abs() < 0.051,
        }
    )
    report["identity_verification_general_base"] = {
        str(conf): {
            "players": int(len(g)),
            "games_played_equal": int(g["gp_equal"].sum()),
            "minutes_per_game_equal": int(g["mpg_equal"].sum()),
            "both_equal": int((g["gp_equal"] & g["mpg_equal"]).sum()),
        }
        for conf, g in check.groupby("confidence", dropna=False)
    }
    med = ref.index.intersection(confidence[confidence == "medium"].index)
    report["medium_confidence_in_reference"] = {
        "players": int(len(med)),
        "share_of_reference_players": round(len(med) / len(ref), 4),
        "share_of_reference_minutes": round(float(ref.loc[med, "total_minutes"].sum() / ref["total_minutes"].sum()), 4),
    }

    # Incremental information over the existing box-score features, 2025-26 cross-section only.
    base_cols = ["USG_PCT", "AST_PCT", "REB_PCT", "TS_PCT", "TM_TOV_PCT", "OREB_PCT", "DREB_PCT", "stl_per_min", "blk_per_min", "fg3a_rate", "fta_rate", "MIN", "AGE"]
    X = ref[base_cols].astype(float)
    X = (X - X.mean()) / X.std(ddof=0)
    incremental: dict = {}
    for group, fields in {
        "hustle": ["deflections", "contested_shots", "charges_drawn", "loose_balls_recovered", "screen_assists", "box_outs"],
        "defense/overall": ["pct_plusminus", "d_fg_pct"],
        "tracking/possessions": ["touches", "avg_sec_per_touch", "avg_drib_per_touch", "pts_per_touch", "paint_touches"],
        "shooting/by_zone": ["restricted_area_fg_pct", "above_the_break_3_fg_pct", "mid-range_fg_pct"],
    }.items():
        if group not in frames:
            continue
        g = frames[group].drop_duplicates("player_id").set_index("player_id")
        idx = ref.index.intersection(g.index)
        for f in fields:
            col = f"v:{f}"
            if col not in g:
                continue
            y = pd.to_numeric(g.loc[idx, col], errors="coerce")
            if group in ("hustle", "tracking/possessions") and "v:min" in g and f not in ("avg_sec_per_touch", "avg_drib_per_touch", "pts_per_touch"):
                y = y / pd.to_numeric(g.loc[idx, "v:min"], errors="coerce") * 36.0  # per 36 minutes
            ok = y.notna() & np.isfinite(y)
            if ok.sum() < 60:
                continue
            A = np.column_stack([np.ones(ok.sum()), X.loc[idx][ok].to_numpy()])
            beta, *_ = np.linalg.lstsq(A, y[ok].to_numpy(), rcond=None)
            resid = y[ok].to_numpy() - A @ beta
            r2 = 1 - resid.var() / y[ok].to_numpy().var()
            adj = 1 - (1 - r2) * (ok.sum() - 1) / (ok.sum() - A.shape[1])
            incremental[f"{group}:{f}{'/36min' if group in ('hustle',) or f in ('touches', 'paint_touches') else ''}"] = {"n": int(ok.sum()), "adj_r2_on_existing_box_score_features": round(float(adj), 3)}
    report["incremental_information_cross_section"] = {
        "note": "adjusted R^2 of each GOAT field on 13 existing NBA.com box-score features (one season, rotation players). "
        "A low value means 'not explained by what Pivot already has' — which is new information OR noise; "
        "without a prior season there is no reliability estimate to tell them apart.",
        "fields": incremental,
    }
    return report


if __name__ == "__main__":
    json.dump(main(sys.argv[1]), sys.stdout, indent=2, sort_keys=True)
    print()
