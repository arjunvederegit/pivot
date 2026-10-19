"""A synthetic player-season panel for the R9.1 tests.

Every row is generated, none is NBA data. The generator plants known structure — a persistent latent
ability that shows up noisily in each season's statistics, a gentle age curve, minutes that depend on
ability, and attrition — so a test can assert that a method recovers what was planted (or that it
does not invent signal where none was planted)."""

import numpy as np
import pandas as pd

from app.modeling.panel import PlayerSeasonPanel, season_label


def make_frame(n_players: int = 220, n_seasons: int = 8, seed: int = 1, first_year: int = 2016) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    talent = rng.normal(0.0, 1.0, n_players)  # latent overall impact
    shoot = rng.normal(0.0, 0.03, n_players)  # latent true-shooting skill (fraction)
    birth_age = rng.integers(19, 33, n_players)
    start = rng.integers(0, max(n_seasons - 3, 1), n_players)  # entry season index
    team_of = rng.integers(0, 12, n_players)
    rows = []
    for i in range(n_players):
        season_talent = talent[i]
        for s in range(start[i], n_seasons):
            age = birth_age[i] + (s - start[i])
            if age > 38 or (s > start[i] + 2 and rng.random() < 0.06):
                break
            season_talent = 0.85 * season_talent + 0.15 * rng.normal() + (0.12 if age < 25 else -0.05 if age > 30 else 0.0)
            minutes_pg = float(np.clip(18 + 6 * season_talent + rng.normal(0, 5), 3, 38))
            gp = int(np.clip(rng.normal(60, 18), 8, 82))
            tsa = (minutes_pg * 0.55) * gp  # attempts scale with minutes
            ts_true = 0.55 + shoot[i] + 0.004 * season_talent
            ts = float(np.clip(ts_true + rng.normal(0, np.sqrt(0.25 * 0.75 / max(tsa, 20)) * 0.9), 0.3, 0.85))
            if rng.random() < 0.05:
                team_of[i] = rng.integers(0, 12)
            rows.append(
                {
                    "player_id": f"p{i:04d}",
                    "season": season_label(first_year + s),
                    "full_name": f"Player {i}",
                    "nba_player_id": 1000 + i,
                    "team_id": f"T{team_of[i]:02d}",
                    "GP": gp,
                    "MIN": minutes_pg,
                    "total_minutes": gp * minutes_pg,
                    "AGE": float(age),
                    "FGA": minutes_pg * 0.45,
                    "FTA": minutes_pg * 0.12,
                    "FG3A": minutes_pg * 0.15,
                    "FG3M": minutes_pg * 0.05,
                    "TS_PCT": ts,
                    "EFG_PCT": ts - 0.03,
                    "USG_PCT": float(np.clip(0.2 + 0.02 * season_talent + rng.normal(0, 0.03), 0.08, 0.4)),
                    "AST_PCT": float(np.clip(0.13 + 0.03 * season_talent + rng.normal(0, 0.04), 0.01, 0.5)),
                    "REB_PCT": float(np.clip(0.09 + rng.normal(0, 0.03), 0.02, 0.25)),
                    "OREB_PCT": float(np.clip(0.04 + rng.normal(0, 0.02), 0.0, 0.15)),
                    "DREB_PCT": float(np.clip(0.14 + rng.normal(0, 0.04), 0.03, 0.35)),
                    "TM_TOV_PCT": float(np.clip(12 + rng.normal(0, 3), 3, 30)),  # percentage points, as NBA.com
                    "PIE": float(0.09 + 0.02 * season_talent + rng.normal(0, 0.012)),
                    "NET_RATING": float(2.0 * season_talent + rng.normal(0, 4)),
                    "DEF_RATING": float(112 + rng.normal(0, 3)),
                    "pts_per75": float(18 + 5 * season_talent + rng.normal(0, 3)),
                    "stl_per_min": float(max(0.02 + rng.normal(0, 0.008), 0.0)),
                    "blk_per_min": float(max(0.012 + rng.normal(0, 0.008), 0.0)),
                    "fg3a_rate": float(np.clip(0.38 + rng.normal(0, 0.1), 0.0, 0.8)),
                    "fta_rate": float(np.clip(0.26 + rng.normal(0, 0.07), 0.0, 0.7)),
                    "PF": 2.0,
                    "def_diff_raw": float(rng.normal(0, 2)),
                    "POSS": gp * minutes_pg * 2.1,
                    "PTS": 10.0,
                    "pf_per_min": 0.09,
                }
            )
    return pd.DataFrame(rows)


