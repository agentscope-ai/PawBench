from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.offsetbox import AnnotationBbox, HPacker, TextArea
from matplotlib.patches import Rectangle
from matplotlib.transforms import blended_transform_factory

from scripts.feature_taxonomy import (
    CODE_TABLE_ZH,
    FEATURES,
    FEATURE_IDS,
    display_code,
)


EX_CODES = ("Ex-1", "Ex-2", "Ex-3")
H_CODES = ("H1", "H2", "H3", "H4", "H5")
M_CODES = ("M1", "M2", "M3", "M4", "M5")
ATTRIBUTION_CHART_FILENAME = "attribution_summary.png"

ANTHROPIC_STYLE_PATH = (
    Path.home() / ".config" / "matplotlib" / "stylelib" / "anthropic.mplstyle"
)
QWEN_PURPLE = "#615CED"
SLATE = "#232326"
CANVAS = "#FFFFFF"
MUTED = "#797B89"
HAIRLINE = "#E1E3EA"
PANEL_BORDER = "#B0AEA5"
PANEL_BORDER_ALPHA = 0.5
PANEL_COLORS = {
    "Ex": "#7A55D1",
    "M": QWEN_PURPLE,
    "H": "#4D57C9",
    "Features": "#8667C7",
}

_CJK_RUN = re.compile(r"[\u3000-\u303f\u3400-\u9fff]+|[^\u3000-\u303f\u3400-\u9fff]+")


def _office_font(filename: str) -> Path:
    for app in ("Microsoft Word.app", "Microsoft Excel.app", "Microsoft PowerPoint.app"):
        candidate = Path("/Applications") / app / "Contents" / "Resources" / "DFonts" / filename
        if candidate.is_file():
            return candidate
    return Path(font_manager.findfont("Arial"))


def _system_font(*names: str) -> Path:
    for name in names:
        try:
            return Path(font_manager.findfont(name, fallback_to_default=False))
        except ValueError:
            continue
    return Path(font_manager.findfont("Arial Unicode MS"))


APTOS_REGULAR = _office_font("Aptos.ttf")
APTOS_SEMIBOLD = _office_font("Aptos-SemiBold.ttf")
KAITI_REGULAR = _system_font("Kaiti SC", "STKaiti")


def _font_properties(*, chinese: bool, size: float, semibold: bool = False) -> Any:
    path = KAITI_REGULAR if chinese else (APTOS_SEMIBOLD if semibold else APTOS_REGULAR)
    return font_manager.FontProperties(fname=str(path), size=size)


def _add_mixed_text(
    ax: Any,
    x: float,
    y: float,
    text: str,
    *,
    fontsize: float,
    color: str,
    semibold: bool = False,
    va: str = "bottom",
) -> None:
    children = []
    for run in _CJK_RUN.findall(text):
        chinese = bool(re.fullmatch(r"[\u3000-\u303f\u3400-\u9fff]+", run))
        children.append(
            TextArea(
                run,
                textprops={
                    "fontproperties": _font_properties(
                        chinese=chinese,
                        size=fontsize,
                        semibold=semibold and not chinese,
                    ),
                    "color": color,
                },
            )
        )
    packed = HPacker(children=children, align="baseline", pad=0, sep=0)
    alignment_y = 0 if va == "bottom" else 0.5
    artist = AnnotationBbox(
        packed,
        (x, y),
        xycoords=ax.transAxes,
        box_alignment=(0, alignment_y),
        frameon=False,
        pad=0,
        annotation_clip=False,
    )
    artist.set_clip_on(False)
    artist.set_zorder(11)
    ax.add_artist(artist)


def display_codes(codes: Iterable[str]) -> list[str]:
    return [display_code(str(code)) for code in codes]


