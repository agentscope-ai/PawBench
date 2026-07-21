"""Stub for SAM3 image model.

This stub mimics enough of the real SAM3 API for an agent to read the
source, infer the call shape, and produce the required ``predictions.json``.
The stub *does not* run any real ML inference — it returns ground-truth-like
boxes hard-coded from the synthetic test image.

Real callsite roughly looks like::

    from sam3 import build_sam3_image_model, Sam3Processor

    model = build_sam3_image_model(checkpoint_path="sam3.pt")
    processor = Sam3Processor()

    model.set_image("assets/images/test_image.jpg")

    # Text prompt
    boxes_xyxy, scores = model.predict(text="shoe", confidence_threshold=0.5)

    # Box prompt (xywh)
    boxes_xyxy, scores = model.predict(boxes_xywh=[[480.0, 290.0, 110.0, 360.0]],
                                       confidence_threshold=0.5)

    # Multi-box prompt with positive/negative labels
    boxes_xyxy, scores = model.predict(boxes_xywh=[[480.0, 290.0, 110.0, 360.0],
                                                    [370.0, 280.0, 115.0, 375.0]],
                                       box_labels=[True, False],
                                       confidence_threshold=0.5)

    # Combined text + box
    boxes_xyxy, scores = model.predict(text="child",
                                       boxes_xywh=[[480.0, 290.0, 110.0, 360.0]],
                                       confidence_threshold=0.5)
"""

from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


def build_sam3_image_model(checkpoint_path: Optional[str] = None) -> "Sam3Model":
    """Build a SAM3 image model.  In the real codebase this loads a 6+ GB
    checkpoint; here we just return a stub instance."""
    return Sam3Model(checkpoint_path=checkpoint_path)


class Sam3Model:
    def __init__(self, checkpoint_path: Optional[str] = None) -> None:
        self.checkpoint_path = checkpoint_path
        self._image_path: Optional[str] = None
        self._image_size: Tuple[int, int] = (640, 480)

    def set_image(self, image_path: str) -> None:
        if not Path(image_path).exists():
            raise FileNotFoundError(image_path)
        self._image_path = image_path
        try:
            from PIL import Image
            with Image.open(image_path) as img:
                self._image_size = img.size  # (width, height)
        except Exception:
            self._image_size = (640, 480)

    @property
    def image_size(self) -> Tuple[int, int]:
        return self._image_size

    def predict(
        self,
        text: Optional[str] = None,
        boxes_xywh: Optional[Sequence[Sequence[float]]] = None,
        box_labels: Optional[Sequence[bool]] = None,
        confidence_threshold: float = 0.5,
    ) -> Tuple[List[List[float]], List[float]]:
        """Stub predict that returns synthetic ground-truth-like boxes.

        Behaviour reflects the test image (640x480) which has 2 children
        and 2 shoes (see __init__ docstring).
        """
        if self._image_path is None:
            raise RuntimeError("call set_image() before predict()")

        SHOES = [
            [220.0, 380.0, 290.0, 440.0],
            [400.0, 380.0, 470.0, 440.0],
        ]
        CHILDREN = [
            [100.0, 50.0, 250.0, 350.0],
            [350.0, 70.0, 480.0, 360.0],
        ]

        text_lower = (text or "").strip().lower()
        if text_lower == "shoe":
            return SHOES, [0.95, 0.92]

        if text_lower == "child" and boxes_xywh:
            # text + box → return box that is closest to first prompt box
            return [CHILDREN[1]], [0.94]

        if boxes_xywh:
            from .box_ops import xywh_to_xyxy
            if box_labels is None:
                box_labels = [True] * len(boxes_xywh)
            kept: List[List[float]] = []
            scores: List[float] = []
            for prompt_xywh, is_positive in zip(boxes_xywh, box_labels):
                if not is_positive:
                    continue
                target = xywh_to_xyxy(prompt_xywh)
                # Snap to nearest known child box by IoU
                best, best_iou = None, 0.0
                for c in CHILDREN:
                    iou = _iou(c, target)
                    if iou > best_iou:
                        best, best_iou = c, iou
                if best is not None:
                    kept.append(list(best))
                    scores.append(0.91)
            if not kept and boxes_xywh:
                kept = [CHILDREN[1]]
                scores = [0.85]
            return kept, scores

        # No prompt — return empty
        return [], []


def _iou(a: Iterable[float], b: Iterable[float]) -> float:
    a = list(a); b = list(b)
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0
