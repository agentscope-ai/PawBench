from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from matplotlib import patches


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.paths import HARNESS_WORK_ROOT  # noqa: E402


DEFAULT_INPUT_DIR = HARNESS_WORK_ROOT / "pawbench_v1_output_ingest_20260709_r2"
DEFAULT_OUT_DIR = HARNESS_WORK_ROOT / "pawbench_ingest_visualization_20260709"

FILES = {
    "normalized": "normalized_pawbench_records.jsonl",
    "score_matrix": "score_matrix_long.jsonl",
    "attribution_input": "attribution_input_runs.jsonl",
}

COLORS = {
    "navy": "#1f3a5f",
    "blue": "#2563eb",
    "blue_light": "#dbeafe",
    "green": "#059669",
    "green_light": "#dcfce7",
    "orange": "#ea580c",
    "orange_light": "#fed7aa",
    "purple": "#7c3aed",
    "purple_light": "#ede9fe",
    "gray": "#6b7280",
    "gray_light": "#f3f4f6",
    "border": "#d1d5db",
    "text": "#111827",
    "muted": "#6b7280",
}


@dataclass
class FileProfile:
    key: str
    path: Path
    row_count: int
    fields: set[str]
    null_counts: dict[str, int]
    source_formats: Counter[str]
    score_present: int
    transcript_present: int
    metrics_present: int
    workspace_present: int


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSONL row: {exc}") from exc
    return rows


def is_present(value: Any) -> bool:
    return value not in (None, "", [], {})


def profile_file(key: str, path: Path) -> tuple[FileProfile, list[dict[str, Any]]]:
    rows = load_jsonl(path)
    fields: set[str] = set()
    null_counts: Counter[str] = Counter()
    source_formats: Counter[str] = Counter()
    score_present = transcript_present = metrics_present = workspace_present = 0
    for row in rows:
        fields.update(row)
        source_formats[str(row.get("source_format") or "unknown")] += 1
        score_present += int(is_present(row.get("score")))
        transcript_present += int(is_present(row.get("transcript_path") or row.get("trajectory_path")))
        metrics_present += int(is_present(row.get("metrics_path")))
        workspace_present += int(is_present(row.get("workspace_path")))
        for field, value in row.items():
            if not is_present(value):
                null_counts[field] += 1
    return (
        FileProfile(
            key=key,
            path=path,
            row_count=len(rows),
            fields=fields,
            null_counts=dict(null_counts),
            source_formats=source_formats,
            score_present=score_present,
            transcript_present=transcript_present,
            metrics_present=metrics_present,
            workspace_present=workspace_present,
        ),
        rows,
    )


def load_profiles(input_dir: Path) -> tuple[dict[str, FileProfile], dict[str, list[dict[str, Any]]]]:
    profiles: dict[str, FileProfile] = {}
    rows_by_key: dict[str, list[dict[str, Any]]] = {}
    for key, filename in FILES.items():
        path = input_dir / filename
        if not path.exists():
            raise FileNotFoundError(path)
        profile, rows = profile_file(key, path)
        profiles[key] = profile
        rows_by_key[key] = rows
    return profiles, rows_by_key


def setup_matplotlib() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "font.family": "DejaVu Sans",
            "text.color": COLORS["text"],
            "axes.labelcolor": COLORS["text"],
            "xtick.color": COLORS["text"],
            "ytick.color": COLORS["text"],
            "axes.edgecolor": COLORS["border"],
        }
    )


def draw_box(ax, xy, wh, title: str, lines: list[str], fill: str, edge: str = COLORS["border"]) -> None:
    x, y = xy
    w, h = wh
    box = patches.FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.012,rounding_size=0.025",
        linewidth=1.4,
        edgecolor=edge,
        facecolor=fill,
    )
    ax.add_patch(box)
    ax.text(x + 0.03, y + h - 0.075, title, fontsize=12.5, weight="bold", ha="left", va="top")
    for index, line in enumerate(lines):
        ax.text(x + 0.03, y + h - 0.135 - index * 0.045, line, fontsize=9.2, color=COLORS["muted"], ha="left", va="top")


def arrow(ax, start, end, color: str = COLORS["blue"], label: str | None = None, rad: float = 0.0) -> None:
    con = patches.FancyArrowPatch(
        start,
        end,
        arrowstyle="-|>",
        mutation_scale=14,
        linewidth=1.8,
        color=color,
        connectionstyle=f"arc3,rad={rad}",
    )
    ax.add_patch(con)
    if label:
        mx = (start[0] + end[0]) / 2
        my = (start[1] + end[1]) / 2
        ax.text(mx, my + 0.025, label, fontsize=8.5, color=color, ha="center", va="bottom")


