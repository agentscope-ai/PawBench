"""Box operations for SAM3 (stub).

Boxes use [x_min, y_min, x_max, y_max] (xyxy) or [x, y, w, h] (xywh).
"""


def xywh_to_xyxy(box):
    x, y, w, h = box
    return [float(x), float(y), float(x + w), float(y + h)]


def xyxy_to_xywh(box):
    x1, y1, x2, y2 = box
    return [float(x1), float(y1), float(x2 - x1), float(y2 - y1)]
