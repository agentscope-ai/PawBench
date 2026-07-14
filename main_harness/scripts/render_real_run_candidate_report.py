from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.paths import HARNESS_WORK_ROOT, LEGACY_ARTIFACTS_ROOT  # noqa: E402

REPORTS = HARNESS_WORK_ROOT / "legacy_candidate_reports"
FIGURES = REPORTS / "figures"
RESULTS = LEGACY_ARTIFACTS_ROOT / "harness_core" / "results"

ORDER = [
    "PydanticAI",
    "AgentScope",
]

BACKUP_CANDIDATES = [
    "LangGraph",
    "OpenAI Agents SDK",
    "mini-swe-agent",
    "QwenPaw",
    "Pi Agent",
]

STATUS_WEIGHT = {
    "implemented_candidate": 1.0,
    "probe_candidate": 4.0,
    "wrapper_required": 7.0,
    "install_gated": 12.0,
}

EVIDENCE_WEIGHT = {
    "behavioral": 0.0,
    "pass": 0.0,
    "behavioral_probe": 0.8,
    "contract": 1.5,
    "partial": 2.0,
    "missing": 3.0,
    "install_gated": 4.0,
}

NATIVE_BLOCKER_WEIGHT = {
    "QwenPaw": 5.0,
    "Pi Agent": 8.0,
}

READY_EVIDENCE_LEVELS = {"behavioral", "behavioral_probe", "pass", "partial"}

LEGACY_FEATURE_IDS = ("F1.1", "F1.2", "F1.5", "F2.1", "F2.3", "F2.4", "F3.1", "F3.3", "F4.1", "F5.1")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifests() -> dict[str, dict[str, Any]]:
    out = {}
    for path in (ROOT / "candidates").glob("*/feature_manifest.json"):
        manifest = read_json(path)
        manifest["path"] = str(path.relative_to(ROOT))
        out[manifest["candidate"]] = manifest
    return out


def count_records(summary: dict[str, Any], candidate: str, key: str) -> tuple[int, int]:
    records = [record for record in summary.get("records", []) if record.get("candidate") == candidate]
    passed = sum(1 for record in records if record.get(key) is True)
    return passed, len(records)


def complexity(manifest: dict[str, Any], native_blocked: bool) -> dict[str, Any]:
    status_score = STATUS_WEIGHT.get(manifest.get("status"), 10.0)
    evidence_items = {fid: manifest["features"][fid]["evidence_level"] for fid in LEGACY_FEATURE_IDS}
    evidence_score = sum(EVIDENCE_WEIGHT.get(level, 2.5) for level in evidence_items.values())
    blocker_score = NATIVE_BLOCKER_WEIGHT.get(manifest["candidate"], 0.0) if native_blocked else 0.0
    total = round(status_score + evidence_score + blocker_score, 1)
    ready_switch_count = sum(1 for level in evidence_items.values() if level in READY_EVIDENCE_LEVELS)
    return {
        "score": total,
        "status_score": status_score,
        "evidence_score": round(evidence_score, 1),
        "blocker_score": blocker_score,
        "evidence_items": evidence_items,
        "ready_switch_count": ready_switch_count,
    }


def feature_evidence_summary(evidence_items: dict[str, str]) -> str:
    counts: dict[str, int] = {}
    for level in evidence_items.values():
        counts[level] = counts.get(level, 0) + 1
    return ", ".join(f"{level}:{count}" for level, count in sorted(counts.items()))


def candidate_note(candidate: str) -> str:
    notes = {
        "PydanticAI": "类型化、最干净；feature switch 和 trace schema 最容易保持可消融。",
        "AgentScope": "真实工具/事件/permission 生态更完整；主要风险是原始事件多，需要坚持 PawBench-normalized trace。",
        "LangGraph": "graph/node/state 边界清楚，适合流程消融；当前仍是 probe/thin-adapter 级别。",
        "OpenAI Agents SDK": "真实 API 和 function-tool 入口顺畅；managed loop 下细粒度 attribution 要由 adapter 固化。",
        "mini-swe-agent": "最小 baseline，真实 LocalEnvironment bash 证据强；PawBench feature layer 多数要 wrapper。",
        "QwenPaw": "产品能力多、skills/tool guard/session 都可参考；native headless 被本地 provider 连接阻塞。",
        "Pi Agent": "TypeScript 参考路线；当前 native runtime 未安装，只能作为 install-gated 候选。",
    }
    return notes[candidate]


