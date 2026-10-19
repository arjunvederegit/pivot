"""Metrics, matched to what is being predicted, and the paired comparison between two models.

* **MAE / RMSE / bias** — in the target's own units. MAE is the headline (robust to the heavy tails
  that low-minute players produce); RMSE is reported because squared error punishes the large misses
  a trade decision cares about.
* **Spearman** — rank agreement, for the question "does it order players correctly?". It is
  undefined for a constant prediction and is reported as ``None`` there rather than as 0.
* **Interval coverage** — the share of outcomes inside the nominal interval. A narrower interval
  that under-covers is *worse*, so width is only ever reported next to coverage.
* **Paired cluster bootstrap** — the same player appears in several folds and their errors are not
  independent, so the unit resampled is the *player*, and the statistic is the difference in loss
  between two models on identical rows. An interval is a statement about this sample of players and
  seasons, not about all future seasons.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

BOOTSTRAP_DRAWS = 2000


def mae(y: np.ndarray, p: np.ndarray, w: np.ndarray | None = None) -> float:
    return float(np.average(np.abs(y - p), weights=w))


def rmse(y: np.ndarray, p: np.ndarray, w: np.ndarray | None = None) -> float:
    return float(np.sqrt(np.average((y - p) ** 2, weights=w)))


def bias(y: np.ndarray, p: np.ndarray, w: np.ndarray | None = None) -> float:
    """Mean of (prediction - outcome): positive = the model over-predicts."""
    return float(np.average(p - y, weights=w))


def spearman(y: np.ndarray, p: np.ndarray) -> float | None:
    if len(y) < 3 or np.ptp(p) == 0 or np.ptp(y) == 0:
        return None
    rho = stats.spearmanr(y, p).statistic
    return None if np.isnan(rho) else float(rho)


def _fold_mean_spearman(frame: pd.DataFrame) -> float | None:
    """Rank correlation computed *within* each fold and averaged.

    Pooling folds first lets between-fold level differences masquerade as ranking: a model that
    predicts a different constant in each fold would show a non-zero pooled correlation while ranking
    no one. If the correlation is undefined in any fold (a constant forecast) the result is ``None``."""
    groups = [g for _, g in frame.groupby("origin")] if "origin" in frame.columns else [frame]
    values = [spearman(g["y"].to_numpy(dtype=float), g["pred"].to_numpy(dtype=float)) for g in groups]
    defined = [v for v in values if v is not None]
    if not defined or len(defined) != len(values):
        return None
    return float(np.mean(defined))


def coverage(y: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    return float(np.mean((y >= lo) & (y <= hi)))


def summarise(frame: pd.DataFrame) -> dict:
    """Headline metrics for rows with ``y``, ``pred`` (and optionally ``lo``/``hi``/``y_minutes``)."""
    y = frame["y"].to_numpy(dtype=float)
    p = frame["pred"].to_numpy(dtype=float)
    out: dict = {
        "n": int(len(frame)),
        "mae": mae(y, p),
        "rmse": rmse(y, p),
        "bias": bias(y, p),
        "spearman": _fold_mean_spearman(frame),
    }
    if "y_minutes" in frame:
        w = frame["y_minutes"].to_numpy(dtype=float)
        out["mae_minutes_weighted"] = mae(y, p, w)
        out["rmse_minutes_weighted"] = rmse(y, p, w)
    if {"lo", "hi"} <= set(frame.columns) and frame["lo"].notna().all():
        lo, hi = frame["lo"].to_numpy(dtype=float), frame["hi"].to_numpy(dtype=float)
        out["coverage"] = coverage(y, lo, hi)
        out["mean_width"] = float(np.mean(hi - lo))
    return out


def paired_cluster_bootstrap(
    loss_a: np.ndarray,
    loss_b: np.ndarray,
    clusters: np.ndarray,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = 20260719,
) -> dict:
    """Mean of ``loss_a - loss_b`` with a percentile interval from resampling players.

    Negative means A has the lower loss. Rows must be the same examples in the same order."""
    d = np.asarray(loss_a, dtype=float) - np.asarray(loss_b, dtype=float)
    codes, uniques = pd.factorize(pd.Series(clusters))
    n_clusters = len(uniques)
    sums = np.bincount(codes, weights=d, minlength=n_clusters)
    counts = np.bincount(codes, minlength=n_clusters).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n_clusters, size=(draws, n_clusters))
    boot = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return {
        "mean_diff": float(d.mean()),
        "ci95": [float(lo), float(hi)],
        "n": int(len(d)),
        "n_clusters": int(n_clusters),
        "excludes_zero": bool(lo > 0 or hi < 0),
    }


def r_squared(x: np.ndarray, y: np.ndarray) -> float | None:
    """R^2 of a one-variable OLS of y on x (the squared Pearson correlation)."""
    if len(x) < 3 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1] ** 2)
