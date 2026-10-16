from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

_SENSITIVE_KEYS = {"ownerphone", "owneraddr", "chipno"}
_SENSITIVE_KEY_ALIASES = {"owner_phone", "owner_phone", "owner_addr", "chip_no"}


def _mask_value(value: str) -> str:
    if not value:
        return value
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 4:
        return f"{digits[:2]}***{digits[-2:]}" if len(digits) > 4 else "***"
    return "***"


def _mask_sensitive(obj: Any, parent_key: str | None = None) -> Any:
    if isinstance(obj, Mapping):
        result: dict[str, Any] = {}
        for key, value in obj.items():
            lowered = str(key).lower().replace("-", "_")
            if "ownerphone" in lowered or "owner_phone" in lowered:
                result[key] = _mask_value(str(value))
            elif "owneraddr" in lowered or "owner_addr" in lowered:
                result[key] = "[REDACTED]"
            elif "chipno" in lowered or "chip_no" in lowered:
                result[key] = _mask_value(str(value))
            else:
                result[key] = _mask_sensitive(value, lowered)
        return result
    if isinstance(obj, list):
        return [_mask_sensitive(item, parent_key) for item in obj]
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
