from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError, field_validator
from starlette.responses import JSONResponse

from .config import get_settings
from .errors import BackendApiError, BackendInvalidResponseError, BackendTimeoutError, BackendUnavailableError, ServiceError, ValidationError as ToolValidationError
from .logging_config import log_tool_call
from .rest_client import AnythingLLMRestClient

settings = get_settings()
server = MCPServer(
    name="anythingllm-mcp",
    version="0.1.0",
    description="Adapter for the AnythingLLM REST API: workspace listing, document upload/embedding, and RAG chat.",
    instructions=(
        "Use list_workspaces to discover AnythingLLM workspaces, upload_document to upload and embed a local "
        "file into a workspace, and rag_query to ask grounded questions against embedded documents "
        "(mode='query' restricts answers to workspace content)."
    ),
)

ALLOWED_CHAT_MODES = ["query", "chat"]
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MiB


class ListWorkspacesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: StrictInt | None = None
    slug: str | None = None
    name: str | None = None
    documentCount: StrictInt | None = None
    threadCount: StrictInt | None = None
    chatMode: str | None = None


class ListWorkspacesSuccess(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workspaces: list[WorkspaceSummary]
    total: StrictInt


class UploadDocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_path: StrictStr
    workspace_slug: StrictStr | None = None
    auto_embed: StrictBool = True

    @field_validator("file_path")
    @classmethod
    def validate_file_exists(cls, value: str) -> str:
        path = Path(value)
        if not path.is_file():
            raise ValueError(f"file not found: {value}")
        size = path.stat().st_size
        if size == 0:
            raise ValueError("file is empty")
        if size > MAX_UPLOAD_BYTES:
            raise ValueError(f"file exceeds {MAX_UPLOAD_BYTES} bytes limit")
        return value


class UploadDocumentSuccess(BaseModel):
    model_config = ConfigDict(extra="ignore")

    location: str | None = None
    title: str | None = None
    workspace_slug: str | None = None
    embedded: StrictBool


class RagQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: StrictStr = Field(min_length=1)
    workspace_slug: StrictStr | None = None
    mode: str | None = None

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in ALLOWED_CHAT_MODES:
            raise ValueError(f"mode must be one of {ALLOWED_CHAT_MODES}")
        return value


class RagQuerySuccess(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workspace_slug: str | None = None
    mode: str
    answer: str
    sources: list[dict[str, Any]] = []


def _error_payload(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _require_api_key() -> None:
    if not settings.anythingllm_api_key:
        raise ToolValidationError(
            "ANYTHINGLLM_API_KEY is not configured; set it in the environment before calling this tool",
            details={"env": "ANYTHINGLLM_API_KEY"},
        )


def _extract_document_location(payload: dict[str, Any]) -> str | None:
    documents = payload.get("documents")
    if isinstance(documents, list) and documents:
        location = documents[0].get("location")
        if isinstance(location, str):
            return location
    local_files = payload.get("localFiles")
    if isinstance(local_files, dict):
        location = local_files.get("location")
        if isinstance(location, str):
            return location
        if isinstance(location, list) and location:
            first = location[0]
            if isinstance(first, dict):
                return first.get("location") or first.get("path")
            if isinstance(first, str):
                return first
    if isinstance(payload.get("location"), str):
        return payload["location"]
    return None


async def _resolve_workspace(rest_client: AnythingLLMRestClient, workspace_slug: str | None) -> str:
    if workspace_slug:
        return workspace_slug
    data = rest_client.list_workspaces()
    workspaces = data.get("workspaces") or []
    if not isinstance(workspaces, list) or not workspaces:
        raise BackendInvalidResponseError("No workspace available in AnythingLLM")
    first = workspaces[0]
    slug = first.get("slug") if isinstance(first, dict) else None
    if not slug:
        raise BackendInvalidResponseError("First workspace is missing a slug")
    return slug


@server.custom_route("/health", methods=["GET"])
async def health_check(request):
    return JSONResponse({"status": "ok", "service": "anythingllm_mcp"})


@server.tool(
    name="list_workspaces",
    description=(
        "List all AnythingLLM workspaces with slug, name, document count, thread count, and chat mode. "
        "Call this first when you need to pick a target workspace for document upload or RAG questions."
    ),
    structured_output=False,
)
async def list_workspaces_tool() -> ListWorkspacesSuccess:
    started = time.perf_counter()
    logger = __import__("logging").getLogger("anythingllm_mcp")
    try:
        ListWorkspacesInput.model_validate({})
        _require_api_key()
        rest_client = AnythingLLMRestClient(settings)
        try:
            data = rest_client.list_workspaces()
        finally:
            rest_client.close()
        workspaces = data.get("workspaces") or []
        result = ListWorkspacesSuccess.model_validate({"workspaces": workspaces, "total": len(workspaces)})
        log_tool_call(logger, tool_name="list_workspaces", params={}, status="success", duration_ms=int((time.perf_counter() - started) * 1000))
        return result
    except ValidationError as exc:
        log_tool_call(logger, tool_name="list_workspaces", params={}, status="validation_error", duration_ms=int((time.perf_counter() - started) * 1000))
        payload = _error_payload("VALIDATION_ERROR", str(exc))
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload,
            is_error=True,
        )
    except (ToolValidationError, ServiceError, BackendTimeoutError, BackendUnavailableError, BackendApiError, BackendInvalidResponseError) as exc:
        log_tool_call(logger, tool_name="list_workspaces", params={}, status="backend_error", duration_ms=int((time.perf_counter() - started) * 1000))
        payload = getattr(exc, "to_dict", lambda: _error_payload("INTERNAL_ERROR", str(exc)))()
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload,
            is_error=True,
        )
    except Exception as exc:  # pragma: no cover
        log_tool_call(logger, tool_name="list_workspaces", params={}, status="internal_error", duration_ms=int((time.perf_counter() - started) * 1000))
        payload = _error_payload("INTERNAL_ERROR", str(exc))
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload,
            is_error=True,
        )


