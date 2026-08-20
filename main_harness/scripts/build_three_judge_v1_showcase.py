from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.paths import HARNESS_WORK_ROOT, RUN_RECORDS_ROOT  # noqa: E402
from scripts.stress_test_reasoning_v2 import call_model, now  # noqa: E402
from scripts.stress_test_real_v1_hf import (  # noqa: E402
    build_prompt,
    load_jsonl,
    logical_key,
    resolve_trajectory,
    validate_response,
)


DEEPSEEK_RUN = RUN_RECORDS_ROOT / "PawBenchV1-deepseek-v4-pro-20260710"
KIMI_RUN = RUN_RECORDS_ROOT / "PawBenchV1-kimi-k2.7-code-20260713"
DEFAULT_OUT = HARNESS_WORK_ROOT / "three_judge_real_v1_showcase_20260713"
QWEN_MODEL = "qwen3.7-max"
SEED = 7_102_026

EXAMPLES: tuple[dict[str, str], ...] = (
    {
        "row_key": "qwen3.7-max-20260612::qwen3.6-plus::codex::T134_skillsbench_manufacturing-equipment-maintenance",
        "input_hash": "798f73eb807592cb6dcc450a2bb6a6dc793b49fb77a42b928c613c25fa60fabe",
        "purpose": "Ex 一致案例：三方都把持续 429 归为外部服务故障。",
        "final_codes": "Ex-3",
        "final_features": "",
        "final_reason": "直接证据是外部模型服务持续返回 429，未显示 Harness 恢复机制本身错误；Ex-code 不映射 Feature。",
    },
    {
        "row_key": "pawbench-4models-opusjudge-20260529::deepseek-v4-pro::hermes::T067_pinchbench_competitive_research",
        "input_hash": "520761406206f3372db08c6faa3bba59a3d62e75e4891491047dbc573d31c085",
        "purpose": "Harness + Model 一致案例：工具错误反馈与模型幻觉同时出现。",
        "final_codes": "H2,M1",
        "final_features": "F2.3",
        "final_reason": "工具把无效网页标记为成功，属于错误反馈语义缺陷；模型又基于不足证据编造具体信息，因此同时保留 M1。",
    },
    {
        "row_key": "qwen3.7-max-20260612::glm-5.1::codex::T116_qwenpawbench_mm_tool_7997",
        "input_hash": "fc84f55b6ba9932daa11ddfc238850b2a4f02b6d82debd23960ed294c7d25fdf",
        "purpose": "分歧案例：DeepSeek/Kimi 归因上下文组装，Qwen 归因外部服务。",
        "final_codes": "H5",
        "final_features": "F5.1",
        "final_reason": "模型请求因输入长度越界立即失败，且没有有效执行轨迹，问题发生在送入模型前的上下文组装。",
    },
)