def enrich_attribution(parsed: Mapping[str, Any]) -> dict[str, Any]:
    enriched = dict(parsed)
    code_items: list[dict[str, Any]] = []
    for raw_item in parsed.get("codes", []):
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        code = str(item.get("code") or "")
        item["code_label"] = display_code(code)
        code_items.append(item)
    enriched["codes"] = code_items
    enriched["code_labels"] = [item["code_label"] for item in code_items]
    return enriched


def harness_code_counts(code_counts: Mapping[str, Any]) -> dict[str, int]:
    return {code: int(code_counts.get(code, 0) or 0) for code in H_CODES}


def _draw_count_panel(
    ax: Any,
    *,
    items: list[tuple[str, int]],
    color: str,
    title: str,
    explanation: str,
) -> None:
    ax.set_facecolor(CANVAS)
    ax.add_artist(
        Rectangle(
            (-0.045, -0.28),
            1.09,
            1.44,
            transform=ax.transAxes,
            fill=False,
            edgecolor=PANEL_BORDER,
            linewidth=1.1,
            alpha=PANEL_BORDER_ALPHA,
            clip_on=False,
            zorder=10,
        )
    )
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color(HAIRLINE)
    ax.spines["bottom"].set_linewidth(1.0)
    _add_mixed_text(
        ax,
        0,
        1.04,
        title,
        fontsize=14,
        color=SLATE,
        semibold=True,
    )
    _add_mixed_text(
        ax,
        0,
        0.94,
        explanation,
        fontsize=9.2,
        color=MUTED,
    )
    if not items:
        ax.set_xlim(-0.5, 0.5)
        ax.set_ylim(0, 1)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.text(
            0.04,
            0.42,
            "0",
            transform=ax.transAxes,
            ha="left",
            va="center",
            fontproperties=_font_properties(chinese=False, size=28),
            color=color,
        )
        ax.text(
            0.12,
            0.415,
            "本次无归因",
            transform=ax.transAxes,
            ha="left",
            va="center",
            fontproperties=_font_properties(chinese=True, size=10),
            color=MUTED,
        )
        return

    upper = max(value for _, value in items)
    item_count = len(items)
    positions = list(range(item_count))
    codes = []
    names = []
    for label, _ in items:
        code, separator, name = label.partition("  ")
        codes.append(code)
        names.append(name if separator else "")

    bars = ax.bar(
        positions,
        [value for _, value in items],
        width=0.46 if item_count <= 4 else 0.62,
        color=color,
        edgecolor="none",
        zorder=2,
    )
    headroom = max(0.65, upper * 0.32)
    ax.set_ylim(0, upper + headroom)
    if item_count == 1:
        ax.set_xlim(-0.9, 0.9)
    else:
        ax.set_xlim(-0.65, item_count - 0.35)
    ax.set_xticks(positions, codes)
    ax.tick_params(
        axis="x",
        colors=SLATE,
        labelsize=9.2 if item_count <= 6 else 8.0,
        length=0,
        pad=9,
    )
    for label in ax.get_xticklabels():
        label.set_fontproperties(
            _font_properties(
                chinese=False,
                size=9.2 if item_count <= 6 else 8.0,
            )
        )
    if item_count <= 6:
        label_transform = blended_transform_factory(ax.transData, ax.transAxes)
        for position, name in zip(positions, names):
            if not name:
                continue
            ax.text(
                position,
                -0.19,
                name,
                transform=label_transform,
                ha="center",
                va="top",
                fontproperties=_font_properties(chinese=True, size=9.2),
                color=SLATE,
                clip_on=False,
            )
    ax.set_yticks(range(0, upper + 1))
    ax.tick_params(axis="y", colors=MUTED, labelsize=8.2, length=0, pad=5)
    for label in ax.get_yticklabels():
        label.set_fontproperties(_font_properties(chinese=False, size=8.2))
    ax.yaxis.grid(False)
    for bar, (_, value) in zip(bars, items):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + headroom * 0.16,
            f"{value:,}",
            ha="center",
            va="bottom",
            fontproperties=_font_properties(chinese=False, size=9.5),
            color=SLATE,
        )