@server.tool(
    name="upload_document",
    description=(
        "Upload a local document (pdf, docx, txt, md, csv and other AnythingLLM-supported formats) to AnythingLLM, "
        "optionally add it to a workspace and embed it for RAG. If workspace_slug is omitted, the first workspace is "
        "used. Returns the stored document location and whether it was embedded into the workspace."
    ),
    structured_output=False,
)
async def upload_document_tool(
    file_path: Annotated[str, Field(description="Absolute or relative path to the local document file.")],
    workspace_slug: Annotated[str | None, Field(default=None, description="Target workspace slug. Defaults to the first workspace.")] = None,
    auto_embed: Annotated[bool, Field(default=True, description="Whether to add the document to the workspace embeddings.")] = True,
) -> UploadDocumentSuccess:
    params: dict[str, Any] = {"file_path": file_path, "workspace_slug": workspace_slug, "auto_embed": auto_embed}
    started = time.perf_counter()
    logger = __import__("logging").getLogger("anythingllm_mcp")
    try:
        validated = UploadDocumentInput.model_validate(params)
        _require_api_key()
        rest_client = AnythingLLMRestClient(settings)
        try:
            add_to: list[str] = []
            if validated.workspace_slug:
                add_to = [validated.workspace_slug]
            upload_payload = rest_client.upload_document(validated.file_path, add_to_workspaces=add_to)
            location = _extract_document_location(upload_payload)
            embedded = False
            target_slug = validated.workspace_slug
            if location and validated.auto_embed:
                if target_slug is None:
                    target_slug = await _resolve_workspace(rest_client, None)
                rest_client.update_embeddings(target_slug, [location])
                embedded = True
            documents = upload_payload.get("documents")
            title = documents[0].get("title") if isinstance(documents, list) and documents else None
        finally:
            rest_client.close()
        result = UploadDocumentSuccess.model_validate(
            {"location": location, "title": title or Path(validated.file_path).name, "workspace_slug": target_slug, "embedded": embedded}
        )
        log_tool_call(logger, tool_name="upload_document", params=params, status="success", duration_ms=int((time.perf_counter() - started) * 1000))
        return result
    except ValidationError as exc:
        log_tool_call(logger, tool_name="upload_document", params=params, status="validation_error", duration_ms=int((time.perf_counter() - started) * 1000))
        payload = _error_payload("VALIDATION_ERROR", str(exc))
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload,
            is_error=True,
        )
    except (ToolValidationError, ServiceError, BackendTimeoutError, BackendUnavailableError, BackendApiError, BackendInvalidResponseError) as exc:
        log_tool_call(logger, tool_name="upload_document", params=params, status="backend_error", duration_ms=int((time.perf_counter() - started) * 1000))
        payload = getattr(exc, "to_dict", lambda: _error_payload("INTERNAL_ERROR", str(exc)))()
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload,
            is_error=True,
        )
    except Exception as exc:  # pragma: no cover
        log_tool_call(logger, tool_name="upload_document", params=params, status="internal_error", duration_ms=int((time.perf_counter() - started) * 1000))
        payload = _error_payload("INTERNAL_ERROR", str(exc))
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload,
            is_error=True,
        )