def make_data_flow(profiles: dict[str, FileProfile], out_dir: Path) -> None:
    normalized = profiles["normalized"]
    source_total = normalized.row_count
    source_parts = ", ".join(f"{k.replace('pawbench_', '')}: {v}" for k, v in normalized.source_formats.most_common())
    missing_transcripts = source_total - normalized.transcript_present

    fig, ax = plt.subplots(figsize=(15.5, 8.0))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.text(0.04, 0.955, "PawBench Ingest Data Flow", fontsize=21, weight="bold", ha="left", va="top")
    ax.text(
        0.04,
        0.912,
        "One adapter output is split into three stable views: inventory, score matrix, and attribution input.",
        fontsize=10.5,
        color=COLORS["muted"],
        ha="left",
        va="top",
    )

    draw_box(
        ax,
        (0.045, 0.50),
        (0.22, 0.27),
        "PawBench Outputs",
        [
            f"{source_total:,} run records",
            source_parts,
            f"{missing_transcripts} missing transcripts flagged",
        ],
        COLORS["gray_light"],
    )
    draw_box(
        ax,
        (0.335, 0.50),
        (0.22, 0.27),
        "Output Adapter",
        [
            "normalizes checkpoint JSON",
            "normalizes legacy metrics tree",
            "preserves path-level evidence",
        ],
        COLORS["blue_light"],
        "#93c5fd",
    )
    draw_box(
        ax,
        (0.68, 0.67),
        (0.27, 0.22),
        "Normalized Records",
        [
            "normalized_pawbench_records.jsonl",
            f"{profiles['normalized'].row_count:,} rows | {len(profiles['normalized'].fields)} fields | provenance",
        ],
        COLORS["green_light"],
        "#86efac",
    )
    draw_box(
        ax,
        (0.68, 0.42),
        (0.27, 0.22),
        "Score Matrix",
        [
            "score_matrix_long.jsonl",
            f"{profiles['score_matrix'].row_count:,} rows | score/report view",
        ],
        COLORS["orange_light"],
        "#fdba74",
    )
    draw_box(
        ax,
        (0.68, 0.17),
        (0.27, 0.22),
        "Attribution Input",
        [
            "attribution_input_runs.jsonl",
            f"{profiles['attribution_input'].row_count:,} rows | Reasoning contract",
        ],
        COLORS["purple_light"],
        "#c4b5fd",
    )

    draw_box(
        ax,
        (0.335, 0.13),
        (0.22, 0.22),
        "Downstream Use",
        [
            "score matrix -> reports",
            "attribution -> H/M/Ex -> H-F",
        ],
        "#ffffff",
    )

    arrow(ax, (0.265, 0.635), (0.335, 0.635), COLORS["blue"], "raw evidence")
    arrow(ax, (0.555, 0.66), (0.68, 0.78), COLORS["green"], "inventory")
    arrow(ax, (0.555, 0.63), (0.68, 0.53), COLORS["orange"], "scores")
    arrow(ax, (0.555, 0.60), (0.68, 0.28), COLORS["purple"], "reasoning")
    arrow(ax, (0.445, 0.50), (0.445, 0.35), COLORS["gray"], "contract")

    legend_y = 0.065
    for idx, (label, color) in enumerate(
        [
            ("Primary ingest", COLORS["blue"]),
            ("Inventory", COLORS["green"]),
            ("Score matrix", COLORS["orange"]),
            ("Attribution path", COLORS["purple"]),
        ]
    ):
        x = 0.05 + idx * 0.21
        ax.plot([x, x + 0.035], [legend_y, legend_y], color=color, linewidth=2)
        ax.text(x + 0.045, legend_y, label, fontsize=9, color=COLORS["muted"], va="center")

    save_figure(fig, out_dir / "pawbench_ingest_data_flow")


def drawio_label(lines: list[str]) -> str:
    return "&#xa;".join(html.escape(line, quote=True) for line in lines)


def xml_attr(value: Any) -> str:
    return html.escape(str(value), quote=True)


