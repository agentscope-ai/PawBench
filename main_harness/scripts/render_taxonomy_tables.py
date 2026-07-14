from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.feature_taxonomy import (  # noqa: E402
    CODE_ORDER,
    CODE_TABLE,
    CODE_TABLE_ZH,
    FEATURES,
    FEATURE_EFFECT_ZH,
    FEATURE_IDS,
    H_TO_FEATURES,
    TAXONOMY_VERSION,
    feature_label,
    h_feature_rows,
    validate_taxonomy,
)
from scripts.paths import HARNESS_WORK_ROOT  # noqa: E402


RENDER_ROOT = HARNESS_WORK_ROOT / "taxonomy_v2"
REPORTS = RENDER_ROOT / "reports"
RESULTS = RENDER_ROOT / "results"


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def md_escape(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", "<br>")


def render_code_table() -> str:
    lines = [
        "# H/M/Ex Code Table",
        "",
        "这张表是 PawBench2 Harness-core 的归因入口。`Ex-*` 归给 benchmark/task，`H*` 归给 harness/runtime，`M*` 归给 model。归因时必须以 trajectory、metrics、trace、tool result 或 scorer log 为证据，不能只用分数猜测。",
        "",
        "| Code | 归属 | 短名 | 什么时候使用 | 不要用于 | 最小证据 | 状态 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for code in CODE_ORDER:
        entry = CODE_TABLE[code]
        zh = CODE_TABLE_ZH[code]
        lines.append(
            "| `{code}` | {owner} | {short_name} | {assign_when} | {do_not_use_when} | {minimum_evidence} | {status} |".format(
                code=entry.code,
                owner=md_escape(zh["owner"]),
                short_name=md_escape(zh["short_name"]),
                assign_when=md_escape(zh["assign_when"]),
                do_not_use_when=md_escape(zh["do_not_use_when"]),
                minimum_evidence=md_escape(zh["minimum_evidence"]),
                status=md_escape(zh["status"]),
            )
        )
    lines += [
        "",
        "## 判定顺序",
        "",
        "1. 先判定 `Ex-*`：任务或评分系统本身是否错。",
        "2. 再判定 `H*`：harness 是否阻止了一次公平运行，或隐藏了真实失败。",
        "3. 最后判定 `M*`：在任务和 harness 都足够公平、可观察时，模型是否犯错。",
        "",
        "`Ex-3` 用于外部 provider、network、quota 或 hosted platform 故障，不自动映射 F-code。只有 harness 对该事件处理错误时，才另外添加有证据的 H/F。",
        "",
    ]
    return "\n".join(lines)


def render_h_feature_table() -> str:
    lines = [
        "# H-F Mapping Table",
        "",
        "这张表是 Harness error code 到可消融 Feature 开关的主映射。每个 `H*` 只保留 1-3 个 primary feature，避免把抽象能力写成无法消融的“大功能”。",
        "",
        "| H code | Harness failure | Primary feature switches | 开关能证明什么 | 主要 trace evidence |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in h_feature_rows():
        feature_ids = [item["feature_id"] for item in row["features"]]
        labels = "<br>".join(feature_label(feature_id, zh=True) for feature_id in feature_ids)
        effects = "<br>".join(FEATURE_EFFECT_ZH[feature_id] for feature_id in feature_ids)
        traces = "<br>".join(FEATURES[feature_id].trace_evidence for feature_id in feature_ids)
        h_name = CODE_TABLE_ZH[row["h_code"]]["short_name"]
        lines.append(
            f"| `{row['h_code']}` | {md_escape(h_name)} | {labels} | {md_escape(effects)} | {md_escape(traces)} |"
        )

    lines += [
        "",
        "## Feature Catalog",
        "",
        "| Feature ID | Canonical key | 新名称 | Layer | Primary codes | Related codes | Legacy names |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for feature_id in FEATURE_IDS:
        entry = FEATURES[feature_id]
        lines.append(
            "| `{feature_id}` | `{key}` | {name} | {layer} | {primary} | {related} | {legacy} |".format(
                feature_id=feature_id,
                key=entry.key,
                name=md_escape(f"{entry.name_en} / {entry.name_zh}"),
                layer=md_escape(entry.layer),
                primary=", ".join(f"`{code}`" for code in entry.primary_codes),
                related=", ".join(f"`{code}`" for code in entry.related_codes) or "-",
                legacy=md_escape(", ".join(entry.legacy_names)),
            )
        )

    lines += [
        "",
        "## Maintenance Rule",
        "",
        "- `feature_id` 保持稳定，用于历史 trace、JSONL、开关名和实验复现。",
        "- `name_en/name_zh/key` 可以继续优化，用于报告、论文和人工讨论。",
        "- 修改 `H_TO_FEATURES` 时必须保持每个 H code 1-3 个 primary feature，并重新运行 `scripts/render_taxonomy_tables.py` 和测试。",
        "- 旧 taxonomy 固定为 `legacy_p0_20260709`；当前表使用 `harness_core_v2_20260710`，禁止静默重解释历史 F-code。",
        "",
    ]
    return "\n".join(lines)


def write_json_summary() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": now(),
        "taxonomy_version": TAXONOMY_VERSION,
        "taxonomy_errors": validate_taxonomy(),
        "codes": {
            code: {
                "family": entry.family,
                "short_name": entry.short_name,
                "short_name_zh": CODE_TABLE_ZH[code]["short_name"],
                "owner": entry.owner,
                "owner_zh": CODE_TABLE_ZH[code]["owner"],
                "status": entry.status,
            }
            for code, entry in CODE_TABLE.items()
        },
        "h_to_features": {h_code: list(feature_ids) for h_code, feature_ids in H_TO_FEATURES.items()},
        "features": {
            feature_id: {
                "key": entry.key,
                "name_en": entry.name_en,
                "name_zh": entry.name_zh,
                "layer": entry.layer,
                "primary_codes": list(entry.primary_codes),
                "related_codes": list(entry.related_codes),
            }
            for feature_id, entry in FEATURES.items()
        },
    }
    (RESULTS / "taxonomy_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    errors = validate_taxonomy()
    if errors:
        raise SystemExit("\n".join(errors))
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "CODE_TABLE_H_M_EX.md").write_text(render_code_table(), encoding="utf-8")
    (REPORTS / "H_CODE_FEATURE_SCHEMA.md").write_text(render_h_feature_table(), encoding="utf-8")
    write_json_summary()
    print(f"wrote {REPORTS / 'CODE_TABLE_H_M_EX.md'}")
    print(f"wrote {REPORTS / 'H_CODE_FEATURE_SCHEMA.md'}")
    print(f"wrote {RESULTS / 'taxonomy_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
