from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DOTENV_KEYS_ALLOWED = {
    "ANYTHINGLLM_API_KEY",
    "ANYTHINGLLM_BASE_URL",
    "ANYTHINGLLM_TIMEOUT_SECONDS",
    "ANYTHINGLLM_MAX_RETRIES",
    "ANYTHINGLLM_DEFAULT_CHAT_MODE",
    "MCP_HOST",
    "MCP_PORT",
    "MCP_TIMEOUT_SECONDS",
    "MCP_MAX_RETRIES",
}


def _load_dotenv() -> None:
    """Load .env from the project root (or cwd) into os.environ if not already set."""
    candidates = [Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"]
    for dotenv in candidates:
        if not dotenv.is_file():
            continue
        try:
            for line in dotenv.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if key in _DOTENV_KEYS_ALLOWED and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue
        break


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
    port: int = 9001
    anythingllm_base_url: str = "http://127.0.0.1:3001"
    anythingllm_api_key: str = ""
    timeout_seconds: float = 30.0
    max_retries: int = 2
    default_chat_mode: str = "query"


def get_settings() -> Settings:
    _load_dotenv()
    return Settings(
        host=_get_env_str("MCP_HOST", "127.0.0.1"),
        port=_get_env_int("MCP_PORT", 9001),
        anythingllm_base_url=_get_env_str("ANYTHINGLLM_BASE_URL", "http://127.0.0.1:3001"),
        anythingllm_api_key=_get_env_str("ANYTHINGLLM_API_KEY", ""),
        timeout_seconds=_get_env_float("ANYTHINGLLM_TIMEOUT_SECONDS", _get_env_float("MCP_TIMEOUT_SECONDS", 30.0)),
        max_retries=_get_env_int("ANYTHINGLLM_MAX_RETRIES", _get_env_int("MCP_MAX_RETRIES", 2)),
        default_chat_mode=_get_env_str("ANYTHINGLLM_DEFAULT_CHAT_MODE", "query"),
    )
