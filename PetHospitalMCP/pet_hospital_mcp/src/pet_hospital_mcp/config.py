from __future__ import annotations

import os
from dataclasses import dataclass


def _get_env_str(key: str, default: str) -> str:
    value = os.getenv(key)
    return default if value is None or value == "" else value


def _get_env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {key} must be an integer, got {raw!r}") from exc


def _get_env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Environment variable {key} must be a number, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 9000
    pet_hospital_base_url: str = "http://127.0.0.1:8080"
    timeout_seconds: float = 10.0
    max_retries: int = 2


def get_settings() -> Settings:
    return Settings(
        host=_get_env_str("MCP_HOST", "127.0.0.1"),
        port=_get_env_int("MCP_PORT", 9000),
        pet_hospital_base_url=_get_env_str("PET_HOSPITAL_BASE_URL", "http://127.0.0.1:8080"),
        timeout_seconds=_get_env_float("PET_HOSPITAL_TIMEOUT_SECONDS", _get_env_float("MCP_TIMEOUT_SECONDS", 10.0)),
        max_retries=_get_env_int("PET_HOSPITAL_MAX_RETRIES", _get_env_int("MCP_MAX_RETRIES", 2)),
    )
