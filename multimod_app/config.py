"""
集中配置：修改本文件即可调整模型、接口、串口、UI 等参数，无需在业务代码中散落魔数。
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent

# ---------------------------------------------------------------------------
# YOLO
# ---------------------------------------------------------------------------
YOLO_MODEL_FILENAME = "yolov8n.pt"
YOLO_CONF = 0.35
YOLO_IOU = 0.45
YOLO_IMGSZ = 640
# COCO 中骑行场景需留意的类别：person bicycle car motorcycle bus truck dog cat
YOLO_OBSTACLE_CLASS_IDS = frozenset({0, 1, 2, 3, 5, 7, 16, 17, 18})
YOLO_DEVICE_ENV = "MULTICHECK_YOLO_DEVICE"

# ---------------------------------------------------------------------------
# 视频采集线程
# ---------------------------------------------------------------------------
VIDEO_TARGET_FPS = 20.0
VIDEO_CAPTURE_BUFFER_SIZE = 1
VIDEO_WORKER_JOIN_MS = 3000

# ---------------------------------------------------------------------------
# 安全融合（safety_engine）
# ---------------------------------------------------------------------------
SAFETY_BASE_MAX_KMH_DEFAULT = 25.0
SAFETY_MIN_RECOMMENDED_KMH = 5.0
SAFETY_SPEED_OVER_MARGIN = 1.12
# 天气因子
SAFETY_WEATHER_PRECIPITATION_MULT = 0.75
SAFETY_WEATHER_WIND_HIGH_KMH = 30.0
SAFETY_WEATHER_WIND_HIGH_MULT = 0.85
SAFETY_WEATHER_WIND_MID_KMH = 20.0
SAFETY_WEATHER_WIND_MID_MULT = 0.92
SAFETY_WEATHER_FOG_CODES = frozenset({45, 48})
SAFETY_WEATHER_FOG_MULT = 0.7
# 障碍因子
SAFETY_OBSTACLE_AREA_CAP = 0.45
SAFETY_OBSTACLE_AREA_COEF = 1.2
SAFETY_OBSTACLE_COUNT_CAP = 0.2
SAFETY_OBSTACLE_COUNT_STEP = 0.04
SAFETY_OBSTACLE_FACTOR_MIN = 0.35
# 骑手（千问 risk）
SAFETY_RIDER_RISK_CAP = 0.4
SAFETY_RIDER_RISK_COEF = 0.45
SAFETY_RIDER_FACTOR_MIN = 0.55
SAFETY_RIDER_REASON_THRESHOLD = 0.05

# ---------------------------------------------------------------------------
# 串口 / 物模型 JSON
# ---------------------------------------------------------------------------
SERIAL_DEFAULT_BAUD = 115200
SERIAL_READ_CHUNK_BYTES = 512
SERIAL_READ_TIMEOUT_S = 0.2
SERIAL_THREAD_JOIN_S = 2.0
SERIAL_IDLE_SLEEP_S = 0.01
SERIAL_ERROR_SLEEP_S = 0.05
SERIAL_SPEED_CLAMP_MAX_KMH = 120.0
SERIAL_JSON_BUFFER_MAX = 32000
SERIAL_JSON_TAIL_KEEP = 16000
SERIAL_JSON_NO_BRACE_TAIL = 8000
SERIAL_RANDOM_SPEED_MIN_KMH = 14.0
SERIAL_RANDOM_SPEED_MAX_KMH = 26.0
SERIAL_RAW_SNIP_LEN = 500
SERIAL_LEGACY_RAW_SNIP_LEN = 200
SERIAL_UI_POLL_MS = 200
# 串口调试日志（仅输出到控制台，需在终端运行 python main.py）
SERIAL_LOG_ENABLED = True
SERIAL_LOG_LEVEL = "DEBUG"
SERIAL_LOG_RAW_MAX_LEN = 400

# ---------------------------------------------------------------------------
# 天气（Open-Meteo）
# ---------------------------------------------------------------------------
WEATHER_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_GEOCODE_COUNT = 8
WEATHER_GEOCODE_LANGUAGE = "zh"
WEATHER_REQUEST_TIMEOUT_S = 8.0
WEATHER_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_FORECAST_CURRENT_FIELDS = "temperature_2m,weather_code,wind_speed_10m"
WEATHER_WIND_SPEED_UNIT = "kmh"
WEATHER_TIMEZONE = "auto"
# WMO 降水相关 code（与 weather_service 判定一致）
WEATHER_PRECIPITATION_CODES = frozenset(
    {
        51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99
    }
)

# ---------------------------------------------------------------------------
# 通义千问 / DashScope
# ---------------------------------------------------------------------------
# 此处填写 DashScope API Key（sk- 开头）。名称沿用 DASHSCOPE_API_KEY_ENV，值为密钥本身；
# 界面已取消输入框，仅从此处读取。请勿将含真实 Key 的 config 提交到公开仓库。
DASHSCOPE_API_KEY_ENV = "sk-60e5c87048c9473bb08b88a6f059227d"
DASHSCOPE_COMPAT_CHAT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
QWEN_DEFAULT_MODEL = "qwen3-vl-plus"
QWEN_UI_MODEL_OPTIONS = ("qwen3-vl-plus", "qwen3-vl-max")
QWEN_REQUEST_TIMEOUT_S = 45.0
QWEN_MAX_TOKENS = 256
QWEN_TEMPERATURE = 0.2
QWEN_RESPONSE_RAW_MAX_LEN = 4000
QWEN_ERROR_MSG_MAX_LEN = 500
QWEN_STATE_MAX_LEN = 80
QWEN_ALERT_MAX_LEN = 120
QWEN_DETAIL_UI_MAX_LEN = 500
QWEN_ERROR_UI_MAX_LEN = 400
QWEN_PARSE_FAIL_FALLBACK_RISK = 0.15
QWEN_PARSE_FAIL_STATE_SNIP = 200

QWEN_RIDER_PROMPT = """你是骑行安全监测助手。请根据画面判断骑行者（若画面为车内/路边监控，以画面中骑电动车或自行车的人为主）。
请严格只输出一行合法 JSON（不要 markdown，不要其它文字），格式如下：
{"risk":0到1的小数,"state":"10字内状态","helmet":true或false或null,"alert":"无或简短提醒"}
risk: 0 表示正常，越高表示越需警惕（单手离把、低头看手机、明显疲劳、危险姿态等）。
无清晰骑行者时：{"risk":0,"state":"未检测到骑手","helmet":null,"alert":"无"}"""

# ---------------------------------------------------------------------------
# 主界面 PyQt5
# ---------------------------------------------------------------------------
UI_WINDOW_WIDTH = 1180
UI_WINDOW_HEIGHT = 720
UI_WINDOW_MIN_WIDTH = 1020
UI_WINDOW_MIN_HEIGHT = 640
UI_VIDEO_LABEL_MIN_WIDTH = 640
UI_VIDEO_LABEL_MIN_HEIGHT = 480
UI_RIGHT_PANEL_MIN_WIDTH = 420
UI_LAYOUT_ROOT_STRETCH_VIDEO = 3
UI_LAYOUT_ROOT_STRETCH_PANEL = 2
UI_DEFAULT_CITY = "北京"
UI_COMBO_PORT_MIN_WIDTH = 120
UI_BTN_SERIAL_MIN_WIDTH = 120
UI_BTN_START_STOP_MIN_WIDTH = 120
UI_SIM_SPEED_DEFAULT = 18.0
UI_BASE_MAX_SPEED_DEFAULT = 25.0
UI_QWEN_POLL_SEC_MIN = 3
UI_QWEN_POLL_SEC_MAX = 120
UI_QWEN_POLL_SEC_DEFAULT = 5
UI_SIM_TIMER_MS = 80
UI_SIM_WOBBLE_AMPLITUDE = 2.0
UI_JPEG_MAX_SIDE = 720
UI_JPEG_QUALITY = 82
UI_STATUS_ERROR_MS = 8000
