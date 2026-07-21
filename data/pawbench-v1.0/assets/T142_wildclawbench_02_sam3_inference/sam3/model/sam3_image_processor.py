"""Sam3Processor (stub).  Wraps box/format helpers for the agent."""

from typing import Sequence, Tuple

from .box_ops import xywh_to_xyxy, xyxy_to_xywh


class Sam3Processor:
    @staticmethod
    def to_xyxy(boxes: Sequence[Sequence[float]]):
        return [xywh_to_xyxy(b) for b in boxes]

    @staticmethod
    def to_xywh(boxes: Sequence[Sequence[float]]):
        return [xyxy_to_xywh(b) for b in boxes]

    @staticmethod
    def normalise(boxes: Sequence[Sequence[float]], image_size: Tuple[int, int]):
        w, h = image_size
        out = []
        for b in boxes:
            out.append([b[0] / w, b[1] / h, b[2] / w, b[3] / h])
        return out
