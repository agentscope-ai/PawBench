---
id: 02_Code_Intelligence_task_1_sam3_inference
name: SAM3 Inference Code Implementation
category: 02_Code_Intelligence
grading_type: hybrid
timeout_seconds: 600
grading_weights:
  automated: 0.7
  llm_judge: 0.3
workspace_files:
- source: assets/T142_wildclawbench_02_sam3_inference/sam3/__init__.py
  dest: sam3/__init__.py
- source: assets/T142_wildclawbench_02_sam3_inference/sam3/model/__init__.py
  dest: sam3/model/__init__.py
- source: assets/T142_wildclawbench_02_sam3_inference/sam3/model/sam3_image.py
  dest: sam3/model/sam3_image.py
- source: assets/T142_wildclawbench_02_sam3_inference/sam3/model/sam3_image_processor.py
  dest: sam3/model/sam3_image_processor.py
- source: assets/T142_wildclawbench_02_sam3_inference/sam3/model/box_ops.py
  dest: sam3/model/box_ops.py
- source: assets/T142_wildclawbench_02_sam3_inference/sam3/assets/images/test_image.jpg
  dest: sam3/assets/images/test_image.jpg
- source: assets/T142_wildclawbench_02_sam3_inference/gt/gt_boxes.json
  dest: gt/gt_boxes.json
labels:
  complexity: L3
  environment: closed
  modality:
    type: multimodal
    channels:
    - image
  scenario: Software_Engineering/Code
  capabilities:
  - Code_Manipulation
  - Tool_Use
  - Logic_Reasoning
---
## Prompt

你是一名 AI 编程专家。在工作目录下有一个 **SAM3**（Segment Anything Model 3）的简化代码库（`sam3/` 目录），但**没有详细文档、README 或示例 Notebook**。你需要通过阅读源代码，理解 SAM3 的使用方法，然后编写推理脚本完成以下 4 个目标检测用例。

### 任务

请编写一个 Python 推理脚本，在测试图像上运行以下 4 个用例，并将结果保存到 `output/predictions.json`。

### 关键信息

- **SAM3 代码库**: `sam3/`（在工作目录下）
- **测试图像**: `sam3/assets/images/test_image.jpg` (640x480)
- **输出文件**: `output/predictions.json`
- **运行设备**: CPU（无需 GPU；本地化简版本不依赖外部权重）

阅读 `sam3/__init__.py`、`sam3/model/sam3_image.py`、`sam3/model/sam3_image_processor.py`、`sam3/model/box_ops.py` 来了解 API。

### 测试用例

**用例 1: `text_shoe`** — 使用文本提示 `"shoe"` 在图像中检测鞋子，置信度阈值 0.5

**用例 2: `single_box`** — 使用一个 bounding box (xywh)：`[480.0, 290.0, 110.0, 360.0]`，置信度阈值 0.5

**用例 3: `multi_box`** — 使用两个 bounding box：`[[480.0, 290.0, 110.0, 360.0], [370.0, 280.0, 115.0, 375.0]]`，对应正/负标签 `[True, False]`，置信度阈值 0.5

**用例 4: `text_box_combined`** — 使用文本提示 `"child"` 和一个 bounding box：`[480.0, 290.0, 110.0, 360.0]`，置信度阈值 0.5

### 输出格式

`predictions.json`：

```json
{
  "image": "test_image.jpg",
  "image_size": [width, height],
  "cases": {
    "text_shoe": {"boxes_xyxy": [[x1,y1,x2,y2], ...], "scores": [...]},
    "single_box": {"boxes_xyxy": [[x1,y1,x2,y2], ...], "scores": [...]},
    "multi_box":  {"boxes_xyxy": [[x1,y1,x2,y2], ...], "scores": [...]},
    "text_box_combined": {"boxes_xyxy": [[x1,y1,x2,y2], ...], "scores": [...]}
  }
}
```

`boxes_xyxy` 是 `[x_min, y_min, x_max, y_max]` 格式（像素），`scores` 是对应的置信度分数。

## Expected Behavior

Agent 应当：

1. 探索 `sam3/` 目录结构，阅读关键源码文件（`__init__.py`、`model/sam3_image.py`、`model/sam3_image_processor.py`、`model/box_ops.py`）
2. 理解 SAM3 的核心 API：`build_sam3_image_model`、`Sam3Processor`、`set_image`、`predict(text=, boxes_xywh=, box_labels=, confidence_threshold=)`
3. 编写推理脚本，正确处理 box 格式转换（xywh ↔ xyxy）
4. 运行脚本，将结果保存到 `output/predictions.json`