def make_panel(**kwargs) -> PlayerSeasonPanel:
    return PlayerSeasonPanel.from_frame(make_frame(**kwargs))


def make_standings(panel: PlayerSeasonPanel, seed: int = 3) -> pd.DataFrame:
    """Win percentage generated from the players' latent quality so a team-level check has signal."""
    rng = np.random.default_rng(seed)
    rows = []
    frame = panel.frame
    for (team, season), g in frame.groupby(["team_id", "season"]):
        strength = np.average(g["PIE"], weights=g["total_minutes"])
        rows.append({"team_id": team, "season": season, "win_pct": float(np.clip(0.5 + 6 * (strength - 0.09) + rng.normal(0, 0.05), 0.1, 0.9))})
    return pd.DataFrame(rows)


def populate_db(db, frame: pd.DataFrame) -> None:
    """Write a synthetic panel into the real tables so DB-backed code paths run on it.

    Base/advanced rows are rebuilt from the generator's derived columns, so the frame the
    application reads back is close to — not identical with — the generator's. Tests that need
    exact values use the reloaded panel, never the generator's frame."""
    from datetime import UTC, datetime

    from app.db.models import Player, PlayerSeasonStats, Team

    now = datetime.now(UTC)
    teams = {}
    for i, label in enumerate(sorted(frame["team_id"].unique())):
        team = Team(nba_team_id=100 + i, full_name=f"Test {label}", abbreviation=label, nickname=label, city="Testville")
        db.add(team)
        teams[label] = team
    players = {}
    for pid, name, nba_id in frame[["player_id", "full_name", "nba_player_id"]].drop_duplicates("player_id").itertuples(index=False):
        player = Player(nba_player_id=int(nba_id), full_name=name, is_active=True)
        db.add(player)
        players[pid] = player
    db.flush()
    for r in frame.itertuples(index=False):
        team = teams[r.team_id]
        gp, mpg = int(r.GP), float(r.MIN)
        poss = float(r.POSS)
        base = {
            "PTS": r.pts_per75 * poss / (75 * gp), "REB": mpg * 0.2, "AST": mpg * 0.12,
            "STL": r.stl_per_min * mpg, "BLK": r.blk_per_min * mpg, "TOV": mpg * 0.08, "PF": 2.0,
            "FGA": r.FGA, "FG3A": r.FG3A, "FG3M": r.FG3M, "FTA": r.FTA, "PLUS_MINUS": 0.0, "AGE": r.AGE, "POSS": poss,
        }
        advanced = {
            "OFF_RATING": 110.0, "DEF_RATING": r.DEF_RATING, "NET_RATING": r.NET_RATING, "AST_PCT": r.AST_PCT,
            "AST_TO": 1.8, "OREB_PCT": r.OREB_PCT, "DREB_PCT": r.DREB_PCT, "REB_PCT": r.REB_PCT,
            "TM_TOV_PCT": r.TM_TOV_PCT, "EFG_PCT": r.EFG_PCT, "TS_PCT": r.TS_PCT, "USG_PCT": r.USG_PCT,
            "PACE": 99.0, "PIE": r.PIE, "POSS": poss,
        }
        for stat_type, stats in (("base", base), ("advanced", advanced)):
            db.add(
                PlayerSeasonStats(
                    player_id=players[r.player_id].id, team_id=team.id, season=r.season,
                    stat_type=stat_type, games_played=gp, minutes=mpg, stats=stats,
                    source_retrieved_at=now,
                )
            )
    db.commit()
