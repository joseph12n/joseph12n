#!/usr/bin/env python3
"""Generate the animated GitHub profile banners.

Run from the repository root:
    python scripts/banner/generate.py

Requires: pip install -r scripts/banner/requirements.txt
Requires: a portrait photo at assets/source/portrait.jpg
Requires: silhouettes in scripts/banner/logos/ (see prepare_logos.py)
"""

from __future__ import annotations

import argparse
import html
import os
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist


ROOT = Path(__file__).resolve().parents[2]
# portrait.jpg is the untouched photo; portrait.png is the cut-out, framed
# head-and-shoulders copy that actually feeds the dither. Both come out of
# scripts/banner/prepare_portrait.py. Override with PORTRAIT=... to point
# somewhere else.
SOURCE = Path(os.environ.get("PORTRAIT") or ROOT / "assets/source/portrait.png")
ASSETS = ROOT / "assets"
LOGOS = Path(__file__).resolve().parent / "logos"

W, H = 1180, 610
INTRO_SECONDS = 3.0
TRANSITION_SECONDS = 1.3
HOLD_SECONDS = 3.4

# Particle budget. The reference build runs 900 travellers over 2,400 hold
# particles and lands around 910 KB per theme. 600/1,600 keeps the same look at
# roughly half the bytes, which matters because GitHub caches these as images.
TRAVELLER_COUNT = int(os.environ.get("TRAVELLERS", 600))
HOLD_PARTICLE_COUNT = int(os.environ.get("HOLD_PARTICLES", 2_600))

# The left-hand visual frame: x, y, width, height. Must match the clipPath and
# the panel rect in render_svg.
VISUAL_MAP = (49.0, 124.0, 390.0, 414.0)
LOGO_SCALE = float(os.environ.get("LOGO_SCALE", 0.90))

SEED = 314159
LOGO_SUFFIXES = {".png", ".webp"}
LOGO_ALIASES = {"kube": "kubernetes", "tux": "linux", "gopher": "go"}
# Animation order, then anything else alphabetically. The face always plays
# first; these are the shapes it turns into.
PREFERRED_LOGO_ORDER = ("linux", "go")

# Fraction of the source photo to keep, and where the crop starts, measured as a
# fraction of the original width/height. assets/source/portrait.png is already
# cut out and framed head-and-shoulders by prepare_portrait.py, so the defaults
# keep the whole thing; raise them to re-zoom into an unframed photo.
CROP_WIDTH_FRACTION = float(os.environ.get("CROP_W", 1.0))
CROP_TOP_FRACTION = float(os.environ.get("CROP_TOP", 0.0))
# Horizontal nudge as a fraction of the leftover margin, -1..1.
CROP_X_SHIFT = float(os.environ.get("CROP_X", 0.0))

GRID_W, GRID_H = 300, 340
MAX_PORTRAIT_DOTS = 18_000

# Extra frames in the rotation, dithered the same way as the opening portrait.
# Each entry is (label, path, width-as-fraction-of-the-source, max-dots).
# These are dithered pictures, not silhouettes: point_path merges their adjacent
# lit pixels into runs, so a full-frame image costs ~60 KiB instead of ~800 KiB.
#
# Two things matter here. The manga panel is 666x378, so 0.50 crops it to the
# character's head and shoulders at the 300:340 aspect. And its dot budget is
# unlimited on purpose: a dense dither is the only way the hatching reads as
# shading instead of noise, while subsampling both thins the picture and splits
# the horizontal runs, which is what keeps point_path compact.
EXTRA_PORTRAITS = [
    ("anime", ROOT / "assets/source/anime-src.png", 0.50, None),
]