## Grading Criteria

- [ ] 成功生成 `output/predictions.json`，格式正确
- [ ] `text_shoe` 用例通过（F1 ≥ 0.8）
- [ ] `single_box` 用例通过（F1 ≥ 0.8）
- [ ] `multi_box` 用例通过（F1 ≥ 0.8）
- [ ] `text_box_combined` 用例通过（F1 ≥ 0.8）

`overall_score = passed / total`

## Automated Checks

```python
import json
from pathlib import Path


def grade(transcript: list, workspace_path: str) -> dict:
    """SAM3 inference grader (ported from WildClawBench task 2.1).

    Compare predictions.json against gt_boxes.json: per-case IoU>=0.5 matching,
    F1>=0.8 ⇒ pass. overall_score = passed / total.
    """
    iou_thresh = 0.5
    f1_pass = 0.8

    pred_path = Path(workspace_path) / "output" / "predictions.json"
    gt_path = Path(workspace_path) / "gt" / "gt_boxes.json"

    def _box_iou(a, b):
        x1, y1 = max(a[0], b[0]), max(a[1], b[1])
        x2, y2 = min(a[2], b[2]), min(a[3], b[3])
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
        return inter / union if union > 0 else 0.0

    def _match(pred_boxes, gt_boxes):
        used = set()
        tp = 0
        for gt in gt_boxes:
            best_iou, best_j = 0, -1
            for j, p in enumerate(pred_boxes):
                if j in used:
                    continue
                iou = _box_iou(p, gt)
                if iou > best_iou:
                    best_iou, best_j = iou, j
            if best_iou >= iou_thresh and best_j >= 0:
                used.add(best_j)
                tp += 1
        return tp, len(pred_boxes) - tp, len(gt_boxes) - tp

    def _f1(tp, fp, fn):
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        return 2 * p * r / (p + r) if p + r else 0.0

    if not pred_path.exists() or not gt_path.exists():
        return {"path_exists": 0.0, "overall_score": 0.0}

    try:
        pred = json.loads(pred_path.read_text())
        gt = json.loads(gt_path.read_text())
    except Exception:
        return {"overall_score": 0.0}

    per_case = {}
    passed = 0
    total = 0

    for name, gt_case in gt["cases"].items():
        gt_boxes = gt_case["boxes_xyxy"]
        pred_boxes = pred.get("cases", {}).get(name, {}).get("boxes_xyxy", [])
        tp, fp, fn = _match(pred_boxes, gt_boxes)
        f1 = _f1(tp, fp, fn)
        case_pass = f1 >= f1_pass
        per_case[name] = {"tp": tp, "fp": fp, "fn": fn, "f1": round(f1, 4), "pass": case_pass}
        if case_pass:
            passed += 1
        total += 1

    return {
        "path_exists": 1.0,
        **{name: 1.0 if case["pass"] else 0.0 for name, case in per_case.items()},
        "overall_score": round(passed / total, 4) if total else 0.0,
    }
```

## LLM Judge Rubric

### Criterion 1: Source Code Comprehension (Weight: 50%)

Evaluate whether the agent demonstrated understanding of the SAM3 stub API:

**Scoring:**
- **1.0**: Agent read multiple source files (`sam3/__init__.py`, `model/sam3_image.py`, `model/box_ops.py`), correctly identified the `build_sam3_image_model` factory, the `set_image` + `predict` workflow, and the `box_labels` semantic for positive/negative box prompts.
- **0.7**: Agent read the source and used the right entry points but missed nuances (e.g., box_labels).
- **0.4**: Agent guessed the API without reading source.
- **0.0**: Agent didn't engage with the source code.

### Criterion 2: Inference Script Quality (Weight: 50%)

**Scoring:**
- **1.0**: Inference script runs all 4 cases, handles xywh ↔ xyxy conversion, writes valid `output/predictions.json` matching the schema, with all per-case `boxes_xyxy` and `scores` arrays well-formed.
- **0.7**: Output file present but missing one case or with minor format issues.
- **0.3**: Script written but didn't produce valid JSON output.
- **0.0**: No script / no output.
