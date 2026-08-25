"""视频采集与推理线程，避免阻塞 UI。"""
from __future__ import annotations

import time
from typing import Any

import cv2
import numpy as np
from PyQt5 import QtCore, QtGui

from multimod_app import config
from multimod_app.safety_engine import compute_safe_speed
from multimod_app.weather_service import WeatherSnapshot
from multimod_app.yolo_detector import YoloDetector


def _bgr_to_qimage(bgr: np.ndarray) -> QtGui.QImage:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    bytes_per_line = ch * w
    return QtGui.QImage(rgb.data, w, h, bytes_per_line, QtGui.QImage.Format_RGB888).copy()


class VideoWorker(QtCore.QThread):
    frame_ready = QtCore.pyqtSignal(QtGui.QImage)
    tick = QtCore.pyqtSignal(dict)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._run_flag = False
        self._cap: cv2.VideoCapture | None = None
        self._source: str | int = 0
        self._use_yolo = True
        self._detector = YoloDetector()
        self._current_speed = 0.0
        self._weather: WeatherSnapshot | None = None
        self._base_max_kmh = config.SAFETY_BASE_MAX_KMH_DEFAULT
        self._target_frame_interval = 1.0 / config.VIDEO_TARGET_FPS
        self._rider_risk = 0.0

    def set_source(self, source: str | int) -> None:
        self._source = source

    def set_yolo(self, on: bool) -> None:
        self._use_yolo = on

    def set_current_speed(self, v: float) -> None:
        self._current_speed = max(0.0, v)

    def set_weather(self, w: WeatherSnapshot | None) -> None:
        self._weather = w

    def set_base_max_kmh(self, v: float) -> None:
        self._base_max_kmh = max(5.0, min(60.0, v))

    def set_rider_risk(self, r: float) -> None:
        self._rider_risk = max(0.0, min(1.0, float(r)))

    def stop_capture(self) -> None:
        self._run_flag = False

    def run(self) -> None:
        self._run_flag = True
        if self._use_yolo:
            try:
                self._detector.load()
            except Exception as e:
                self.tick.emit({"error": f"YOLO 加载失败: {e}"})
                self._run_flag = False

        src = self._source
        self._cap = cv2.VideoCapture(src if isinstance(src, int) else str(src))
        if not self._cap.isOpened():
            self.tick.emit({"error": "无法打开视频源"})
            self._run_flag = False
            return

        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, config.VIDEO_CAPTURE_BUFFER_SIZE)

        while self._run_flag and self._cap.isOpened():
            ok, frame = self._cap.read()
            if not ok or frame is None:
                if isinstance(self._source, str):
                    self.tick.emit({"error": "视频结束或读取失败"})
                break

            obstacles: list[dict[str, Any]] = []
            max_ratio = 0.0
            if self._use_yolo and self._detector.loaded:
                frame, obstacles, max_ratio = self._detector.infer(frame)

            advice = compute_safe_speed(
                self._current_speed,
                max_ratio,
                len(obstacles),
                self._weather,
                base_max_kmh=self._base_max_kmh,
                rider_risk=self._rider_risk,
            )

            self.frame_ready.emit(_bgr_to_qimage(frame))
            self.tick.emit(
                {
                    "obstacles": obstacles,
                    "obstacle_count": len(obstacles),
                    "max_area_ratio": max_ratio,
                    "recommended": advice.recommended_speed_kmh,
                    "need_decelerate": advice.need_decelerate,
                    "reason": advice.reason,
                    "w_factor": advice.weather_factor,
                    "o_factor": advice.obstacle_factor,
                    "r_factor": advice.rider_factor,
                    "rider_risk": self._rider_risk,
                }
            )
            time.sleep(self._target_frame_interval)

        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._run_flag = False
