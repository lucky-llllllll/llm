from __future__ import annotations

import math
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.errors import BackendApiError, BackendInvalidResponseError, BackendTimeoutError, BackendUnavailableError, ServiceError, ValidationError
from pet_hospital_mcp.logging_config import log_tool_call
from pet_hospital_mcp.rest_client import PetHospitalRestClient

ALLOWED_SPECIES = ["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"]
ALLOWED_STATUS = ["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"]
ALLOWED_SORT_FIELDS = ["id", "name", "ownerName", "species", "doctor", "disease", "status", "totalCost", "visitCount", "createdAt", "updatedAt"]
ALLOWED_ORDERS = ["asc", "desc"]


class ListPetsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    q: str | None = None
    name: str | None = None
    ownerName: str | None = None
    ownerPhone: str | None = None
    species: Literal[tuple(ALLOWED_SPECIES)] | None = None
    doctor: str | None = None
    disease: str | None = None
    status: Literal[tuple(ALLOWED_STATUS)] | None = None
    min: float | None = Field(default=None, ge=0)
    max: float | None = Field(default=None, ge=0)
    sortBy: Literal[tuple(ALLOWED_SORT_FIELDS)] | None = Field(default=None, alias="sortBy")
    order: Literal[tuple(ALLOWED_ORDERS)] | None = None
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=500)

    @field_validator("min", "max", mode="before")
    @classmethod
    def validate_non_negative_number(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, bool):
            raise ValueError("must be a number")
        if isinstance(value, int | float):
            if math.isnan(float(value)) or math.isinf(float(value)):
                raise ValueError("must be finite")
            return float(value)
        raise ValueError("must be a number")

    @model_validator(mode="after")
    def validate_range(self) -> "ListPetsInput":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min must be <= max")
        return self


class ListPetsItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    name: str | None = None
    species: str | None = None
    breed: str | None = None
    gender: str | None = None
    ageMonths: int | None = None
    color: str | None = None
    chipNo: str | None = None
    ownerName: str | None = None
    ownerPhone: str | None = None
    ownerAddr: str | None = None
    doctor: str | None = None
    disease: str | None = None
    status: str | None = None
    allergy: str | None = None
    note: str | None = None
    records: list[dict[str, Any]] | None = None
    charges: list[dict[str, Any]] | None = None
    totalCost: float | None = None
    visitCount: int | None = None
    createdAt: str | None = None
    updatedAt: str | None = None


class ListPetsSuccess(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[ListPetsItem]
    total: int
    page: int
    pageSize: int
    totalPages: int
    totalCost: float | None = None


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    error: dict[str, Any]


class ListPetsTool:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = PetHospitalRestClient(settings)

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        logger = __import__("logging").getLogger("pet_hospital_mcp.tool")
        try:
            validated = ListPetsInput.model_validate(params)
            backend_params = validated.model_dump(exclude_none=True, by_alias=True)
            backend_params = {key: value for key, value in backend_params.items() if key not in {"page", "pageSize"} or value is not None}
            # keep exact request mapping
            data = self.client.list_pets(backend_params)
            result = ListPetsSuccess.model_validate(data)
            log_tool_call(logger, tool_name="list_pets", params=params, status="success", duration_ms=int((time.perf_counter() - started) * 1000))
            return result.model_dump(mode="json")
        except ValidationError as exc:
            envelope = {"error": {"code": "VALIDATION_ERROR", "message": str(exc), "details": {}}}
            log_tool_call(logger, tool_name="list_pets", params=params, status="validation_error", duration_ms=int((time.perf_counter() - started) * 1000))
            raise ValueError(str(envelope))
        except (BackendTimeoutError, BackendUnavailableError, BackendApiError, BackendInvalidResponseError) as exc:
            env = exc.to_dict() if hasattr(exc, "to_dict") else {"error": {"code": "INTERNAL_ERROR", "message": str(exc), "details": {}}}
            log_tool_call(logger, tool_name="list_pets", params=params, status="backend_error", duration_ms=int((time.perf_counter() - started) * 1000))
            raise ValueError(str(env))
        except Exception as exc:
            env = {"error": {"code": "INTERNAL_ERROR", "message": "Internal error", "details": {}}}
            log_tool_call(logger, tool_name="list_pets", params=params, status="internal_error", duration_ms=int((time.perf_counter() - started) * 1000))
            raise ValueError(str(env)) from exc


def list_pets(params: dict[str, Any], settings: Settings) -> dict[str, Any]:
    return ListPetsTool(settings).execute(params)
