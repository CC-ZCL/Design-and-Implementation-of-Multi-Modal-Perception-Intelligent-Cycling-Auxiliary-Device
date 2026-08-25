"""
MultiCheck 入口：Python 3.10+，PyQt5 + YOLOv8n + 串口速度 + Open-Meteo 天气。

运行（在项目根目录）:
  pip install -r requirements.txt
  python main.py

树莓派: 建议安装 libgl 与较新 OpenCV；首次运行会自动下载 yolov8n.pt（或手动放到项目根）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from multimod_app.main_window import main
from multimod_app.serial_speed import setup_serial_logging

if __name__ == "__main__":
    setup_serial_logging()
    main()
