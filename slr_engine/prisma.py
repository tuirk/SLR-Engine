"""PRISMA flow diagram SVG generator.

Two diagrams:
  - prisma_flow.svg    — canonical PRISMA 2020 (publication-ready)
  - expanded_prisma.svg — engine-aware (scoping + synthesis stages)

Both are pure SVG built from audit-log counts. No external dependencies.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from .protocol import _flow_counts
from .store import ProjectConfig, ProjectPaths


# ---------- SVG primitives ----------

def _box(x: int, y: int, w: int, h: int, lines: list[str],
         fill: str = "#ffffff", stroke: str = "#222") -> str:
    """A rectangle with text lines centered."""
    line_height = 16
    total_h = len(lines) * line_height
    y_start = y + (h - total_h) // 2 + line_height - 4
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.5" rx="4"/>'
    ]
    cx = x + w // 2
    for i, line in enumerate(lines):
        ty = y_start + i * line_height
        # Escape XML-sensitive characters
        safe = (line.replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;"))
        # First line bold
        weight = "600" if i == 0 else "400"
        out.append(
            f'<text x="{cx}" y="{ty}" font-family="sans-serif" '
            f'font-size="12" font-weight="{weight}" '
            f'text-anchor="middle">{safe}</text>'
        )
    return "\n".join(out)


def _arrow(x1: int, y1: int, x2: int, y2: int) -> str:
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
        f'stroke="#222" stroke-width="1.5" marker-end="url(#arrow)"/>'
    )


def _arrow_def() -> str:
    return """<defs>
<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5"
        markerWidth="6" markerHeight="6" orient="auto-start-reverse">
  <path d="M0,0 L10,5 L0,10 z" fill="#222"/>
