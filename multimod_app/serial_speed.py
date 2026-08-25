"""通过 COM 串口读取物模型 JSON（thing.event.property.post）及兼容旧版纯数字速度。"""
from __future__ import annotations

import json
import logging
import random
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

import serial

from multimod_app import config

ParseMode = Literal["strict", "repaired", "regex", "failed"]
_SENSOR_KEYS = frozenset({"tem", "humi", "light", "human", "speed", "distance", "device_id"})

_logger: logging.Logger | None = None


def setup_serial_logging() -> logging.Logger:
    """初始化串口模块日志（仅控制台）。"""
    global _logger
    logger = logging.getLogger("multimod_app.serial")
    if _logger is not None:
        return _logger
    _logger = logger
    if logger.handlers:
        return logger

    level = getattr(logging, str(config.SERIAL_LOG_LEVEL).upper(), logging.DEBUG)
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    if config.SERIAL_LOG_ENABLED:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        logger.addHandler(ch)
        logger.debug("串口日志已启用（控制台）")
    else:
        logger.addHandler(logging.NullHandler())

    return logger


def _log() -> logging.Logger:
    return setup_serial_logging()


def _snip(text: str, limit: int | None = None) -> str:
    if limit is None:
        limit = config.SERIAL_LOG_RAW_MAX_LEN
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _clamp_speed(v: float) -> float:
    return max(0.0, min(float(config.SERIAL_SPEED_CLAMP_MAX_KMH), float(v)))


def _pick_param(params: dict[str, Any], *candidates: str) -> Any | None:
    """大小写不敏感匹配 params 中的键。"""
    lower_map = {str(k).lower(): v for k, v in params.items()}
    for name in candidates:
        key = name.lower()
        if key in lower_map:
            return lower_map[key]
    return None


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _random_ride_speed_kmh() -> float:
    return round(
        random.uniform(config.SERIAL_RANDOM_SPEED_MIN_KMH, config.SERIAL_RANDOM_SPEED_MAX_KMH),
        1,
    )


def _repair_json_text(text: str) -> str:
    """修复第三方硬件常见 JSON 笔误（不改固件，仅接收端兼容）。"""
    s = text.strip().lstrip("\ufeff").strip("\x00")
    # 去掉 } 之后的垃圾字符
    close = s.rfind("}")
    if close >= 0:
        s = s[: close + 1]
    # 尾逗号、数字/字符串后多余引号
    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*]", "]", s)
    s = re.sub(r'":(-?\d+(?:\.\d+)?)"(\s*[,}])', r'":\1\2', s)
    s = re.sub(r'":((?:\\.|[^"\\])*)""(\s*[,}])', r'":"\1"\2', s)
    return s


def _regex_kv_pairs(text: str) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for m in re.finditer(
        r'"(\w+)"\s*:\s*("(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?|true|false|null)',
        text,
        flags=re.IGNORECASE,
    ):
        key, raw = m.group(1), m.group(2)
        if raw.startswith('"'):
            try:
                params[key] = json.loads(raw)
            except json.JSONDecodeError:
                params[key] = raw.strip('"')
        elif raw in ("true", "false", "null"):
            params[key] = {"true": True, "false": False, "null": None}[raw]
        else:
            try:
                params[key] = int(raw) if "." not in raw else float(raw)
            except ValueError:
                params[key] = raw
    return params


def _loads_tolerant_json(text: str) -> tuple[dict[str, Any] | None, ParseMode]:
    """解析 JSON；兼容 ESP32 等第三方硬件的非标准输出。"""
    raw = text.strip()
    if not raw:
        return None, "failed"

    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj, "strict"
    except json.JSONDecodeError as e:
        _log().debug("严格 JSON 解析失败: %s | raw=%s", e, _snip(raw))

    repaired = _repair_json_text(raw)
    if repaired != raw:
        try:
            obj = json.loads(repaired)
            if isinstance(obj, dict):
                _log().info("修复后 JSON 解析成功 | raw=%s | repaired=%s", _snip(raw), _snip(repaired))
                return obj, "repaired"
        except json.JSONDecodeError as e:
            _log().debug("修复后 JSON 仍失败: %s | repaired=%s", e, _snip(repaired))

    params = _regex_kv_pairs(raw)
    if params and any(str(k).lower() in _SENSOR_KEYS for k in params):
        _log().info("正则兜底解析成功 keys=%s | raw=%s", list(params.keys()), _snip(raw))
        return params, "regex"

    _log().warning("JSON 解析全部失败 | raw=%s", _snip(raw))
    return None, "failed"


def _resolve_params(obj: dict[str, Any]) -> dict[str, Any] | None:
    params = obj.get("params")
    if params is None:
        if any(str(k).lower() in _SENSOR_KEYS for k in obj):
            return obj
        return None
    if isinstance(params, str):
        nested, mode = _loads_tolerant_json(params)
        if nested is not None:
            _log().debug("嵌套 params 解析 mode=%s", mode)
            return nested
        return None
    if isinstance(params, dict):
        return params
    return None