def write_attribution_overview_chart(
    code_counts: Mapping[str, Any],
    feature_counts: Mapping[str, Any],
    path: Path,
    *,
    title: str = "Failure Attribution Overview",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    panel_sizes = (
        sum(int(code_counts.get(code, 0) or 0) > 0 for code in EX_CODES),
        sum(int(code_counts.get(code, 0) or 0) > 0 for code in M_CODES),
        sum(int(code_counts.get(code, 0) or 0) > 0 for code in H_CODES),
        sum(int(feature_counts.get(feature_id, 0) or 0) > 0 for feature_id in FEATURE_IDS),
    )
    top_rows = max(3, panel_sizes[0], panel_sizes[1])
    bottom_rows = max(3, panel_sizes[2], panel_sizes[3])
    figure_height = 6.8 + max(0, top_rows + bottom_rows - 6) * 0.38
    style = {
        "font.family": "sans-serif",
        "font.sans-serif": [
            "Aptos",
            "Kaiti SC",
            "Arial Unicode MS",
            "Arial",
            "DejaVu Sans",
        ],
        "text.color": SLATE,
        "axes.labelcolor": SLATE,
        "axes.edgecolor": SLATE,
        "axes.unicode_minus": False,
        "savefig.facecolor": CANVAS,
    }
    base_style = str(ANTHROPIC_STYLE_PATH) if ANTHROPIC_STYLE_PATH.is_file() else "default"
    with plt.style.context(base_style), plt.rc_context(style):
        fig, axes = plt.subplots(
            2,
            2,
            figsize=(13.6, figure_height),
            facecolor=CANVAS,
            gridspec_kw={"height_ratios": (top_rows, bottom_rows)},
        )
        fig.suptitle(
            title,
            x=0.06,
            y=0.962,
            ha="left",
            fontproperties=_font_properties(chinese=False, size=19, semibold=True),
            color=SLATE,
        )
        fig.text(
            0.06,
            0.916,
            "仅显示非零项；数字为最终归因出现次数",
            ha="left",
            va="top",
            fontproperties=_font_properties(chinese=True, size=9.5),
            color=MUTED,
        )
        for ax in axes.flat:
            ax.set_facecolor(CANVAS)

        panels = (
            (axes[0, 0], EX_CODES, "Ex 外部问题", "任务、评分或外部服务", PANEL_COLORS["Ex"]),
            (axes[0, 1], M_CODES, "M 模型行为", "可观察的模型侧错误", PANEL_COLORS["M"]),
            (axes[1, 0], H_CODES, "H Harness 机制", "可由 Harness 修复的机制", PANEL_COLORS["H"]),
        )
        for ax, codes, panel_title, explanation, color in panels:
            _draw_count_panel(
                ax,
                items=[
                    (f"{code}  {CODE_TABLE_ZH[code]['short_name']}", count)
                    for code in codes
                    if (count := int(code_counts.get(code, 0) or 0)) > 0
                ],
                color=color,
                title=f"{panel_title}  ·  {sum(int(code_counts.get(code, 0) or 0) for code in codes):,}",
                explanation=explanation,
            )

        _draw_count_panel(
            axes[1, 1],
            items=[
                (f"{feature_id}  {FEATURES[feature_id].name_zh}", count)
                for feature_id in FEATURE_IDS
                if (count := int(feature_counts.get(feature_id, 0) or 0)) > 0
            ],
            color=PANEL_COLORS["Features"],
            title=f"Features 消融目标  ·  {sum(int(feature_counts.get(feature_id, 0) or 0) for feature_id in FEATURE_IDS):,}",
            explanation="有直接证据的 Harness 功能",
        )

        fig.subplots_adjust(
            left=0.06,
            right=0.975,
            top=0.82,
            bottom=0.075,
            hspace=0.62,
            wspace=0.2,
        )
        fig.savefig(path, dpi=300, bbox_inches="tight", facecolor=CANVAS)
        plt.close(fig)
