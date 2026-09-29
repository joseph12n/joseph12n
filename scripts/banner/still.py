#!/usr/bin/env python3
"""Preview one animation frame of the banner as a PNG, without a full render.

The banner is SMIL, so most rasterisers only ever show the first frame — the
portrait. This draws the same particle cloud the banner would show while it is
holding on a logo, which is what you actually want to check before committing a
700 KB render: is the silhouette recognisable, is it centred, does it fill the
frame.

    python scripts/banner/still.py                 # the portrait frame
    python scripts/banner/still.py linux           # the Tux hold
    python scripts/banner/still.py go --theme light
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

import generate as g


def render(points: np.ndarray, theme: str, dest: Path, scale: float = 1.0) -> None:
    t = g.THEMES[theme]
    # The banner centres the logo silhouette inside the VISUAL.MAP frame; draw
    # the same frame so the preview lines up with the finished banner.
    frame_x, frame_y, frame_w, frame_h = g.VISUAL_MAP
    width, height = int(frame_w * scale), int(frame_h * scale)
    rgb = tuple(int(t["portrait"][i : i + 2], 16) for i in (1, 3, 5))
    canvas = Image.new("RGB", (width, height), t["panel2"])
    pixels = np.asarray(canvas).copy()

    xs = np.clip(((points[:, 0] - frame_x) * scale).round().astype(int), 0, width - 1)
    ys = np.clip(((points[:, 1] - frame_y) * scale).round().astype(int), 0, height - 1)
    pixels[ys, xs] = rgb
    Image.fromarray(pixels).save(dest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logo", nargs="?", default=None,
                        help="logo to hold on; omit for the portrait frame")
    parser.add_argument("--theme", choices=tuple(g.THEMES), default="dark")
    parser.add_argument("--count", type=int, default=g.HOLD_PARTICLE_COUNT,
                        help="particles to draw (defaults to HOLD_PARTICLES)")
    parser.add_argument("--out", type=Path,
                        default=Path("/tmp/banner-still.png"))
    args = parser.parse_args()

    rng = np.random.default_rng(g.SEED + 7)
    if args.logo:
        logos = g.make_logos()
        if args.logo not in logos:
            raise SystemExit(f"no such logo: {args.logo} (have {', '.join(logos)})")
        points = g.sample_logo_points(logos[args.logo], rng, args.count)
    else:
        points = g.portrait_points(args.theme, np.random.default_rng(g.SEED))

    render(points, args.theme, args.out)
    print(f"wrote {args.out}  ({len(points):,} particles, theme={args.theme})")


if __name__ == "__main__":
    main()
