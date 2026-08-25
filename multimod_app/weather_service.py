"""天气数据：Open-Meteo（免 Key）；支持按城市名地理编码后取预报。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from multimod_app import config


@dataclass
class WeatherSnapshot:
    temperature_c: float
    wind_speed_kmh: float
    weather_code: int
    description: str
    is_precipitation: bool
    raw: dict[str, Any]


@dataclass
class CityGeocode:
    """地理编码结果，用于界面展示与预报坐标。"""

    name: str
    admin1: str
    country: str
    latitude: float
    longitude: float


def _wmo_text(code: int) -> tuple[str, bool]:
    precip = code in config.WEATHER_PRECIPITATION_CODES
    table = {
        0: "晴朗",
        1: "大部晴朗",
        2: "多云",
        3: "阴",
        45: "雾",
        48: "沉积雾",
        51: "小毛毛雨",
        53: "毛毛雨",
        55: "大毛毛雨",
        61: "小雨",
        63: "中雨",
        65: "大雨",
        71: "小雪",
        73: "中雪",
        75: "大雪",
        80: "阵雨",
        81: "强阵雨",
        82: "暴雨",
        95: "雷暴",
    }
    return table.get(code, f"代码{code}"), precip


def geocode_city(city: str) -> CityGeocode | None:
    """Open-Meteo 地理编码，中文城市名可用。"""
    name = (city or "").strip()
    if not name:
        return None
    url = config.WEATHER_GEOCODE_URL
    params = {
        "name": name,
        "count": config.WEATHER_GEOCODE_COUNT,
        "language": config.WEATHER_GEOCODE_LANGUAGE,
        "format": "json",
    }
    try:
        r = requests.get(url, params=params, timeout=config.WEATHER_REQUEST_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        results = data.get("results") or []
        if not results:
            return None
        hit = results[0]
        admin1 = str(hit.get("admin1") or hit.get("admin2") or "")
        country = str(hit.get("country") or hit.get("country_code") or "")
        return CityGeocode(
            name=str(hit.get("name") or name),
            admin1=admin1,
            country=country,
            latitude=float(hit["latitude"]),
            longitude=float(hit["longitude"]),
        )
    except Exception:
        return None


def fetch_weather(lat: float, lon: float) -> WeatherSnapshot | None:
    url = config.WEATHER_FORECAST_URL
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": config.WEATHER_FORECAST_CURRENT_FIELDS,
        "wind_speed_unit": config.WEATHER_WIND_SPEED_UNIT,
        "timezone": config.WEATHER_TIMEZONE,
    }
    try:
        r = requests.get(url, params=params, timeout=config.WEATHER_REQUEST_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        cur = data.get("current") or {}
        code = int(cur.get("weather_code", 0))
        desc, precip = _wmo_text(code)
        return WeatherSnapshot(
            temperature_c=float(cur.get("temperature_2m", 0.0)),
            wind_speed_kmh=float(cur.get("wind_speed_10m", 0.0)),
            weather_code=code,
            description=desc,
            is_precipitation=precip,
            raw=data,
        )
    except Exception:
        return None


def fetch_weather_by_city(city: str) -> tuple[WeatherSnapshot | None, CityGeocode | None, str]:
    """
    返回 (天气, 地理编码信息, 错误/提示文案)。
    地理编码失败时后两者说明原因。
    """
    geo = geocode_city(city)
    if geo is None:
        return None, None, "未找到该城市，请换关键词或检查拼写"
    w = fetch_weather(geo.latitude, geo.longitude)
    if w is None:
        return None, geo, "天气接口失败（网络或限流）"
    loc = f"{geo.name}"
    if geo.admin1:
        loc += f" · {geo.admin1}"
    if geo.country:
        loc += f" ({geo.country})"
    return w, geo, loc
