from __future__ import annotations

import json
import math
import time
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, ValidationError, field_validator, model_validator
from starlette.responses import JSONResponse

from .config import get_settings
from .errors import BackendApiError, BackendInvalidResponseError, BackendTimeoutError, BackendUnavailableError, ServiceError, ValidationError as ToolValidationError
from .logging_config import log_tool_call
from .rest_client import PetHospitalRestClient

settings = get_settings()
server = MCPServer(
    name="pet-hospital-mcp",
    version="0.1.0",
    description="Adapter for the Go pet hospital REST API.",
    instructions="Use the list_pets tool to query pet records from the upstream API.",
)

ALLOWED_SPECIES = ["犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"]
ALLOWED_STATUS = ["待就诊", "就诊中", "住院中", "已康复", "慢性病随访"]
ALLOWED_SORT_FIELDS = ["id", "name", "ownerName", "species", "doctor", "disease", "status", "totalCost", "visitCount", "createdAt", "updatedAt"]
ALLOWED_ORDERS = ["asc", "desc"]


class ListPetsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: StrictStr | None = None
    name: StrictStr | None = None
    ownerName: StrictStr | None = None
    ownerPhone: StrictStr | None = None
    species: str | None = Field(default=None)
    doctor: StrictStr | None = None
    disease: StrictStr | None = None
    status: str | None = Field(default=None)
    min: StrictFloat | None = Field(default=None, ge=0)
    max: StrictFloat | None = Field(default=None, ge=0)
    sortBy: str | None = Field(default=None, alias="sortBy")
    order: str | None = Field(default=None)
    page: StrictInt = Field(default=1, ge=1)
    pageSize: StrictInt = Field(default=20, ge=1, le=500)

    @field_validator("species", "status", "sortBy", "order")
    @classmethod
    def validate_allowed_values(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return value
        field_name = info.field_name
        if field_name == "species" and value not in ALLOWED_SPECIES:
            raise ValueError(f"species must be one of {ALLOWED_SPECIES}")
        if field_name == "status" and value not in ALLOWED_STATUS:
            raise ValueError(f"status must be one of {ALLOWED_STATUS}")
        if field_name == "sortBy" and value not in ALLOWED_SORT_FIELDS:
            raise ValueError(f"sortBy must be one of {ALLOWED_SORT_FIELDS}")
        if field_name == "order" and value not in ALLOWED_ORDERS:
            raise ValueError(f"order must be one of {ALLOWED_ORDERS}")
        return value

    @field_validator("min", "max", mode="before")
    @classmethod
    def ensure_finite(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, bool):
            raise TypeError("boolean is not valid")
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            raise ValueError("value must be finite")
        return f

    @model_validator(mode="after")
    def ensure_range(self) -> "ListPetsInput":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min must be <= max")
        return self


class ListPetsRecord(BaseModel):
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

    items: list[ListPetsRecord]
    total: int
    page: int
    pageSize: int
    totalPages: int
    totalCost: float | None = None


def _error_payload(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


@server.custom_route("/health", methods=["GET"])
async def health_check(request):
    return JSONResponse({"status": "ok", "service": "pet_hospital_mcp"})


@server.tool(
    name="list_pets",
    description=(
        "Query the upstream pet hospital archive list with the same filtering, sorting, and paging semantics "
        "as GET /api/v1/pets. Use this tool when you need to search or browse pet records by keyword, owner, "
        "species, doctor, disease, status, price range, sort order, or page. Returns the upstream data payload "
        "with items, total, page, pageSize, totalPages, and totalCost."
    ),
    structured_output=False,
)
async def list_pets_tool(
    q: Annotated[str | None, Field(default=None, description="Free-text search across pet fields.")] = None,
    name: Annotated[str | None, Field(default=None, description="Pet name filter.")] = None,
    ownerName: Annotated[str | None, Field(default=None, description="Owner name filter.")] = None,
    ownerPhone: Annotated[str | None, Field(default=None, description="Owner phone filter.")] = None,
    species: Annotated[str | None, Field(default=None, description="Allowed values: 犬, 猫, 兔, 鸟, 仓鼠, 爬宠, 其他.")] = None,
    doctor: Annotated[str | None, Field(default=None, description="Doctor name filter.")] = None,
    disease: Annotated[str | None, Field(default=None, description="Disease filter.")] = None,
    status: Annotated[str | None, Field(default=None, description="Allowed values: 待就诊, 就诊中, 住院中, 已康复, 慢性病随访.")] = None,
    min: Annotated[float | None, Field(default=None, description="Minimum total cost.")] = None,
    max: Annotated[float | None, Field(default=None, description="Maximum total cost.")] = None,
    sortBy: Annotated[str | None, Field(default=None, description="Allowed values: id, name, ownerName, species, doctor, disease, status, totalCost, visitCount, createdAt, updatedAt.")] = None,
    order: Annotated[str | None, Field(default=None, description="Allowed values: asc, desc.")] = None,
    page: Annotated[int, Field(default=1, ge=1, description="One-based page number.")] = 1,
    pageSize: Annotated[int, Field(default=20, ge=1, le=500, description="Rows per page, 1..500.")] = 20,
) -> ListPetsSuccess:
    params: dict[str, Any] = {
        "q": q,
        "name": name,
        "ownerName": ownerName,
        "ownerPhone": ownerPhone,
        "species": species,
        "doctor": doctor,
        "disease": disease,
        "status": status,
        "min": min,
        "max": max,
        "sortBy": sortBy,
        "order": order,
        "page": page,
        "pageSize": pageSize,
    }
    started = time.perf_counter()
    try:
        validated = ListPetsInput.model_validate(params)
        filtered = validated.model_dump(exclude_none=True, by_alias=True)
        rest_client = PetHospitalRestClient(settings)
        try:
            data = rest_client.list_pets(filtered)
        finally:
            rest_client.close()
        result = ListPetsSuccess.model_validate(data)
        log_tool_call(__import__("logging").getLogger("pet_hospital_mcp"), tool_name="list_pets", params=params, status="success", duration_ms=int((time.perf_counter() - started) * 1000))
        return result
    except ValidationError as exc:
        payload = _error_payload("VALIDATION_ERROR", str(exc))
        log_tool_call(__import__("logging").getLogger("pet_hospital_mcp"), tool_name="list_pets", params=params, status="validation_error", duration_ms=int((time.perf_counter() - started) * 1000))
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload,
            is_error=True,
        )
    except (ToolValidationError, ServiceError, BackendTimeoutError, BackendUnavailableError, BackendApiError, BackendInvalidResponseError) as exc:
        payload = getattr(exc, "to_dict", lambda: _error_payload("INTERNAL_ERROR", str(exc)))()
        log_tool_call(__import__("logging").getLogger("pet_hospital_mcp"), tool_name="list_pets", params=params, status="backend_error", duration_ms=int((time.perf_counter() - started) * 1000))
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload,
            is_error=True,
        )
    except Exception as exc:  # pragma: no cover
        payload = _error_payload("INTERNAL_ERROR", str(exc))
        log_tool_call(__import__("logging").getLogger("pet_hospital_mcp"), tool_name="list_pets", params=params, status="internal_error", duration_ms=int((time.perf_counter() - started) * 1000))
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload,
            is_error=True,
        )


app = server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True)