def write_drawio_flow(summary: dict[str, Any], out_dir: Path) -> None:
    files = summary["files"]
    normalized = files["normalized"]
    row_count = normalized["row_count"]
    source_formats = normalized["source_formats"]
    legacy_count = source_formats.get("pawbench_legacy_metrics", 0)
    checkpoint_count = source_formats.get("pawbench_checkpoint", 0)
    missing_transcripts = normalized["missing_transcripts"]

    nodes = [
        {
            "id": "n_raw",
            "title": "PawBench Outputs",
            "lines": [
                f"{row_count:,} run records",
                f"legacy {legacy_count:,} / checkpoint {checkpoint_count:,}",
                f"{missing_transcripts:,} transcript gaps flagged",
            ],
            "x": 70,
            "y": 230,
            "w": 230,
            "h": 120,
            "fill": "#f8fafc",
            "stroke": "#94a3b8",
            "accent": "#64748b",
        },
        {
            "id": "n_adapter",
            "title": "Output Adapter",
            "lines": [
                "canonicalizes raw artifacts",
                "preserves metrics/transcript paths",
                "emits stable JSONL contracts",
            ],
            "x": 390,
            "y": 230,
            "w": 240,
            "h": 120,
            "fill": "#eff6ff",
            "stroke": "#93c5fd",
            "accent": "#2563eb",
        },
        {
            "id": "n_normalized",
            "title": "Normalized Records",
            "lines": [
                "normalized_pawbench_records.jsonl",
                "source inventory + provenance",
                f"{row_count:,} aligned rows",
            ],
            "x": 740,
            "y": 80,
            "w": 300,
            "h": 110,
            "fill": "#ecfdf5",
            "stroke": "#86efac",
            "accent": "#059669",
        },
        {
            "id": "n_score",
            "title": "Score Matrix",
            "lines": [
                "score_matrix_long.jsonl",
                "score/status/report view",
                "matrix builder input",
            ],
            "x": 740,
            "y": 235,
            "w": 300,
            "h": 110,
            "fill": "#fff7ed",
            "stroke": "#fdba74",
            "accent": "#ea580c",
        },
        {
            "id": "n_attr_input",
            "title": "Attribution Input",
            "lines": [
                "attribution_input_runs.jsonl",
                "trajectory + grading evidence",
                "Reasoning input contract",
            ],
            "x": 740,
            "y": 390,
            "w": 300,
            "h": 110,
            "fill": "#f5f3ff",
            "stroke": "#c4b5fd",
            "accent": "#7c3aed",
        },
        {
            "id": "n_reasoning",
            "title": "Attribution Analysis",
            "lines": [
                "H / M / Ex code assignment",
                "LLM adjudication + rule checks",
                "harness-side error matrix",
            ],
            "x": 1130,
            "y": 300,
            "w": 260,
            "h": 120,
            "fill": "#f8fafc",
            "stroke": "#94a3b8",
            "accent": "#475569",
        },
        {
            "id": "n_bridge",
            "title": "Harness-core Ablation",
            "lines": [
                "H-F feature mapping",
                "switches on/off by error code",
                "re-test feature impact",
            ],
            "x": 1470,
            "y": 300,
            "w": 260,
            "h": 120,
            "fill": "#f0fdf4",
            "stroke": "#86efac",
            "accent": "#16a34a",
        },
    ]
    node_by_id = {node["id"]: node for node in nodes}
    edges = [
        ("e_raw_adapter", "n_raw", "n_adapter", "raw", "#2563eb", 1.0, 0.50, 0.0, 0.50),
        ("e_adapter_norm", "n_adapter", "n_normalized", "inventory", "#059669", 1.0, 0.25, 0.0, 0.55),
        ("e_adapter_score", "n_adapter", "n_score", "scores", "#ea580c", 1.0, 0.50, 0.0, 0.50),
        ("e_adapter_attr", "n_adapter", "n_attr_input", "evidence", "#7c3aed", 1.0, 0.75, 0.0, 0.45),
        ("e_score_reason", "n_score", "n_reasoning", "scores", "#ea580c", 1.0, 0.55, 0.0, 0.35),
        ("e_attr_reason", "n_attr_input", "n_reasoning", "trajectory", "#7c3aed", 1.0, 0.45, 0.0, 0.65),
        ("e_reason_bridge", "n_reasoning", "n_bridge", "H-F", "#16a34a", 1.0, 0.50, 0.0, 0.50),
    ]

    def cell_for_node(node: dict[str, Any]) -> str:
        value = drawio_label([node["title"], *node["lines"]])
        style = (
            "rounded=1;whiteSpace=wrap;html=1;arcSize=8;"
            f"fillColor={node['fill']};strokeColor={node['stroke']};fontColor=#111827;"
            "spacing=12;fontSize=12;fontStyle=0;align=left;verticalAlign=middle;"
        )
        return (
            f'        <mxCell id="{node["id"]}" value="{value}" style="{style}" vertex="1" parent="1">\n'
            f'          <mxGeometry x="{node["x"]}" y="{node["y"]}" width="{node["w"]}" height="{node["h"]}" as="geometry" />\n'
            "        </mxCell>"
        )

    def cell_for_edge(edge: tuple[str, str, str, str, str, float, float, float, float]) -> str:
        edge_id, source_id, target_id, label, color, exit_x, exit_y, entry_x, entry_y = edge
        style = (
            "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;"
            "endArrow=block;endFill=1;strokeWidth=2;"
            f"strokeColor={color};fontColor={color};labelBackgroundColor=#ffffff;fontSize=11;"
            f"exitX={exit_x};exitY={exit_y};exitDx=0;exitDy=0;"
            f"entryX={entry_x};entryY={entry_y};entryDx=0;entryDy=0;"
        )
        return (
            f'        <mxCell id="{edge_id}" value="{xml_attr(label)}" style="{style}" edge="1" parent="1" source="{source_id}" target="{target_id}">\n'
            '          <mxGeometry relative="1" as="geometry" />\n'
            "        </mxCell>"
        )

    def label_cell(cell_id: str, label: str, x: int, y: int, w: int = 230) -> str:
        style = "text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;fontSize=13;fontStyle=1;fontColor=#475569;"
        return (
            f'        <mxCell id="{cell_id}" value="{xml_attr(label)}" style="{style}" vertex="1" parent="1">\n'
            f'          <mxGeometry x="{x}" y="{y}" width="{w}" height="30" as="geometry" />\n'
            "        </mxCell>"
        )

    cells = [
        '        <mxCell id="0" />',
        '        <mxCell id="1" parent="0" />',
        label_cell("l_raw", "1. Benchmark output", 70, 185),
        label_cell("l_adapter", "2. Harness-core adapter", 390, 185),
        label_cell("l_views", "3. Stable JSONL views", 740, 35),
        label_cell("l_reasoning", "4. Error-code attribution", 1130, 255),
        label_cell("l_ablation", "5. Feature ablation loop", 1470, 255),
        *[cell_for_node(node) for node in nodes],
        *[cell_for_edge(edge) for edge in edges],
    ]
    drawio = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<mxfile host="drawio" version="26.0.0">',
            '  <diagram id="pawbench-ingest-flow" name="PawBench Ingest Flow">',
            '    <mxGraphModel dx="1780" dy="610" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1800" pageHeight="610" math="0" shadow="0">',
            "      <root>",
            *cells,
            "      </root>",
            "    </mxGraphModel>",
            "  </diagram>",
            "</mxfile>",
            "",
        ]
    )
    (out_dir / "pawbench_ingest_flow.drawio").write_text(drawio, encoding="utf-8")

    def abs_point(node_id: str, side_x: float, side_y: float) -> tuple[float, float]:
        node = node_by_id[node_id]
        return node["x"] + node["w"] * side_x, node["y"] + node["h"] * side_y

    def edge_path(source_id: str, target_id: str, exit_x: float, exit_y: float, entry_x: float, entry_y: float) -> str:
        sx, sy = abs_point(source_id, exit_x, exit_y)
        tx, ty = abs_point(target_id, entry_x, entry_y)
        mid_x = (sx + tx) / 2
        return f"M {sx:.0f} {sy:.0f} L {mid_x:.0f} {sy:.0f} L {mid_x:.0f} {ty:.0f} L {tx:.0f} {ty:.0f}"

    def svg_text_lines(node: dict[str, Any]) -> str:
        x = node["x"] + 18
        y = node["y"] + 34
        title = f'<text x="{x}" y="{y}" class="node-title">{xml_attr(node["title"])}</text>'
        lines = []
        for index, line in enumerate(node["lines"]):
            lines.append(f'<text x="{x}" y="{y + 24 + index * 20}" class="node-line">{xml_attr(line)}</text>')
        return "\n      ".join([title, *lines])

    stage_labels = [
        ("1. Benchmark output", 70, 185),
        ("2. Harness-core adapter", 390, 185),
        ("3. Stable JSONL views", 740, 35),
        ("4. Error-code attribution", 1130, 255),
        ("5. Feature ablation loop", 1470, 255),
    ]
    svg_parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="610" viewBox="0 0 1800 610" role="img" aria-labelledby="title desc">',
        "  <title id=\"title\">PawBench Ingest Flow</title>",
        "  <desc id=\"desc\">PawBench output adapter splits benchmark outputs into three JSONL views, then sends score and trajectory evidence to attribution analysis and harness feature ablation.</desc>",
        "  <defs>",
        '    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="8" stdDeviation="8" flood-color="#0f172a" flood-opacity="0.10"/></filter>',
        '    <marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker>',
        "  </defs>",
        "  <style>",
        "    .bg { fill: #ffffff; }",
        "    .grid { stroke: #e5e7eb; stroke-width: 1; opacity: .75; }",
        "    .stage { fill: #475569; font: 700 14px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; letter-spacing: .01em; }",
        "    .node { rx: 8; filter: url(#shadow); }",
        "    .accent { rx: 8; }",
        "    .node-title { fill: #111827; font: 700 17px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }",
        "    .node-line { fill: #64748b; font: 13px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }",
        "    .edge { fill: none; stroke-width: 2.5; marker-end: url(#arrow); }",
        "    .edge-label-bg { fill: #ffffff; stroke: #e5e7eb; rx: 6; }",
        "    .edge-label { font: 12px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-weight: 650; }",
        "  </style>",
        '  <rect class="bg" width="1800" height="610" rx="12"/>',
    ]
    for x in range(40, 1781, 40):
        svg_parts.append(f'  <path class="grid" d="M{x} 40 V570"/>')
    for y in range(40, 571, 40):
        svg_parts.append(f'  <path class="grid" d="M40 {y} H1760"/>')
    for label, x, y in stage_labels:
        svg_parts.append(f'  <text x="{x}" y="{y}" class="stage">{xml_attr(label)}</text>')
    for edge in edges:
        _edge_id, source_id, target_id, label, color, exit_x, exit_y, entry_x, entry_y = edge
        path = edge_path(source_id, target_id, exit_x, exit_y, entry_x, entry_y)
        sx, sy = abs_point(source_id, exit_x, exit_y)
        tx, ty = abs_point(target_id, entry_x, entry_y)
        label_x = (sx + tx) / 2 - max(44, len(label) * 3.6)
        label_y = (sy + ty) / 2 - 12
        label_w = max(88, len(label) * 7.2 + 22)
        svg_parts.append(f'  <path class="edge" d="{path}" stroke="{color}"/>')
        svg_parts.append(f'  <rect class="edge-label-bg" x="{label_x:.0f}" y="{label_y:.0f}" width="{label_w:.0f}" height="24"/>')
        svg_parts.append(f'  <text class="edge-label" x="{label_x + 11:.0f}" y="{label_y + 16:.0f}" fill="{color}">{xml_attr(label)}</text>')
    for node in nodes:
        svg_parts.append(
            f'  <rect class="node" x="{node["x"]}" y="{node["y"]}" width="{node["w"]}" height="{node["h"]}" fill="{node["fill"]}" stroke="{node["stroke"]}" stroke-width="1.5"/>'
        )
        svg_parts.append(
            f'  <rect class="accent" x="{node["x"]}" y="{node["y"]}" width="7" height="{node["h"]}" fill="{node["accent"]}"/>'
        )
        svg_parts.append(f"  {svg_text_lines(node)}")
    svg_parts.append("</svg>\n")
    (out_dir / "pawbench_ingest_flow.svg").write_text("\n".join(svg_parts), encoding="utf-8")


