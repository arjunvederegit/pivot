"""Makes and attempts, recovered from what the provider actually stored.

NBA.com's season tables give *per-game* averages rounded to one decimal (``FG3A = 5.4``), plus a
few exact season totals in the advanced table (``FGM``, ``FGA``) and the published rates to three
decimals (``FG3_PCT``, ``FT_PCT``, ``EFG_PCT``, ``TS_PCT``). Multiplying a rounded average by games
played gives attempts that are wrong by up to ``0.05 * GP`` (about +-4 for a full season), and the
rounded percentage fields cannot be treated as the data: ``0.355`` is a *rate*, not 11 makes of 31.

This module recovers the integer season counts by constraint satisfaction. An integer count ``T``
is *feasible* when

* it rounds to the stored per-game value (``|T / GP - per_game| <= 0.05``),
* ``round(makes / attempts, 3)`` equals the stored percentage,
* ``(FGM + 0.5 * FG3M) / FGA`` rounds to the stored ``EFG_PCT`` (exact ``FGM`` / ``FGA`` are in the
  advanced table), and
* ``PTS = 2 * FGM + FG3M + FTM`` with ``TS_PCT = PTS / (2 * (FGA + 0.44 * FTA))`` holds to the stored
  precision.

If one tuple of counts is feasible the counts are *exact*. If several are, the reported value is the
feasible tuple nearest the rounded point estimate and the *range* of attempts over all feasible
tuples is returned beside it, so a consumer sees how much the provider's rounding left undetermined.
If none is feasible (a provider quirk) the point estimate is returned and flagged
``unconstrained`` — never silently trusted.

**Validation.** 573 rows of 2025-26 carry exact season totals from an independent CSV import; the
reconstruction is scored against them in ``tests`` and in ``docs/r9-2``. Nothing here is fitted.

A player with no attempts has *no rate* (``NaN``), never a rate of zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import product
from typing import Any

import numpy as np
import pandas as pd

PER_GAME_HALF_ULP = 0.05 + 1e-9  # a one-decimal per-game figure is within 0.05 of the truth
PCT_HALF_ULP = 0.0005 + 1e-9  # a three-decimal rate is within 0.0005 of the truth
#: Cap on the Cartesian product searched per player-season (windows are tiny; this is a guard).
MAX_TUPLES = 250_000

QUALITY_EXACT = "exact"
QUALITY_BOUNDED = "bounded"
QUALITY_UNCONSTRAINED = "unconstrained"


@dataclass(frozen=True)
class Count:
    makes: int
    attempts: int


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def _window(per_game: float, gp: int) -> range:
    lo = max(0, math.ceil((per_game - PER_GAME_HALF_ULP) * gp - 1e-9))
    hi = math.floor((per_game + PER_GAME_HALF_ULP) * gp + 1e-9)
    return range(lo, max(lo, hi) + 1)


def _pair_candidates(m_pg: float, a_pg: float, pct: float | None, gp: int) -> list[Count]:
    """All integer (makes, attempts) pairs consistent with the per-game figures and the rate."""
    pairs: list[Count] = []
    for a in _window(a_pg, gp):
        for m in _window(m_pg, gp):
            if m > a:
                continue
            if a == 0:
                # No attempts: the provider publishes a zero rate; accept only m == 0.
                if m == 0 and (pct is None or abs(pct) <= PCT_HALF_ULP):
                    pairs.append(Count(0, 0))
                continue
            if pct is not None and abs(m / a - pct) > PCT_HALF_ULP:
                continue
            pairs.append(Count(m, a))
    return pairs


@dataclass(frozen=True)
class Reconstruction:
    fgm: int
    fga: int
    fg3m: int
    fg3a: int
    ftm: int
    fta: int
    pts: int
    quality: str
    #: Range of 3PA / FTA over every feasible tuple; equal to the point value when exact.
    fg3a_min: int
    fg3a_max: int
    fta_min: int
    fta_max: int
    n_feasible: int


def reconstruct(base: dict[str, Any], advanced: dict[str, Any], gp: int) -> Reconstruction | None:
    """Integer season counts for one player-season, or ``None`` when the inputs are unusable."""
    if gp is None or gp <= 0:
        return None
    fgm, fga = _num(advanced.get("FGM")), _num(advanced.get("FGA"))
    if fgm is None or fga is None:
        fgm, fga = _num(base.get("FGM")), _num(base.get("FGA"))
        if fgm is None or fga is None:
            return None
        fgm, fga = round(fgm * gp), round(fga * gp)  # last resort: rounded per-game
        exact_fg = False
    else:
        exact_fg = True
    fgm, fga = int(round(fgm)), int(round(fga))

    m3_pg, a3_pg, p3 = _num(base.get("FG3M")), _num(base.get("FG3A")), _num(base.get("FG3_PCT"))
    mf_pg, af_pg, pf = _num(base.get("FTM")), _num(base.get("FTA")), _num(base.get("FT_PCT"))
    pts_pg = _num(base.get("PTS"))
    efg, ts = _num(advanced.get("EFG_PCT")), _num(advanced.get("TS_PCT"))
    if None in (m3_pg, a3_pg, mf_pg, af_pg):
        return None

    def point(m_pg: float, a_pg: float) -> Count:
        a = int(round(a_pg * gp))
        return Count(min(int(round(m_pg * gp)), a), a)

    three = _pair_candidates(m3_pg, a3_pg, p3, gp)  # type: ignore[arg-type]
    free = _pair_candidates(mf_pg, af_pg, pf, gp)  # type: ignore[arg-type]
    point3, pointf = point(m3_pg, a3_pg), point(mf_pg, af_pg)  # type: ignore[arg-type]

    if three and exact_fg and fga > 0 and efg is not None:
        keep = [c for c in three if c.makes <= fgm and c.attempts <= fga and abs((fgm + 0.5 * c.makes) / fga - efg) <= PCT_HALF_ULP]
        three = keep
    if three and exact_fg:
        three = [c for c in three if c.makes <= fgm and c.attempts <= fga]

    feasible: list[tuple[Count, Count]] = []
    if three and free and len(three) * len(free) <= MAX_TUPLES:
        pts_lo = pts_hi = None
        if pts_pg is not None and exact_fg:
            # The points and true-shooting identities need the EXACT field-goal totals; with only a rounded per-game
            # FGM they would exclude the truth, so without an advanced row only the per-game windows and the
            # published makes/attempts rates constrain the counts.
            pts_lo, pts_hi = (pts_pg - PER_GAME_HALF_ULP) * gp - 1e-9, (pts_pg + PER_GAME_HALF_ULP) * gp + 1e-9
        for c3, cf in product(three, free):
            pts = 2 * fgm + c3.makes + cf.makes
            if pts_lo is not None and not (pts_lo <= pts <= pts_hi):  # type: ignore[operator]
                continue
            if ts is not None and exact_fg:
                tsa = fga + 0.44 * cf.attempts
                if tsa <= 0:
                    if pts != 0:
                        continue
                elif abs(pts / (2 * tsa) - ts) > PCT_HALF_ULP:
                    continue
            feasible.append((c3, cf))

    if feasible:
        def distance(t: tuple[Count, Count]) -> float:
            return abs(t[0].attempts - point3.attempts) + abs(t[0].makes - point3.makes) + abs(t[1].attempts - pointf.attempts) + abs(t[1].makes - pointf.makes)

        best = min(feasible, key=lambda t: (distance(t), t[0].attempts, t[0].makes, t[1].attempts, t[1].makes))
        a3s, afs = [t[0].attempts for t in feasible], [t[1].attempts for t in feasible]
        exact = len(set(feasible)) == 1
        c3, cf = best
        quality = QUALITY_EXACT if exact else QUALITY_BOUNDED
        n = len(set(feasible))
        mn3, mx3, mnf, mxf = min(a3s), max(a3s), min(afs), max(afs)
    else:
        c3, cf = point3, pointf
        quality = QUALITY_UNCONSTRAINED
        n = 0
        mn3 = mx3 = c3.attempts
        mnf = mxf = cf.attempts

    if not exact_fg and quality != QUALITY_UNCONSTRAINED:
        quality = QUALITY_BOUNDED if quality == QUALITY_EXACT else quality
    pts = 2 * fgm + c3.makes + cf.makes
    return Reconstruction(
        fgm=fgm, fga=fga, fg3m=c3.makes, fg3a=c3.attempts, ftm=cf.makes, fta=cf.attempts, pts=pts,
        quality=quality, fg3a_min=mn3, fg3a_max=mx3, fta_min=mnf, fta_max=mxf, n_feasible=n,
    )


COMPONENT_COLUMNS = (
    "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "pts", "tsa", "fg2m", "fg2a",
    "fg3_pct_exact", "ft_pct_exact", "ts_pct_computed", "shooting_counts_quality",
    "fg3a_min", "fg3a_max", "fta_min", "fta_max",
)


def components_frame(rows: pd.DataFrame, base_col: str = "base_json", adv_col: str = "advanced_json") -> pd.DataFrame:
    """Vectorised wrapper: one row of stored ``base`` / ``advanced`` JSON dicts in, components out.

    ``rows`` needs ``player_id``, ``season``, ``GP`` and the two dict columns. Rates are ``NaN``
    when the player took no attempts."""
    out = []
    for r in rows.itertuples(index=False):
        rec = reconstruct(getattr(r, base_col) or {}, getattr(r, adv_col) or {}, int(r.GP) if pd.notna(r.GP) else 0)
        if rec is None:
            out.append(dict.fromkeys(COMPONENT_COLUMNS, np.nan) | {"player_id": r.player_id, "season": r.season, "shooting_counts_quality": "missing"})
            continue
        tsa = rec.fga + 0.44 * rec.fta
        out.append(
            {
                "player_id": r.player_id, "season": r.season,
                "fgm": rec.fgm, "fga": rec.fga, "fg3m": rec.fg3m, "fg3a": rec.fg3a, "ftm": rec.ftm, "fta": rec.fta,
                "pts": rec.pts, "tsa": tsa, "fg2m": rec.fgm - rec.fg3m, "fg2a": rec.fga - rec.fg3a,
                "fg3_pct_exact": rec.fg3m / rec.fg3a if rec.fg3a > 0 else np.nan,
                "ft_pct_exact": rec.ftm / rec.fta if rec.fta > 0 else np.nan,
                "ts_pct_computed": rec.pts / (2 * tsa) if tsa > 0 else np.nan,
                "shooting_counts_quality": rec.quality,
                "fg3a_min": rec.fg3a_min, "fg3a_max": rec.fg3a_max, "fta_min": rec.fta_min, "fta_max": rec.fta_max,
            }
        )
    return pd.DataFrame(out)
