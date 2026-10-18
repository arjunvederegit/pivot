#!/usr/bin/env python3
"""Build the production Pivot logo from its source artwork.

Source: scripts/brand/source/pivot-logo-source.png — the approved design (rounded white "Pivot",
orange basketball for the "o") exported with a transparent ground. That export carries
background-removal smears around the lettering, so it is not used as-is:

  * Lettering: every stroke is thicker than any smear, so a morphological opening keeps the
    letters and drops the noise; each glyph contour is then smoothed along its length and
    re-filled with anti-aliasing. Shapes, proportions and spacing are the source's.
  * Basketball: the source pixels themselves (orange fill, black outline, white seams, white ring),
    kept inside the ring so nothing outside leaks in. It is not redrawn or recoloured.

Usage:  python3 build_logo.py SOURCE.png OUT_LOGO.png [OUT_MARK.png]
OUT_MARK is a square, circular crop of the ball alone for the favicon and the collapsed sidebar.
Requires: numpy, pillow, opencv-python-headless, scipy.
"""
from __future__ import annotations

import sys

import cv2
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter1d

OPEN_RADIUS = 32  # px at source scale: > smear thickness, < stroke thickness (~105 px)
SUPERSAMPLE = 4
CONTOUR_SIGMA = 20  # along-contour smoothing, in supersampled px
RING = 177.0  # outer radius of the ball's white ring, measured from the source


def ellipse(r: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def ball_centre(rgba: np.ndarray) -> tuple[float, float]:
    r, g, b, al = (rgba[..., i].astype(int) for i in range(4))
    ys, xs = np.where((al > 200) & (r > 200) & (g > 70) & (g < 150) & (b < 60))
    return (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2


def clean_letters(alpha: np.ndarray, dist: np.ndarray) -> np.ndarray:
    h, w = alpha.shape
    mask = ((alpha > 140) & (dist > RING + 13)).astype(np.uint8) * 255
    core = cv2.morphologyEx(mask, cv2.MORPH_OPEN, ellipse(OPEN_RADIUS))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(core)
    keep = np.zeros_like(core)
    for i in range(1, n):
        if stats[i][4] > 8000:
            keep[lab == i] = 255
    big = cv2.resize(keep, (w * SUPERSAMPLE, h * SUPERSAMPLE), interpolation=cv2.INTER_CUBIC)
    big = (cv2.GaussianBlur(big, (0, 0), SUPERSAMPLE * 1.2) > 127).astype(np.uint8)
    cnts, hier = cv2.findContours(big, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)

    def smooth(c):
        p = c[:, 0, :].astype(np.float64)
        q = np.stack([gaussian_filter1d(p[:, k], CONTOUR_SIGMA, mode="wrap") for k in (0, 1)], 1)
        return np.round(q * 16).astype(np.int32).reshape(-1, 1, 2)

    canvas = np.zeros_like(big, dtype=np.uint8)
    for i in sorted(range(len(cnts)), key=lambda i: hier[0][i][3] != -1):  # outer first, holes after
        hole = hier[0][i][3] != -1
        cv2.fillPoly(canvas, [smooth(cnts[i])], 0 if hole else 255, lineType=cv2.LINE_AA, shift=4)
    return cv2.resize(canvas, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32) / 255


def main(src: str, out_logo: str, out_mark: str | None) -> None:
    rgba = np.asarray(Image.open(src).convert("RGBA")).copy()
    h, w = rgba.shape[:2]
    cx, cy = ball_centre(rgba)
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)

    out = np.zeros((h, w, 4), np.float32)
    out[..., :3] = 255
    out[..., 3] = clean_letters(rgba[..., 3], dist)
    inside = np.clip(RING - dist + 0.5, 0, 1)
    ball = inside > 0
    for c in range(3):
        out[..., c] = np.where(ball, rgba[..., c], out[..., c])
    out[..., 3] = np.where(ball, rgba[..., 3] / 255 * inside, out[..., 3])
    out[..., 3] *= 255
    full = Image.fromarray(np.clip(out, 0, 255).round().astype(np.uint8))
    box = full.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    pad = 6
    logo = full.crop((box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad))
    logo.save(out_logo, optimize=True)
    print(f"logo {logo.size} -> {out_logo}")
    if out_mark:
        # The ball alone: the same pixels, with everything outside the ring (neighbouring letters) cut away.
        solo = rgba.astype(np.float32)
        solo[..., 3] = solo[..., 3] * inside
        solo_img = Image.fromarray(np.clip(solo, 0, 255).round().astype(np.uint8))
        half = int(RING + 5)
        mark = solo_img.crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half)))
        mark.save(out_mark, optimize=True)
        print(f"mark {mark.size} -> {out_mark}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