def make_quality_chart(profiles: dict[str, FileProfile], out_dir: Path) -> None:
    profile = profiles["normalized"]
    total = profile.row_count
    source_names = [name.replace("pawbench_", "").replace("_", " ") for name in profile.source_formats]
    source_values = [profile.source_formats[name] for name in profile.source_formats]
    availability_labels = ["score", "transcript", "metrics path", "workspace path"]
    availability_values = [
        profile.score_present / total,
        profile.transcript_present / total,
        profile.metrics_present / total,
        profile.workspace_present / total,
    ]

    fig, axes = plt.subplots(1, 2, figsize=(13.5, 6.4), gridspec_kw={"width_ratios": [1, 1.45]})
    fig.suptitle("PawBench Ingest Quality Overview", x=0.055, y=0.98, ha="left", fontsize=20, weight="bold")
    fig.text(
        0.055,
        0.925,
        "The three JSONL outputs have identical row counts; this chart checks source composition and evidence availability.",
        ha="left",
        fontsize=10.5,
        color=COLORS["muted"],
    )

    ax = axes[0]
    bar_colors = [COLORS["blue"], COLORS["gray"]]
    ax.bar(source_names, source_values, color=bar_colors[: len(source_values)], width=0.55)
    ax.set_title("Source Composition", loc="left", fontsize=13, weight="bold")
    ax.set_ylabel("rows")
    ax.grid(axis="y", color="#e5e7eb")
    ax.spines[["top", "right"]].set_visible(False)
    for idx, value in enumerate(source_values):
        ax.text(idx, value + max(source_values) * 0.025, f"{value:,}", ha="center", fontsize=10, weight="bold")
    ax.tick_params(axis="x", labelrotation=18)

    ax = axes[1]
    y = list(range(len(availability_labels)))
    ax.barh(y, [1.0] * len(y), color="#f3f4f6", height=0.42)
    ax.barh(y, availability_values, color=[COLORS["green"], COLORS["blue"], COLORS["orange"], COLORS["purple"]], height=0.42)
    ax.set_yticks(y)
    ax.set_yticklabels(availability_labels)
    ax.set_xlim(0, 1.05)
    ax.set_title("Evidence Availability", loc="left", fontsize=13, weight="bold")
    ax.set_xlabel("share of rows")
    ax.grid(axis="x", color="#e5e7eb")
    ax.spines[["top", "right"]].set_visible(False)
    ax.invert_yaxis()
    for idx, value in enumerate(availability_values):
        count = [profile.score_present, profile.transcript_present, profile.metrics_present, profile.workspace_present][idx]
        ax.text(min(value + 0.015, 0.98), idx, f"{value:.2%} ({count:,})", va="center", fontsize=10, color=COLORS["text"])

    fig.tight_layout(rect=[0.04, 0.03, 0.98, 0.90])
    save_figure(fig, out_dir / "pawbench_ingest_quality")


