#!/usr/bin/env python3
"""Extract the official Pivot wordmark from its source image as a transparent PNG.

The artwork is drawn on a black ground, so the ground is removed by keying on brightness:
alpha = the brightest channel, and each pixel's colour is un-multiplied against that alpha.
Anti-aliased edges therefore keep their true colour (white stays white, the orange ball stays
orange) and fade to transparent instead of leaving a dark fringe. Nothing is redrawn,
recoloured or re-traced — the output is the source pixels with the black removed.

Usage:  python3 extract_logo.py SOURCE.png OUT_LOGO.png [OUT_MARK.png]

OUT_MARK (optional) is a square crop of the basketball "o" taken from the same pixels, for the
favicon and the collapsed sidebar rail.
Requires: pillow, numpy.
"""
import sys

import numpy as np
from PIL import Image

FLOOR, CEIL = 10, 245  # brightness range mapped to alpha 0..1 (noise floor / solid)


def key_out_black(path: str) -> Image.Image:
    a = np.asarray(Image.open(path).convert("RGBA")).astype(np.float64)
    rgb = a[..., :3] * (a[..., 3:4] / 255.0)  # composite over black
    peak = rgb.max(axis=2)
    alpha = np.clip((peak - FLOOR) / (CEIL - FLOOR), 0.0, 1.0)
    safe = np.maximum(peak, 1.0)[..., None]
    colour = np.clip(rgb / safe * 255.0, 0, 255)  # un-multiply: full-brightness colour
    colour = np.where(alpha[..., None] >= 0.999, rgb, colour)  # solid pixels keep exact source
    out = np.dstack([colour, alpha * 255.0]).round().astype(np.uint8)
    return Image.fromarray(out)


def crop_to_content(im: Image.Image, pad: int) -> Image.Image:
    alpha = np.asarray(im)[..., 3]
    ys, xs = np.where(alpha > 20)
    box = (max(xs.min() - pad, 0), max(ys.min() - pad, 0), min(xs.max() + pad + 1, im.width), min(ys.max() + pad + 1, im.height))
    return im.crop(box)


def ball_mark(im: Image.Image) -> Image.Image:
    a = np.asarray(im).astype(int)
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    orange = (al > 200) & (r > 190) & (g > 70) & (g < 150) & (b < 90)
    ys, xs = np.where(orange)
    cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    radius = max(xs.max() - xs.min(), ys.max() - ys.min()) / 2 * 1.14  # orange fill + white ring
    half = int(radius * 1.12)
    return im.crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half)))


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    keyed = key_out_black(src)
    logo = crop_to_content(keyed, pad=4)
    logo.save(out, optimize=True)
    print(f"logo  {logo.size} -> {out}")
    if len(sys.argv) > 3:
        mark = ball_mark(keyed)
        mark.save(sys.argv[3], optimize=True)
        print(f"mark  {mark.size} -> {sys.argv[3]}")
