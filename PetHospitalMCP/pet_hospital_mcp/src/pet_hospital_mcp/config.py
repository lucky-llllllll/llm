from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 9000
    pet_hospital_base_url: str = "http://127.0.0.1:8080"
    timeout_seconds: float = 10.0
    max_retries: int = 2


def get_settings() -> Settings:
    return Settings(
        host=os.getenv("MCP_HOST", "127.0.0.1"),
        port=int(os.getenv("MCP_PORT", "9000")),
        pet_hospital_base_url=os.getenv("PET_HOSPITAL_BASE_URL", "http://127.0.0.1:8080"),
    )
