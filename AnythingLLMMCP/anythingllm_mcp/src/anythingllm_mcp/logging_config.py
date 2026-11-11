from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

_SENSITIVE_KEY_MARKERS = ("apikey", "api_key", "authorization", "bearer", "secret", "token")


def _mask_secret(value: str) -> str:
    if not value:
        return "***"
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}***{value[-3:]}"


def _mask_sensitive(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        result: dict[str, Any] = {}
        for key, value in obj.items():
            lowered = str(key).lower().replace("-", "_")
            if any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS):
                result[key] = _mask_secret(str(value))
            else:
                result[key] = _mask_sensitive(value)
        return result
    if isinstance(obj, list):
        return [_mask_sensitive(item) for item in obj]
    return obj


def sanitize_for_logging(payload: Any) -> Any:
    return _mask_sensitive(payload)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
    return logger


def log_tool_call(logger: logging.Logger, *, tool_name: str, params: dict[str, Any], status: str, duration_ms: int) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_name": tool_name,
        "params": sanitize_for_logging(params),
        "status": status,
        "duration_ms": duration_ms,
    }
    logger.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