def make_schema_matrix(profiles: dict[str, FileProfile], out_dir: Path) -> None:
    groups = [
        ("Identity", {"run_group", "benchmark", "model", "harness", "task_id"}),
        ("Score", {"score", "max_score", "passed", "status", "grading_type", "breakdown"}),
        ("Evidence paths", {"result_path", "metrics_path", "transcript_path", "workspace_path"}),
        ("Transcript alias", {"trajectory_path"}),
        ("Audit key", {"run_key"}),
        ("Score flags", {"metrics_found", "transcript_found"}),
        ("Anomaly", {"anomaly", "anomaly_items"}),
        ("Timing", {"execution_time", "wall_time_s", "timed_out", "exit_code"}),
        ("Task metadata", {"task_name", "labels"}),
        ("Provider usage", {"usage"}),
        ("Error text", {"error", "notes"}),
        ("Source provenance", {"source_format"}),
    ]
    columns = ["normalized", "score_matrix", "attribution_input"]
    display_columns = ["normalized\nrecords", "score\nmatrix", "attribution\ninput"]

    fig, ax = plt.subplots(figsize=(12, 7.2))
    ax.set_xlim(0, len(columns) + 1)
    ax.set_ylim(0, len(groups) + 3.0)
    ax.axis("off")
    ax.text(0, len(groups) + 2.65, "Schema and Role Matrix", fontsize=20, weight="bold", ha="left", va="top")
    ax.text(
        0,
        len(groups) + 1.82,
        "Filled cells mean at least one field from the group is present in that JSONL view.",
        fontsize=10.5,
        color=COLORS["muted"],
        ha="left",
        va="top",
    )

    cell_w = 0.82
    cell_h = 0.62
    x0 = 1.05
    y0 = len(groups) + 0.45
    for col_idx, label in enumerate(display_columns):
        ax.text(x0 + col_idx * 0.95 + cell_w / 2, y0 + 0.58, label, ha="center", va="bottom", fontsize=11, weight="bold")

    for row_idx, (group_name, fields) in enumerate(groups):
        y = y0 - row_idx * 0.78
        ax.text(0, y + cell_h / 2, group_name, ha="left", va="center", fontsize=10.5, weight="bold")
        for col_idx, column in enumerate(columns):
            profile_fields = profiles[column].fields
            present = bool(fields & profile_fields)
            exact = fields <= profile_fields
            fill = COLORS["blue_light"] if exact else "#eef2ff" if present else "#f9fafb"
            edge = "#93c5fd" if present else COLORS["border"]
            rect = patches.FancyBboxPatch(
                (x0 + col_idx * 0.95, y),
                cell_w,
                cell_h,
                boxstyle="round,pad=0.012,rounding_size=0.04",
                linewidth=1.1,
                edgecolor=edge,
                facecolor=fill,
            )
            ax.add_patch(rect)
            label = "full" if exact else "partial" if present else "-"
            color = COLORS["navy"] if present else COLORS["muted"]
            ax.text(x0 + col_idx * 0.95 + cell_w / 2, y + cell_h / 2, label, ha="center", va="center", fontsize=9.5, color=color)

    ax.text(0, 0.15, "Role summary:", fontsize=10.5, weight="bold", ha="left", va="bottom")
    ax.text(
        1.05,
        0.15,
        "normalized = source-of-truth inventory | score_matrix = score/report view | attribution_input = Reasoning input contract",
        fontsize=10,
        color=COLORS["muted"],
        ha="left",
        va="bottom",
    )

    save_figure(fig, out_dir / "pawbench_ingest_schema_matrix")


def save_figure(fig, base_path: Path) -> None:
    base_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(base_path.with_suffix(".png"), dpi=180, bbox_inches="tight")
    fig.savefig(base_path.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)


def build_summary(profiles: dict[str, FileProfile]) -> dict[str, Any]:
    all_fields = sorted(set().union(*(profile.fields for profile in profiles.values())))
    return {
        "generated_at": now(),
        "files": {
            key: {
                "path": str(profile.path),
                "row_count": profile.row_count,
                "field_count": len(profile.fields),
                "source_formats": dict(profile.source_formats.most_common()),
                "score_present": profile.score_present,
                "transcript_present": profile.transcript_present,
                "metrics_present": profile.metrics_present,
                "workspace_present": profile.workspace_present,
                "missing_transcripts": profile.row_count - profile.transcript_present,
                "missing_scores": profile.row_count - profile.score_present,
                "source_formats": dict(profile.source_formats.most_common()),
                "null_counts": dict(sorted(profile.null_counts.items(), key=lambda item: (-item[1], item[0]))),
                "fields": sorted(profile.fields),
            }
            for key, profile in profiles.items()
        },
        "all_fields": all_fields,
        "field_overlap": {
            field: [key for key, profile in profiles.items() if field in profile.fields]
            for field in all_fields
        },
    }