</marker>
</defs>"""


def _phase_label(x: int, y: int, w: int, label: str) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="22" '
        f'fill="#e8e8e8" stroke="#222" stroke-width="1"/>'
        f'<text x="{x + w // 2}" y="{y + 16}" font-family="sans-serif" '
        f'font-size="13" font-weight="700" text-anchor="middle">{label}</text>'
    )


def _included_lines(ft: dict, ta_included: int, awaiting: int) -> list[str]:
    """Final box text. Before full-text screening there is no final count yet."""
    if not sum(ft.values()) and ta_included:
        return ["Studies included in review",
                "pending: full-text screening not run",
                f"({ta_included} included at title/abstract)"]
    lines = ["Studies included in review", f"n = {ft.get('include', 0)}"]
    if awaiting:
        lines.append(f"({awaiting} reports still awaiting full-text screening)")
    return lines


# ---------- canonical PRISMA 2020 ----------

def render_canonical(cfg: ProjectConfig, flow: dict) -> str:
    """Build the standard PRISMA 2020 four-phase flow diagram."""
    W = 880
    H = 760

    # Per-source identification text. Scopus and Web of Science are databases
    # even when their results were exported by hand; seed papers the user
    # supplied are not a database search.
    per_source = flow.get("per_source") or {}
    id_lines = ["Records identified from databases:"]
    for src, n in per_source.items():
        if src == "seed":
            continue
        manual = " (manual export)" if src in ("scopus", "web_of_science") else ""
        id_lines.append(f"  {src}{manual}: n = {n}")

    # Other-sources column
    other_lines = ["Records identified from other sources:"]
    if per_source.get("seed"):
        other_lines.append(f"  seed papers (user-supplied): n = {per_source['seed']}")
    snow = flow.get("snowball_by_direction") or {}
    if snow:
        for direction, n in snow.items():
            other_lines.append(f"  snowball ({direction}): n = {n}")
    if len(other_lines) == 1:
        other_lines.append("  (none)")

    after_dedup = flow.get("records_total", 0)
    dedup_removed = flow.get("duplicates_removed", flow.get("dedup_merges", 0))

    ta = flow.get("ta_decisions") or {}
    ta_total = sum(ta.values())
    ta_excluded = ta.get("exclude", 0)
    ta_included = ta.get("include", 0)
    ta_unsure = ta.get("unsure", 0)

    dl = flow.get("downloads") or {}
    dl_rec = flow.get("download_records") or {}
    dl_success = dl_rec.get("success", dl.get("success", 0))
    dl_failed = dl_rec.get(
        "not_retrieved",
        dl.get("failed", 0) + dl.get("skipped_closed", 0)
        + dl.get("skipped_no_license", 0),
    )

    ft = flow.get("ft_decisions") or {}
    ft_excluded = ft.get("exclude", 0)
    ft_unsure = ft.get("unsure", 0)
    ft_screened = sum(ft.values())
    awaiting = max(dl_success - ft_screened, 0)

    assessed_lines = ["Reports assessed for eligibility", f"n = {ft_screened}"]
    if awaiting:
        assessed_lines.append(f"(awaiting full-text: {awaiting})")

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}">',
        _arrow_def(),
        f'<rect width="{W}" height="{H}" fill="#fafafa"/>',
        # Title
        f'<text x="{W//2}" y="28" font-family="sans-serif" font-size="16" '
        f'font-weight="700" text-anchor="middle">'
        f'PRISMA 2020 Flow Diagram — {cfg.project_id}</text>',
    ]

    # Phase labels (left rail)
    parts += [
        _phase_label(20, 60, 110, "Identification"),
        _phase_label(20, 220, 110, "Screening"),
        _phase_label(20, 400, 110, "Eligibility"),
        _phase_label(20, 620, 110, "Included"),
    ]

    # Identification phase: two columns
    parts.append(_box(150, 60, 330, 130, id_lines, fill="#eef4ff"))
    parts.append(_box(510, 60, 330, 130, other_lines, fill="#eef4ff"))

    # Combined → after dedup
    parts.append(
        _box(280, 220, 400, 60,
             [f"Records after deduplication",
              f"n = {after_dedup}",
              f"(removed {dedup_removed} duplicates)"],
             fill="#fff8d6")
    )
    parts.append(_arrow(290, 195, 380, 220))   # left ID -> dedup
    parts.append(_arrow(670, 195, 580, 220))   # right ID -> dedup

    # Screening phase
    parts.append(
        _box(280, 320, 220, 60,
             ["Records screened (T/A)", f"n = {ta_total}"],
             fill="#eef4ff")
    )
    parts.append(
        _box(560, 320, 220, 60,
             ["Records excluded (T/A)",
              f"n = {ta_excluded}",
              f"(unsure: {ta_unsure})"],
             fill="#fde8e8")
    )
    parts.append(_arrow(480, 280, 390, 320))
    parts.append(_arrow(500, 350, 560, 350))

    # Eligibility phase
    parts.append(
        _box(280, 420, 220, 60,
             ["Reports sought for retrieval", f"n = {ta_included}"],
             fill="#eef4ff")
    )
    parts.append(
        _box(560, 420, 220, 60,
             ["Reports not retrieved", f"n = {dl_failed}"],
             fill="#fde8e8")
    )
    parts.append(_arrow(390, 380, 390, 420))
    parts.append(_arrow(500, 450, 560, 450))

    parts.append(_box(280, 510, 220, 60, assessed_lines, fill="#eef4ff"))
    parts.append(
        _box(560, 510, 220, 60,
             ["Reports excluded (full-text)",
              f"n = {ft_excluded}",
              f"(unsure: {ft_unsure})"],
             fill="#fde8e8")
    )
    parts.append(_arrow(390, 480, 390, 510))
    parts.append(_arrow(500, 540, 560, 540))

    # Included
    parts.append(
        _box(280, 640, 320, 70, _included_lines(ft, ta_included, awaiting),
             fill="#dff5e1")
    )
    parts.append(_arrow(390, 570, 410, 640))

    parts.append("</svg>")
    return "\n".join(parts)


# ---------- expanded engine-aware ----------

def render_expanded(cfg: ProjectConfig, flow: dict) -> str:
    """Build the expanded diagram with scoping and synthesis phases."""
    W = 920
    H = 1080

    n_rqs = len(cfg.research_questions or [])
    n_hypotheses = len(cfg.hypotheses or [])
    framework_type = (cfg.framework or {}).get("type", "none")

    pre_lines = [
        "Scoping",
        f"Topic: {'set' if cfg.topic else 'unset'}",
        f"Aim: {'set' if cfg.aim else 'unset'}",
        f"Research questions: {n_rqs}",
        f"Framework: {framework_type}",
        f"Hypotheses: {n_hypotheses}",
        f"Inclusion criteria: {len(cfg.inclusion)}",
        f"Exclusion criteria: {len(cfg.exclusion)}",
    ]

    # Reuse canonical for the middle of the flow, with small offsets
    after_dedup = flow.get("records_total", 0)
    dedup_removed = flow.get("duplicates_removed", flow.get("dedup_merges", 0))
    ta = flow.get("ta_decisions") or {}
    ta_total = sum(ta.values())
    ta_excluded = ta.get("exclude", 0)
    ta_included = ta.get("include", 0)

    dl = flow.get("downloads") or {}
    dl_rec = flow.get("download_records") or {}
    dl_success = dl_rec.get("success", dl.get("success", 0))
    dl_failed = dl_rec.get(
        "not_retrieved",
        dl.get("failed", 0) + dl.get("skipped_closed", 0),
    )

    ft = flow.get("ft_decisions") or {}
    ft_excluded = ft.get("exclude", 0)
    ft_included = ft.get("include", 0)
    ft_screened = sum(ft.values())
    awaiting = max(dl_success - ft_screened, 0)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}">',
        _arrow_def(),
        f'<rect width="{W}" height="{H}" fill="#fafafa"/>',
        f'<text x="{W//2}" y="28" font-family="sans-serif" font-size="16" '
        f'font-weight="700" text-anchor="middle">'
        f'Expanded PRISMA — {cfg.project_id}</text>',
        f'<text x="{W//2}" y="48" font-family="sans-serif" font-size="11" '
        f'font-style="italic" text-anchor="middle">'
        f'Engine-aware extension. For publication, use prisma_flow.svg.</text>',
    ]

    # Phase rail
    parts += [
        _phase_label(20, 70, 110, "Scoping"),
        _phase_label(20, 230, 110, "Search"),
        _phase_label(20, 360, 110, "Identification"),
        _phase_label(20, 480, 110, "Screening"),
        _phase_label(20, 600, 110, "Eligibility"),
        _phase_label(20, 800, 110, "Included"),
        _phase_label(20, 920, 110, "Synthesis"),
    ]

    # Scoping box (engine extension)
    parts.append(
        _box(140, 70, 760, 140, pre_lines, fill="#f3eaff")
    )
    parts.append(_arrow(520, 210, 520, 230))

    # Search strategy
    per_source = flow.get("per_source") or {}
    n_sources = flow.get(
        "sources_searched", sum(1 for s in per_source if s != "seed")
    )
    parts.append(
        _box(140, 240, 760, 80,
             [f"Search executed against {n_sources} sources",
              f"Total source-hits: {flow.get('source_hits_total', 0)}"],
             fill="#e3effe")
    )
    parts.append(_arrow(520, 320, 520, 360))

    # Identification — collapsed
    id_lines = ["Records identified"]
    for src, n in per_source.items():
        label = "seed papers (user-supplied)" if src == "seed" else src
        id_lines.append(f"{label}: {n}")
    parts.append(
        _box(140, 370, 470, 100, id_lines, fill="#eef4ff")
    )
    parts.append(
        _box(640, 370, 260, 100,
             [f"After deduplication", f"n = {after_dedup}",
              f"(removed {dedup_removed} duplicates)"],
             fill="#fff8d6")
    )
    parts.append(_arrow(610, 420, 640, 420))
    parts.append(_arrow(770, 470, 410, 480))

    # Screening
    parts.append(
        _box(140, 490, 470, 80,
             [f"Title/abstract screened: {ta_total}",
              f"included: {ta_included} | excluded: {ta_excluded}"],
             fill="#eef4ff")
    )
    parts.append(
        _box(640, 490, 260, 80,
             ["Risk-of-bias assessment",
              f"performed on: {flow.get('extractions_with_risk_of_bias', 0)}"],
             fill="#f3eaff")
    )
    parts.append(_arrow(380, 570, 380, 600))

    # Eligibility
    parts.append(
        _box(140, 610, 470, 70,
             [f"Full text retrieved: {dl_success}",
              f"(not retrieved: {dl_failed})"],
             fill="#eef4ff")
    )
    parts.append(
        _box(140, 690, 470, 70,
             [f"Full-text screened: {ft_screened}"
              + (f" (awaiting: {awaiting})" if awaiting else ""),
              f"included: {ft_included} | excluded: {ft_excluded}"],
             fill="#eef4ff")
    )
    parts.append(_arrow(380, 680, 380, 690))

    # Snowball stub
    if flow.get("snowball_links", 0):
        parts.append(
            _box(640, 610, 260, 150,
                 ["Snowball sampling",
                  f"links discovered: {flow['snowball_links']}",
                  f"backward: {flow.get('snowball_by_direction', {}).get('backward', 0)}",
                  f"forward: {flow.get('snowball_by_direction', {}).get('forward', 0)}"],
                 fill="#f3eaff")
        )

    parts.append(_arrow(380, 760, 380, 800))

    # Included
    parts.append(
        _box(140, 810, 760, 80, _included_lines(ft, ta_included, awaiting),
             fill="#dff5e1")
    )
    parts.append(_arrow(520, 890, 520, 920))

    # Synthesis (engine extension)
    parts.append(
        _box(140, 930, 760, 110,
             ["Synthesis artifacts",
              f"Extractions: {flow.get('extractions_total', 0)}",
              f"With RoB: {flow.get('extractions_with_risk_of_bias', 0)}",
              f"Hypotheses tracked: {n_hypotheses}"],
             fill="#f3eaff")
    )

    parts.append("</svg>")
    return "\n".join(parts)


# ---------- public API ----------

def write_diagrams(cfg: ProjectConfig, paths: ProjectPaths) -> tuple[Path, Path]:
    """Generate both PRISMA diagrams. Returns (canonical_path, expanded_path)."""
    paths.exports.mkdir(parents=True, exist_ok=True)
    if not paths.db.exists():
        # Empty placeholders if DB doesn't exist (init only)
        flow = {"per_source": {}, "source_hits_total": 0,
                "records_total": 0, "dedup_merges": 0, "snowball_links": 0,
                "snowball_by_direction": {}, "ta_decisions": {},
                "ft_decisions": {}, "downloads": {}, "extractions_total": 0,
                "extractions_with_quality": 0,
                "extractions_with_risk_of_bias": 0}
    else:
        with sqlite3.connect(paths.db) as conn:
            conn.row_factory = sqlite3.Row
            flow = _flow_counts(conn)

    canonical_path = paths.exports / "prisma_flow.svg"
    canonical_path.write_text(render_canonical(cfg, flow), encoding="utf-8")

    expanded_path = paths.exports / "expanded_prisma.svg"
    expanded_path.write_text(render_expanded(cfg, flow), encoding="utf-8")

    return canonical_path, expanded_path