YAML_ROWS = [
    (0, "profile", ""),
    (1, "subject", "Joseph Varón"),
    (1, "role", "Full Stack Developer"),
    (1, "origin", "Bogotá, Colombia · UTC-5"),
    (1, "focus", "Java/Spring · React/Next · Mobile"),
    (1, "status", "Aprendiendo · Construyendo · Enviando"),
    (1, "toolchain", "Maven · Gradle · Docker · GitHub Actions"),
    (0, "stack", ""),
    (1, "backend", "Java · Kotlin · Spring Boot"),
    (1, "frontend", "React · Next.js · TypeScript · Tailwind"),
    (1, "mobile", "Android · Jetpack Compose · Expo"),
    (1, "data", "MongoDB · PostgreSQL · MySQL · Redis"),
    (1, "qa", "JUnit · Vitest · Cypress · Qase · JMeter"),
    (1, "ai", "Agentic coding · MCP · Ollama"),
    (0, "contact", ""),
    (1, "linkedin", "/in/joseph-varon"),
    (1, "github", "joseph12n"),
    (1, "location", "Bogotá, Colombia"),
]

# Ember. The portrait is the only thing tinted, so it carries the accent — a hot
# orange that reads as flame against the near-black warm background — while the
# chrome stays in amber so the panel does not compete with the particles.
THEMES = {
    "dark": {
        "bg":      "#160C05",   # warm black
        "panel":   "#20120A",
        "panel2":  "#2A180D",
        "line":    "#4A2A15",
        "muted":   "#A8714F",   # faded ember
        "text":    "#FFE0BE",   # warm cream
        "portrait":"#FF8A2B",   # flame orange
        "chrome":  "#FFC24A",   # amber
        "accent":  "#FF5A14",   # deep ember
        "shadow":  "#0A0502",
    },
    "light": {
        "bg":      "#FFF6EC",
        "panel":   "#FFFFFF",
        "panel2":  "#FCEEE0",
        "line":    "#F0D3BC",
        "muted":   "#A8714F",
        "text":    "#2A1408",
        "portrait":"#E2620F",
        "chrome":  "#B23F08",
        "accent":  "#8A3D08",
        "shadow":  "#E9BFA2",
    },
}


def edge_connected(mask: np.ndarray) -> np.ndarray:
    """Return true values in ``mask`` that connect to an image edge."""
    connected = np.zeros(mask.shape, dtype=bool)
    queue: deque[tuple[int, int]] = deque()
    height, width = mask.shape
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


