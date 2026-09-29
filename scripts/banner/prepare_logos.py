#!/usr/bin/env python3
"""Turn dropped-in images into clean silhouettes for the banner animation.

The banner generator needs a single-colour shape with a transparent background:
it samples every visible pixel and flies particles to them. Feed this script
whatever you downloaded — a logo PNG, a JPEG mascot on a flat background — and
it writes a normalised silhouette into scripts/banner/logos/.

    python scripts/banner/prepare_logos.py path/to/logo.png path/to/mascot.jpg

How it decides what is background: it looks at the border of the image and
removes the border-connected pixels that match it. Pixels of the same colour
that are *enclosed* by the subject survive, which is what keeps dark eyes and
outlines inside a light logo. Anything already carrying a real alpha channel is
only re-centred and scaled.
"""

from __future__ import annotations

import argparse
import io
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "scripts/banner/logos"
CANVAS = 400
MAX_EDGE = 512


def edge_connected(mask: np.ndarray) -> np.ndarray:
    """True for values in ``mask`` reachable from the image border."""
    connected = np.zeros(mask.shape, dtype=bool)
    height, width = mask.shape
    queue: deque[tuple[int, int]] = deque()
    for x in range(width):
        queue.extend(((0, x), (height - 1, x)))
    for y in range(height):
        queue.extend(((y, 0), (y, width - 1)))
    while queue:
        y, x = queue.popleft()
        if connected[y, x] or not mask[y, x]:
            continue
        connected[y, x] = True
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < height and 0 <= nx < width:
                queue.append((ny, nx))
    return connected


def silhouette_alpha(image: Image.Image, tolerance: int) -> np.ndarray:
    """Alpha mask for a flat-background image, whichever colour that background is."""
    rgb = np.asarray(image.convert("RGB")).astype(np.int16)
    border = np.concatenate(
        [rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]], axis=0
    ).astype(np.float32)
    bg = np.median(border, axis=0)
    distance = np.linalg.norm(rgb - bg, axis=2)
    return ~(edge_connected(distance <= tolerance) * 255).astype(np.uint8)


def clean(alpha: np.ndarray) -> np.ndarray:
    """Close 1px anti-aliasing holes, then fill anything fully enclosed."""
    closed = np.asarray(
        Image.fromarray(alpha).filter(ImageFilter.MaxFilter(3))
    )
    # Exterior = transparent pixels reachable from the border. Anything opaque
    # and not exterior is an island: fill it so thin detail reads as solid.
    exterior = edge_connected(closed < 128)
    return np.where(exterior, closed, 255).astype(np.uint8)


def rasterize(path: Path) -> Image.Image:
    """Open an image, rendering SVG through cairosvg at a usable resolution.

    Vector logos are worth keeping in the repo as SVG — they rasterise crisply
    at whatever size the sampler needs, and stay a couple of KB on disk.
    """
    if path.suffix.lower() != ".svg":
        return Image.open(path)
    try:
        import cairosvg
    except ImportError:
        raise SystemExit(
            f"{path.name} is SVG and needs cairosvg: pip install cairosvg\n"
            "(or hand it a PNG instead)"
        )
    # Rasterise wide rather than exact so a tall viewBox keeps its aspect ratio
    # when only one output dimension is given.
    return Image.open(io.BytesIO(cairosvg.svg2png(
        url=str(path), output_width=MAX_EDGE * 2
    )))


def to_silhouette(path: Path, tolerance: int, fill: bool = False) -> Image.Image:
    """Return an RGBA image whose alpha is the subject's outline.

    Line art is kept hollow by default. Filling the enclosed areas gives a solid
    body that survives heavy downsampling, but at the few-thousand particles the
    banner uses it turns into a uniform speckled blob — the outline keeps the
    detail that identifies the shape (a gopher's eyes, Tux's beak and flippers).
    """
    image = rasterize(path)
    has_alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
    if has_alpha:
        rgba = image.convert("RGBA")
        alpha = np.asarray(rgba.getchannel("A"))
        if alpha.min() < 250:  # a real cut-out, keep it
            image = rgba
        else:  # opaque but declared transparent: treat as flat background
            image = Image.new("RGB", rgba.size, (255, 255, 255))
            image.paste(rgba, mask=rgba.getchannel("A"))
            has_alpha = False

    if has_alpha:
        alpha = np.asarray(image.getchannel("A"))
    else:
        alpha = silhouette_alpha(image, tolerance)

    if fill:
        # A line drawing sampled at a few thousand points turns into a dotted
        # outline with holes. Filling the enclosed areas gives the particle
        # field a solid body to land on, which survives downsampling.
        alpha = clean(alpha)

    shape = Image.new("RGBA", image.size, (0, 0, 0, 0))
    # Solid black + the extracted alpha: the generator colours the shape itself.
    shape.paste((0, 0, 0, 255), (0, 0), Image.fromarray(alpha))
    return shape


def place_on_canvas(shape: Image.Image) -> Image.Image:
    if max(shape.size) < MAX_EDGE:
        scale = MAX_EDGE / max(shape.size)
        shape = shape.resize(
            (round(shape.width * scale), round(shape.height * scale)),
            Image.Resampling.LANCZOS,
        )
    shape.thumbnail((CANVAS, CANVAS), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    canvas.alpha_composite(shape, ((CANVAS - shape.width) // 2,
                                   (CANVAS - shape.height) // 2))
    if canvas.getchannel("A").getbbox() is None:
        raise SystemExit("nothing left after background removal")
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--tolerance", type=int, default=42,
                        help="how far a pixel may sit from the border colour "
                             "and still count as background (0-255)")
    parser.add_argument("--fill", action="store_true",
                        help="fill the enclosed areas into a solid silhouette "
                             "instead of keeping line art hollow")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    for source in args.sources:
        if not source.exists():
            raise SystemExit(f"no such file: {source}")
        result = place_on_canvas(to_silhouette(source, args.tolerance, args.fill))
        dest = args.out / f"{source.stem}.png"
        result.save(dest)
        covered = sum(1 for v in result.getchannel("A").get_flattened_data() if v > 127)
        try:
            shown = dest.relative_to(ROOT)
        except ValueError:  # --out pointed outside the repo
            shown = dest
        print(
            f"{shown}  {covered:,} filled px "
            f"({100 * covered / (CANVAS * CANVAS):.1f}% of canvas)"
        )


if __name__ == "__main__":
    main()