def write_html(out_dir: Path, summary: dict[str, Any]) -> None:
    files = summary["files"]
    normalized = files["normalized"]
    row_count = normalized["row_count"]
    score_rate = normalized["score_present"] / row_count if row_count else 0
    transcript_rate = normalized["transcript_present"] / row_count if row_count else 0
    metrics_rate = normalized["metrics_present"] / row_count if row_count else 0
    workspace_rate = normalized["workspace_present"] / row_count if row_count else 0
    source_formats = normalized["source_formats"]
    legacy_count = source_formats.get("pawbench_legacy_metrics", 0)
    checkpoint_count = source_formats.get("pawbench_checkpoint", 0)
    dashboard_data = {
        "generatedAt": summary["generated_at"],
        "files": files,
        "fileNames": FILES,
        "rates": {
            "score": score_rate,
            "transcript": transcript_rate,
            "metrics": metrics_rate,
            "workspace": workspace_rate,
        },
        "sourceFormats": source_formats,
        "schemaGroups": [
            ["Identity", ["run_group", "benchmark", "model", "harness", "task_id"]],
            ["Score", ["score", "max_score", "passed", "status", "grading_type", "breakdown"]],
            ["Evidence Paths", ["result_path", "metrics_path", "transcript_path", "workspace_path"]],
            ["Transcript Alias", ["trajectory_path"]],
            ["Audit Key", ["run_key"]],
            ["Score Flags", ["metrics_found", "transcript_found"]],
            ["Anomaly", ["anomaly", "anomaly_items"]],
            ["Timing", ["execution_time", "wall_time_s", "timed_out", "exit_code"]],
            ["Task Metadata", ["task_name", "labels"]],
            ["Provider Usage", ["usage"]],
            ["Error Text", ["error", "notes"]],
            ["Source Provenance", ["source_format"]],
        ],
        "columns": ["normalized", "score_matrix", "attribution_input"],
    }
    data_json = json.dumps(dashboard_data, ensure_ascii=False)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PawBench Ingest Contract</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --surface: #ffffff;
      --surface-2: #f9fafb;
      --text: #101828;
      --muted: #667085;
      --border: #d9dee7;
      --border-strong: #b9c1d0;
      --blue: #1f5eff;
      --green: #098b67;
      --orange: #d85b12;
      --purple: #6f3ee8;
      --shadow: 0 16px 50px rgba(16, 24, 40, .08);
      --radius: 8px;
      --mono: "SFMono-Regular", Menlo, Consolas, monospace;
      --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    }}
    [data-theme="dark"] {{
      color-scheme: dark;
      --bg: #0d1117;
      --surface: #151b23;
      --surface-2: #10161f;
      --text: #eef2f7;
      --muted: #9aa6b7;
      --border: #2b3544;
      --border-strong: #435166;
      --shadow: 0 16px 50px rgba(0, 0, 0, .30);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background:
        radial-gradient(circle at 12% 0%, rgba(31, 94, 255, .10), transparent 28rem),
        linear-gradient(180deg, var(--bg), var(--bg));
      color: var(--text);
      font-family: var(--sans);
    }}
    button, a {{ font: inherit; }}
    .shell {{ width: min(1480px, calc(100vw - 48px)); margin: 0 auto; padding: 32px 0 44px; }}
    .topbar {{ display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 26px; }}
    .brandline {{ display: flex; align-items: center; gap: 10px; color: var(--muted); font-size: 13px; letter-spacing: .02em; text-transform: uppercase; }}
    .brandmark {{
      width: 48px; height: 10px; flex: 0 0 48px;
      background:
        radial-gradient(circle at 5px 5px, var(--blue) 0 5px, transparent 5.5px),
        radial-gradient(circle at 24px 5px, var(--green) 0 5px, transparent 5.5px),
        radial-gradient(circle at 43px 5px, var(--orange) 0 5px, transparent 5.5px);
    }}
    h1 {{ margin: 8px 0 10px; max-width: 860px; font-size: clamp(34px, 4vw, 58px); line-height: .98; letter-spacing: -0.035em; }}
    .lead {{ max-width: 860px; margin: 0; color: var(--muted); font-size: 17px; line-height: 1.55; }}
    .actions {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }}
    .button {{
      display: inline-flex; align-items: center; justify-content: center; gap: 8px;
      height: 38px; padding: 0 13px; border: 1px solid var(--border); border-radius: var(--radius);
      color: var(--text); background: var(--surface); text-decoration: none; cursor: pointer;
    }}
    .button:hover {{ border-color: var(--border-strong); transform: translateY(-1px); }}
    .metric-grid {{ display: grid; grid-template-columns: 1.4fr repeat(3, 1fr); gap: 12px; margin: 24px 0; }}
    .metric-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px; box-shadow: var(--shadow); min-height: 128px; }}
    .metric-card .label {{ color: var(--muted); font-size: 13px; }}
    .metric-card .value {{ margin-top: 12px; font-size: 34px; line-height: 1; font-weight: 750; letter-spacing: -0.025em; }}
    .metric-card .note {{ margin-top: 12px; color: var(--muted); font-size: 13px; line-height: 1.45; }}
    .metric-card.primary {{ background: linear-gradient(135deg, #10213f, #1f5eff); color: #fff; border-color: transparent; }}
    .metric-card.primary .label, .metric-card.primary .note {{ color: rgba(255,255,255,.74); }}
    .dashboard {{ display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(360px, .85fr); gap: 16px; }}
    .panel {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); overflow: hidden; }}
    .flow-panel {{ grid-column: 1 / -1; }}
    .panel-header {{ padding: 18px 20px 0; display: flex; align-items: baseline; justify-content: space-between; gap: 16px; }}
    .panel h2 {{ margin: 0; font-size: 17px; letter-spacing: -0.01em; }}
    .panel .subtitle {{ color: var(--muted); font-size: 13px; }}
    .panel-body {{ padding: 18px 20px 20px; }}
    .flow-canvas {{ position: relative; min-height: 520px; border-radius: var(--radius); background: linear-gradient(180deg, var(--surface-2), transparent); overflow: hidden; }}
    .flow-canvas svg {{ position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }}
    .flow-node {{
      position: absolute; border: 1px solid var(--border); background: var(--surface); border-radius: var(--radius); padding: 15px;
      min-width: 190px;
    }}
    .flow-node h3 {{ margin: 0 0 10px; font-size: 15px; }}
    .flow-node p {{ margin: 6px 0; color: var(--muted); font-size: 12.5px; line-height: 1.35; }}
    .node-source {{ left: 3%; top: 29%; width: 23%; }}
    .node-adapter {{ left: 34%; top: 28%; width: 24%; border-color: rgba(31, 94, 255, .35); }}
    .node-normalized {{ right: 3%; top: 6%; width: 28%; min-height: 132px; border-color: rgba(9,139,103,.38); }}
    .node-score {{ right: 3%; top: 37%; width: 28%; min-height: 132px; border-color: rgba(216,91,18,.38); }}
    .node-attribution {{ right: 3%; top: 68%; width: 28%; min-height: 132px; border-color: rgba(111,62,232,.38); }}
    .node-downstream {{ left: 34%; bottom: 7%; width: 24%; }}
    .tag {{ display: inline-flex; align-items: center; height: 24px; padding: 0 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); font-size: 12px; }}
    .file-code {{ font-family: var(--mono); color: var(--text); font-size: 12px; overflow-wrap: anywhere; word-break: break-word; }}
    .drawio-frame {{ border: 1px solid var(--border); border-radius: var(--radius); background: #fff; overflow: auto; }}
    .drawio-frame img {{ display: block; width: 100%; min-width: 1320px; height: auto; }}
    .drawio-caption {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 14px; border-top: 1px solid var(--border); background: var(--surface-2); color: var(--muted); font-size: 12.5px; }}
    .drawio-caption a {{ color: var(--blue); text-decoration: none; font-weight: 650; }}
    .drawio-caption a:hover {{ text-decoration: underline; }}
    .quality-layout {{ display: grid; gap: 20px; }}
    .bar-list {{ display: grid; gap: 12px; }}
    .bar-row {{ display: grid; grid-template-columns: 122px 1fr 108px; align-items: center; gap: 12px; }}
    .bar-label {{ color: var(--muted); font-size: 13px; }}
    .bar-track {{ height: 11px; border-radius: 999px; background: var(--surface-2); border: 1px solid var(--border); overflow: hidden; }}
    .bar-fill {{ height: 100%; border-radius: inherit; }}
    .bar-value {{ text-align: right; font-variant-numeric: tabular-nums; font-size: 13px; }}
    .schema-panel {{ grid-column: 1 / -1; }}
    .schema-table {{ width: 100%; border-collapse: separate; border-spacing: 0 8px; }}
    .schema-table th {{ color: var(--muted); font-size: 12px; text-align: left; font-weight: 650; padding: 0 10px 4px; }}
    .schema-table td {{ padding: 10px; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); background: var(--surface-2); font-size: 13px; }}
    .schema-table td:first-child {{ border-left: 1px solid var(--border); border-radius: var(--radius) 0 0 var(--radius); font-weight: 650; background: var(--surface); }}
    .schema-table td:last-child {{ border-right: 1px solid var(--border); border-radius: 0 var(--radius) var(--radius) 0; }}
    .status-cell {{ display: inline-flex; align-items: center; gap: 7px; height: 26px; padding: 0 10px; border-radius: 999px; font-size: 12px; border: 1px solid var(--border); }}
    .status-full {{ color: var(--blue); background: rgba(31,94,255,.08); border-color: rgba(31,94,255,.18); }}
    .status-partial {{ color: var(--orange); background: rgba(216,91,18,.09); border-color: rgba(216,91,18,.20); }}
    .status-none {{ color: var(--muted); }}
    .inspector-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }}
    .file-card {{ border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; background: var(--surface-2); }}
    .file-card h3 {{ margin: 0 0 8px; font-size: 15px; }}
    .file-card ul {{ margin: 12px 0 0; padding: 0; list-style: none; display: grid; gap: 7px; }}
    .file-card li {{ display: flex; justify-content: space-between; gap: 12px; color: var(--muted); font-size: 12.5px; }}
    .file-card li strong {{ color: var(--text); font-weight: 650; }}
    .field-cloud {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; max-height: 104px; overflow: hidden; }}
    .field-cloud span {{ font-family: var(--mono); font-size: 11px; color: var(--muted); background: var(--surface); border: 1px solid var(--border); border-radius: 999px; padding: 4px 7px; }}
    .footer {{ margin-top: 18px; color: var(--muted); font-size: 12px; display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }}
    @media (max-width: 1080px) {{
      .metric-grid, .dashboard, .inspector-grid {{ grid-template-columns: 1fr; }}
      .drawio-frame img {{ min-width: 1120px; }}
      .flow-canvas {{ min-height: 760px; }}
      .flow-node {{ position: relative; left: auto; right: auto; top: auto; bottom: auto; width: auto; margin: 12px; }}
      .flow-canvas svg {{ display: none; }}
    }}
  </style>
