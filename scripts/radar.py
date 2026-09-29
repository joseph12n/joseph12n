#!/usr/bin/env python3
"""
radar.py - render a spider / radar chart as a standalone SVG. Stdlib only.

Two sources of data:

  1. a JSON file you control (default)
        python scripts/radar.py --data assets/skills.json -o assets/radar

  2. live language stats from the GitHub API
        python scripts/radar.py --github YOUR_USERNAME -o assets/radar-langs

Writes <out>-dark.svg and <out>-light.svg so the README can swap them with
<picture> + prefers-color-scheme.

skills.json shape:
    {
      "title": "Skill Radar",
      "axes": [ {"label": "Python", "value": 88}, ... ]      // value 0-100
    }
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Ember. Must keep enough contrast between grid/spoke/label to stay readable on
# the GitHub canvas in both colour schemes.
THEMES = {
    "dark": {
        "grid": "#4A2A15",
        "spoke": "#2A180D",
        "label": "#FFE0BE",
        "value": "#FF8A2B",
        "title": "#FFD9A8",
        "fill": "#FF5A14",
        "stroke": "#FF7A2B",
        "vertex": "#FFC24A",
        "bg": "#160C05",
    },
    "light": {
        "grid": "#F0D3BC",
        "spoke": "#FBEADC",
        "label": "#2A1408",
        "value": "#C2410C",
        "title": "#2A1408",
        "fill": "#FF7A2B",
        "stroke": "#E2620F",
        "vertex": "#B23F08",
        "bg": "#FFF6EC",
    },
}

UA = {"User-Agent": "radar.py"}

# Defaults resolve against the repository, not the shell's working directory, so
# `python scripts/radar.py --rate` behaves the same from the repo root, from
# ~/.local/bin, or from a cron entry.
ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# data sources
# --------------------------------------------------------------------------- #


def from_json(path: Path):
    d = json.loads(path.read_text(encoding="utf-8"))
    axes = [(a["label"], float(a["value"])) for a in d["axes"]]
    return d.get("title", "Skill Radar"), axes


def write_json(path: Path, title: str, axes, comment: str | None) -> None:
    """Write skills.json back in the same shape it was read in."""
    payload = {"title": title}
    if comment:
        payload["_comment"] = comment
    payload["axes"] = [{"label": label, "value": value} for label, value in axes]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def prompt_number(current: float) -> float:
    """Ask for a 0-100 value. Enter keeps what is already there."""
    while True:
        try:
            answer = input(f"  [{current:g}] > ").strip()
        except EOFError:  # piped or non-interactive
            return current
        if not answer:
            return current
        try:
            value = float(answer)
        except ValueError:
            print("    eso no es un numero")
            continue
        if not 0 <= value <= 100:
            print("    tiene que estar entre 0 y 100")
            continue
        return value


def rate(path: Path, axis_labels: str | None, render_to: Path | None) -> None:
    """Walk the axes and collect self-assessed values.

    There is no public signal for proficiency — the API can tell you how many
    commits exist, not what they are worth — so these numbers are a judgement
    call and they stay yours to make. What this buys is that they land in
    skills.json in the right shape, ready for the workflow to draw.
    """
    existing = {"title": "Skill Radar", "axes": [], "_comment": None}
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        existing = {"title": raw.get("title", "Skill Radar"),
                    "axes": [(a["label"], float(a["value"])) for a in raw["axes"]],
                    "_comment": raw.get("_comment")}

    if axis_labels:
        labels = [s.strip() for s in axis_labels.split(",") if s.strip()]
        previous = dict(existing["axes"])
        # Keep the old value when a label survives the rename, start at 50 for
        # genuinely new axes so they are visibly unrated rather than a fake zero.
        axes = [(label, previous.get(label, 50.0)) for label in labels]
    else:
        axes = list(existing["axes"])

    if not axes:
        raise SystemExit(
            "no hay ejes que calificar: pasa --axes \"Uno,Dos,Tres\" "
            "para definir el juego de ejes primero"
        )

    print(f"\n  {existing['title']} — {len(axes)} ejes, valores de 0 a 100")
    print("  Enter deja el valor actual.\n")
    rated = [(label, prompt_number(value)) for label, value in axes]

    write_json(path, existing["title"], rated, existing["_comment"])
    print(f"\n  escrito {path}")
    for label, value in rated:
        bar = "#" * int(round(value / 5))
        print(f"    {label:<24} {bar:<20} {value:g}")

    if render_to:
        for theme in ("dark", "light"):
            dest = render_to.with_name(f"{render_to.name}-{theme}.svg")
            dest.write_text(render(existing["title"], rated, theme,
                                   440, 4, True, True), encoding="utf-8")
            print(f"  wrote {dest}")



def _api(url, token):
    req = urllib.request.Request(url, headers=dict(UA))
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def from_github(user: str, token: str | None, limit: int, exclude: set[str],
                curve: float):
    """Sum language bytes across the user's non-fork public repos."""
    totals: dict[str, int] = {}
    page = 1
    while True:
        repos = _api(
            f"https://api.github.com/users/{user}/repos"
            f"?per_page=100&page={page}&type=owner&sort=pushed",
            token,
        )
        if not repos:
            break
        for repo in repos:
            if repo.get("fork") or repo.get("archived"):
                continue
            try:
                langs = _api(repo["languages_url"], token)
            except urllib.error.HTTPError:
                continue
            for name, count in langs.items():
                if name.lower() in exclude:
                    continue
                totals[name] = totals.get(name, 0) + count
        if len(repos) < 100:
            break
        page += 1

    if not totals:
        sys.exit(f"no language data found for '{user}' (private repos need a token)")

    top = sorted(totals.items(), key=lambda kv: -kv[1])[:limit]
    peak = top[0][1]
    # Raw byte ratios are brutally lopsided — one dominant language leaves every
    # other axis pinned near the centre and the shape reads as a spike. `curve`
    # compresses that: 1.0 is linear, 0.5 (default) is sqrt, lower spreads more.
    axes = [(n, round(100 * (c / peak) ** curve, 1)) for n, c in top]
    return f"{user} · language mix", axes


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


