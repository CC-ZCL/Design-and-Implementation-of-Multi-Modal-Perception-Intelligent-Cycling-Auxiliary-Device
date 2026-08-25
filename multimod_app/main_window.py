"""PyQt5 主界面：左侧视频预览，右侧控制与多模态信息。"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

from multimod_app import config
from multimod_app.qwen_rider import analyze_rider_jpeg
from multimod_app.serial_speed import SerialSpeedReader, setup_serial_logging
from multimod_app.video_worker import VideoWorker
from multimod_app.weather_service import fetch_weather_by_city
from multimod_app.yolo_detector import default_model_path


def _app_stylesheet() -> str:
    return """
    QWidget { background-color: #1a1d23; color: #e8eaed; font-size: 13px; }
    QGroupBox {
        border: 1px solid #2d3340;
        border-radius: 8px;
        margin-top: 10px;
        padding-top: 8px;
        font-weight: bold;
    }
    QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #7cb7ff; }
    QPushButton {
        background-color: #2a6fdb;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 6px 12px;
        min-height: 30px;
        min-width: 96px;
    }
    QPushButton:hover { background-color: #3b7fe8; }
    QPushButton:pressed { background-color: #1f5bb5; }
    QPushButton#stopBtn { background-color: #c44c4c; min-width: 96px; }
    QPushButton#stopBtn:hover { background-color: #d65a5a; }
    QPushButton#wideBtn { min-width: 140px; }
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
        background-color: #242830;
        border: 1px solid #3a4252;
        border-radius: 6px;
        padding: 6px 8px;
        min-height: 28px;
        selection-background-color: #2a6fdb;
    }
    QComboBox::drop-down { border: none; width: 28px; }
    QLabel#videoLabel {
        background-color: #0f1115;
        border: 1px solid #2d3340;
        border-radius: 10px;
    }
    QLabel#alertLabel { color: #ffb74d; font-weight: bold; font-size: 14px; }
    QLabel#okLabel { color: #81c784; font-weight: bold; }
    QCheckBox { spacing: 8px; }
    QScrollArea { border: none; background: transparent; }
    """


class QwenCallThread(QtCore.QThread):
    """后台调用千问 VL，避免阻塞 UI。"""

    done = QtCore.pyqtSignal(dict)

    def __init__(self, jpeg_bytes: bytes, api_key: str, model: str, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._jpeg = jpeg_bytes
        self._key = api_key
        self._model = model

    def run(self) -> None:
        self.done.emit(analyze_rider_jpeg(self._jpeg, self._key, self._model))


def _qimage_to_jpeg(img: QtGui.QImage, max_side: int | None = None) -> bytes:
    if max_side is None:
        max_side = config.UI_JPEG_MAX_SIDE
    if img.isNull():
        return b""
    im = img
    if max(img.width(), img.height()) > max_side:
        im = img.scaled(
            max_side,
            max_side,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
    ba = QtCore.QByteArray()
    buffer = QtCore.QBuffer(ba)
    buffer.open(QtCore.QIODevice.WriteOnly)
    im.save(buffer, "JPEG", config.UI_JPEG_QUALITY)
    return bytes(ba)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("路况 / 多模态安全骑行监测")
        self.resize(config.UI_WINDOW_WIDTH, config.UI_WINDOW_HEIGHT)
        self.setMinimumSize(config.UI_WINDOW_MIN_WIDTH, config.UI_WINDOW_MIN_HEIGHT)
        self.setStyleSheet(_app_stylesheet())

        self._worker: VideoWorker | None = None
        self._serial: SerialSpeedReader | None = None
        self._sim_timer: QtCore.QTimer | None = None
        self._sim_phase = 0.0
        self._sim_speed_live = 0.0
        self._weather = None
        self._last_geo = None
        self._last_frame: QtGui.QImage | None = None
        self._qwen_thread: QwenCallThread | None = None
        self._qwen_busy = False
        self._qwen_timer: QtCore.QTimer | None = None
        self._last_rider_risk = 0.0
        self._serial_timer: QtCore.QTimer | None = None

        self._build_ui()
        self._refresh_ports()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)
        root.setSpacing(16)
        root.setContentsMargins(16, 16, 16, 16)

        # 左侧视频
        left = QtWidgets.QVBoxLayout()
        self.video_label = QtWidgets.QLabel("预览区：开始后可显示摄像头或视频")
        self.video_label.setObjectName("videoLabel")
        self.video_label.setAlignment(QtCore.Qt.AlignCenter)
        self.video_label.setMinimumSize(config.UI_VIDEO_LABEL_MIN_WIDTH, config.UI_VIDEO_LABEL_MIN_HEIGHT)
        self.video_label.setScaledContents(False)
        left.addWidget(self.video_label, 1)
        root.addLayout(left, config.UI_LAYOUT_ROOT_STRETCH_VIDEO)

        # 右侧滚动控制
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        right_inner = QtWidgets.QWidget()
        right_inner.setMinimumWidth(config.UI_RIGHT_PANEL_MIN_WIDTH)
        right = QtWidgets.QVBoxLayout(right_inner)
        right.setSpacing(10)

        g_src = QtWidgets.QGroupBox("视频源")
        gl = QtWidgets.QFormLayout(g_src)
        gl.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        gl.setHorizontalSpacing(12)
        gl.setVerticalSpacing(8)
        self.radio_cam = QtWidgets.QRadioButton("摄像头")
        self.radio_file = QtWidgets.QRadioButton("视频文件")
        self.radio_cam.setChecked(True)
        self.cam_index = QtWidgets.QSpinBox()
        self.cam_index.setRange(0, 8)
        self.cam_index.setValue(0)
        self.file_path = QtWidgets.QLineEdit()
        self.btn_browse = QtWidgets.QPushButton("浏览…")
        self.btn_browse.setMinimumWidth(100)
        self.btn_browse.clicked.connect(self._browse_video)
        row_file = QtWidgets.QHBoxLayout()
        row_file.addWidget(self.file_path, 1)
        row_file.addWidget(self.btn_browse)
        gl.addRow(self.radio_cam, self.cam_index)
        gl.addRow(self.radio_file, row_file)

        g_ai = QtWidgets.QGroupBox("路况检测 (YOLOv8n)")
        gf = QtWidgets.QFormLayout(g_ai)
        gf.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        gf.setHorizontalSpacing(12)
        self.chk_yolo = QtWidgets.QCheckBox("启用障碍物检测")
        self.chk_yolo.setChecked(True)
        self.lbl_model = QtWidgets.QLabel(default_model_path())
        self.lbl_model.setWordWrap(True)
        gf.addRow(self.chk_yolo)
        gf.addRow("模型路径", self.lbl_model)

        g_com = QtWidgets.QGroupBox("串口传感器 (COM / 物模型 JSON)")
        gc = QtWidgets.QFormLayout(g_com)
        gc.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        gc.setHorizontalSpacing(12)
        self.combo_port = QtWidgets.QComboBox()
        self.combo_port.setMinimumWidth(config.UI_COMBO_PORT_MIN_WIDTH)
        self.btn_refresh = QtWidgets.QPushButton("刷新端口")
        self.btn_refresh.setMinimumWidth(108)
        self.btn_refresh.clicked.connect(self._refresh_ports)
        row_p = QtWidgets.QHBoxLayout()
        row_p.addWidget(self.combo_port, 1)
        row_p.addWidget(self.btn_refresh)
        self.baud = QtWidgets.QSpinBox()
        self.baud.setRange(9600, 921600)
        self.baud.setValue(config.SERIAL_DEFAULT_BAUD)
        self.btn_serial = QtWidgets.QPushButton("连接串口")
        self.btn_serial.setMinimumWidth(config.UI_BTN_SERIAL_MIN_WIDTH)
        self.btn_serial.setCheckable(True)
        self.btn_serial.toggled.connect(self._toggle_serial)
        self.chk_sim = QtWidgets.QCheckBox("模拟速度（无硬件时）")
        self.sim_speed = QtWidgets.QDoubleSpinBox()
        self.sim_speed.setRange(0.0, 60.0)
        self.sim_speed.setValue(config.UI_SIM_SPEED_DEFAULT)
        self.sim_speed.setSuffix(" km/h")
        hint_com = QtWidgets.QLabel(
            "支持阿里云物模型上报 JSON（含 params.tem / humi / Light / Human）；"
            "无速度字段时自动生成 14~26 km/h。也兼容单行数字速度。"
        )
        hint_com.setWordWrap(True)
        hint_com.setStyleSheet("color:#9aa0a6;font-size:12px;")
        gc.addRow("端口", row_p)
        gc.addRow("波特率", self.baud)
        gc.addRow(self.btn_serial)
        gc.addRow(hint_com)
        self.lbl_s_tem = QtWidgets.QLabel("—")
        self.lbl_s_humi = QtWidgets.QLabel("—")
        self.lbl_s_light = QtWidgets.QLabel("—")
        self.lbl_s_human = QtWidgets.QLabel("—")
        self.lbl_s_speed = QtWidgets.QLabel("—")
        gc.addRow("温度 ℃", self.lbl_s_tem)
        gc.addRow("湿度 %", self.lbl_s_humi)
        gc.addRow("光照", self.lbl_s_light)
        gc.addRow("人体感应", self.lbl_s_human)
        gc.addRow("速度(串口)", self.lbl_s_speed)
        gc.addRow(self.chk_sim, self.sim_speed)

        g_w = QtWidgets.QGroupBox("天气（按城市 · Open-Meteo）")
        gw = QtWidgets.QFormLayout(g_w)
        gw.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        gw.setHorizontalSpacing(12)
        self.ed_city = QtWidgets.QLineEdit()
        self.ed_city.setPlaceholderText("例如：北京、上海、深圳市")
        self.ed_city.setText(config.UI_DEFAULT_CITY)
        self.btn_weather = QtWidgets.QPushButton("按城市获取天气")
        self.btn_weather.setObjectName("wideBtn")
        self.btn_weather.clicked.connect(self._pull_weather)
        self.lbl_geo = QtWidgets.QLabel("")
        self.lbl_geo.setStyleSheet("color:#9aa0a6;font-size:12px;")
        self.lbl_weather = QtWidgets.QLabel("未更新")
        self.lbl_weather.setWordWrap(True)
        gw.addRow("城市", self.ed_city)
        gw.addRow(self.btn_weather)
        gw.addRow("坐标", self.lbl_geo)
        gw.addRow("实况", self.lbl_weather)

        g_qwen = QtWidgets.QGroupBox("骑手状态（通义千问 VL · DashScope）")
        gq = QtWidgets.QFormLayout(g_qwen)
        gq.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        gq.setHorizontalSpacing(12)
        self.chk_qwen = QtWidgets.QCheckBox("启用大模型分析视频帧")
        self.chk_qwen.toggled.connect(self._on_qwen_toggle)
        self.combo_qwen_model = QtWidgets.QComboBox()
        self.combo_qwen_model.addItems(list(config.QWEN_UI_MODEL_OPTIONS))
        self.spn_qwen_sec = QtWidgets.QSpinBox()
        self.spn_qwen_sec.setRange(config.UI_QWEN_POLL_SEC_MIN, config.UI_QWEN_POLL_SEC_MAX)
        self.spn_qwen_sec.setValue(config.UI_QWEN_POLL_SEC_DEFAULT)
        self.spn_qwen_sec.setSuffix(" 秒/次")
        hint_q = QtWidgets.QLabel(
            "按间隔截取当前画面调用千问多模态；API Key 从 multimod_app/config.py 的 "
            "DASHSCOPE_API_KEY_ENV 读取。risk 会参与下方安全车速融合。"
        )
        hint_q.setWordWrap(True)
        hint_q.setStyleSheet("color:#9aa0a6;font-size:12px;")
        self.lbl_qwen_status = QtWidgets.QLabel("千问: 未启用")
        self.lbl_qwen_detail = QtWidgets.QLabel("")
        self.lbl_qwen_detail.setWordWrap(True)
        gq.addRow(self.chk_qwen)
        gq.addRow("模型", self.combo_qwen_model)
        gq.addRow("调用间隔", self.spn_qwen_sec)
        gq.addRow(hint_q)
        gq.addRow("状态", self.lbl_qwen_status)
        gq.addRow("详情", self.lbl_qwen_detail)
        self.spn_qwen_sec.valueChanged.connect(self._sync_qwen_timer_interval)

        g_safe = QtWidgets.QGroupBox("安全策略")
        gs = QtWidgets.QFormLayout(g_safe)
        gs.setHorizontalSpacing(12)
        self.base_max = QtWidgets.QDoubleSpinBox()
        self.base_max.setRange(10.0, 50.0)
        self.base_max.setValue(config.UI_BASE_MAX_SPEED_DEFAULT)
        self.base_max.setSuffix(" km/h")
        self.base_max.setToolTip("良好天气、无障碍时的参考最高建议速度上限")
        gs.addRow("场景基准上限", self.base_max)

        g_run = QtWidgets.QGroupBox("运行")
        gr = QtWidgets.QVBoxLayout(g_run)
        row_run = QtWidgets.QHBoxLayout()
        self.btn_start = QtWidgets.QPushButton("开始监测")
        self.btn_start.setMinimumWidth(config.UI_BTN_START_STOP_MIN_WIDTH)
        self.btn_start.clicked.connect(self._start)
        self.btn_stop = QtWidgets.QPushButton("停止")
        self.btn_stop.setObjectName("stopBtn")
        self.btn_stop.setMinimumWidth(config.UI_BTN_START_STOP_MIN_WIDTH)
        self.btn_stop.clicked.connect(self._stop)
        row_run.addWidget(self.btn_start)
        row_run.addWidget(self.btn_stop)
        gr.addLayout(row_run)

        g_stat = QtWidgets.QGroupBox("实时融合结果")
        gst = QtWidgets.QFormLayout(g_stat)
        gst.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        gst.setHorizontalSpacing(12)
        self.lbl_speed = QtWidgets.QLabel("0 km/h")
        self.lbl_rec = QtWidgets.QLabel("-")
        self.lbl_alert = QtWidgets.QLabel("—")
        self.lbl_alert.setObjectName("okLabel")
        self.lbl_obs = QtWidgets.QLabel("0")
        self.lbl_factors = QtWidgets.QLabel("-")
        self.lbl_reason = QtWidgets.QLabel("")
        self.lbl_reason.setWordWrap(True)
        gst.addRow("当前速度", self.lbl_speed)
        gst.addRow("建议安全速度", self.lbl_rec)
        gst.addRow("减速提示", self.lbl_alert)
        gst.addRow("前方障碍数", self.lbl_obs)
        gst.addRow("融合因子(天/障/骑)", self.lbl_factors)
        gst.addRow("说明", self.lbl_reason)

        right.addWidget(g_src)
        right.addWidget(g_ai)
        right.addWidget(g_com)
        right.addWidget(g_w)
        right.addWidget(g_qwen)
        right.addWidget(g_safe)
        right.addWidget(g_run)
        right.addWidget(g_stat)
        right.addStretch(1)

        scroll.setWidget(right_inner)
        root.addWidget(scroll, config.UI_LAYOUT_ROOT_STRETCH_PANEL)

        self.statusBar().showMessage(
            "就绪 — 千问 API Key 读取自 multimod_app/config.py；天气按城市名自动地理编码"
        )

    def _browse_video(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择视频", str(Path.home()), "视频 (*.mp4 *.avi *.mkv *.mov);;所有 (*.*)"
        )
        if path:
            self.file_path.setText(path)
            self.radio_file.setChecked(True)

    def _refresh_ports(self) -> None:
        self.combo_port.clear()
        try:
            from serial.tools import list_ports

            for p in list_ports.comports():
                self.combo_port.addItem(f"{p.device} — {p.description}", p.device)
        except Exception:
            self.combo_port.addItem("COM1", "COM1")
        if self.combo_port.count() == 0:
            self.combo_port.addItem("(无端口)", "")

    def _stop_serial_timer(self) -> None:
        if self._serial_timer:
            self._serial_timer.stop()
            self._serial_timer.deleteLater()
            self._serial_timer = None

    def _refresh_serial_labels(self) -> None:
        if not self._serial:
            return
        t = self._serial.get_telemetry()
        if not t.raw_line:
            return
        self.lbl_s_tem.setText(f"{t.tem_c:.1f}" if t.tem_c is not None else "—")
        self.lbl_s_humi.setText(f"{t.humi_pct:.0f}" if t.humi_pct is not None else "—")
        self.lbl_s_light.setText(f"{t.light:.0f}" if t.light is not None else "—")
        self.lbl_s_human.setText(str(t.human) if t.human is not None else "—")
        sp = f"{t.speed_kmh:.1f} km/h"
        if t.speed_synthetic:
            sp += "（随机）"
        self.lbl_s_speed.setText(sp)

    def _toggle_serial(self, on: bool) -> None:
        self._stop_serial_timer()
        if self._serial:
            self._serial.stop()
            self._serial = None
        if not on:
            self.btn_serial.setText("连接串口")
            self._reset_serial_sensor_labels()
            return
        dev = self.combo_port.currentData()
        if not dev:
            QtWidgets.QMessageBox.warning(self, "串口", "请选择有效串口")
            self.btn_serial.setChecked(False)
            return
        try:
            setup_serial_logging()
            self._serial = SerialSpeedReader(str(dev), baudrate=int(self.baud.value()))
            self._serial.start()
            self._serial_timer = QtCore.QTimer(self)
            self._serial_timer.timeout.connect(self._refresh_serial_labels)
            self._serial_timer.start(config.SERIAL_UI_POLL_MS)
            self.btn_serial.setText("断开串口")
            self.statusBar().showMessage("串口已连接，调试日志见运行终端控制台", 8000)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "串口", str(e))
            self.btn_serial.setChecked(False)
            self.btn_serial.setText("连接串口")

    def _reset_serial_sensor_labels(self) -> None:
        self.lbl_s_tem.setText("—")
        self.lbl_s_humi.setText("—")
        self.lbl_s_light.setText("—")
        self.lbl_s_human.setText("—")
        self.lbl_s_speed.setText("—")

    def _sync_qwen_timer_interval(self) -> None:
        if self._qwen_timer and self._qwen_timer.isActive():
            self._qwen_timer.setInterval(
                max(config.UI_QWEN_POLL_SEC_MIN, int(self.spn_qwen_sec.value())) * 1000
            )

    def _on_qwen_toggle(self, checked: bool) -> None:
        if self._worker and self._worker.isRunning():
            if checked:
                self._worker.set_rider_risk(self._last_rider_risk)
                self._start_qwen_timer()
            else:
                self._stop_qwen_timer()
                self._worker.set_rider_risk(0.0)
        elif self._worker:
            self._worker.set_rider_risk(self._last_rider_risk if checked else 0.0)
        if not checked:
            self._last_rider_risk = 0.0
            if not (self._worker and self._worker.isRunning()):
                self.lbl_qwen_status.setText("千问: 未启用")
                self.lbl_qwen_detail.clear()

    def _start_qwen_timer(self) -> None:
        self._stop_qwen_timer()
        if not self.chk_qwen.isChecked():
            return
        self._qwen_timer = QtCore.QTimer(self)
        self._qwen_timer.timeout.connect(self._qwen_tick)
        self._qwen_timer.start(max(config.UI_QWEN_POLL_SEC_MIN, int(self.spn_qwen_sec.value())) * 1000)

    def _stop_qwen_timer(self) -> None:
        if self._qwen_timer:
            self._qwen_timer.stop()
            self._qwen_timer.deleteLater()
            self._qwen_timer = None

    def _qwen_tick(self) -> None:
        if self._qwen_busy or not self._worker or not self._worker.isRunning():
            return
        if not self.chk_qwen.isChecked():
            return
        key = (config.DASHSCOPE_API_KEY_ENV or "").strip()
        if not key:
            self.lbl_qwen_status.setText("千问: 请在 config.py 中填写 DASHSCOPE_API_KEY_ENV")
            return
        if self._last_frame is None or self._last_frame.isNull():
            return
        jpeg = _qimage_to_jpeg(self._last_frame)
        if len(jpeg) < 32:
            return
        if self._qwen_thread and self._qwen_thread.isRunning():
            return
        self._qwen_busy = True
        self.lbl_qwen_status.setText("千问: 请求中…")
        model = self.combo_qwen_model.currentText()
        self._qwen_thread = QwenCallThread(jpeg, key, model, self)
        self._qwen_thread.done.connect(self._on_qwen_done)
        self._qwen_thread.start()

    @QtCore.pyqtSlot(dict)
    def _on_qwen_done(self, d: dict) -> None:
        self._qwen_busy = False
        self._qwen_thread = None
        if not d.get("ok"):
            err = d.get("error") or "未知错误"
            self.lbl_qwen_status.setText("千问: 失败")
            self.lbl_qwen_detail.setText(str(err)[: config.QWEN_ERROR_UI_MAX_LEN])
            return
        risk = float(d.get("risk", 0) or 0)
        self._last_rider_risk = risk
        if self._worker and self.chk_qwen.isChecked():
            self._worker.set_rider_risk(risk)
        state = str(d.get("state", "") or "")
        hel = d.get("helmet")
        if hel is True:
            hel_s = "佩戴"
        elif hel is False:
            hel_s = "未佩戴/不明"
        else:
            hel_s = "未知"
        alert = str(d.get("alert", "") or "")
        self.lbl_qwen_status.setText(f"千问: risk={risk:.2f}  头盔:{hel_s}")
        self.lbl_qwen_detail.setText(f"{state}  {alert}".strip()[: config.QWEN_DETAIL_UI_MAX_LEN])

    def _pull_weather(self) -> None:
        city = self.ed_city.text().strip()
        if not city:
            QtWidgets.QMessageBox.warning(self, "天气", "请输入城市名称")
            return
        self.lbl_weather.setText("请求中…")
        self.lbl_geo.setText("")
        QtWidgets.QApplication.processEvents()
        w, geo, msg = fetch_weather_by_city(city)
        self._weather = w
        self._last_geo = geo
        if w and geo:
            self.lbl_geo.setText(f"{geo.latitude:.4f}°N, {geo.longitude:.4f}°E")
            self.lbl_weather.setText(
                f"{msg}\n{w.description}  {w.temperature_c:.1f}℃  风速 {w.wind_speed_kmh:.1f} km/h"
            )
        elif geo:
            self.lbl_geo.setText(f"{geo.latitude:.4f}°N, {geo.longitude:.4f}°E")
            self.lbl_weather.setText(msg)
        else:
            self.lbl_weather.setText(msg)

    def _current_speed_value(self) -> float:
        if self.chk_sim.isChecked():
            return float(self._sim_speed_live)
        if self._serial:
            return self._serial.last_speed
        return 0.0

    def _start(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        if self.radio_cam.isChecked():
            src = int(self.cam_index.value())
        else:
            p = self.file_path.text().strip()
            if not p:
                QtWidgets.QMessageBox.warning(self, "视频", "请选择视频文件")
                return
            src = p

        self._worker = VideoWorker(self)
        self._worker.set_source(src)
        self._worker.set_yolo(self.chk_yolo.isChecked())
        self._worker.set_base_max_kmh(float(self.base_max.value()))
        self._worker.set_weather(self._weather)
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.tick.connect(self._on_tick)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

        if self.chk_qwen.isChecked():
            self._worker.set_rider_risk(self._last_rider_risk)
            self._start_qwen_timer()
        else:
            self._worker.set_rider_risk(0.0)

        if self.chk_sim.isChecked():
            self._sim_speed_live = float(self.sim_speed.value())
            self._sim_timer = QtCore.QTimer(self)
            self._sim_timer.timeout.connect(self._sim_tick)
            self._sim_timer.start(config.UI_SIM_TIMER_MS)

        self.btn_start.setEnabled(False)

    def _sim_tick(self) -> None:
        if not self._worker or not self._worker.isRunning():
            return
        self._sim_phase += 0.08
        base = float(self.sim_speed.value())
        wobble = config.UI_SIM_WOBBLE_AMPLITUDE * (0.5 + 0.5 * math.sin(self._sim_phase))
        self._sim_speed_live = max(0.0, base + wobble - 1.0)
        self._worker.set_current_speed(self._sim_speed_live)

    def _stop(self) -> None:
        self._stop_qwen_timer()
        self._qwen_busy = False
        if self._sim_timer:
            self._sim_timer.stop()
            self._sim_timer = None
        if self._worker:
            self._worker.stop_capture()
            if self._worker.isRunning():
                self._worker.wait(config.VIDEO_WORKER_JOIN_MS)
            self._worker = None
        self.btn_start.setEnabled(True)

    def _on_worker_finished(self) -> None:
        self._stop_qwen_timer()
        self.btn_start.setEnabled(True)

    @QtCore.pyqtSlot(QtGui.QImage)
    def _on_frame(self, img: QtGui.QImage) -> None:
        self._last_frame = img.copy()
        pix = QtGui.QPixmap.fromImage(img)
        self.video_label.setPixmap(
            pix.scaled(self.video_label.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        )

    @QtCore.pyqtSlot(dict)
    def _on_tick(self, d: dict) -> None:
        if "error" in d:
            self.statusBar().showMessage(d["error"], config.UI_STATUS_ERROR_MS)
            return
        spd = self._current_speed_value()
        if self._worker:
            self._worker.set_current_speed(spd)
        self.lbl_speed.setText(f"{spd:.1f} km/h")
        self.lbl_rec.setText(f"{d.get('recommended', 0):.1f} km/h")
        if d.get("need_decelerate"):
            self.lbl_alert.setText("建议减速")
            self.lbl_alert.setObjectName("alertLabel")
        else:
            self.lbl_alert.setText("速度合理")
            self.lbl_alert.setObjectName("okLabel")
        self.lbl_alert.style().unpolish(self.lbl_alert)
        self.lbl_alert.style().polish(self.lbl_alert)
        self.lbl_obs.setText(str(d.get("obstacle_count", 0)))
        self.lbl_factors.setText(
            f"天气 {d.get('w_factor', 1):.2f}  |  障碍 {d.get('o_factor', 1):.2f}  |  骑手 {d.get('r_factor', 1):.2f}"
        )
        self.lbl_reason.setText(str(d.get("reason", "")))
        self._refresh_serial_labels()

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:
        super().resizeEvent(e)
        pm = self.video_label.pixmap()
        if pm is not None and not pm.isNull():
            self.video_label.setPixmap(
                pm.scaled(
                    self.video_label.size(),
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation,
                )
            )

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        self._stop()
        self._stop_serial_timer()
        if self.btn_serial.isChecked():
            self.btn_serial.setChecked(False)
        super().closeEvent(e)


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    app.setFont(QtGui.QFont("Microsoft YaHei UI", 10))
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())
