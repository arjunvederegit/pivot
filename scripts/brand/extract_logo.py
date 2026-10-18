#!/usr/bin/env python3
"""Extract the official Pivot wordmark from its source image as a transparent PNG.

The artwork is drawn on a black ground, so the ground is removed by keying on brightness:
alpha = the brightest channel, and each pixel's colour is un-multiplied against that alpha.
Anti-aliased edges therefore keep their true colour (white stays white, the orange ball stays
orange) and fade to transparent instead of leaving a dark fringe. Nothing is redrawn,
recoloured or re-traced — the output is the source pixels with the black removed (the black seams inside the ball are kept opaque so the
ball looks identical on any background).

Usage:  python3 extract_logo.py SOURCE.png OUT_LOGO.png [OUT_MARK.png]

OUT_MARK (optional) is a square crop of the basketball "o" taken from the same pixels, for the
favicon and the collapsed sidebar rail.
Requires: pillow, numpy.
"""
import sys

import numpy as np
from PIL import Image

FLOOR, CEIL = 10, 245  # brightness range mapped to alpha 0..1 (noise floor / solid)


def key_out_black(path: str) -> tuple[Image.Image, np.ndarray]:
    a = np.asarray(Image.open(path).convert("RGBA")).astype(np.float64)
    rgb = a[..., :3] * (a[..., 3:4] / 255.0)  # composite over black
    peak = rgb.max(axis=2)
    alpha = np.clip((peak - FLOOR) / (CEIL - FLOOR), 0.0, 1.0)
    safe = np.maximum(peak, 1.0)[..., None]
    colour = np.clip(rgb / safe * 255.0, 0, 255)  # un-multiply: full-brightness colour
    colour = np.where(alpha[..., None] >= 0.999, rgb, colour)  # solid pixels keep exact source
    out = np.dstack([colour, alpha * 255.0]).round().astype(np.uint8)
    return Image.fromarray(out), rgb


def ball_geometry(im: Image.Image) -> tuple[float, float, float, float]:
    """Centre, orange radius and outer (white ring) radius of the basketball, measured."""
    a = np.asarray(im).astype(int)
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    ys, xs = np.where((al > 200) & (r > 190) & (g > 70) & (g < 150) & (b < 90))
    cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    orange_r = max(xs.max() - xs.min(), ys.max() - ys.min()) / 2
    # outer radius: walk out along many rays until a clear gap (the ring ends, the letters are apart)
    radii = []
    for t in np.linspace(0, 2 * np.pi, 72, endpoint=False):
        last, gap = orange_r, 0
        for d in np.arange(orange_r, orange_r * 1.6, 0.5):
            x, y = int(round(cx + d * np.cos(t))), int(round(cy + d * np.sin(t)))
            if al[y, x] > 40:
                last, gap = d, 0
            else:
                gap += 1
                if gap > 6:
                    break
        radii.append(last)
    return cx, cy, orange_r, float(np.median(radii))


def seal_ball(im: Image.Image, rgb: np.ndarray) -> Image.Image:
    """Make the black seams inside the ball opaque black, so the ball reads the same on any ground."""
    cx, cy, _, outer = ball_geometry(im)
    a = np.asarray(im).copy()
    yy, xx = np.mgrid[0 : a.shape[0], 0 : a.shape[1]]
    inside = (xx - cx) ** 2 + (yy - cy) ** 2 <= (outer - 1.5) ** 2
    seam = inside & (a[..., 3] < 255)
    a[seam, :3] = np.clip(rgb[seam], 0, 255).round().astype(np.uint8)
    a[seam, 3] = 255
    return Image.fromarray(a)


def crop_to_content(im: Image.Image, pad: int) -> Image.Image:
    alpha = np.asarray(im)[..., 3]
    ys, xs = np.where(alpha > 20)
    box = (max(xs.min() - pad, 0), max(ys.min() - pad, 0), min(xs.max() + pad + 1, im.width), min(ys.max() + pad + 1, im.height))
    return im.crop(box)


def ball_mark(im: Image.Image) -> Image.Image:
    """The basketball alone — a circular crop of the same pixels (the neighbouring letters are masked off)."""
    cx, cy, _, outer = ball_geometry(im)
    half = int(outer + 6)
    crop = im.crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half)))
    a = np.asarray(crop).copy()
    yy, xx = np.mgrid[0 : a.shape[0], 0 : a.shape[1]]
    c = (a.shape[1] - 1) / 2
    d = np.sqrt((xx - c) ** 2 + (yy - c) ** 2)
    a[..., 3] = (a[..., 3] * np.clip(outer + 2.5 - d, 0, 1)).round().astype(np.uint8)
    return Image.fromarray(a)


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    keyed, rgb = key_out_black(src)
    keyed = seal_ball(keyed, rgb)
    logo = crop_to_content(keyed, pad=4)
    logo.save(out, optimize=True)
    print(f"logo  {logo.size} -> {out}")
    if len(sys.argv) > 3:
        mark = ball_mark(keyed)
        mark.save(sys.argv[3], optimize=True)
        print(f"mark  {mark.size} -> {sys.argv[3]}")