def _try_pop_json_object(buf: str) -> tuple[str | None, str]:
    """
    从缓冲区取出一个完整 JSON 对象（从首字符 { 起括号匹配）。
    返回 (json_text, 剩余缓冲区)。不完整则 (None, buf)。
    """
    i = buf.find("{")
    if i < 0:
        tail = buf[-config.SERIAL_JSON_NO_BRACE_TAIL :] if len(buf) > config.SERIAL_JSON_NO_BRACE_TAIL else buf
        if buf and buf != tail:
            _log().debug("缓冲区无 {，丢弃前缀 len=%d", len(buf) - len(tail))
        return None, tail
    depth = 0
    for j in range(i, len(buf)):
        c = buf[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                chunk = buf[i : j + 1]
                rest = buf[j + 1 :]
                return chunk, rest
    if len(buf) > config.SERIAL_JSON_BUFFER_MAX:
        _log().warning("JSON 缓冲区超限 %d，截断保留尾部", len(buf))
        return None, buf[-config.SERIAL_JSON_TAIL_KEEP :]
    return None, buf


@dataclass
class SerialTelemetry:
    """最近一次串口解析结果（树莓派物模型）。"""

    speed_kmh: float = 0.0
    speed_synthetic: bool = False
    tem_c: float | None = None
    humi_pct: float | None = None
    light: float | None = None
    human: int | None = None
    raw_line: str = ""
    parse_mode: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "speed_kmh": self.speed_kmh,
            "speed_synthetic": self.speed_synthetic,
            "tem_c": self.tem_c,
            "humi_pct": self.humi_pct,
            "light": self.light,
            "human": self.human,
            "parse_mode": self.parse_mode,
        }


