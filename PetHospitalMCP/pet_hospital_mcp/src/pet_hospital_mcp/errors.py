from __future__ import annotations

from typing import Any


class ServiceError(Exception):
    """Base error for translated backend and validation problems."""

    code: str = "INTERNAL_ERROR"
    message: str = "Internal error"
    details: dict[str, Any] | None = None

    def __init__(self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code or self.code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details or {}}}


class ValidationError(ServiceError):
    code = "VALIDATION_ERROR"


class BackendTimeoutError(ServiceError):
    code = "BACKEND_TIMEOUT"


class BackendUnavailableError(ServiceError):
    code = "BACKEND_UNAVAILABLE"


class BackendApiError(ServiceError):
    code = "BACKEND_API_ERROR"


class BackendInvalidResponseError(ServiceError):
    code = "BACKEND_INVALID_RESPONSE"


class InternalServiceError(ServiceError):
    code = "INTERNAL_ERROR"
