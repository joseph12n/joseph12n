#!/usr/bin/env python3
"""Render assets/whoami-ember.svg, with the skill meters read from skills.json.

The meters used to be hand-typed into the SVG, which meant the terminal card and
the radar could quote different numbers for the same six skills — and did, twice,
before this script existed. Reading the numbers from the one file that owns them
makes that class of drift impossible.

    python scripts/whoami/render.py

Everything else on the card is fixed layout and lives in LAYOUT below.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "assets/skills.json"
OUT = ROOT / "assets/whoami-ember.svg"

W, H = 960, 420
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'DejaVu Sans Mono', monospace"

# Ember, matching THEMES["dark"] in scripts/banner/generate.py.
BG, PANEL, PANEL2, LINE = "#160C05", "#20120A", "#2A180D", "#4A2A15"
MUTED, TEXT, DIM = "#A8714F", "#FFE0BE", "#7A5233"
FLAME, AMBER, EMBER = "#FF8A2B", "#FFC24A", "#E2620F"

# Short labels for the meter panel. Keyed by position, because the labels in
# skills.json ("Backend (Java/Spring)") are too long for a 10-column gutter.
METER_LABELS = ("backend", "frontend", "qa", "data", "mobile", "devops")

METER_X, METER_Y0, METER_STEP = 648, 142, 28
BAR_X, VALUE_X, BAR_CELLS = 744, 918, 10

# Left panel: label, value, colour, font size.
DETAIL = [
    (34, 168, "where:", "Bogotá, Colombia \U0001F1E8\U0001F1F4", AMBER, 15),
    (34, 196, "mission:", "productos completos, de la DB a la interfaz", EMBER, 15),
    (34, 224, "learning:", "Docker · Kubernetes · MongoDB aggregations", AMBER, 15),
]
EXPERTISE = [
    (298, "backend/", "Java · Spring Boot · Kotlin · POO · capas · DTOs"),
    (322, "frontend/", "React · Next.js · TypeScript · Tailwind · Vite"),
    (346, "mobile/", "Android · Compose · Expo · React Native"),
    (370, "qa/", "JUnit · Selenium · Cypress · pytest · JMeter · Qase · ZenHub"),
]


def text(x, y, body, fill, size=13, anchor=None, weight=None) -> str:
    attrs = f'x="{x}" y="{y}" fill="{fill}" font-family="{MONO}" font-size="{size}"'
    if anchor:
        attrs += f' text-anchor="{anchor}"'
    if weight:
        attrs += f' font-weight="{weight}"'
    return f"  <text {attrs}>{body}</text>"


def meter(value: float) -> str:
    """A ten-cell block meter. Partial cells round down so 84 reads as eight."""
    filled = round(value / 100 * BAR_CELLS)
    return "█" * filled + "░" * (BAR_CELLS - filled)


def render(values: list[float]) -> str:
    p = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" role="img" aria-labelledby="title desc">',
        '  <title id="title">Joseph Varón — Full Stack Developer</title>',
        '  <desc id="desc">Terminal card with Joseph\'s location, mission, '
        'expertise folders and skill meters.</desc>',
        "",
        "  <!-- window chrome -->",
        f'  <rect width="{W}" height="{H}" rx="16" fill="{BG}"/>',
        f'  <rect x="1" y="1" width="{W-2}" height="{H-2}" rx="15" fill="none" stroke="{LINE}"/>',
        f'  <circle cx="34" cy="30" r="6" fill="#f7768e"/>',
        f'  <circle cx="56" cy="30" r="6" fill="#e0af68"/>',
        f'  <circle cx="78" cy="30" r="6" fill="#9ece6a"/>',
        text(106, 36, "joseph12n@fedora — ~/profile", MUTED, 14),
        text(932, 36, "bash", DIM, 14, anchor="end"),
        f'  <path d="M20 58H940" stroke="{LINE}"/>',
        "",
        "  <!-- ================= left panel ================= -->",
        text(34, 92, "❯ whoami", FLAME, 17, weight="700"),
        text(34, 122, "joseph_varon", TEXT, 16),
        text(176, 122, "—", MUTED, 16),
        text(200, 122, "Full Stack Developer", EMBER, 16),
        f'  <path d="M34 140H606" stroke="{PANEL2}"/>',
    ]
    for x, y, label, value, colour, size in DETAIL:
        p.append(text(x, y, label, MUTED, size))
        p.append(text(132, y, value, colour, size))
    p += [
        "",
        text(34, 266, "❯ ls expertise/", FLAME, 17, weight="700"),
    ]
    for y, folder, items in EXPERTISE:
        p.append(text(34, y, folder, EMBER, 15))
        p.append(text(168, y, items, "#E8CBA6", 15))

    p += [
        "",
        "  <!-- ================= right panel ================= -->",
        f'  <rect x="626" y="74" width="314" height="312" rx="9" fill="{PANEL2}" stroke="{LINE}"/>',
        text(648, 102, "SKILL METERS", AMBER, 13, weight="700"),
        f'  <path d="M648 114H918" stroke="{LINE}"/>',
    ]
    for index, (label, value) in enumerate(zip(METER_LABELS, values)):
        y = METER_Y0 + index * METER_STEP
        p.append("")
        p.append(text(METER_X, y, f"{label:<9}", MUTED, 13))
        p.append(text(BAR_X, y, meter(value), FLAME, 13))
        p.append(text(VALUE_X, y, f"{value:g}", TEXT, 13, anchor="end"))

    p += [
        "",
        f'  <path d="M648 302H918" stroke="{LINE}"/>',
        text(648, 330, "os", MUTED, 13),
        text(700, 330, "Fedora", AMBER, 13),
        text(648, 354, "utc", MUTED, 13),
        text(700, 354, "UTC-5", AMBER, 13),
        text(780, 354, "· bogota", DIM, 13),
        "",
        "  <!-- ================= status line ================= -->",
        f'  <path d="M20 396H940" stroke="{LINE}"/>',
        text(34, 414,
             "status: construyendo · environment: producción · uptime: aprendiendo a diario",
             DIM, 12),
        "</svg>",
    ]
    return "\n".join(p) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skills", type=Path, default=SKILLS)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    if not args.skills.exists():
        raise SystemExit(f"no such file: {args.skills}")
    axes = json.loads(args.skills.read_text(encoding="utf-8"))["axes"]
    values = [float(a["value"]) for a in axes]

    if len(values) != len(METER_LABELS):
        raise SystemExit(
            f"{args.skills.name} has {len(values)} axes but the card draws "
            f"{len(METER_LABELS)} meters. Add a row to METER_LABELS or drop an axis."
        )

    args.out.write_text(render(values), encoding="utf-8")
    pairs = ", ".join(f"{lbl}={v:g}" for lbl, v in zip(METER_LABELS, values))
    print(f"wrote {args.out.relative_to(ROOT)}  ({len(values)} meters: {pairs})")


if __name__ == "__main__":
    main()