def normalize_logo(image: Image.Image) -> Image.Image:
    """Centre an icon on the sampling canvas, extracting opaque white backgrounds."""
    icon = image.convert("RGBA")
    alpha = icon.getchannel("A")
    if alpha.getextrema() == (255, 255):
        # Remove only light pixels connected to the canvas edge. This keeps light
        # details enclosed by a dark outline (for example, Linux's belly) opaque.
        gray = np.asarray(ImageOps.grayscale(icon))
        background = edge_connected(gray >= 220)
        visible = ~background
        # Close tiny anti-aliased gaps in the outline, then fill enclosed light
        # areas so an opaque source becomes a complete single-colour silhouette.
        visible = np.asarray(
            Image.fromarray((visible * 255).astype("uint8"))
            .filter(ImageFilter.MaxFilter(3))
        ) > 0
        exterior = edge_connected(~visible)
        icon.putalpha(Image.fromarray(np.where(exterior, 0, 255).astype("uint8")))
    icon.thumbnail((320, 320), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    offset = ((400 - icon.width) // 2, (400 - icon.height) // 2)
    canvas.alpha_composite(icon, offset)
    if canvas.getchannel("A").getbbox() is None:
        raise ValueError("icon has no visible pixels")
    return canvas


def load_logo_files() -> dict[str, Image.Image]:
    """Load PNG and WebP silhouettes supplied in scripts/banner/logos."""
    loaded: dict[str, Image.Image] = {}
    for path in sorted(LOGOS.iterdir()):
        if not path.is_file() or path.suffix.lower() not in LOGO_SUFFIXES:
            continue
        name = LOGO_ALIASES.get(path.stem.lower(), path.stem.lower())
        with Image.open(path) as image:
            loaded[name] = normalize_logo(image)
    return loaded


def make_logos() -> dict[str, Image.Image]:
    """Load user-supplied PNG/WebP icon files in a stable animation order."""
    LOGOS.mkdir(parents=True, exist_ok=True)
    logos = load_logo_files()
    if not logos:
        raise SystemExit(f"No PNG or WebP icons found in {LOGOS.relative_to(ROOT)}")
    ordered_names = [name for name in PREFERRED_LOGO_ORDER if name in logos]
    ordered_names.extend(name for name in sorted(logos) if name not in ordered_names)
    return {name: logos[name] for name in ordered_names}


def floyd_steinberg(gray: np.ndarray) -> np.ndarray:
    """Serpentine 1-bit Floyd-Steinberg diffusion; True means a lit pixel."""
    work = gray.astype(np.float32) / 255.0
    out = np.zeros_like(work, dtype=bool)
    height, width = work.shape
    for y in range(height):
        left_to_right = y % 2 == 0
        xs = range(width) if left_to_right else range(width - 1, -1, -1)
        direction = 1 if left_to_right else -1
        for x in xs:
            old = work[y, x]
            new = 1.0 if old >= 0.5 else 0.0
            out[y, x] = bool(new)
            err = old - new
            nx = x + direction
            if 0 <= nx < width:
                work[y, nx] += err * 7 / 16
            if y + 1 < height:
                if 0 <= x - direction < width:
                    work[y + 1, x - direction] += err * 3 / 16
                work[y + 1, x] += err * 5 / 16
                if 0 <= nx < width:
                    work[y + 1, nx] += err * 1 / 16
    return out


def portrait_points(theme: str, rng: np.random.Generator,
                    source_path: Path | None = None,
                    width_fraction: float | None = None,
                    top_fraction: float | None = None,
                    x_shift: float | None = None,
                    max_dots: int | None = -1) -> np.ndarray:
    """Return sampled x/y banner coordinates from a GRID_W x GRID_H dither grid."""
    source = Image.open(source_path or SOURCE).convert("RGBA")
    # Frame the head instead of the whole photo: most of a selfie is ceiling,
    # wall and furniture, which only adds noise the dither would amplify.
    crop_w_frac = CROP_WIDTH_FRACTION if width_fraction is None else width_fraction
    crop_top_frac = CROP_TOP_FRACTION if top_fraction is None else top_fraction
    shift = CROP_X_SHIFT if x_shift is None else x_shift
    w, h = source.size
    crop_w = min(int(w * crop_w_frac), w)
    crop_h = min(int(crop_w * (GRID_H / GRID_W)), h)
    slack = w - crop_w
    left = int(round(slack / 2 + shift * slack / 2))
    top = int(min(max(h * crop_top_frac, 0), h - crop_h))
    crop = source.crop((left, top, left + crop_w, top + crop_h)).resize(
        (GRID_W, GRID_H), Image.Resampling.LANCZOS
    )
    rgb = crop.convert("RGB")
    alpha = np.asarray(crop.getchannel("A"), dtype=np.float32) / 255.0

    # Put the portrait over a solid background to process lighting
    if theme == "dark":
        bg = Image.new("RGBA", crop.size, "black")
        bg.alpha_composite(crop)
        prepared = ImageOps.grayscale(bg.convert("RGB"))
        prepared = ImageOps.autocontrast(prepared, cutoff=1)
        prepared = ImageEnhance.Contrast(prepared).enhance(1.55)
        prepared = ImageEnhance.Brightness(prepared).enhance(1.10)
        prepared = prepared.filter(ImageFilter.UnsharpMask(radius=2.0, percent=200, threshold=1))
        select_lit = True
    else:
        bg = Image.new("RGBA", crop.size, "white")
        bg.alpha_composite(crop)
        prepared = ImageOps.grayscale(bg.convert("RGB"))
        prepared = ImageOps.autocontrast(prepared, cutoff=1)
        prepared = ImageEnhance.Contrast(prepared).enhance(1.85)
        prepared = ImageEnhance.Brightness(prepared).enhance(0.92)
        prepared = prepared.filter(ImageFilter.UnsharpMask(radius=2.0, percent=200, threshold=1))
        select_lit = False

    bits = floyd_steinberg(np.asarray(prepared))
    active = bits if select_lit else ~bits
    # Both themes need the cut-out mask. The light theme inverts (it selects
    # shadow), so its background arrives as white and any ringing the resample
    # left behind shows up as a speckle halo unless it is clipped harder.
    active &= alpha > (0.08 if theme == "dark" else 0.35)

    # Keep the full 300×340 lattice — skipping 2×2 cells was the soft/blurry look.
    ys, xs = np.where(active)
    if len(xs) == 0:
        return np.zeros((0, 2), dtype=np.float32)
    points = np.column_stack((74 + xs, 154 + ys)).astype(np.float32)
    limit = MAX_PORTRAIT_DOTS if max_dots == -1 else max_dots
    if limit and len(points) > limit:
        points = points[rng.choice(len(points), limit, replace=False)]
    return points


def logo_silhouette_points(image: Image.Image) -> np.ndarray:
    """Return every visible logo pixel in the portrait frame's coordinate space.

    The silhouette canvas is centred inside the VISUAL.MAP frame (the clip rect
    in render_svg) at LOGO_SCALE, so a logo reads at the same visual weight as
    the head-and-shoulders portrait rather than as a small stamp in the corner.
    """
    alpha = np.asarray(image.getchannel("A"))
    ys, xs = np.where(alpha > 127)
    frame_x, frame_y, frame_w, frame_h = VISUAL_MAP
    edge = min(LOGO_SCALE * 400, min(frame_w, frame_h))
    origin_x = frame_x + (frame_w - edge) / 2
    origin_y = frame_y + (frame_h - edge) / 2
    return np.column_stack((origin_x + xs * LOGO_SCALE,
                            origin_y + ys * LOGO_SCALE)).astype(np.float32)


def sample_logo_points(
    image: Image.Image, rng: np.random.Generator, count: int
) -> np.ndarray:
    """Choose travellers from a silhouette in the portrait frame's visual space."""
    points = logo_silhouette_points(image)
    if not len(points):
        raise ValueError("icon has no visible pixels")
    chosen = rng.choice(len(points), count, replace=len(points) < count)
    return points[chosen]


def transport(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Order target points by minimum-cost assignment from source points."""
    rows, cols = linear_sum_assignment(cdist(source, target, metric="sqeuclidean"))
    ordered = np.empty_like(target)
    ordered[rows] = target[cols]
    return ordered


def num(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def time_num(value: float) -> str:
    """Format SMIL timeline values without collapsing adjacent keyframes."""
    return f"{value:.4f}".rstrip("0").rstrip(".")


def point_path(points: np.ndarray) -> str:
    """Aggregate adjacent horizontal one-pixel dots into compact SVG path runs."""
    if not len(points):
        return ""
    integer = np.rint(points).astype(int)
    unique = sorted({(int(x), int(y)) for x, y in integer}, key=lambda p: (p[1], p[0]))
    chunks: list[str] = []
    i = 0
    while i < len(unique):
        x0, y = unique[i]
        x1 = x0
        i += 1
        while i < len(unique) and unique[i][1] == y and unique[i][0] <= x1 + 1:
            x1 = unique[i][0]
            i += 1
        chunks.append(f"M{x0} {y}h{x1 - x0 + 1}")
    return "".join(chunks)


def particle_path(points: np.ndarray) -> str:
    """Render independent one-pixel particles without joining adjacent dots."""
    integer = np.rint(points).astype(int)
    unique = sorted({(int(x), int(y)) for x, y in integer}, key=lambda p: (p[1], p[0]))
    return "".join(f"M{x} {y}h1" for x, y in unique)


def dotted_leader(x1: float, x2: float, y: float) -> str:
    if x2 <= x1:
        return ""
    return "".join(f"M{x} {num(y)}h1" for x in np.arange(x1, x2, 5.0))


def text_width(text: str, font_size: float) -> float:
    """Stable monospace width used both for textLength and leader placement."""
    return len(text) * font_size * 0.605


def animate_values(points: list[np.ndarray], index: int) -> str:
    return ";".join(f"{num(p[index, 0])} {num(p[index, 1])}" for p in points)


def render_svg(
    theme_name: str,
    portrait: np.ndarray,
    holds: list[tuple[str, np.ndarray, bool]],
    rng: np.random.Generator,
) -> str:
    """Render one theme's banner.

    ``portrait`` is the opening frame. ``holds`` is the rest of the rotation as
    ``(name, points, is_dither)``: a dithered portrait is drawn with
    ``point_path`` (adjacent lit pixels merge into runs, ~13x smaller than one
    path command per pixel), while a logo silhouette keeps independent
    particles so it stays speckled.
    """
    t = THEMES[theme_name]
    n = min(TRAVELLER_COUNT, len(portrait))
    source = portrait[rng.choice(len(portrait), n, replace=False)]
    targets: list[np.ndarray] = []
    current = source
    for _, points, _ in holds:
        current = transport(current, points[:n])
        targets.append(current)

    # Three seconds of portrait, then a transition and a hold on each frame.
    # Returning to the portrait keeps the loop seamless.
    times = [0.0, INTRO_SECONDS]
    frames = [source, source]
    for target in targets:
        times.extend((times[-1] + TRANSITION_SECONDS,
                      times[-1] + TRANSITION_SECONDS + HOLD_SECONDS))
        frames.extend((target, target))
    times.append(times[-1] + TRANSITION_SECONDS)
    frames.append(source)
    loop_seconds = times[-1]
    loop_duration = time_num(loop_seconds)
    key_times = ";".join(time_num(v / loop_seconds) for v in times)
    opacity_values = ";".join(["0", "0"] + ["1"] * (len(frames) - 3) + ["0"])
    parts: list[str] = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" '
        'aria-labelledby="title desc">',
        "<title id=\"title\">Joseph Varon's live system profile</title>",
        '<desc id="desc">Animated terminal profile with a dithered portrait and '
        "tool silhouettes.</desc>",
        "<defs>",
        '<filter id="shadow" x="-20%" y="-20%" width="140%" height="150%">'
        f'<feDropShadow dx="0" dy="12" stdDeviation="16" flood-color="{t["shadow"]}" '
        'flood-opacity=".28"/></filter>',
        '<filter id="glow" x="-100%" y="-100%" width="300%" height="300%">'
        f'<feGaussianBlur stdDeviation="3" result="b"/><feFlood flood-color="{t["chrome"]}" '
        'flood-opacity=".35"/><feComposite in2="b" operator="in"/>'
        '<feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>',
        '<clipPath id="visualClip"><rect x="49" y="124" width="390" height="414" rx="3"/></clipPath>',
        "</defs>",
        f'<rect width="{W}" height="{H}" rx="18" fill="{t["bg"]}"/>',
        f'<rect x="13" y="13" width="1154" height="584" rx="13" fill="{t["panel"]}" '
        f'stroke="{t["line"]}" filter="url(#shadow)"/>',
        f'<path d="M13 62H1167" stroke="{t["line"]}"/>',
        '<circle cx="38" cy="38" r="6" fill="#FF5F57"/>'
        '<circle cx="59" cy="38" r="6" fill="#FEBC2E"/>'
        '<circle cx="80" cy="38" r="6" fill="#28C840"/>',
        f'<text x="590" y="43" text-anchor="middle" fill="{t["muted"]}" '
        'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="13" '
        'letter-spacing=".4">vim profile.yml</text>',
        # Left visual frame.
        f'<rect x="35" y="88" width="418" height="472" rx="6" fill="{t["panel2"]}" '
        f'stroke="{t["line"]}"/>',
        f'<path d="M35 124H453" stroke="{t["line"]}"/>',
        f'<text x="49" y="111" fill="{t["chrome"]}" '
        'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="13" '
        'font-weight="700" letter-spacing="1.2">VISUAL.MAP</text>',
        f'<text x="438" y="111" text-anchor="end" fill="{t["muted"]}" '
        'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="11">300×340 / 1-BIT</text>',
        f'<path d="M49 141h12M49 141v12M439 141h-12M439 141v12M49 539h12M49 539v-12'
        f'M439 539h-12M439 539v-12" fill="none" stroke="{t["chrome"]}" opacity=".55"/>',
        '<g clip-path="url(#visualClip)" shape-rendering="crispEdges">',
        # Loop layer stays visible at t=0 so camo/static first frames still show the face.
        # Intro duplicate below shimmers on top, then hands off at 3.2s.
        '<g opacity="1">',
    ]

    # Dense portrait drift moves toward the first logo in the current sequence.
    first_centroid = targets[0].mean(axis=0)
    band_ids = rng.integers(0, 94, size=len(portrait))
    noise = rng.normal(0, 4, size=(94, 2))
    for band in range(94):
        pts = portrait[band_ids == band]
        if not len(pts):
            continue
        centroid = pts.mean(axis=0)
        delta = (first_centroid - centroid) * 0.18 + noise[band]
        drift_positions = ["0 0", "0 0", f"{num(delta[0])} {num(delta[1])}",
                           f"{num(delta[0])} {num(delta[1])}"]
        drift_positions.extend(["0 0"] * (len(frames) - len(drift_positions)))
        drift_opacity = [".94", ".94", "0", "0"]
        drift_opacity.extend(["0"] * (len(frames) - len(drift_opacity) - 1))
        drift_opacity.append(".94")
        drift_position_values = ";".join(drift_positions)
        drift_opacity_values = ";".join(drift_opacity)
        d = point_path(pts)
        parts.append(
            f'<path d="{d}" fill="none" stroke="{t["portrait"]}" stroke-width="1" '
            'opacity=".94">'
            f'<animateTransform attributeName="transform" type="translate" begin="{INTRO_SECONDS}s" '
            f'dur="{loop_duration}s" repeatCount="indefinite" calcMode="linear" '
            f'keyTimes="{key_times}" values="{drift_position_values}"/>'
            f'<animate attributeName="opacity" begin="{INTRO_SECONDS}s" dur="{loop_duration}s" '
            f'repeatCount="indefinite" keyTimes="{key_times}" '
            f'values="{drift_opacity_values}"/></path>'
        )

    # Optimal-transport travellers, represented as tiny path squares (never glyphs).
    for i in range(n):
        positions = animate_values(frames, i)
        parts.append(
            f'<path d="M-.65-.65h1.3v1.3h-1.3z" fill="{t["portrait"]}">'
            f'<animateTransform attributeName="transform" type="translate" begin="{INTRO_SECONDS}s" '
            f'dur="{loop_duration}s" repeatCount="indefinite" calcMode="linear" '
            f'keyTimes="{key_times}" values="{positions}"/>'
            f'<animate attributeName="opacity" begin="{INTRO_SECONDS}s" dur="{loop_duration}s" '
            f'repeatCount="indefinite" calcMode="linear" keyTimes="{key_times}" '
            f'values="{opacity_values}"/></path>'
        )

    # Travellers give the transition its motion. A denser cloud takes over when
    # they arrive, preserving the look during each hold.
    for index, (name, points, is_dither) in enumerate(holds):
        path = point_path(points) if is_dither else particle_path(points)
        visible = ["0"] * len(frames)
        visible[index * 2 + 2] = ".82"
        visible[index * 2 + 3] = ".82"
        parts.append(
            f'<path d="{path}" fill="none" stroke="{t["portrait"]}" '
            'stroke-width="1" opacity="0">'
            f'<animate attributeName="opacity" begin="{INTRO_SECONDS}s" dur="{loop_duration}s" '
            f'repeatCount="indefinite" calcMode="linear" keyTimes="{key_times}" '
            f'values="{";".join(visible)}"/></path>'
        )
    parts.append("</g>")

    # One-shot scattered intro: sixty random, interleaved point groups.
    intro_ids = rng.integers(0, 60, size=len(portrait))
    order = rng.permutation(60)
    starts = np.empty(60)
    starts[order] = np.linspace(0.05, 1.2, 60)
    for group in range(60):
        pts = portrait[intro_ids == group]
        if not len(pts):
            continue
        parts.append(
            f'<path d="{point_path(pts)}" fill="none" stroke="{t["portrait"]}" '
            'stroke-width="1" opacity="0">'
            f'<animate attributeName="opacity" begin="{num(starts[group])}s" dur=".8s" '
            'values="0;1" fill="freeze"/>'
            '<animate attributeName="opacity" begin="3.08s" dur=".12s" values="1;0" fill="freeze"/>'
            "</path>"
        )
    parts.extend(
        [
            "</g>",
            # Small frame telemetry.
            f'<text x="58" y="551" fill="{t["muted"]}" '
            'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="10">'
            f'PTS {len(portrait):05d} · FS/SERPENTINE</text>',
            # Right information panel (Vim YAML editor view).
            f'<rect x="474" y="88" width="672" height="472" rx="6" fill="{t["panel2"]}" '
            f'stroke="{t["line"]}"/>',
            f'<path d="M474 124H1146" stroke="{t["line"]}"/>',
            f'<text x="490" y="111" fill="{t["chrome"]}" '
            'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="13" '
            'font-weight="700" letter-spacing=".5">profile.yml</text>',
            f'<text x="580" y="111" fill="{t["muted"]}" '
            'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="11">[YAML]</text>',
            f'<rect x="996" y="94" width="132" height="24" rx="12" fill="{t["chrome"]}" opacity=".16" '
            f'stroke="{t["chrome"]}"/>',
            f'<text x="1062" y="111" text-anchor="middle" fill="{t["chrome"]}" '
            'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="13" '
            'font-weight="700">@joseph12n</text>',
        ]
    )

    row_y = 148.0
    for idx, (indent, key, value) in enumerate(YAML_ROWS, 1):
        line_num = f"{idx:2d}"
        if indent == 0:
            content = f'<tspan fill="{t["chrome"]}" font-weight="700">{html.escape(key)}:</tspan>'
            text_x = 525.0
        else:
            content = (
                f'<tspan fill="{t["portrait"]}">{html.escape(key)}: </tspan>'
                f'<tspan fill="{t["text"]}">{html.escape(value)}</tspan>'
            )
            text_x = 542.0

        parts.extend(
            [
                f'<text x="506" y="{num(row_y)}" text-anchor="end" fill="{t["muted"]}" opacity=".45" '
                'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="13">'
                f"{line_num}</text>",
                f'<text x="{num(text_x)}" y="{num(row_y)}" '
                'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="13">'
                f"{content}</text>",
            ]
        )
        row_y += 21.5

    # Vim status line at bottom of panel
    parts.extend(
        [
            f'<path d="M474 526H1146" stroke="{t["line"]}"/>',
            f'<rect x="475" y="527" width="670" height="32" fill="{t["panel"]}" rx="0 0 5 5"/>',
            f'<rect x="485" y="533" width="72" height="20" rx="3" fill="{t["portrait"]}"/>',
            f'<text x="521" y="547" text-anchor="middle" fill="{t["bg"]}" '
            'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="11" font-weight="700">NORMAL</text>',
            f'<text x="569" y="547" fill="{t["text"]}" '
            'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="12" font-weight="600">profile.yml</text>',
            f'<text x="740" y="547" fill="{t["muted"]}" '
            'font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" font-size="11">utf-8</text>',
            f'<text x="1134" y="547" text-anchor="end" fill="{t["muted"]}" '
            'font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" font-size="11">'
            f'{len(YAML_ROWS)}L  100%  utf-8</text>',
            "</svg>",
        ]
    )
    return "".join(parts)


def write_preview(points: np.ndarray, theme: str, dest: Path) -> None:
    """Dump the dither lattice as a PNG so the crop can be judged before the
    full banner is rendered. Each dot becomes one pixel of the grid."""
    t = THEMES[theme]
    height = int(154 + GRID_H) + 20
    width = int(74 + GRID_W) + 20
    canvas = Image.new("RGB", (width, height), t["bg"])
    xs = np.clip(points[:, 0].astype(int) - 54, 0, width - 1)
    ys = np.clip(points[:, 1].astype(int) - 144, 0, height - 1)
    pixels = np.asarray(canvas).copy()
    hex_rgb = tuple(int(t["portrait"][i : i + 2], 16) for i in (1, 3, 5))
    pixels[ys, xs] = hex_rgb
    Image.fromarray(pixels).save(dest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preview", action="store_true",
                        help="only write the dithered portrait as a PNG, so the "
                             "crop and contrast can be checked cheaply")
    parser.add_argument("--theme", choices=tuple(THEMES), default=None,
                        help="limit the render to a single theme")
    args = parser.parse_args()

    if not SOURCE.exists():
        raise SystemExit(f"Missing source portrait: {SOURCE}")
    ASSETS.mkdir(parents=True, exist_ok=True)

    themes = [args.theme] if args.theme else list(THEMES)
    portraits: dict[str, np.ndarray] = {}
    extras: dict[str, np.ndarray] = {}
    for theme in themes:
        offset = list(THEMES).index(theme)
        rng = np.random.default_rng(SEED + offset)
        portraits[theme] = portrait_points(theme, rng)
        print(f"{theme}: {len(portraits[theme]):,} portrait dots")
        for label, path, width_fraction, max_dots in EXTRA_PORTRAITS:
            if path.exists():
                extras[label] = portrait_points(
                    theme, np.random.default_rng(SEED + 50 + offset),
                    source_path=path, width_fraction=width_fraction,
                    top_fraction=0.0, x_shift=0.0, max_dots=max_dots,
                )
                print(f"  + {label}: {len(extras[label]):,} dots")

    if args.preview:
        for theme in themes:
            dest = ASSETS / f"portrait-preview-{theme}.png"
            write_preview(portraits[theme], theme, dest)
            print(f"wrote {dest.relative_to(ROOT)}")
        return

    logos = make_logos()
    for theme in themes:
        rng = np.random.default_rng(SEED + 100 + list(THEMES).index(theme))
        # Rotation: the extra dithered portraits right after the real face, then
        # one dense hold per logo. During each transition the sparse travellers
        # are what you see; the dense layer fades in on arrival, so a shape looks
        # like it crystallises rather than being replaced.
        holds: list[tuple[str, np.ndarray, bool]] = [
            (label, points, True) for label, points in extras.items()
        ]
        holds.extend(
            (name, sample_logo_points(image, rng, HOLD_PARTICLE_COUNT), False)
            for name, image in logos.items()
        )
        svg = render_svg(theme, portraits[theme], holds, rng)
        output = ASSETS / f"banner-{theme}.svg"
        output.write_text(svg, encoding="utf-8")
        byte_size = output.stat().st_size
        print(
            f"{output.relative_to(ROOT)}: {byte_size:,} bytes "
            f"({byte_size / 1024:.1f} KiB), {len(portraits[theme]):,} portrait dots, "
            f"{TRAVELLER_COUNT} travellers"
        )
        print("  rotation: " + " -> ".join(["self"] + [h[0] for h in holds] + ["self"]))


if __name__ == "__main__":
    main()
