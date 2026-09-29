#!/usr/bin/env python3
"""Fetch the real brand icons for the tech-stack table and emit its HTML.

Two sources, tried in order per icon:

  skill:<slug>  skillicons.dev — rounded tiles in the brand's own colour
  simple:<slug> cdn.simpleicons.org — flat monochrome glyphs + brand hex

Why the validation matters: skillicons.dev answers HTTP 200 with a valid but
empty SVG for any slug it does not have, with the literal string "undefined"
inside the group. A browser renders that as nothing at all, so a broken slug
shows up as a blank cell in the README with no error anywhere. Everything here
is checked before it is trusted — HTTP status, "undefined", path count, and
finally the rasterised alpha coverage, which catches an SVG that parsed but
drew nothing.

A simpleicons fallback is rebuilt into a rounded tile using the brand colour
the CDN bakes into the response, so icons from both sources look like they came
from the same set.

    python scripts/icons/fetch_icons.py             # fetch + write assets/icons
    python scripts/icons/fetch_icons.py --table     # also print the README table
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "assets/icons"

UA = {"User-Agent": "joseph12n/fetch-icons"}
TIMEOUT = 30

# skillicons renders 48x48 tiles; glyphs sit inside a rounded square.
TILE = 48
RADIUS = 8
GLYPH = 28  # side of the centred brand mark

# (category key, tree glyph, [ (label, candidate sources...) ])
# Each icon lists its fallbacks in order; the first source that validates wins.
STACK: list[tuple[str, str, list[tuple[str, tuple[str, ...]]]]] = [
    ("backend_java", "⚙", [
        ("Java",        ("skill:java", "simple:java")),
        ("Kotlin",      ("skill:kotlin", "simple:kotlin")),
        ("Spring Boot", ("skill:spring", "simple:springboot")),
        ("Maven",       ("skill:maven", "simple:apachemaven")),
        ("Gradle",      ("skill:gradle", "simple:gradle")),
    ]),
    ("frontend_web", "⚛", [
        ("React",       ("skill:react", "simple:react")),
        ("Next.js",     ("skill:nextjs", "simple:nextdotjs")),
        ("TypeScript",  ("skill:typescript", "simple:typescript")),
        ("Tailwind",    ("skill:tailwindcss", "simple:tailwindcss")),
        ("Node.js",     ("skill:nodejs", "simple:nodedotjs")),
    ]),
    ("data_storage", "▣", [
        ("MongoDB",     ("skill:mongodb", "simple:mongodb")),
        ("PostgreSQL",  ("skill:postgresql", "simple:postgresql")),
        ("MySQL",       ("skill:mysql", "simple:mysql")),
        ("Redis",       ("skill:redis", "simple:redis")),
        ("SQLite",      ("skill:sqlite", "simple:sqlite")),
    ]),
    ("qa_quality", "◉", [
        ("JUnit 5",     ("skill:junit5", "simple:junit5")),
        ("Vitest",      ("skill:vitest", "simple:vitest")),
        ("Cypress",     ("skill:cypress", "simple:cypress")),
        ("pytest",      ("simple:pytest", "skill:pytest")),
        ("Qase",        ("simple:qase", "skill:qase")),
    ]),
    ("mobile_apps", "☁", [
        ("Android",     ("simple:android", "skill:android")),
        ("Jetpack Compose", ("simple:jetpackcompose", "skill:jetpackcompose")),
        ("Expo",        ("simple:expo", "skill:expo")),
    ]),
    ("ai_agentic", "✦", [
        ("MCP",         ("simple:modelcontextprotocol", "skill:modelcontextprotocol")),
        ("Ollama",      ("simple:ollama", "skill:ollama")),
        ("OpenCode",    ("simple:opencode", "skill:opencode")),
        ("Docker",      ("skill:docker", "simple:docker")),
    ]),
]


# --------------------------------------------------------------------------- #
# fetching
# --------------------------------------------------------------------------- #


def get(url: str) -> str:
    request = urllib.request.Request(url, headers=dict(UA))
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read().decode("utf-8", "replace")


def url_for(source: str) -> str:
    provider, slug = source.split(":", 1)
    if provider == "skill":
        return f"https://skillicons.dev/icons?i={slug}"
    return f"https://cdn.simpleicons.org/{slug}"


def fetch(source: str) -> str:
    """Return the SVG text, or raise if the source does not have the icon."""
    try:
        body = get(url_for(source))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise LookupError(f"{source}: 404") from None
        raise
    except urllib.error.URLError as error:
        raise LookupError(f"{source}: {error.reason}") from None

    if "undefined" in body:
        raise LookupError(f"{source}: empty tile (skillicons 'undefined')")
    if not body.lstrip().startswith(("<svg", "<?xml")):
        raise LookupError(f"{source}: not an SVG")
    shapes = len(re.findall(r"<(path|rect|circle|polygon)\b", body))
    if shapes == 0:
        raise LookupError(f"{source}: SVG has no shapes")
    return body.strip()


# --------------------------------------------------------------------------- #
# rebuilding a simpleicons glyph as a tile
# --------------------------------------------------------------------------- #


def tile_from_simpleicons(svg: str) -> str:
    """Wrap a monochrome glyph in a rounded tile coloured with its brand hex.

    The simpleicons CDN bakes the official brand colour into the ``fill``
    attribute of the response, which is why this reads it back out instead of
    hard-coding a palette that would drift.
    """
    colour_match = re.search(r'fill="(#[0-9A-Fa-f]{6})"', svg)
    brand = (colour_match.group(1) if colour_match else "#5A6270").upper()

    body = svg[svg.index(">") + 1 : svg.rindex("</svg>")].strip()
    if not body:
        raise LookupError("simpleicons: SVG body is empty")

    # 24x24 viewBox into a centred GLYPH x GLYPH box on the TILE canvas.
    scale = GLYPH / 24
    offset = (TILE - GLYPH) / 2
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{TILE}" height="{TILE}" '
        f'viewBox="0 0 {TILE} {TILE}" role="img">'
        f'<rect width="{TILE}" height="{TILE}" rx="{RADIUS}" fill="{brand}"/>'
        f'<g transform="translate({offset:g},{offset:g}) scale({scale:.5f})" '
        f'fill="#FFFFFF">{body}</g>'
        "</svg>"
    )


def normalise(svg: str) -> str:
    """One shape of output: sized, so the README does not need width attributes."""
    body = svg if svg.lstrip().startswith("<svg") else svg[svg.index("<svg"):]
    body = re.sub(r'\swidth="[^"]*"', "", body, count=1)
    body = re.sub(r'\sheight="[^"]*"', "", body, count=1)
    return body


def renders_something(svg: str) -> bool:
    """Rasterise and check there is actual ink on the canvas."""
    try:
        import cairosvg
        import io

        from PIL import Image
        import numpy as np
    except ImportError:
        return True  # cannot verify here; the textual checks already ran
    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=TILE)
    alpha = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA").getchannel("A"))
    # A blank tile covers ~0% of pixels; a real icon covers a few percent to
    # most of it. Anything under 1% is a glyph that failed to draw.
    return float((alpha > 24).mean()) > 0.01


# --------------------------------------------------------------------------- #
# driving
# --------------------------------------------------------------------------- #


def slugify(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def resolve(label: str, sources: tuple[str, ...]) -> tuple[str, str]:
    for source in sources:
        try:
            svg = fetch(source)
        except LookupError:
            continue
        if source.startswith("simple:"):
            svg = tile_from_simpleicons(svg)
        if not renders_something(svg):
            continue
        return source, normalise(svg)
    raise LookupError(f"no source worked: {label} ({', '.join(sources)})")


def table_html() -> str:
    """Render the tech-stack table body: two categories per row.

    The last row closes the tree with the corner branch, matching the ASCII in
    the README rather than repeating the tee.
    """
    entries = []
    base = OUT.relative_to(ROOT).as_posix()  # "assets/icons", as the README sees it
    for key, glyph, icons in STACK:
        cells = [
            f'<img src="{base}/{slugify(label)}.svg" alt="{label}">'
            for label, _ in icons
            if (OUT / f"{slugify(label)}.svg").exists()
        ]
        entries.append((glyph, key, cells, " · ".join(label for label, _ in icons)))

    rows = []
    for start in range(0, len(entries), 2):
        pair = entries[start : start + 2]
        tds = []
        for index, (glyph, key, cells, labels) in enumerate(pair):
            branch = "╰─" if start + index == len(entries) - 1 else "├─"
            icons_html = "\n".join(f"        {cell}" for cell in cells)
            tds.append(f'''      <td width="50%" valign="top"><code>{branch} {glyph} {key}:</code><br><br>
{icons_html}<br>
        <sub><code>{labels}</code></sub>
      </td>''')
        if len(tds) == 1:  # odd count, keep the grid intact
            tds.append('      <td width="50%" valign="top"></td>')
        rows.append("    <tr>\n" + "\n".join(tds) + "\n    </tr>")
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--table", action="store_true",
                        help="print the README table markup to stdout")
    args = parser.parse_args()

    if args.table:
        print(table_html())
        return

    OUT.mkdir(parents=True, exist_ok=True)
    wanted = [(key, label, sources) for key, _, icons in STACK
              for label, sources in icons]
    failed = []
    for _, label, sources in wanted:
        try:
            source, svg = resolve(label, sources)
        except LookupError as error:
            failed.append((label, str(error)))
            print(f"  FALLA  {error}")
            continue
        dest = OUT / f"{slugify(label)}.svg"
        dest.write_text(svg, encoding="utf-8")
        print(f"  ok     {dest.name:<24} {len(svg):>6,} B  <- {source}")

    print(f"\n{len(wanted) - len(failed)}/{len(wanted)} iconos en {OUT.relative_to(ROOT)}")
    if failed:
        print("faltantes: " + ", ".join(label for label, _ in failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