JUDGE_LABELS = (
    ("deepseek", "DeepSeek V4 Pro"),
    ("qwen", "Qwen 3.7 Max"),
    ("kimi", "Kimi K2.7 Code"),
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def job_identity(row_key: str) -> tuple[str, int]:
    job_id = hashlib.sha256(f"{row_key}::1".encode()).hexdigest()[:20]
    definition_seed = SEED + 1_000_003 + int(job_id[:8], 16)
    return job_id, definition_seed


def latest_base_results(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for item in load_jsonl(path):
        latest[item["job_id"]] = item
    return {
        item["row_key"]: item
        for item in latest.values()
        if int(item.get("repeat_no", 0)) == 1
    }


def load_sources() -> dict[str, Any]:
    deep_inputs_path = DEEPSEEK_RUN / "inputs.jsonl"
    kimi_inputs_path = KIMI_RUN / "inputs.jsonl"
    if file_sha256(deep_inputs_path) != file_sha256(kimi_inputs_path):
        raise RuntimeError("DeepSeek and Kimi inputs.jsonl hashes differ")

    inputs = {item["row_key"]: item for item in load_jsonl(kimi_inputs_path)}
    samples = {logical_key(item): item for item in load_jsonl(KIMI_RUN / "sample.jsonl")}
    deepseek = latest_base_results(DEEPSEEK_RUN / "results.jsonl")
    kimi = latest_base_results(KIMI_RUN / "results.jsonl")

    for example in EXAMPLES:
        row_key = example["row_key"]
        if row_key not in inputs or row_key not in samples or row_key not in deepseek or row_key not in kimi:
            raise RuntimeError(f"selected row is incomplete: {row_key}")
        if inputs[row_key]["input_hash"] != example["input_hash"]:
            raise RuntimeError(f"input hash drifted: {row_key}")
        expected_job_id, _ = job_identity(row_key)
        for model_name, result in (("DeepSeek", deepseek[row_key]), ("Kimi", kimi[row_key])):
            if result["job_id"] != expected_job_id:
                raise RuntimeError(f"{model_name} job id mismatch: {row_key}")

    return {
        "inputs": inputs,
        "samples": samples,
        "deepseek": deepseek,
        "kimi": kimi,
        "inputs_sha256": file_sha256(kimi_inputs_path),
        "sample_sha256": file_sha256(KIMI_RUN / "sample.jsonl"),
    }


def run_qwen(row_key: str, input_item: dict[str, Any], *, timeout: int, retries: int) -> dict[str, Any]:
    job_id, definition_seed = job_identity(row_key)
    prompt = build_prompt(input_item["package"], definition_seed=definition_seed)
    response = call_model(QWEN_MODEL, prompt, timeout=timeout, retries=retries)
    return {
        "job_id": job_id,
        "row_key": row_key,
        "repeat_no": 1,
        "input_hash": input_item["input_hash"],
        "definition_seed": definition_seed,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response": response,
        "validation": validate_response(response, input_item["ground_text"]),
    }


def code_and_feature_signature(result: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    validation = result.get("validation", {})
    return (
        tuple(sorted(set(validation.get("codes", [])))),
        tuple(sorted(set(validation.get("feature_ids", [])))),
    )


def display_list(values: list[str] | tuple[str, ...]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "无"


def csv_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def one_line(value: Any, limit: int = 800) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def quote_block(text: str) -> list[str]:
    compact = str(text or "").strip()
    if not compact:
        return ["> 无显式证据引用。"]
    return [f"> {line}" if line else ">" for line in compact.splitlines()]


def evidence_items(result: dict[str, Any]) -> list[tuple[str, str]]:
    parsed = result.get("response", {}).get("parsed", {})
    labels_by_quote: dict[str, list[str]] = {}
    for item in parsed.get("codes", []) if isinstance(parsed, dict) else []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("code") or "code")
        quote = str(item.get("evidence_quote") or "")
        if quote:
            labels_by_quote.setdefault(quote, []).append(label)
    for item in parsed.get("features", []) if isinstance(parsed, dict) else []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("feature_id") or "feature")
        quote = str(item.get("evidence_quote") or "")
        if quote and label not in labels_by_quote.setdefault(quote, []):
            labels_by_quote[quote].append(label)
    return [(" / ".join(labels), quote) for quote, labels in labels_by_quote.items()]


def validation_label(result: dict[str, Any]) -> str:
    validation = result.get("validation", {})
    if not result.get("response", {}).get("ok"):
        return "API 失败"
    strict = "通过" if validation.get("ok") else "失败"
    structural = "通过" if validation.get("structural_valid") else "失败"
    grounded = "通过" if validation.get("grounded") else "失败"
    pairs = "通过" if not validation.get("invalid_pairs") else "失败"
    return f"严格 {strict}；结构 {structural}；引用 {grounded}；H/F 配对 {pairs}"


def result_table_row(label: str, result: dict[str, Any]) -> str:
    validation = result.get("validation", {})
    return (
        f"| {label} | {display_list(validation.get('codes', []))} | "
        f"{display_list(validation.get('feature_ids', []))} | {validation_label(result)} |"
    )


def model_section(label: str, result: dict[str, Any]) -> list[str]:
    response = result.get("response", {})
    validation = result.get("validation", {})
    lines = [f"#### {label}", ""]
    if not response.get("ok"):
        lines.extend([f"- API 错误：`{one_line(response.get('error'))}`", ""])
        return lines

    lines.append(
        f"- **结论：** Codes {display_list(validation.get('codes', []))}；"
        f"Features {display_list(validation.get('feature_ids', []))}。"
    )
    items = evidence_items(result)
    if items:
        for code, quote in items:
            lines.append(f"- **证据（`{code}`）：** {one_line(quote, 360)}")
    else:
        lines.append("- **证据：** 未返回显式引用。")
    lines.append(f"- **说明：** {one_line(validation.get('reason'), 320) or '未提供。'}")
    errors = validation.get("errors", [])
    if errors:
        lines.append("- **校验问题：** " + "；".join(str(error) for error in errors) + "。")
    lines.append("")
    return lines


def difference_lines(results: dict[str, dict[str, Any]]) -> list[str]:
    signatures = {key: code_and_feature_signature(value) for key, value in results.items()}
    unique = set(signatures.values())
    grounded = {
        key: bool(value.get("validation", {}).get("grounded"))
        for key, value in results.items()
    }
    lines: list[str] = []
    if len(unique) == 1:
        codes, features = next(iter(unique))
        lines.append(
            f"三方结论完全一致：Codes 为 {display_list(codes)}，Features 为 {display_list(features)}。"
        )
    else:
        parts = []
        for key, label in JUDGE_LABELS:
            codes, features = signatures[key]
            parts.append(f"{label}={display_list(codes)} / {display_list(features)}")
        lines.append("三方结论不一致：" + "；".join(parts) + "。")

        h_sets = {
            key: tuple(code for code in signature[0] if code.startswith("H"))
            for key, signature in signatures.items()
        }
        if len(set(h_sets.values())) > 1:
            lines.append("Harness 责任层存在分歧，不能直接把多数票当作真值。")
        elif len({signature[1] for signature in signatures.values()}) > 1:
            lines.append("H-code 一致但 Feature 不一致，分歧发生在具体机制定位。")
        else:
            lines.append("Harness 层一致，分歧主要发生在 M-code 或 Ex-code 边界。")
    failed_grounding = [label for key, label in JUDGE_LABELS if not grounded[key]]
    if failed_grounding:
        lines.append("证据引用未通过本地原文校验：" + "、".join(failed_grounding) + "。")
    else:
        lines.append("三方证据引用均通过本地原文校验。")
    return lines


def render_markdown(
    sources: dict[str, Any],
    qwen: dict[str, dict[str, Any]],
) -> str:
    aligned = 0
    strict_counts = {key: 0 for key, _ in JUDGE_LABELS}
    for example in EXAMPLES:
        row_key = example["row_key"]
        results = {
            "deepseek": sources["deepseek"][row_key],
            "qwen": qwen[row_key],
            "kimi": sources["kimi"][row_key],
        }
        aligned += len({code_and_feature_signature(item) for item in results.values()}) == 1
        for result_key, _ in JUDGE_LABELS:
            strict_counts[result_key] += bool(results[result_key]["validation"].get("ok"))
    final_features = sorted(
        {
            feature
            for example in EXAMPLES
            for feature in csv_values(example["final_features"])
        }
    )
    lines = [
        "# PawBench V1 三模型归因案例对比",
        "",
        "## 数据口径",
        "",
        "- 三个归因 Judge 使用同一真实 V1 证据包、同一 `input_hash`、同一 Taxonomy、同一提示词、同一 `definition_seed` 和同一本地验证器。",
        "- DeepSeek 与 Kimi 取各自正式压力测试的 `repeat_no=1`；Qwen 对相同输入重新调用一次。",
        "- 展示的是 API 返回的证据引用、简短 `reason` 和归因结论，统称“可观察归因说明”；不展示或推断隐藏思维链。",
        "- 输入来自真实 PawBench V1 轨迹，但 Judge 看到的是统一构建的轨迹证据包，而不是未经裁剪的完整轨迹。",
        f"- 这 {len(EXAMPLES)} 条为解释性案例，不代表总体准确率。",
        f"- 完整输入文件 SHA256：`{sources['inputs_sha256']}`。",
        "",
        "## 结果概览",
        "",
        f"- 三方 H/F 完全一致：`{aligned}/{len(EXAMPLES)}`；存在分歧：`{len(EXAMPLES) - aligned}/{len(EXAMPLES)}`。",
        f"- 严格有效：DeepSeek `{strict_counts['deepseek']}/{len(EXAMPLES)}`；Qwen `{strict_counts['qwen']}/{len(EXAMPLES)}`；Kimi `{strict_counts['kimi']}/{len(EXAMPLES)}`。",
        f"- 最终 Harness Feature 覆盖：{display_list(final_features)}。",
        "- M-code、Ex-code 和正常样本不强行映射 Feature。",
        "",
        "## 快速索引",
        "",
        "| 示例 | 任务 | 被评测 Agent 模型 | Harness | 分数 | 三方 H/F | 最终 Feature |",
        "| ---: | --- | --- | --- | ---: | --- | --- |",
    ]
    for index, example in enumerate(EXAMPLES, start=1):
        key = example["row_key"]
        metadata = sources["inputs"][key]["package"]["metadata"]
        results = {
            "deepseek": sources["deepseek"][key],
            "qwen": qwen[key],
            "kimi": sources["kimi"][key],
        }
        agreement = "一致" if len({code_and_feature_signature(item) for item in results.values()}) == 1 else "分歧"
        lines.append(
            f"| {index:02d} | `{metadata.get('task_id')}` | `{metadata.get('model')}` | "
            f"`{metadata.get('harness')}` | {metadata.get('score')} | {agreement} | "
            f"{display_list(csv_values(example['final_features']))} |"
        )

    for index, example in enumerate(EXAMPLES, start=1):
        key = example["row_key"]
        input_item = sources["inputs"][key]
        package = input_item["package"]
        metadata = package["metadata"]
        metrics = package["metrics"]
        sample = sources["samples"][key]
        trajectory_path = resolve_trajectory(sample)
        results = {
            "deepseek": sources["deepseek"][key],
            "qwen": qwen[key],
            "kimi": sources["kimi"][key],
        }
        lines.extend(
            [
                "",
                "---",
                "",
                f"## 示例 {index:02d}：`{metadata.get('task_id')}`",
                "",
                f"**选择意义：** {example['purpose']}",
                "",
                "### 真实运行",
                "",
                "| 字段 | 内容 |",
                "| --- | --- |",
                f"| Run group | `{metadata.get('run_group')}` |",
                f"| 被评测 Agent 模型 | `{metadata.get('model')}` |",
                f"| Harness | `{metadata.get('harness')}` |",
                f"| 得分 / 通过 | `{metadata.get('score')}` / `{metadata.get('passed')}` |",
                f"| 状态 / 评分类型 | `{metadata.get('status')}` / `{metadata.get('grading_type')}` |",
                f"| 输入哈希 | `{input_item['input_hash']}` |",
                f"| 原始轨迹 | [打开文件]({trajectory_path}) |" if trajectory_path else "| 原始轨迹 | 未找到 |",
                "",
                "### 真实证据包",
                "",
                "**评分说明：**",
                "",
                *quote_block(str(metrics.get("notes") or "无")),
                "",
                f"**运行状态：** `exit_code={metrics.get('exit_code')}`；`timed_out={metrics.get('timed_out')}`；`error={one_line(metrics.get('error')) or '无'}`。",
                "",
                f"<details><summary>轨迹摘录（{len(package.get('trajectory_excerpts', []))} 条）</summary>",
                "",
                "```text",
                *[str(item) for item in package.get("trajectory_excerpts", [])],
                "```",
                "",
                "</details>",
                "",
                "### 三方结论对照",
                "",
                "| 归因 Judge | Codes | Features | 本地校验 |",
                "| --- | --- | --- | --- |",
                result_table_row("DeepSeek V4 Pro", results["deepseek"]),
                result_table_row("Qwen 3.7 Max", results["qwen"]),
                result_table_row("Kimi K2.7 Code", results["kimi"]),
                "",
                "### 可观察归因说明",
                "",
            ]
        )
        for result_key, label in JUDGE_LABELS:
            lines.extend(model_section(label, results[result_key]))
        lines.extend(["### 模型差异", ""])
        for difference in difference_lines(results):
            lines.append(f"- {difference}")
        lines.extend(
            [
                "",
                "### 最终工程归因",
                "",
                f"- 最终 Codes：{display_list(csv_values(example['final_codes']))}",
                f"- 最终 Features：{display_list(csv_values(example['final_features']))}",
                f"- 判定依据：{example['final_reason']}",
            ]
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 文件说明",
            "",
            f"- `selected_inputs.jsonl`：本展示使用的 {len(EXAMPLES)} 个原始统一证据包。",
            f"- `qwen_results.jsonl`：Qwen 3.7 Max 对这 {len(EXAMPLES)} 个输入的同口径原始响应与本地校验。",
            "- `manifest.json`：输入哈希、模型、种子和源运行目录。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a fair three-judge showcase on real PawBench V1 inputs.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument(
        "--desktop-markdown",
        type=Path,
        default=Path.home() / "Desktop" / "PawBench_V1_三模型归因_3例.md",
    )
    args = parser.parse_args()
    if args.workers <= 0:
        raise SystemExit("--workers must be positive")

    sources = load_sources()
    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    qwen_path = out_dir / "qwen_results.jsonl"
    existing_rows = load_jsonl(qwen_path) if qwen_path.is_file() else []
    qwen = {item["row_key"]: item for item in existing_rows}
    pending = []
    for example in EXAMPLES:
        row_key = example["row_key"]
        existing = qwen.get(row_key)
        if existing is None or (args.retry_failures and not existing.get("response", {}).get("ok")):
            pending.append(row_key)

    if pending:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    run_qwen,
                    row_key,
                    sources["inputs"][row_key],
                    timeout=args.timeout,
                    retries=args.retries,
                ): row_key
                for row_key in pending
            }
            for future in as_completed(futures):
                row_key = futures[future]
                qwen[row_key] = future.result()
                completed = sum(key in qwen for key in (item["row_key"] for item in EXAMPLES))
                print(f"[three-judge-showcase] qwen completed={completed}/{len(EXAMPLES)}", flush=True)

    ordered_qwen = [qwen[item["row_key"]] for item in EXAMPLES if item["row_key"] in qwen]
    qwen_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in ordered_qwen),
        encoding="utf-8",
    )
    selected_inputs = [sources["inputs"][item["row_key"]] for item in EXAMPLES]
    (out_dir / "selected_inputs.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in selected_inputs),
        encoding="utf-8",
    )
    manifest = {
        "generated_at": now(),
        "source": "real PawBench V1 trajectory evidence packages",
        "taxonomy_version": "harness_core_v2_20260710",
        "models": ["deepseek-v4-pro", QWEN_MODEL, "kimi-k2.7-code"],
        "repeat_no": 1,
        "seed": SEED,
        "inputs_sha256": sources["inputs_sha256"],
        "sample_sha256": sources["sample_sha256"],
        "deepseek_run": str(DEEPSEEK_RUN),
        "kimi_run": str(KIMI_RUN),
        "examples": [
            {
                **item,
                "definition_seed": job_identity(item["row_key"])[1],
                "qwen_prompt_sha256": qwen.get(item["row_key"], {}).get("prompt_sha256"),
            }
            for item in EXAMPLES
        ],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    missing = [item["row_key"] for item in EXAMPLES if item["row_key"] not in qwen]
    if missing:
        raise RuntimeError(f"missing Qwen results: {missing}")
    markdown = render_markdown(sources, qwen)
    (out_dir / "README.md").write_text(markdown, encoding="utf-8")
    desktop_markdown = args.desktop_markdown.expanduser().resolve()
    desktop_markdown.parent.mkdir(parents=True, exist_ok=True)
    desktop_markdown.write_text(markdown, encoding="utf-8")

    api_ok = sum(item.get("response", {}).get("ok", False) for item in ordered_qwen)
    strict = sum(item.get("validation", {}).get("ok", False) for item in ordered_qwen)
    print(
        json.dumps(
            {
                "examples": len(EXAMPLES),
                "qwen_api_ok": api_ok,
                "qwen_strict_valid": strict,
                "out_dir": str(out_dir),
                "desktop_markdown": str(desktop_markdown),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if api_ok == len(EXAMPLES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