@server.tool(
    name="rag_query",
    description=(
        "Ask a question against an AnythingLLM workspace. mode='query' answers strictly from embedded documents "
        "(grounded RAG), mode='chat' allows general LLM answers with document context. If workspace_slug is omitted, "
        "the first workspace is used. Returns the answer and citation sources."
    ),
    structured_output=False,
)
async def rag_query_tool(
    message: Annotated[str, Field(min_length=1, description="Question to ask the workspace.")],
    workspace_slug: Annotated[str | None, Field(default=None, description="Target workspace slug. Defaults to the first workspace.")] = None,
    mode: Annotated[str | None, Field(default=None, description="Allowed values: query, chat. Defaults to the server configured mode (query).")] = None,
) -> RagQuerySuccess:
    params: dict[str, Any] = {"message": message, "workspace_slug": workspace_slug, "mode": mode}
    started = time.perf_counter()
    logger = __import__("logging").getLogger("anythingllm_mcp")
    try:
        validated = RagQueryInput.model_validate(params)
        _require_api_key()
        chat_mode = validated.mode or settings.default_chat_mode
        rest_client = AnythingLLMRestClient(settings)
        try:
            slug = await _resolve_workspace(rest_client, validated.workspace_slug)
            data = rest_client.workspace_chat(slug, validated.message, chat_mode)
        finally:
            rest_client.close()
        result = RagQuerySuccess.model_validate(
            {
                "workspace_slug": slug,
                "mode": chat_mode,
                "answer": str(data.get("textResponse") or data.get("response") or ""),
                "sources": data.get("sources") or [],
            }
        )
        log_tool_call(logger, tool_name="rag_query", params=params, status="success", duration_ms=int((time.perf_counter() - started) * 1000))
        return result
    except ValidationError as exc:
        log_tool_call(logger, tool_name="rag_query", params=params, status="validation_error", duration_ms=int((time.perf_counter() - started) * 1000))
        payload = _error_payload("VALIDATION_ERROR", str(exc))
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload,
            is_error=True,
        )
    except (ToolValidationError, ServiceError, BackendTimeoutError, BackendUnavailableError, BackendApiError, BackendInvalidResponseError) as exc:
        log_tool_call(logger, tool_name="rag_query", params=params, status="backend_error", duration_ms=int((time.perf_counter() - started) * 1000))
        payload = getattr(exc, "to_dict", lambda: _error_payload("INTERNAL_ERROR", str(exc)))()
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload,
            is_error=True,
        )
    except Exception as exc:  # pragma: no cover
        log_tool_call(logger, tool_name="rag_query", params=params, status="internal_error", duration_ms=int((time.perf_counter() - started) * 1000))
        payload = _error_payload("INTERNAL_ERROR", str(exc))
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload,
            is_error=True,
        )


app = server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True)
