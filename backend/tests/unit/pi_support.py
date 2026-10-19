"""Synthetic fixtures for the R9.2 tests. Every row is generated; none is NBA data.

``provider_rows`` rounds integer season totals exactly as NBA.com publishes them. ``populate_shooting_db``
writes a synthetic league whose shooting statistics come from *known* latent abilities and integer
counts, so a test can assert the engine recovers what was planted — and that it does not invent signal."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from .modeling_support import make_frame


def provider_rows(gp, fgm, fga, fg3m, fg3a, ftm, fta):
    """The rounded fields NBA.com would publish for these integer season totals."""
    pts = 2 * fgm + fg3m + ftm
    pg = lambda x: round(x / gp, 1)  # noqa: E731
    rate = lambda m, a: round(m / a, 3) if a else 0.0  # noqa: E731
    tsa = fga + 0.44 * fta
    base = {
        "GP": gp, "FG3M": pg(fg3m), "FG3A": pg(fg3a), "FG3_PCT": rate(fg3m, fg3a),
        "FTM": pg(ftm), "FTA": pg(fta), "FT_PCT": rate(ftm, fta), "PTS": pg(pts), "FGM": pg(fgm), "FGA": pg(fga),
    }
    adv = {
        "FGM": fgm, "FGA": fga, "EFG_PCT": round((fgm + 0.5 * fg3m) / fga, 3) if fga else 0.0,
        "TS_PCT": round(pts / (2 * tsa), 3) if tsa else 0.0,
    }
    return base, adv


def make_shooting_frame(n_players: int = 130, n_seasons: int = 9, seed: int = 4):
    """A panel frame plus the integer truth behind it: ``(frame, truth)`` where ``truth`` maps
    ``(player_id, season)`` to the integer counts and ``abilities`` to the planted latent rates."""
    frame = make_frame(n_players=n_players, n_seasons=n_seasons, seed=seed)
    rng = np.random.default_rng(seed + 100)
    ids = sorted(frame["player_id"].unique())
    ability = {
        pid: {"p3": float(np.clip(rng.normal(0.35, 0.03), 0.2, 0.5)), "pft": float(np.clip(rng.normal(0.77, 0.07), 0.4, 0.95)), "p2": float(np.clip(rng.normal(0.52, 0.03), 0.4, 0.65))}
        for pid in ids
    }
    truth: dict[tuple[str, str], dict] = {}
    for r in frame.itertuples(index=False):
        gp = int(r.GP)
        fga = max(int(round(r.FGA * gp)), 5)
        fg3a = min(int(round(r.FG3A * gp)), fga)
        fta = max(int(round(r.FTA * gp)), 0)
        a = ability[r.player_id]
        fg3m = int(rng.binomial(fg3a, a["p3"]))
        fg2m = int(rng.binomial(fga - fg3a, a["p2"]))
        ftm = int(rng.binomial(fta, a["pft"]))
        truth[(r.player_id, r.season)] = {"gp": gp, "fga": fga, "fg3a": fg3a, "fg3m": fg3m, "fgm": fg2m + fg3m, "fta": fta, "ftm": ftm}
    # the panel's published-style columns must agree with the counts
    ts, efg = [], []
    for r in frame.itertuples(index=False):
        t = truth[(r.player_id, r.season)]
        base, adv = provider_rows(t["gp"], t["fgm"], t["fga"], t["fg3m"], t["fg3a"], t["ftm"], t["fta"])
        ts.append(adv["TS_PCT"])
        efg.append(adv["EFG_PCT"])
    frame = frame.assign(TS_PCT=ts, EFG_PCT=efg)
    return frame, truth, ability


def populate_shooting_db(db, n_players: int = 130, n_seasons: int = 9, seed: int = 4):
    """Write the synthetic league into the real tables with provider-style rounded shooting figures."""
    from app.db.models import Player, PlayerSeasonStats, Team

    frame, truth, ability = make_shooting_frame(n_players, n_seasons, seed)
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
        t = truth[(r.player_id, r.season)]
        gp, mpg, poss = t["gp"], float(r.MIN), float(r.POSS)
        shoot_base, shoot_adv = provider_rows(gp, t["fgm"], t["fga"], t["fg3m"], t["fg3a"], t["ftm"], t["fta"])
        base = {
            "REB": mpg * 0.2, "AST": mpg * 0.12, "STL": r.stl_per_min * mpg, "BLK": r.blk_per_min * mpg,
            "TOV": mpg * 0.08, "PF": 2.0, "PLUS_MINUS": 0.0, "AGE": r.AGE, "POSS": poss, **shoot_base,
        }
        advanced = {
            "OFF_RATING": 110.0, "DEF_RATING": r.DEF_RATING, "NET_RATING": r.NET_RATING, "AST_PCT": r.AST_PCT,
            "AST_TO": 1.8, "OREB_PCT": r.OREB_PCT, "DREB_PCT": r.DREB_PCT, "REB_PCT": r.REB_PCT,
            "TM_TOV_PCT": r.TM_TOV_PCT, "USG_PCT": r.USG_PCT, "PACE": 99.0, "PIE": r.PIE, "POSS": poss, **shoot_adv,
        }
        for stat_type, stats in (("base", base), ("advanced", advanced)):
            db.add(
                PlayerSeasonStats(
                    player_id=players[r.player_id].id, team_id=teams[r.team_id].id, season=r.season,
                    stat_type=stat_type, games_played=gp, minutes=mpg, stats=stats, source_retrieved_at=now,
                )
            )
    db.commit()
    return {"frame": frame, "truth": truth, "ability": ability, "players": players}


def make_test_engine():
    """An engine for the test database: PostgreSQL when ``PIVOT_TEST_DB_URL`` is set (the schema is dropped and
    recreated, so it must be disposable — the same convention as ``tests/conftest.py``), otherwise in-memory SQLite.
    Module-scoped fixtures use this so the "PostgreSQL run" of the R9.2 tests really runs on PostgreSQL."""
    import os

    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.db.models import Base

    url = os.environ.get("PIVOT_TEST_DB_URL", "")
    if url:
        engine = create_engine(url)
        Base.metadata.drop_all(engine)
    else:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


def fake_experiment(root, quantity: str, specs: dict[str, bool], fingerprint: str, run_id: str, stage: str = "development"):
    """A minimal experiment-artifact directory a candidate can be trained against (``--experiment``).

    ``specs`` maps a model name to whether it *advanced*. It carries the data fingerprint the candidates will see and
    the configuration they will be trained under, so the evidence matches exactly — the control for the tests that
    show evidence is NOT attached when it does not."""
    import json

    from app.modeling.experiment import PREREGISTRATION_R92
    from app.modeling.pairs import FeatureConfig

    d = root / f"{stage}-{quantity}"
    d.mkdir(parents=True, exist_ok=True)
    cands = {m: {"advance": adv, "confirmed": adv, "vs_reference": {}} for m, adv in specs.items()}
    (d / "decisions.json").write_text(json.dumps({"target": quantity, "family": "confirmatory", "reference_baseline": "ols_history", "advanced": [m for m, a in specs.items() if a], "candidates": cands, "confirmed": [m for m, a in specs.items() if a]}))
    (d / "config.json").write_text(json.dumps({"stage": stage, "backtest": {"features": FeatureConfig().as_dict(), "seed": 20260719, "interval_level": 0.8}, "preregistration": PREREGISTRATION_R92}))
    (d / "data_snapshot.json").write_text(json.dumps({"fingerprint": fingerprint}))
    (d / "run_id.txt").write_text(run_id + "\n")
    return d


RUN_ID = "exp-test-development-fg3_pct"


def build_league(tmp_path_factory, seed: int = 4, n_players: int = 130, cutoff: str = "2023-24"):
    """The synthetic league plus two stored candidates at ``cutoff``, trained against a fake experiment so the
    registry's evidence legitimately applies to them. Returns a dict (db session, frame, truth, players, experiment_dir,
    fingerprint); the caller owns closing the session."""
    from sqlalchemy.orm import sessionmaker

    from app.modeling.provenance import DataSnapshot
    from app.modeling.registry import train_candidate
    from app.player_intelligence.panel import build_skill_panel

    engine = make_test_engine()
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    info = populate_shooting_db(session, n_players=n_players, n_seasons=9, seed=seed)
    fingerprint = DataSnapshot.of_panel(build_skill_panel(session).frame).fingerprint
    exp = fake_experiment(tmp_path_factory.mktemp("experiments"), "fg3_pct", {"state_space": True}, fingerprint, RUN_ID)
    for spec in ("state_space", "ols_history"):
        train_candidate(session, spec, "fg3_pct", cutoff, experiment_dir=exp)
    info.update({"db": session, "engine": engine, "experiment_dir": exp, "fingerprint": fingerprint, "cutoff": cutoff})
    return info


def make_registry(fingerprint: str, **overrides):
    """A validation registry whose ``fg3_pct`` evidence names the fake experiment (so it applies to ``build_league`` models)."""
    from app.modeling.pairs import FeatureConfig
    from app.player_intelligence.validation import ValidationRegistry

    entry = lambda method, tier, status, **kw: {  # noqa: E731
        "method": method, "tier": tier, "evaluation_status": status, "evaluation_ref": RUN_ID, "reason": "test", **kw,
    }
    q = {
        "fg3_pct": {
            "projected": entry("state_space", "experimental", "development_only", calibration={"folds": 5, "n": 1367, "empirical_coverage": 0.79, "evaluation_ref": RUN_ID}, numbers={"attrition": {"share_evaluated_of_scored": 0.46}}),
            "inferred": entry("state_space", "experimental", "development_only"),
        },
        "ts_pct": {"projected": entry("ols_history", "benchmarked", "benchmarked"), "inferred": entry("state_space", "withheld", "failed", reason="did not meet the serving rule")},
    }
    q.update(overrides)
    return ValidationRegistry({"version": "test", "quantities": q, "generated_from": {"fg3_pct": {"development": RUN_ID, "holdout": None, "data_fingerprint": fingerprint, "config": {"features": FeatureConfig().as_dict(), "seed": 20260719, "interval_level": 0.8}}}})


__all__ = ["RUN_ID", "build_league", "make_registry", "fake_experiment", "make_shooting_frame", "populate_shooting_db", "provider_rows", "pd", "make_test_engine"]
