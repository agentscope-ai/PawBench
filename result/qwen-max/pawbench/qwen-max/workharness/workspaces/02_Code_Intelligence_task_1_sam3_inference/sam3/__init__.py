"""SAM3 stub package for the WildClawBench-converted task.

In the real WildClawBench task, this directory contains the actual SAM3
codebase (Segment Anything Model 3), 6+ GB model weights, and assets.
Because we cannot ship that here, this stub provides a *minimal* API that
mirrors the real one closely enough for the agent to read source, infer
how the API works, and produce a correctly formatted ``predictions.json``.

The grader compares the agent's predictions against a fixed ground-truth
box set (see ``gt/gt_boxes.json``).  The ground-truth boxes correspond to
the synthetic test image (640x480), with two child-shaped rectangles:
  - "child blue":     [100, 50, 250, 350]
  - "child red":      [350, 70, 480, 360]
  - "shoe-left":      [220, 380, 290, 440]
  - "shoe-right":     [400, 380, 470, 440]

Public API (read source for details):

  * ``build_sam3_image_model(checkpoint_path: str | None = None) -> Sam3Model``
  * ``Sam3Model``  has ``set_image(image_path)`` and ``predict(...)``
  * ``Sam3Processor``  exposes preprocessing helpers (xyxy <-> xywh).
"""

from .model.sam3_image import Sam3Model, build_sam3_image_model  # noqa: F401
from .model.sam3_image_processor import Sam3Processor  # noqa: F401
from .model.box_ops import xywh_to_xyxy, xyxy_to_xywh  # noqa: F401

__all__ = [
    "Sam3Model",
    "Sam3Processor",
    "build_sam3_image_model",
    "xywh_to_xyxy",
    "xyxy_to_xywh",
]