def native_status(candidate: str, blockers: dict[str, Any]) -> str:
    if candidate in blockers.get("native_runtime_attempts", {}):
        return blockers["native_runtime_attempts"][candidate]["status"]
    if candidate in {"PydanticAI", "AgentScope"}:
        return "implemented/native adapter available"
    if candidate in {"LangGraph", "OpenAI Agents SDK", "mini-swe-agent"}:
        return "probe/native loop smoke available"
    return "not separately tested"


def shared_switch_coverage(summary: dict[str, Any], candidate: str) -> str:
    checks = summary["candidate_summary"][candidate]["feature_switches_checked"]
    passed = sum(1 for item in checks.values() if item["disabled_feature_enabled_absent"])
    return f"{passed}/{len(checks)}"


def native_ablation_evidence(candidate: str) -> str:
    evidence = {
        "PydanticAI": "native: F1.2/F2.4 impact 10轮；pytest 12项；shared API/file matrix 22项",
        "AgentScope": "native: F1.2/F2.4 impact 10轮；pytest 15项；F2.3/F5.1 explicit off；real all_p0 pass + without_F2.3 blocked",
    }
    return evidence[candidate]


def render_chart(rows: list[dict[str, Any]]) -> tuple[Path, Path]:
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    FIGURES.mkdir(parents=True, exist_ok=True)
    font_path = Path("/System/Library/Fonts/Supplemental/Songti.ttc")
    if font_path.exists():
        font_manager.fontManager.addfont(str(font_path))
        plt.rcParams["font.family"] = "Songti SC"
    plt.rcParams["axes.unicode_minus"] = False

    rows_sorted = sorted(rows, key=lambda row: row["complexity_score"])
    names = [row["candidate"] for row in rows_sorted]
    values = [row["complexity_score"] for row in rows_sorted]
    colors = []
    for value in values:
        if value <= 5:
            colors.append("#6fbf73")
        elif value <= 18:
            colors.append("#f2b84b")
        elif value <= 35:
            colors.append("#e6815b")
        else:
            colors.append("#c85b5b")

    fig, ax = plt.subplots(figsize=(11, 6.2), dpi=180)
    fig.patch.set_facecolor("#fbf4e8")
    ax.set_facecolor("#fbf4e8")
    bars = ax.barh(names, values, color=colors, edgecolor="#2f2f2f", linewidth=1.1)
    ax.invert_yaxis()
    ax.set_title("候选 Harness 可消融接入复杂度指数", fontsize=18, pad=18, color="#2d2a26")
    ax.set_xlabel("复杂度指数：越高表示 native 可消融化工程负担越大", fontsize=11, color="#3a362f")
    ax.grid(axis="x", color="#d8cbb8", linestyle="-", linewidth=0.8, alpha=0.8)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#807568")
    ax.tick_params(axis="both", colors="#342f2a", labelsize=10)
    xmax = max(values) + 5
    ax.set_xlim(0, xmax)
    for bar, row in zip(bars, rows_sorted):
        value = row["complexity_score"]
        label = f"{value:g}  = 状态{row['status_score']:g} + 特征债{row['evidence_score']:g} + 阻塞{row['blocker_score']:g}"
        ax.text(value + 0.4, bar.get_y() + bar.get_height() / 2, label, va="center", ha="left", fontsize=9, color="#2d2a26")
    fig.text(
        0.02,
        0.02,
        "数据来源：real_api_feature_switch_matrix、real_feature_switch_matrix、feature_manifest、native_runtime_blockers。共享 API 通过不等于 native runtime 通过。",
        fontsize=9,
        color="#5e554c",
    )
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    png = FIGURES / "real_run_ablation_complexity_index.png"
    svg = FIGURES / "real_run_ablation_complexity_index.svg"
    fig.savefig(png, facecolor=fig.get_facecolor(), bbox_inches="tight")
    fig.savefig(svg, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return png, svg


def render_ready_switch_chart(rows: list[dict[str, Any]]) -> tuple[Path, Path]:
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    FIGURES.mkdir(parents=True, exist_ok=True)
    font_path = Path("/System/Library/Fonts/Supplemental/Songti.ttc")
    if font_path.exists():
        font_manager.fontManager.addfont(str(font_path))
        plt.rcParams["font.family"] = "Songti SC"
    plt.rcParams["axes.unicode_minus"] = False

    rows_sorted = sorted(rows, key=lambda row: (-row["ready_switch_count"], row["complexity_score"]))
    names = [row["candidate"] for row in rows_sorted]
    values = [row["ready_switch_count"] for row in rows_sorted]
    colors = ["#4f9f68" if value >= 10 else "#74a9cf" if value >= 7 else "#f2b84b" if value >= 4 else "#c85b5b" for value in values]

    fig, ax = plt.subplots(figsize=(10.8, 5.8), dpi=180)
    fig.patch.set_facecolor("#fbf4e8")
    ax.set_facecolor("#fbf4e8")
    bars = ax.barh(names, values, color=colors, edgecolor="#2f2f2f", linewidth=1.1)
    ax.invert_yaxis()
    ax.set_xlim(0, 10.8)
    ax.set_xticks(range(0, 11, 2))
    ax.set_title("候选 Harness 开关证据数量", fontsize=18, pad=18, color="#2d2a26")
    ax.set_xlabel("10 个 P0 features 中已有可执行或部分可执行开关证据的数量", fontsize=11, color="#3a362f")
    ax.grid(axis="x", color="#d8cbb8", linestyle="-", linewidth=0.8, alpha=0.8)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#807568")
    ax.tick_params(axis="both", colors="#342f2a", labelsize=10)
    for bar, row in zip(bars, rows_sorted):
        value = row["ready_switch_count"]
        ax.text(value + 0.15, bar.get_y() + bar.get_height() / 2, f"{value}/10", va="center", ha="left", fontsize=10, color="#2d2a26")
    fig.text(
        0.02,
        0.02,
        "计数规则：behavioral / behavioral_probe / pass / partial 计入；contract / missing / install_gated 不计入。该图不等于全 native 消融完成。",
        fontsize=9,
        color="#5e554c",
    )
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    png = FIGURES / "ready_feature_switch_count.png"
    svg = FIGURES / "ready_feature_switch_count.svg"
    fig.savefig(png, facecolor=fig.get_facecolor(), bbox_inches="tight")
    fig.savefig(svg, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return png, svg


def markdown_table(rows: list[dict[str, Any]]) -> str:
    header = (
        "| Candidate | 当前定位 | 真实 API quick matrix | 真实文件任务 matrix | 共享矩阵 off 覆盖 | 候选原生消融证据 | Native runtime 记录 | 现成开关证据数量 | 复杂度指数 |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    )
    lines = [header]
    for row in rows:
        lines.append(
            "| {candidate} | {status} / {validation} | {api_pass}/{api_total} API cases，all_p0={api_all} | "
            "{file_pass}/{file_total} validator cases，all_p0={file_all} | API {api_off_coverage}; file {file_off_coverage} | "
            "{native_ablation} | {native} | {ready_switch_count}/10 | {complexity_score} |".format(**row)
        )
    return "\n".join(lines)


def main() -> int:
    REPORTS.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    manifests = load_manifests()
    api = read_json(RESULTS / "real_api_feature_switch_matrix_summary.json")
    real = read_json(RESULTS / "real_feature_switch_matrix_summary.json")
    blockers = read_json(RESULTS / "native_runtime_blockers.json")
    blocker_candidates = set(blockers.get("native_runtime_attempts", {}))

    rows: list[dict[str, Any]] = []
    for candidate in ORDER:
        manifest = manifests[candidate]
        api_pass, api_total = count_records(api, candidate, "external_validator_passed")
        file_pass, file_total = count_records(real, candidate, "accepted")
        api_all = api["candidate_summary"][candidate]["all_p0_external_validator_passed"]
        file_all = real["candidate_summary"][candidate]["all_p0_validator_passed"]
        comp = complexity(manifest, candidate in blocker_candidates)
        rows.append(
            {
                "candidate": candidate,
                "status": manifest["status"],
                "validation": manifest["validation_level"],
                "api_pass": api_pass,
                "api_total": api_total,
                "api_all": str(api_all).lower(),
                "file_pass": file_pass,
                "file_total": file_total,
                "file_all": str(file_all).lower(),
                "api_off_coverage": shared_switch_coverage(api, candidate),
                "file_off_coverage": shared_switch_coverage(real, candidate),
                "native_ablation": native_ablation_evidence(candidate),
                "native": native_status(candidate, blockers),
                "note": candidate_note(candidate),
                "complexity_score": comp["score"],
                "ready_switch_count": comp["ready_switch_count"],
                "status_score": comp["status_score"],
                "evidence_score": comp["evidence_score"],
                "blocker_score": comp["blocker_score"],
                "evidence_summary": feature_evidence_summary(comp["evidence_items"]),
            }
        )

    png, svg = render_chart(rows)
    ready_png, ready_svg = render_ready_switch_chart(rows)
    active_count = len(rows)
    api_case_count = api.get("case_count", 0)
    file_case_count = real.get("case_count", 0)
    backup_lines = "\n".join(f"- `{name}`：已移动到 `candidates_backup/`，保留作后续参考，不再进入 active harness 默认扫描。" for name in BACKUP_CANDIDATES)
    doc = f"""# 真实运行记录下的 Candidate Harness 特点

生成位置：Harness-core 工作区

这份文档只总结已经落盘的真实运行记录，不把 manifest/contract 当成真实通过证据。主要依据：

- `results/real_api_feature_switch_matrix_summary.json`：{active_count} 个 active 候选，共 {api_case_count} 次真实 OpenAI-compatible API quick test。注意：这是 shared PawBench-side adapter matrix，不等于每个候选的 native harness loop。
- `results/real_feature_switch_matrix_summary.json`：{active_count} 个 active 候选，共 {file_case_count} 次真实文件任务/validator test。注意：这是 shared PawBench-side adapter matrix，不等于每个候选的 native harness loop。
- `results/native_runtime_blockers.json`：QwenPaw 和 Pi Agent 的 native runtime 阻塞证据。
- `candidates/*/feature_manifest.json`：每个 feature 的证据等级和实现位置。

## 当前决策

Active harness base 只保留两个：

- `candidates/pydantic-ai`
- `candidates/agentscope`

Backup candidates：

{backup_lines}

## 消融证据边界

结论先写清楚：**当前 active 决策是 PydanticAI + AgentScope；其他候选进入 backup，不再作为本轮 harness base。**

当前有两层证据：

1. **共享 PawBench-side matrix**：两个 active 候选都跑了 `all_p0 + without_Fx`，可以证明 feature config 在统一 harness 层能开关，且关闭的 feature 不再进入 `feature_enabled` trace。这个层面是 `10/10` off 覆盖。
2. **候选原生/候选 adapter 消融**：PydanticAI 和 AgentScope 都有 candidate 层代码、pytest、10 轮 impact smoke。AgentScope 额外补了真实 native `all_p0` pass 与 `without_F2.3` blocked 证据。

所以，下表里的“共享矩阵 off 覆盖”仍然和“candidate-native impact 消融”分开记录。对于最终论文/系统实验，建议把 shared matrix 作为开关完整性证据，把 native impact smoke/real run 作为因果影响证据。

## 总览表

{markdown_table(rows)}

说明：`without_F2.3` 是特意关闭 default tool availability，因此工具被阻断、validator 失败是预期的消融效果。上表的 pass 计数按最终 artifact validator 统计，所以每个候选通常是 10/11，而不是 11/11。

## 工程指标图

这里使用两个指标：

1. **现成开关证据数量**：10 个 P0 features 中，证据等级为 `behavioral / behavioral_probe / pass / partial` 的数量。`contract / missing / install_gated` 不计入。这个指标越高，说明现在越容易直接做 feature on/off，但它不等于全 candidate-native 消融完成。
2. **可消融接入复杂度指数**：衡量“把这个候选变成 native、可逐 feature 消融的 PawBench harness”还需要多少工程工作。

![现成可开关特征数量](figures/{ready_png.name})

SVG 版本：`reports/figures/{ready_svg.name}`

公式：

```text
复杂度指数 = 路线成熟度分 + feature 证据债 + native runtime blocker 分
```

取值规则：

- 路线成熟度分：implemented=1，probe=4，wrapper_required=7，install_gated=12。
- feature 证据债：behavioral/pass=0，behavioral_probe=0.8，contract=1.5，partial=2，missing=3，install_gated=4。
- native blocker：QwenPaw +5，Pi Agent +8。

![可消融接入复杂度指数](figures/{png.name})

SVG 版本：`reports/figures/{svg.name}`

附加参考图：`reports/figures/harness_core_code_lengths_zh_claude_style.png`。这张是 LOC 复杂度图，仅作为代码体量参考，不作为真实运行结论。

## 逐候选观察

"""
    for row in rows:
        doc += f"""### {row['candidate']}

- 定位：`{row['status']}`，验证等级：`{row['validation']}`。
- 真实 API quick matrix：`{row['api_pass']}/{row['api_total']}` 个 case 通过 artifact validator，`all_p0={row['api_all']}`。
- 真实文件任务 matrix：`{row['file_pass']}/{row['file_total']}` 个 case 通过 artifact validator，`all_p0={row['file_all']}`。
- 共享矩阵 off 覆盖：API `{row['api_off_coverage']}`，file `{row['file_off_coverage']}`。
- 候选原生消融证据：{row['native_ablation']}。
- Native runtime：`{row['native']}`。
- Feature 证据分布：`{row['evidence_summary']}`。
- 现成开关证据数量：`{row['ready_switch_count']}/10`。
- 真实记录下的特点：{row['note']}
- 复杂度指数：`{row['complexity_score']}`。

"""
    doc += """## 关键解释

1. `PydanticAI` 和 `AgentScope` 的复杂度最低，因为它们已经有完整 feature config、trace、verifier 和真实测试脚本。
2. `LangGraph` 与 `OpenAI Agents SDK` 的 shared API/文件矩阵通过，但它们仍是 probe/thin-adapter 层，需要把 skill/context、retry、prompt rewrite 等 contract feature 变成 native adapter 行为。
3. `mini-swe-agent` 真实环境交互很简单可靠，但 PawBench feature layer 大多需要 wrapper，所以接入复杂度高于 graph/SDK probe。
4. `QwenPaw` 的产品能力很多，但 native headless 在本机被 local-mlx provider connection 阻塞；因此 shared API matrix 不能解释为 QwenPaw native 通过。
5. `Pi Agent` 目前仍是 TypeScript install-gated；shared API matrix 只说明 PawBench wrapper 层可以模拟该路线的 feature contract。
"""
    out = REPORTS / "Real_Run_Candidate_Characteristics.md"
    out.write_text(doc, encoding="utf-8")
    machine = RESULTS / "real_run_candidate_characteristics.json"
    machine.write_text(
        json.dumps(
            {
                "rows": rows,
                "complexity_figure_png": str(png),
                "complexity_figure_svg": str(svg),
                "ready_switch_figure_png": str(ready_png),
                "ready_switch_figure_svg": str(ready_svg),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(out)
    print(png)
    print(svg)
    print(ready_png)
    print(ready_svg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