class SerialSpeedReader:
    """
    解析：
    1) 物模型 JSON 行：{"method":"thing.event.property.post","params":{"tem":28,"humi":45,"Light":33,"Human":11,...}}
       - tem: 温度(℃)；humi: 湿度；Light: 光照；Human: 人体感应
       - 若无速度字段，则随机生成 14~26 km/h 的骑行合理速度
    2) 兼容旧协议：行内数字作为速度，如 18.5 或 SPEED:20
    3) 兼容第三方硬件非标准 JSON（自动修复 + 正则兜底）
    """

    _num_re = re.compile(r"[-+]?\d*\.?\d+")
    _speed_field_re = re.compile(r'"(?:speed|velocity|vel|kmh|km_h|v)"\s*:\s*(-?\d+(?:\.\d+)?)', re.I)

    def __init__(
        self,
        port: str,
        baudrate: int | None = None,
        on_speed: Callable[[float], None] | None = None,
        on_telemetry: Callable[[SerialTelemetry], None] | None = None,
    ) -> None:
        self.port = port
        self.baudrate = baudrate if baudrate is not None else config.SERIAL_DEFAULT_BAUD
        self._on_speed = on_speed
        self._on_telemetry = on_telemetry
        self._ser: serial.Serial | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_speed = 0.0
        self._telemetry = SerialTelemetry()
        self._rx_bytes = 0
        self._parse_ok = 0
        self._parse_fail = 0

    @property
    def last_speed(self) -> float:
        with self._lock:
            return self._last_speed

    def get_telemetry(self) -> SerialTelemetry:
        with self._lock:
            return SerialTelemetry(
                speed_kmh=self._telemetry.speed_kmh,
                speed_synthetic=self._telemetry.speed_synthetic,
                tem_c=self._telemetry.tem_c,
                humi_pct=self._telemetry.humi_pct,
                light=self._telemetry.light,
                human=self._telemetry.human,
                raw_line=self._telemetry.raw_line,
                parse_mode=self._telemetry.parse_mode,
            )

    def start(self) -> None:
        setup_serial_logging()
        self.stop()
        self._stop.clear()
        self._rx_bytes = 0
        self._parse_ok = 0
        self._parse_fail = 0
        _log().info("正在打开串口 %s @ %d", self.port, self.baudrate)
        self._ser = serial.Serial(self.port, self.baudrate, timeout=config.SERIAL_READ_TIMEOUT_S)
        _log().info("串口已连接 %s | timeout=%.2fs | 日志见控制台", self.port, config.SERIAL_READ_TIMEOUT_S)
        self._thread = threading.Thread(target=self._loop, name="SerialSpeedReader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._ser is not None or self._thread is not None:
            _log().info(
                "断开串口 %s | rx_bytes=%d parse_ok=%d parse_fail=%d",
                self.port,
                self._rx_bytes,
                self._parse_ok,
                self._parse_fail,
            )
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=config.SERIAL_THREAD_JOIN_S)
            self._thread = None
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception as e:
                _log().warning("关闭串口异常: %s", e)
            self._ser = None

    def _publish_telemetry(self, tel: SerialTelemetry) -> None:
        with self._lock:
            self._last_speed = tel.speed_kmh
            self._telemetry = tel
        if self._on_speed:
            self._on_speed(tel.speed_kmh)
        if self._on_telemetry:
            self._on_telemetry(tel)

    def _telemetry_from_params(self, params: dict[str, Any], raw: str, mode: ParseMode) -> SerialTelemetry:
        tem = _to_float(_pick_param(params, "tem", "Tem", "TEMP", "temperature"))
        humi = _to_float(_pick_param(params, "humi", "Humi", "humidity", "rh"))
        light = _to_float(_pick_param(params, "light", "Light", "lux", "illuminance"))
        human = _to_int(_pick_param(params, "human", "Human", "pir", "body"))

        speed_raw = _pick_param(
            params,
            "speed",
            "Speed",
            "vel",
            "velocity",
            "kmh",
            "km_h",
            "v",
        )
        speed_val = _to_float(speed_raw)
        if speed_val is not None:
            spd = _clamp_speed(speed_val)
            synthetic = False
        else:
            m = self._speed_field_re.search(raw)
            if m:
                spd = _clamp_speed(float(m.group(1)))
                synthetic = False
                _log().debug("从原始文本兜底提取速度 %.1f", spd)
            else:
                spd = _clamp_speed(_random_ride_speed_kmh())
                synthetic = True
                _log().debug("无速度字段，使用随机速度 %.1f", spd)

        return SerialTelemetry(
            speed_kmh=spd,
            speed_synthetic=synthetic,
            tem_c=tem,
            humi_pct=humi,
            light=light,
            human=human,
            raw_line=raw[: config.SERIAL_RAW_SNIP_LEN],
            parse_mode=mode,
        )

    def _apply_thing_json(self, text: str) -> bool:
        obj, mode = _loads_tolerant_json(text)
        if obj is None:
            m = self._speed_field_re.search(text)
            if m:
                spd = _clamp_speed(float(m.group(1)))
                tel = SerialTelemetry(
                    speed_kmh=spd,
                    speed_synthetic=False,
                    raw_line=text[: config.SERIAL_RAW_SNIP_LEN],
                    parse_mode="regex_speed_only",
                )
                self._parse_ok += 1
                _log().info("仅速度字段兜底成功 speed=%.1f | raw=%s", spd, _snip(text))
                self._publish_telemetry(tel)
                return True
            self._parse_fail += 1
            return False

        params = _resolve_params(obj)
        if params is None:
            self._parse_fail += 1
            _log().warning("JSON 已解析但无可用传感器字段 | keys=%s | raw=%s", list(obj.keys()), _snip(text))
            return False

        tel = self._telemetry_from_params(params, text, mode)
        self._parse_ok += 1
        _log().info(
            "遥测更新 mode=%s speed=%.1f%s tem=%s light=%s | raw=%s",
            mode,
            tel.speed_kmh,
            "(随机)" if tel.speed_synthetic else "",
            tel.tem_c,
            tel.light,
            _snip(text),
        )
        self._publish_telemetry(tel)
        return True

    def _apply_legacy_number_line(self, line: str) -> bool:
        m = self._num_re.search(line)
        if not m:
            _log().debug("非 JSON 行无法提取数字 | line=%s", _snip(line))
            return False
        try:
            v = float(m.group())
        except ValueError:
            return False
        v = _clamp_speed(v)
        tel = SerialTelemetry(
            speed_kmh=v,
            speed_synthetic=False,
            raw_line=line[: config.SERIAL_LEGACY_RAW_SNIP_LEN],
            parse_mode="legacy_number",
        )
        self._parse_ok += 1
        _log().info("旧版数字行解析 speed=%.1f | line=%s", v, _snip(line))
        self._publish_telemetry(tel)
        return True

    def _consume_json_buffer(self, buf: str) -> str:
        while True:
            obj, rest = _try_pop_json_object(buf)
            if obj is None:
                return rest
            self._apply_thing_json(obj)
            buf = rest

    def _loop(self) -> None:
        buf = ""
        _log().debug("串口读取线程已启动")
        while not self._stop.is_set() and self._ser:
            try:
                chunk = self._ser.read(config.SERIAL_READ_CHUNK_BYTES)
                if not chunk:
                    time.sleep(config.SERIAL_IDLE_SLEEP_S)
                    continue
                self._rx_bytes += len(chunk)
                decoded = chunk.decode("utf-8", errors="ignore")
                _log().debug("收到 %d 字节 | snippet=%s", len(chunk), _snip(decoded, 120))
                buf += decoded
            except Exception as e:
                _log().error("串口读取异常: %s", e, exc_info=True)
                time.sleep(config.SERIAL_ERROR_SLEEP_S)
                continue

            buf = self._consume_json_buffer(buf)

            while "\n" in buf or "\r" in buf:
                line, sep, rest = buf.partition("\n")
                if sep == "":
                    line, sep, rest = buf.partition("\r")
                buf = rest
                line = line.strip()
                if not line:
                    continue
                if line.startswith("{"):
                    buf = line + buf
                    buf = self._consume_json_buffer(buf)
                    continue
                self._apply_legacy_number_line(line)

            if len(buf) > config.SERIAL_JSON_BUFFER_MAX:
                _log().warning("行缓冲区超限 %d，截断", len(buf))
                buf = buf[-config.SERIAL_JSON_TAIL_KEEP :]

        _log().debug("串口读取线程已退出")
