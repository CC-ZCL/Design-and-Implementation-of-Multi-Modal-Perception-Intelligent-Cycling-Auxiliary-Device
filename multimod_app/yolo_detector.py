"""YOLOv8n 前方障碍物检测（轻量，适合树莓派）。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

from multimod_app import config


def default_model_path() -> str:
    local = config.PROJECT_ROOT / config.YOLO_MODEL_FILENAME
    if local.is_file():
        return str(local)
    return config.YOLO_MODEL_FILENAME


class YoloDetector:
    def __init__(
        self,
        model_path: str | None = None,
        conf: float | None = None,
        iou: float | None = None,
    ) -> None:
        self.conf = config.YOLO_CONF if conf is None else conf
        self.iou = config.YOLO_IOU if iou is None else iou
        self._model: Any = None
        self._path = model_path or default_model_path()

    def load(self) -> None:
        if self._model is not None:
            return
        from ultralytics import YOLO

        self._model = YOLO(self._path)
        self._model.fuse()

    def unload(self) -> None:
        self._model = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def infer(self, bgr: np.ndarray) -> tuple[np.ndarray, list[dict[str, Any]], float]:
        """
        返回: 绘制后的图像, 障碍物列表[{cls, name, conf, xyxy, area_ratio}], 最大 area_ratio(0~1)
        """
        if self._model is None:
            return bgr, [], 0.0
        h, w = bgr.shape[:2]
        pred_kw: dict[str, Any] = {
            "source": bgr,
            "conf": self.conf,
            "iou": self.iou,
            "verbose": False,
            "imgsz": config.YOLO_IMGSZ,
        }
        dev = os.environ.get(config.YOLO_DEVICE_ENV, "").strip()
        if dev:
            pred_kw["device"] = dev
        results = self._model.predict(**pred_kw)
        obstacles: list[dict[str, Any]] = []
        max_ratio = 0.0
        annotated = bgr.copy()
        if not results:
            return annotated, obstacles, max_ratio
        r0 = results[0]
        names = r0.names or {}
        if r0.boxes is None or len(r0.boxes) == 0:
            return annotated, obstacles, max_ratio

        boxes = r0.boxes
        for i in range(len(boxes)):
            cls_id = int(boxes.cls[i].item())
            if cls_id not in config.YOLO_OBSTACLE_CLASS_IDS:
                continue
            xyxy = boxes.xyxy[i].cpu().numpy().tolist()
            conf = float(boxes.conf[i].item())
            x1, y1, x2, y2 = xyxy
            bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
            area_ratio = (bw * bh) / float(w * h)
            max_ratio = max(max_ratio, area_ratio)
            name = names.get(cls_id, str(cls_id))
            obstacles.append(
                {
                    "cls": cls_id,
                    "name": name,
                    "conf": conf,
                    "xyxy": [int(x1), int(y1), int(x2), int(y2)],
                    "area_ratio": area_ratio,
                }
            )

        annotated = r0.plot()

        return annotated, obstacles, max_ratio
