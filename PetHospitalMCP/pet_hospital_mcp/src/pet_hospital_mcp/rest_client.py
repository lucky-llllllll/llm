from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from .config import Settings
from .errors import BackendApiError, BackendInvalidResponseError, BackendTimeoutError, BackendUnavailableError, ServiceError


class PetHospitalRestClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.Client(
            base_url=settings.pet_hospital_base_url,
            timeout=httpx.Timeout(settings.timeout_seconds, connect=settings.timeout_seconds, read=settings.timeout_seconds),
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def list_pets(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._request("GET", "/api/v1/pets", params=params)

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                response = self._client.request(method, path, params=params)
                if response.is_error:
                    status_code = response.status_code
                    if status_code in {408, 429} or status_code >= 500:
                        if attempt < self.settings.max_retries:
                            continue
                        raise BackendUnavailableError(
                            "Backend service unavailable",
                            details={"status_code": status_code, "path": path},
                        )
                    raise BackendApiError(
                        f"Backend API returned {status_code}", details={"status_code": status_code, "path": path}
                    )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise BackendInvalidResponseError("Backend returned invalid JSON") from exc
                if not isinstance(payload, dict):
                    raise BackendInvalidResponseError("Backend response was not a JSON object")
                if payload.get("code") not in (200, 0):
                    raise BackendApiError(
                        "Backend API returned an error response",
                        details={"code": payload.get("code"), "message": payload.get("message")},
                    )
                data = payload.get("data")
                if data is None:
                    raise BackendInvalidResponseError("Backend response missing data")
                return data
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < self.settings.max_retries:
                    continue
                raise BackendTimeoutError("Backend request timed out") from exc
            except httpx.NetworkError as exc:
                last_exc = exc
                if attempt < self.settings.max_retries:
                    continue
                raise BackendUnavailableError("Backend service unavailable") from exc
            except ServiceError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < self.settings.max_retries:
                    continue
                raise BackendInvalidResponseError("Backend response handling failed") from exc
        if last_exc is not None:
            raise BackendUnavailableError("Backend service unavailable") from last_exc
        raise BackendUnavailableError("Backend service unavailable")