FONT = "ui-sans-serif,-apple-system,Segoe UI,Helvetica,Arial,sans-serif"
LBL, VAL, TTL = 13, 11, 15  # font sizes: axis label, axis value, title


def ring(radius, n, start=-math.pi / 2):
    """Vertices of a regular n-gon centred on (0, 0), first one straight up."""
    return [
        (radius * math.cos(start + i * 2 * math.pi / n),
         radius * math.sin(start + i * 2 * math.pi / n))
        for i in range(n)
    ]


def text_width(s, font_size):
    """Rough advance width for a humanist sans. Deliberately generous —
    it only feeds the bounding box, so over-estimating just adds padding."""
    return len(s) * font_size * 0.62


def render(title, axes, theme: str, size: int, rings: int, show_values: bool,
           animate: bool) -> str:
    c = THEMES[theme]
    n = len(axes)
    r = size / 2 - 8
    gap = 20  # how far the labels sit beyond the outer ring

    vals = [max(0.0, min(100.0, v)) for _, v in axes]
    outer = ring(r, n)

    # Lay the labels out first, in centre-relative coordinates, so the viewBox
    # can be sized around whatever they actually occupy. A fixed viewBox clips
    # long labels on the left/right spokes.
    labels = []
    for i, (label, _) in enumerate(axes):
        ang = -math.pi / 2 + i * 2 * math.pi / n
        cosv, sinv = math.cos(ang), math.sin(ang)
        lx, ly = (r + gap) * cosv, (r + gap) * sinv
        anchor = "middle" if abs(cosv) < 0.25 else ("start" if cosv > 0 else "end")
        dy = 4 if abs(sinv) < 0.25 else (14 if sinv > 0 else -5)
        labels.append((lx, ly + dy, anchor, label, vals[i]))

    minx, maxx, miny, maxy = -r, r, -r, r
    for lx, ly, anchor, label, v in labels:
        w = max(text_width(label, LBL),
                text_width(f"{v:g}", VAL) if show_values else 0.0)
        if anchor == "start":
            x0, x1 = lx, lx + w
        elif anchor == "end":
            x0, x1 = lx - w, lx
        else:
            x0, x1 = lx - w / 2, lx + w / 2
        y0 = ly - LBL
        y1 = ly + 4 + (VAL + 4 if show_values else 0)
        minx, maxx = min(minx, x0), max(maxx, x1)
        miny, maxy = min(miny, y0), max(maxy, y1)

    pad = 10
    title_h = TTL + 14 if title else 0
    W = round((maxx - minx) + 2 * pad)
    H = round((maxy - miny) + 2 * pad + title_h)
    ox, oy = -minx + pad, -miny + pad + title_h

    # A long title ("<user> · language mix") can be wider than the chart itself.
    # Widen the canvas and re-centre the chart inside it rather than clipping.
    if title:
        need = round(text_width(title, TTL) + 2 * pad)
        if need > W:
            ox += (need - W) / 2
            W = need

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" role="img" '
        f'aria-label="{esc(title) or "radar chart"}" font-family="{FONT}">'
    ]
    if c["bg"] != "none":
        parts.append(f'<rect width="100%" height="100%" fill="{c["bg"]}"/>')
    if title:
        parts.append(
            f'<text x="{W / 2:.1f}" y="{pad + TTL:.0f}" text-anchor="middle" '
            f'font-size="{TTL}" font-weight="700" fill="{c["title"]}">'
            f'{esc(title)}</text>'
        )
    parts.append(f'<g transform="translate({ox:.1f},{oy:.1f})">')

    # concentric rings, faintest in the middle
    for k in range(rings, 0, -1):
        d = " ".join(f"{x:.1f},{y:.1f}" for x, y in ring(r * k / rings, n))
        parts.append(
            f'<polygon points="{d}" fill="none" stroke="{c["grid"]}" '
            f'stroke-width="1" opacity="{0.35 + 0.5 * k / rings:.2f}"/>'
        )

    # spokes
    for x, y in outer:
        parts.append(
            f'<line x1="0" y1="0" x2="{x:.1f}" y2="{y:.1f}" '
            f'stroke="{c["spoke"]}" stroke-width="1"/>'
        )

    # the data shape. SMIL rather than CSS: animateTransform scales about the
    # group's own origin, which is already the centre of the chart here.
    shape = [(px * v / 100, py * v / 100) for (px, py), v in zip(outer, vals)]
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in shape)
    parts.append("<g>")
    if animate:
        parts.append(
            '<animateTransform attributeName="transform" type="scale" '
            'values="0.04;1" dur="1.1s" calcMode="spline" keyTimes="0;1" '
            'keySplines="0.22 1 0.36 1" fill="freeze"/>'
        )
    parts.append(
        f'<polygon points="{d}" fill="{c["fill"]}" fill-opacity="0.22" '
        f'stroke="{c["stroke"]}" stroke-width="2.5" stroke-linejoin="round"/>'
    )
    for x, y in shape:
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.6" fill="{c["vertex"]}" '
            f'stroke="{c["stroke"]}" stroke-width="1.2"/>'
        )
    parts.append("</g>")

    # axis labels
    for lx, ly, anchor, label, v in labels:
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
            f'font-size="{LBL}" font-weight="600" fill="{c["label"]}">'
            f'{esc(label)}</text>'
        )
        if show_values:
            parts.append(
                f'<text x="{lx:.1f}" y="{ly + VAL + 4:.1f}" text-anchor="{anchor}" '
                f'font-size="{VAL}" fill="{c["value"]}">{v:g}</text>'
            )

    parts.append("</g></svg>")
    return "".join(parts)


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# --------------------------------------------------------------------------- #


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group()
    src.add_argument("--data", type=Path, default=ROOT / "assets/skills.json")
    src.add_argument("--github", metavar="USER",
                     help="build the radar from GitHub language stats instead")
    p.add_argument("-o", "--out", type=Path, default=ROOT / "assets/radar",
                   help="output path WITHOUT extension")
    p.add_argument("--title", help="override the chart title ('' for none)")
    p.add_argument("--size", type=int, default=440)
    p.add_argument("--rings", type=int, default=4)
    p.add_argument("--limit", type=int, default=7,
                   help="max axes when using --github")
    p.add_argument("--exclude", default="html,css,shell,makefile,dockerfile,batchfile",
                   help="comma-separated languages to skip in --github mode")
    p.add_argument("--curve", type=float, default=0.5,
                   help="--github axis scaling: 1.0 linear, 0.5 sqrt (default), "
                        "0.3 flattens a one-language-dominant profile")
    p.add_argument("--values", action="store_true", help="print the number per axis")
    p.add_argument("--no-animate", dest="animate", action="store_false",
                   help="disable the grow-in animation")
    p.add_argument("--rate", action="store_true",
                   help="interactively rate the axes and write them back to "
                        "--data (Enter keeps the current value)")
    p.add_argument("--axes", metavar="A,B,C",
                   help="with --rate, replace the axis set, comma separated")
    p.add_argument("--no-render", dest="render", action="store_false",
                   help="with --rate, only write the JSON, do not redraw")
    args = p.parse_args(argv)

    if args.rate:
        rate(args.data, args.axes, None if not args.render else args.out)
        return

    if args.github:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        excl = {s.strip().lower() for s in args.exclude.split(",") if s.strip()}
        title, axes = from_github(args.github, token, args.limit, excl, args.curve)
    else:
        if not args.data.exists():
            sys.exit(f"no data file: {args.data}")
        title, axes = from_json(args.data)

    if args.title is not None:
        title = args.title
    if len(axes) < 3:
        sys.exit("a radar chart needs at least 3 axes")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    for theme in ("dark", "light"):
        svg = render(title, axes, theme, args.size, args.rings, args.values,
                     args.animate)
        dest = args.out.with_name(f"{args.out.name}-{theme}.svg")
        dest.write_text(svg, encoding="utf-8")
        print(f"wrote {dest}  ({len(axes)} axes)")


if __name__ == "__main__":
    main()
