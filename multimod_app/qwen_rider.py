"""通义千问多模态（DashScope 兼容 OpenAI 格式）分析骑手状态。"""
from __future__ import annotations

import base64
import json
import re
from typing import Any

import requests

from multimod_app import config


def analyze_rider_jpeg(
    jpeg_bytes: bytes,
    api_key: str,
    model: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """
    调用千问 VL。返回 dict: ok, risk, state, helmet, alert, raw, error
    """
    out: dict[str, Any] = {
        "ok": False,
        "risk": 0.0,
        "state": "",
        "helmet": None,
        "alert": "",
        "raw": "",
        "error": "",
    }
    model = model or config.QWEN_DEFAULT_MODEL
    timeout = config.QWEN_REQUEST_TIMEOUT_S if timeout is None else timeout
    key = (api_key or "").strip()
    if not key:
        out["error"] = "未配置 API Key"
        return out
    if len(jpeg_bytes) < 32:
        out["error"] = "图像数据无效"
        return out

    b64 = base64.standard_b64encode(jpeg_bytes).decode("ascii")
    data_url = f"data:image/jpeg;base64,{b64}"

    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": config.QWEN_RIDER_PROMPT},
                ],
            }
        ],
        "max_tokens": config.QWEN_MAX_TOKENS,
        "temperature": config.QWEN_TEMPERATURE,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        r = requests.post(config.DASHSCOPE_COMPAT_CHAT_URL, headers=headers, json=body, timeout=timeout)
        out["raw"] = r.text[: config.QWEN_RESPONSE_RAW_MAX_LEN]
        if r.status_code != 200:
            try:
                err_j = r.json()
                msg = err_j.get("message") or err_j.get("error", {}).get("message") or r.text
            except Exception:
                msg = r.text
            out["error"] = f"HTTP {r.status_code}: {msg[:config.QWEN_ERROR_MSG_MAX_LEN]}"
            return out
        resp = r.json()
        choices = resp.get("choices") or []
        if not choices:
            out["error"] = "响应无 choices"
            return out
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, list):
            text_parts = [c.get("text", "") for c in content if isinstance(c, dict)]
            text = "".join(text_parts).strip()
        else:
            text = (content or "").strip()
        parsed = _parse_rider_json(text)
        out.update(parsed)
        out["ok"] = True
        return out
    except requests.RequestException as e:
        out["error"] = str(e)[: config.QWEN_ERROR_MSG_MAX_LEN]
        return out


def _parse_rider_json(text: str) -> dict[str, Any]:
    risk, state, helmet, alert = 0.0, "", None, ""
    raw = text
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text).strip()
    try:
        j = json.loads(text)
    except Exception:
        lo, hi = text.find("{"), text.rfind("}")
        if lo >= 0 and hi > lo:
            try:
                j = json.loads(text[lo : hi + 1])
            except Exception:
                j = None
        else:
            j = None
    try:
        if j is None:
            raise ValueError("no json")
        risk = float(j.get("risk", 0) or 0)
        risk = max(0.0, min(1.0, risk))
        state = str(j.get("state", "") or "")[: config.QWEN_STATE_MAX_LEN]
        h = j.get("helmet")
        if h is True or h is False:
            helmet = h
        else:
            helmet = None
        alert = str(j.get("alert", "") or "")[: config.QWEN_ALERT_MAX_LEN]
    except Exception:
        state = text[: config.QWEN_PARSE_FAIL_STATE_SNIP] if text else "解析失败"
        risk = config.QWEN_PARSE_FAIL_FALLBACK_RISK
    return {"risk": risk, "state": state, "helmet": helmet, "alert": alert, "raw": raw}
