"""多模态融合：天气 + 路况(YOLO) + 速度 -> 建议安全车速与是否减速。"""
from __future__ import annotations

from dataclasses import dataclass

from multimod_app import config
from multimod_app.weather_service import WeatherSnapshot


@dataclass
class SafetyAdvice:
    recommended_speed_kmh: float
    need_decelerate: bool
    reason: str
    weather_factor: float
    obstacle_factor: float
    rider_factor: float


def compute_safe_speed(
    current_speed_kmh: float,
    obstacle_max_area_ratio: float,
    obstacle_count: int,
    weather: WeatherSnapshot | None,
    base_max_kmh: float = config.SAFETY_BASE_MAX_KMH_DEFAULT,
    rider_risk: float = 0.0,
) -> SafetyAdvice:
    """
    obstacle_max_area_ratio: 最大检测框占画面比例，越大表示目标越近（启发式）。
    rider_risk: 千问多模态给出的风险 0~1，越高越压低建议车速。
    """
    w_factor = 1.0
    if weather:
        if weather.is_precipitation:
            w_factor *= config.SAFETY_WEATHER_PRECIPITATION_MULT
        if weather.wind_speed_kmh > config.SAFETY_WEATHER_WIND_HIGH_KMH:
            w_factor *= config.SAFETY_WEATHER_WIND_HIGH_MULT
        elif weather.wind_speed_kmh > config.SAFETY_WEATHER_WIND_MID_KMH:
            w_factor *= config.SAFETY_WEATHER_WIND_MID_MULT
        if weather.weather_code in config.SAFETY_WEATHER_FOG_CODES:
            w_factor *= config.SAFETY_WEATHER_FOG_MULT

    o_factor = 1.0
    if obstacle_count > 0:
        o_factor -= min(
            config.SAFETY_OBSTACLE_AREA_CAP,
            obstacle_max_area_ratio * config.SAFETY_OBSTACLE_AREA_COEF,
        )
        o_factor -= min(
            config.SAFETY_OBSTACLE_COUNT_CAP,
            config.SAFETY_OBSTACLE_COUNT_STEP * max(0, obstacle_count - 1),
        )
    o_factor = max(config.SAFETY_OBSTACLE_FACTOR_MIN, o_factor)

    rr = max(0.0, min(1.0, float(rider_risk)))
    rider_factor = 1.0 - min(config.SAFETY_RIDER_RISK_CAP, rr * config.SAFETY_RIDER_RISK_COEF)
    rider_factor = max(config.SAFETY_RIDER_FACTOR_MIN, rider_factor)

    rec = base_max_kmh * w_factor * o_factor * rider_factor
    rec = max(config.SAFETY_MIN_RECOMMENDED_KMH, min(base_max_kmh, rec))

    margin = config.SAFETY_SPEED_OVER_MARGIN
    need_slow = current_speed_kmh > rec * margin
    parts = []
    if weather:
        parts.append(f"天气:{weather.description} {weather.temperature_c:.0f}℃ 风{weather.wind_speed_kmh:.0f}km/h")
    else:
        parts.append("天气:未获取")
    if obstacle_count:
        parts.append(f"前方障碍:{obstacle_count}个 接近度:{obstacle_max_area_ratio:.2f}")
    else:
        parts.append("前方障碍:未检出")
    if rr > config.SAFETY_RIDER_REASON_THRESHOLD:
        parts.append(f"骑手状态风险:{rr:.2f}")
    reason = "；".join(parts)

    return SafetyAdvice(
        recommended_speed_kmh=round(rec, 1),
        need_decelerate=need_slow,
        reason=reason,
        weather_factor=round(w_factor, 2),
        obstacle_factor=round(o_factor, 2),
        rider_factor=round(rider_factor, 2),
    )
