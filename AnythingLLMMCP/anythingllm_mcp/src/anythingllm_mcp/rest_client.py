from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .errors import BackendApiError, BackendInvalidResponseError, BackendTimeoutError, BackendUnavailableError, ServiceError


class AnythingLLMRestClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        headers: dict[str, str] = {}
        if settings.anythingllm_api_key:
            headers["Authorization"] = f"Bearer {settings.anythingllm_api_key}"
        self._client = httpx.Client(
            base_url=settings.anythingllm_base_url,
            timeout=httpx.Timeout(settings.timeout_seconds, connect=settings.timeout_seconds, read=settings.timeout_seconds),
            follow_redirects=True,
            headers=headers,
        )

    def close(self) -> None:
        self._client.close()

    def list_workspaces(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/workspaces")

    def upload_document(self, file_path: str, *, add_to_workspaces: list[str] | None = None) -> dict[str, Any]:
        path = Path(file_path)
        data: dict[str, Any] = {}
        if add_to_workspaces:
            data["addToWorkspaces"] = ",".join(add_to_workspaces)
        files = {"file": (path.name, path.read_bytes())}
        return self._request("POST", "/api/v1/document/upload", data=data or None, files=files)

    def update_embeddings(self, workspace_slug: str, locations: list[str]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/workspace/{workspace_slug}/update-embeddings",
            json_payload={"adds": list(locations)},
        )

    def workspace_chat(self, workspace_slug: str, message: str, mode: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/workspace/{workspace_slug}/chat",
            json_payload={"message": message, "mode": mode},
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                response = self._client.request(method, path, params=params, json=json_payload, data=data, files=files)
                if response.is_error:
                    status_code = response.status_code
                    if status_code in {408, 429} or status_code >= 500:
                        if attempt < self.settings.max_retries:
                            continue
                        raise BackendUnavailableError(
                            "AnythingLLM service unavailable",
                            details={"status_code": status_code, "path": path},
                        )
                    try:
                        body = response.json()
                    except ValueError:
                        body = {}
                    message = body.get("error") if isinstance(body, dict) else None
                    raise BackendApiError(
                        message or f"AnythingLLM API returned {status_code}",
                        details={"status_code": status_code, "path": path},
                    )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise BackendInvalidResponseError("AnythingLLM returned invalid JSON") from exc
                if not isinstance(payload, dict):
                    raise BackendInvalidResponseError("AnythingLLM response was not a JSON object")
                if payload.get("success") is False:
                    raise BackendApiError(
                        str(payload.get("error") or "AnythingLLM API returned an error response"),
                        details={"path": path},
                    )
                return payload
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < self.settings.max_retries:
                    continue
                raise BackendTimeoutError("AnythingLLM request timed out") from exc
            except httpx.NetworkError as exc:
                last_exc = exc
                if attempt < self.settings.max_retries:
                    continue
                raise BackendUnavailableError("AnythingLLM service unavailable") from exc
            except ServiceError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < self.settings.max_retries:
                    continue
                raise BackendInvalidResponseError("AnythingLLM response handling failed") from exc
        if last_exc is not None:
            raise BackendUnavailableError("AnythingLLM service unavailable") from last_exc
        raise BackendUnavailableError("AnythingLLM service unavailable")
