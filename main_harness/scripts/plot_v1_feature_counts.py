#!/usr/bin/env python3
"""Render the current 15-feature distribution from a V1 stress summary."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

import matplotlib.font_manager as font_manager
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator


GROUPS = [
    (
        "H1",
        "环境 / 工作区",
        "#147D87",
        [
            ("F1.1", "工作区绑定"),
            ("F1.2", "就绪检查"),
            ("F1.3", "隔离与权限"),
        ],
    ),
    (
        "H2",
        "工具契约",
        "#D97706",
        [
            ("F2.1", "动作契约"),
            ("F2.2", "工具可用性"),
            ("F2.3", "错误反馈"),
        ],
    ),
    (
        "H3",
        "运行循环",
        "#3568C0",
        [
            ("F3.1", "完成判定"),
            ("F3.2", "预算"),
            ("F3.3", "恢复"),
        ],
    ),
    (
        "H4",
        "可观测性",
        "#B33A63",
        [
            ("F4.1", "诊断轨迹"),
            ("F4.2", "状态"),
            ("F4.3", "验证"),
        ],
    ),
    (
        "H5",
        "上下文记忆",
        "#4C7A3F",
        [
            ("F5.1", "上下文组装"),
            ("F5.2", "持久记忆"),
            ("F5.3", "上下文压缩"),
        ],
    ),
]

DEFAULT_SUMMARY = Path(
    "run_records/PawBenchV1-deepseek-v4-pro-20260710/summary.json"
)
DEFAULT_OUTPUT_DIR = Path(
    "run_records/PawBenchV1-deepseek-v4-pro-20260710/figures"
)
DEFAULT_RESULTS = DEFAULT_SUMMARY.with_name("results.jsonl")


def configure_font() -> str:
    candidates = [
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    ]
    for path in candidates:
        if path.exists():
            font_manager.fontManager.addfont(path)
            family = font_manager.FontProperties(fname=path).get_name()
            plt.rcParams["font.family"] = family
            plt.rcParams["axes.unicode_minus"] = False
            return family
    plt.rcParams["axes.unicode_minus"] = False
    return "sans-serif"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def load_data(
    summary_path: Path,
    results_path: Path,
) -> tuple[int, int, dict[str, int], dict[str, int]]:
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    sample_count = int(payload["sample"]["sample_count"])
    raw_counts = {key: int(value) for key, value in payload["feature_counts"].items()}
    expected = {code for _, _, _, features in GROUPS for code, _ in features}
    if set(raw_counts) != expected:
        missing = sorted(expected - set(raw_counts))
        extra = sorted(set(raw_counts) - expected)
        raise ValueError(f"feature set mismatch: missing={missing}, extra={extra}")

    latest_by_job: dict[str, dict] = {}
    with results_path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                item = json.loads(line)
                latest_by_job[item["job_id"]] = item
    base_results = [item for item in latest_by_job.values() if item["repeat_no"] == 1]
    if len(base_results) != sample_count:
        raise ValueError(
            f"base result count mismatch: expected={sample_count}, actual={len(base_results)}"
        )

    computed_raw = Counter(
        feature_id
        for item in base_results
        for feature_id in item["validation"].get("feature_ids", [])
    )
    normalized_raw = {code: computed_raw.get(code, 0) for code in expected}
    if normalized_raw != raw_counts:
        raise ValueError("summary feature counts do not match latest base results")

    strict_results = [item for item in base_results if item["validation"].get("ok")]
    strict_counter = Counter(
        feature_id
        for item in strict_results
        for feature_id in item["validation"].get("feature_ids", [])
    )
    strict_counts = {code: strict_counter.get(code, 0) for code in expected}
    return sample_count, len(strict_results), raw_counts, strict_counts


def render(
    sample_count: int,
    strict_result_count: int,
    raw_counts: dict[str, int],
    strict_counts: dict[str, int],
    output_dir: Path,
) -> list[Path]:
    configure_font()

    background = "#F7F8FA"
    ink = "#17202A"
    muted = "#68717D"
    grid = "#DDE2E8"

    fig, ax = plt.subplots(figsize=(14.4, 11.2), facecolor=background)
    ax.set_facecolor(background)
    fig.subplots_adjust(left=0.34, right=0.94, top=0.84, bottom=0.15)

    rows: list[tuple[float, str, str, str, int, int]] = []
    group_centers: list[tuple[float, str, str, str, int, int]] = []
    separators: list[float] = []
    y = 17.2
    for group_index, (h_code, h_name, color, features) in enumerate(GROUPS):
        group_rows = []
        raw_group_total = 0
        strict_group_total = 0
        for feature_code, feature_name in features:
            raw_value = raw_counts[feature_code]
            strict_value = strict_counts[feature_code]
            rows.append(
                (y, feature_code, feature_name, color, raw_value, strict_value)
            )
            group_rows.append(y)
            raw_group_total += raw_value
            strict_group_total += strict_value
            y -= 0.82
        group_centers.append(
            (
                sum(group_rows) / len(group_rows),
                h_code,
                h_name,
                color,
                raw_group_total,
                strict_group_total,
            )
        )
        if group_index < len(GROUPS) - 1:
            separators.append(y + 0.19)
            y -= 0.45

    max_count = max(raw_counts.values())
    x_limit = max(110, int(max_count * 1.17))

    for row_y, code, name, color, raw_value, strict_value in rows:
        if raw_value:
            ax.barh(
                row_y,
                raw_value,
                height=0.5,
                color=color,
                edgecolor="none",
                alpha=0.2,
                zorder=2,
            )
            ax.barh(
                row_y,
                strict_value,
                height=0.5,
                color=color,
                edgecolor="none",
                zorder=3,
            )
            label = (
                f"{strict_value} / {raw_value}"
                f"   {strict_value / sample_count:.2%}"
            )
            ax.text(
                raw_value + 1.4,
                row_y,
                label,
                va="center",
                ha="left",
                fontsize=12.2,
                color=ink,
                fontweight="normal",
            )
        else:
            ax.scatter(
                [0.9],
                [row_y],
                s=58,
                facecolor=background,
                edgecolor=color,
                linewidth=1.7,
                zorder=4,
            )
            ax.text(
                3.0,
                row_y,
                "0 / 0   未归因",
                va="center",
                ha="left",
                fontsize=12,
                color=muted,
                fontweight="normal",
            )

        ax.text(
            -3.1,
            row_y,
            code,
            va="center",
            ha="right",
            fontsize=12.6,
            color=ink,
            fontweight="normal",
            clip_on=False,
        )
        ax.text(
            -18.5,
            row_y,
            name,
            va="center",
            ha="right",
            fontsize=12.2,
            color=ink,
            clip_on=False,
        )

    for center_y, h_code, h_name, color, raw_total, strict_total in group_centers:
        ax.plot(
            [-49.0, -49.0],
            [center_y - 0.63, center_y + 0.63],
            color=color,
            linewidth=4.2,
            solid_capstyle="round",
            clip_on=False,
        )
        ax.text(
            -46.2,
            center_y + 0.19,
            h_code,
            va="center",
            ha="left",
            fontsize=15,
            color=color,
            fontweight="normal",
            clip_on=False,
        )
        ax.text(
            -46.2,
            center_y - 0.18,
            h_name,
            va="center",
            ha="left",
            fontsize=11.5,
            color=ink,
            fontweight="normal",
            clip_on=False,
        )
        ax.text(
            -46.2,
            center_y - 0.5,
            f"严格 / 原始  {strict_total} / {raw_total}",
            va="center",
            ha="left",
            fontsize=9.8,
            color=muted,
            clip_on=False,
        )

    for separator in separators:
        ax.hlines(
            separator,
            -50,
            x_limit,
            color=grid,
            linewidth=0.9,
            zorder=1,
            clip_on=False,
        )

    ax.set_xlim(0, x_limit)
    ax.set_ylim(y + 0.1, 17.9)
    ax.xaxis.set_major_locator(MultipleLocator(20))
    ax.grid(axis="x", color=grid, linewidth=0.9, zorder=0)
    ax.tick_params(axis="x", labelsize=10.8, colors=muted, length=0, pad=9)
    ax.tick_params(axis="y", left=False, labelleft=False)
    for spine in ax.spines.values():
        spine.set_visible(False)

    fig.text(
        0.055,
        0.94,
        "PawBench V1：15 个 Harness Feature 归因统计",
        ha="left",
        va="top",
        fontsize=25,
        color=ink,
        fontweight="normal",
    )
    fig.text(
        0.055,
        0.895,
        "DeepSeek-v4-pro · 真实 V1 死亡压力测试 · 基础轨迹 n = 2,000",
        ha="left",
        va="top",
        fontsize=12.8,
        color=muted,
    )

    strict_observed = sum(value > 0 for value in strict_counts.values())
    strict_assignments = sum(strict_counts.values())
    raw_assignments = sum(raw_counts.values())
    fig.text(
        0.94,
        0.936,
        f"严格有效轨迹  {strict_result_count:,} / {sample_count:,}",
        ha="right",
        va="top",
        fontsize=15,
        color=ink,
        fontweight="normal",
    )
    fig.text(
        0.94,
        0.899,
        f"有归因 {strict_observed} / 15  ·  合计 {strict_assignments} / {raw_assignments}",
        ha="right",
        va="top",
        fontsize=11.5,
        color=muted,
    )

    fig.text(
        0.64,
        0.105,
        "归因次数   ·   标签为 严格 / 原始   ·   百分比为严格次数 / 2,000 条轨迹",
        ha="center",
        va="center",
        fontsize=11.2,
        color=muted,
    )
    fig.text(
        0.055,
        0.052,
        "统计口径：仅计 2,000 条基础轨迹，复判调用不计入；严格结果同时通过结构、H/F 映射和原文证据校验。",
        ha="left",
        va="bottom",
        fontsize=10.1,
        color=muted,
    )
    fig.text(
        0.055,
        0.027,
        "注：这是 DeepSeek 归因分布，不是人工确认的因果真值；0 表示本次样本未归因到，不代表 Feature 无效。",
        ha="left",
        va="bottom",
        fontsize=10.1,
        color="#9B3A42",
        fontweight="normal",
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = [
        output_dir / "v1_15_feature_counts_zh.png",
        output_dir / "v1_15_feature_counts_zh.svg",
        output_dir / "v1_15_feature_counts_zh.pdf",
    ]
    fig.savefig(outputs[0], dpi=200, facecolor=background)
    fig.savefig(outputs[1], facecolor=background)
    fig.savefig(outputs[2], facecolor=background)
    plt.close(fig)
    return outputs


def main() -> None:
    args = parse_args()
    sample_count, strict_result_count, raw_counts, strict_counts = load_data(
        args.summary,
        args.results,
    )
    for output in render(
        sample_count,
        strict_result_count,
        raw_counts,
        strict_counts,
        args.output_dir,
    ):
        print(output)


if __name__ == "__main__":
    main()