</head>
<body>
  <script id="dashboard-data" type="application/json">{data_json}</script>
  <div class="shell">
    <header class="topbar">
      <div>
        <div class="brandline"><span class="brandmark"></span><span>Harness-core / PawBench ingest</span></div>
        <h1>Three JSONL views, one ingestion contract.</h1>
        <p class="lead">The adapter turns PawBench outputs into a source-of-truth inventory, a score matrix, and a Reasoning-ready input stream. This page shows whether those views remain aligned.</p>
      </div>
      <div class="actions">
        <button class="button" id="theme-toggle" type="button">Dark mode</button>
        <a class="button" href="visualization_summary.json">Summary JSON</a>
      </div>
    </header>

    <section class="metric-grid" aria-label="Key ingest metrics">
      <article class="metric-card primary">
        <div class="label">Rows in each JSONL view</div>
        <div class="value">{row_count:,}</div>
        <div class="note">All three files are row-aligned from the same adapter output.</div>
      </article>
      <article class="metric-card">
        <div class="label">Score coverage</div>
        <div class="value">{score_rate:.2%}</div>
        <div class="note">{normalized['missing_scores']} missing scores across the normalized inventory.</div>
      </article>
      <article class="metric-card">
        <div class="label">Transcript coverage</div>
        <div class="value">{transcript_rate:.2%}</div>
        <div class="note">{normalized['missing_transcripts']} rows still need transcript evidence.</div>
      </article>
      <article class="metric-card">
        <div class="label">Source mix</div>
        <div class="value">{legacy_count:,} / {checkpoint_count:,}</div>
        <div class="note">legacy metrics rows / checkpoint rows.</div>
      </article>
    </section>

    <main class="dashboard">
      <section class="panel flow-panel">
        <div class="panel-header">
          <h2>Data Flow</h2>
          <span class="subtitle">Adapter split and downstream contract</span>
        </div>
        <div class="panel-body">
          <div class="drawio-frame">
            <img src="pawbench_ingest_flow.svg" alt="PawBench ingest flow diagram">
            <div class="drawio-caption">
              <span>Draw.io-style source is generated beside this page for editing and export.</span>
              <a href="pawbench_ingest_flow.drawio">Open editable .drawio</a>
            </div>
          </div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-header">
          <h2>Quality</h2>
          <span class="subtitle">Coverage and source composition</span>
        </div>
        <div class="panel-body">
          <div class="quality-layout">
            <div>
              <h3>Evidence availability</h3>
              <div class="bar-list" id="coverage-bars"></div>
            </div>
            <div>
              <h3>Source composition</h3>
              <div class="bar-list" id="source-bars"></div>
            </div>
          </div>
        </div>
      </section>

      <section class="panel schema-panel">
        <div class="panel-header">
          <h2>Schema and Role Matrix</h2>
          <span class="subtitle">Full means every field in the group exists in that view</span>
        </div>
        <div class="panel-body">
          <table class="schema-table" id="schema-table"></table>
        </div>
      </section>

      <section class="panel schema-panel">
        <div class="panel-header">
          <h2>File Inspector</h2>
          <span class="subtitle">Concrete role, coverage, and fields for each JSONL</span>
        </div>
        <div class="panel-body">
          <div class="inspector-grid" id="file-inspector"></div>
        </div>
      </section>
    </main>
    <footer class="footer">
      <span>Generated at {summary['generated_at']}</span>
      <span>Static export: PNG/SVG figures remain in this folder for documents.</span>
    </footer>
  </div>

  <script>
    const data = JSON.parse(document.getElementById('dashboard-data').textContent);
    const fmt = new Intl.NumberFormat('en-US');
    const pct = value => `${{(value * 100).toFixed(2)}}%`;
    const themeToggle = document.getElementById('theme-toggle');
    const savedTheme = localStorage.getItem('pawbench-ingest-theme');
    if (savedTheme === 'dark') document.documentElement.dataset.theme = 'dark';
    themeToggle.textContent = document.documentElement.dataset.theme === 'dark' ? 'Light mode' : 'Dark mode';
    themeToggle.addEventListener('click', () => {{
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      if (next === 'dark') document.documentElement.dataset.theme = 'dark';
      else delete document.documentElement.dataset.theme;
      localStorage.setItem('pawbench-ingest-theme', next);
      themeToggle.textContent = next === 'dark' ? 'Light mode' : 'Dark mode';
    }});

    function bar(container, label, value, count, color) {{
      const row = document.createElement('div');
      row.className = 'bar-row';
      row.innerHTML = `
        <div class="bar-label">${{label}}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${{Math.max(0, Math.min(1, value)) * 100}}%; background:${{color}}"></div></div>
        <div class="bar-value">${{pct(value)}} · ${{fmt.format(count)}}</div>
      `;
      container.appendChild(row);
    }}

    const coverage = document.getElementById('coverage-bars');
    const n = data.files.normalized.row_count;
    bar(coverage, 'score', data.rates.score, data.files.normalized.score_present, 'var(--green)');
    bar(coverage, 'transcript', data.rates.transcript, data.files.normalized.transcript_present, 'var(--blue)');
    bar(coverage, 'metrics path', data.rates.metrics, data.files.normalized.metrics_present, 'var(--orange)');
    bar(coverage, 'workspace path', data.rates.workspace, data.files.normalized.workspace_present, 'var(--purple)');

    const sources = document.getElementById('source-bars');
    Object.entries(data.sourceFormats).forEach(([name, count], index) => {{
      const color = index === 0 ? 'var(--blue)' : 'var(--muted)';
      bar(sources, name.replace('pawbench_', '').replaceAll('_', ' '), count / n, count, color);
    }});

    const columnLabels = {{
      normalized: 'normalized records',
      score_matrix: 'score matrix',
      attribution_input: 'attribution input',
    }};
    const schema = document.getElementById('schema-table');
    const head = document.createElement('thead');
    head.innerHTML = `<tr><th>Field group</th>${{data.columns.map(c => `<th>${{columnLabels[c]}}</th>`).join('')}}</tr>`;
    schema.appendChild(head);
    const body = document.createElement('tbody');
    data.schemaGroups.forEach(([group, fields]) => {{
      const cells = data.columns.map(column => {{
        const have = new Set(data.files[column].fields);
        const present = fields.filter(field => have.has(field));
        let status = 'none';
        if (present.length === fields.length) status = 'full';
        else if (present.length > 0) status = 'partial';
        return `<td><span class="status-cell status-${{status}}">${{status}}</span></td>`;
      }}).join('');
      const row = document.createElement('tr');
      row.innerHTML = `<td>${{group}}</td>${{cells}}`;
      body.appendChild(row);
    }});
    schema.appendChild(body);

    const roles = {{
      normalized: 'Source-of-truth inventory and path-level provenance.',
      score_matrix: 'Long-form score/status view for matrix building and reports.',
      attribution_input: 'Reasoning contract with trajectory and grading evidence pointers.',
    }};
    const inspector = document.getElementById('file-inspector');
    data.columns.forEach(column => {{
      const item = data.files[column];
      const card = document.createElement('article');
      card.className = 'file-card';
      const topFields = item.fields.slice(0, 18).map(field => `<span>${{field}}</span>`).join('');
      card.innerHTML = `
        <h3>${{columnLabels[column]}}</h3>
        <div class="file-code">${{data.fileNames[column]}}</div>
        <p class="subtitle">${{roles[column]}}</p>
        <ul>
          <li><span>Rows</span><strong>${{fmt.format(item.row_count)}}</strong></li>
          <li><span>Fields</span><strong>${{item.field_count}}</strong></li>
          <li><span>Score rows</span><strong>${{fmt.format(item.score_present)}}</strong></li>
          <li><span>Transcript rows</span><strong>${{fmt.format(item.transcript_present)}}</strong></li>
          <li><span>Missing transcripts</span><strong>${{fmt.format(item.missing_transcripts)}}</strong></li>
        </ul>
        <div class="field-cloud">${{topFields}}</div>
      `;
      inspector.appendChild(card);
    }});
  </script>
</body>
</html>
"""
    (out_dir / "index.html").write_text(html, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Visualize the three PawBench ingest JSONL outputs.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    setup_matplotlib()
    profiles, _rows = load_profiles(args.input_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    make_data_flow(profiles, args.out_dir)
    make_quality_chart(profiles, args.out_dir)
    make_schema_matrix(profiles, args.out_dir)
    summary = build_summary(profiles)
    write_drawio_flow(summary, args.out_dir)
    (args.out_dir / "visualization_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_html(args.out_dir, summary)
    print(
        json.dumps(
            {
                "ok": True,
                "out_dir": str(args.out_dir),
                "figures": [
                    "pawbench_ingest_flow.drawio",
                    "pawbench_ingest_flow.svg",
                    "pawbench_ingest_data_flow.png",
                    "pawbench_ingest_quality.png",
                    "pawbench_ingest_schema_matrix.png",
                ],
                "html": str(args.out_dir / "index.html"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
