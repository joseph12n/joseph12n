#!/usr/bin/env python3
"""Cut the portrait out of its background and frame it for the dither.

The banner dithers the portrait to a 300×340 lattice and keeps only the opaque
pixels. A raw phone photo fails that twice over: the tiled wall behind you turns
into a field of noise, and the framing centres on the ceiling rather than your
head. So the photo goes through a person-segmentation model first, then gets
cropped to the 300:340 box the sampler expects.

    pip install rembg pillow numpy
    python scripts/banner/prepare_portrait.py assets/source/portrait.jpg

Writes assets/source/portrait.png, which is what generate.py reads.

This is deliberately NOT part of the banner workflow. The input is a photo of a
person, assets/source/portrait.jpg is gitignored, and the banner is committed
instead — regenerate it locally and push the result.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
GRID_W, GRID_H = 300, 340

# u2net_human_seg is the person-only model. The general "u2net" saliency model
# happily grabs the interesting background instead.
MODEL = "u2net_human_seg"


def cutout(source: Path) -> Image.Image:
    try:
        from rembg import new_session, remove
    except ImportError:
        raise SystemExit(
            "rembg is required for this step only: pip install rembg\n"
            "(the banner itself does not need it — generate.py reads the "
            "already-cut-out portrait.png)"
        )
    session = new_session(os.environ.get("REMBG_MODEL", MODEL))
    image = Image.open(source).convert("RGB")
    # A 3 megapixel selfie costs real seconds in the model for no extra detail
    # once it is down to a 300×340 lattice.
    image.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
    return remove(image, session=session, post_process_mask=True)


def frame(image: Image.Image, pad: float) -> Image.Image:
    """Crop to a GRID_W:GRID_H box around the subject and pad to its aspect."""
    alpha = np.asarray(image.getchannel("A"))
    ys, xs = np.where(alpha > 40)
    if not len(xs):
        raise SystemExit("segmentation found nothing — check the photo")

    top = max(int(ys.min()) - pad, 0)
    height = min(image.height - top, image.height)
    width = min(int(round(height * GRID_W / GRID_H)), image.width)
    centre = int((xs.min() + xs.max()) / 2)
    left = max(0, min(centre - width // 2, image.width - width))
    return image.crop((left, top, left + width, top + height))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", nargs="?",
                        type=Path, default=ROOT / "assets/source/portrait.jpg")
    parser.add_argument("--out", type=Path, default=ROOT / "assets/source/portrait.png")
    parser.add_argument("--headroom", type=float, default=30,
                        help="pixels of empty space kept above the hair")
    parser.add_argument("--max-edge", type=int, default=680,
                        help="longest edge of the written PNG")
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"no such photo: {args.source}")

    subject = cutout(args.source)
    intermediate = args.source.with_name("portrait-cutout.png")
    subject.save(intermediate)

    result = frame(subject, args.headroom)
    result.thumbnail((args.max_edge, args.max_edge), Image.Resampling.LANCZOS)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result.save(args.out, optimize=True)

    covered = float((np.asarray(result.getchannel("A")) > 40).mean())
    print(f"{args.out.relative_to(ROOT)}  {result.size}  "
          f"{args.out.stat().st_size:,} bytes  {100 * covered:.0f}% opaque")
    print(f"intermediate: {intermediate.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
