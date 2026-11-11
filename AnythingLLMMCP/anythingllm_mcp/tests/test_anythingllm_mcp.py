import json
from typing import Any

import httpx
import pytest

import anythingllm_mcp.server as server_module
from anythingllm_mcp.config import Settings
from anythingllm_mcp.server import app, rag_query_tool, upload_document_tool


@pytest.fixture
def api_settings(monkeypatch):
    replaced = Settings(anythingllm_api_key="test-key-12345678", default_chat_mode="query")
    monkeypatch.setattr(server_module, "settings", replaced)
    return replaced


WORKSPACES_PAYLOAD = {
    "workspaces": [
        {
            "id": 1,
            "slug": "ws-1",
            "name": "演示工作区",
            "documentCount": 2,
            "threadCount": 0,
            "chatMode": "query",
        }
    ]
}

UPLOAD_PAYLOAD = {
    "documents": [
        {"location": "custom-documents/demo-txt-abc123.json", "title": "demo.txt"}
    ]
}

CHAT_PAYLOAD = {
    "id": "chat-1",
    "type": "response",
    "textResponse": "文档中说 AnythingLLM 支持多工作区。",
    "sources": [{"title": "demo.txt", "chunk": "AnythingLLM 支持多工作区。"}],
    "close": True,
}


@pytest.mark.asyncio
async def test_list_workspaces_success(api_settings, monkeypatch):
    captured: dict[str, Any] = {}

    def fake_request(self, method, url, **kwargs):
        captured["method"] = method
        captured["url"] = str(url)
        captured["headers"] = dict(self.headers)
        return httpx.Response(200, json=WORKSPACES_PAYLOAD)

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    result = await server_module.server.call_tool("list_workspaces", {})
    assert result.is_error is False
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/api/v1/workspaces")
    lower_headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert lower_headers.get("authorization") == "Bearer test-key-12345678"


@pytest.mark.asyncio
async def test_upload_document_with_auto_embed(api_settings, monkeypatch, tmp_path):
    target = tmp_path / "demo.txt"
    target.write_text("AnythingLLM 支持多工作区。", encoding="utf-8")
    calls: list[dict[str, Any]] = []

    def fake_request(self, method, url, **kwargs):
        path = str(url)
        calls.append({"method": method, "url": path, "json": kwargs.get("json"), "files": kwargs.get("files")})
        if path.endswith("/api/v1/document/upload"):
            return httpx.Response(200, json=UPLOAD_PAYLOAD)
        if path.endswith("/api/v1/workspace/ws-1/update-embeddings"):
            return httpx.Response(200, json={"success": True, "message": "Embeddings updated"})
        return httpx.Response(404, json={"error": "unexpected path"})

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    result = await upload_document_tool(str(target), workspace_slug="ws-1")
    assert result.embedded is True
    assert result.workspace_slug == "ws-1"
    assert result.location == "custom-documents/demo-txt-abc123.json"
    assert calls[0]["method"] == "POST" and calls[0]["url"].endswith("/api/v1/document/upload")
    assert calls[1]["url"].endswith("/api/v1/workspace/ws-1/update-embeddings")
    assert calls[1]["json"] == {"adds": ["custom-documents/demo-txt-abc123.json"]}


@pytest.mark.asyncio
async def test_upload_document_missing_file(api_settings):
    result = await upload_document_tool("Z:/no/such/file.txt")
    assert result.is_error is True
    payload = result.structured_content
    assert payload["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_rag_query_forwards_message_and_mode(api_settings, monkeypatch):
    captured: dict[str, Any] = {}

    def fake_request(self, method, url, **kwargs):
        captured["method"] = method
        captured["url"] = str(url)
        captured["json"] = kwargs.get("json")
        return httpx.Response(200, json=CHAT_PAYLOAD)

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    result = await rag_query_tool("AnythingLLM 支持多工作区吗？", workspace_slug="ws-1", mode="query")
    assert result.answer.startswith("文档中说")
    assert result.workspace_slug == "ws-1"
    assert result.mode == "query"
    assert result.sources[0]["title"] == "demo.txt"
    assert captured["url"].endswith("/api/v1/workspace/ws-1/chat")
    assert captured["json"] == {"message": "AnythingLLM 支持多工作区吗？", "mode": "query"}


@pytest.mark.asyncio
async def test_rag_query_defaults_to_first_workspace(api_settings, monkeypatch):
    calls: list[str] = []

    def fake_request(self, method, url, **kwargs):
        path = str(url)
        calls.append(path)
        if path.endswith("/api/v1/workspaces"):
            return httpx.Response(200, json=WORKSPACES_PAYLOAD)
        if path.endswith("/chat"):
            return httpx.Response(200, json=CHAT_PAYLOAD)
        return httpx.Response(404, json={"error": "unexpected path"})

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    result = await rag_query_tool("测试")
    assert result.workspace_slug == "ws-1"
    assert calls[0].endswith("/api/v1/workspaces")
    assert calls[1].endswith("/api/v1/workspace/ws-1/chat")


@pytest.mark.asyncio
async def test_missing_api_key_is_validation_error(monkeypatch):
    monkeypatch.setattr(server_module, "settings", Settings(anythingllm_api_key=""))
    result = await server_module.server.call_tool("list_workspaces", {})
    assert result.is_error is True
    payload = result.structured_content
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert "ANYTHINGLLM_API_KEY" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_rag_query_invalid_mode():
    result = await rag_query_tool("hi", mode="completion")
    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_rag_query_backend_5xx(monkeypatch, api_settings):
    def fake_request(self, method, url, **kwargs):
        return httpx.Response(503, json={"error": "anythingllm down"})

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    result = await rag_query_tool("hi", workspace_slug="ws-1")
    payload = result.structured_content
    assert payload["error"]["code"] in {"BACKEND_API_ERROR", "BACKEND_UNAVAILABLE", "INTERNAL_ERROR"}


@pytest.mark.asyncio
async def test_rag_query_timeout(monkeypatch, api_settings):
    def timeout_request(self, method, url, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx.Client, "request", timeout_request)
    result = await rag_query_tool("hi", workspace_slug="ws-1")
    assert result.structured_content["error"]["code"] == "BACKEND_TIMEOUT"


@pytest.mark.asyncio
async def test_rag_query_invalid_json(monkeypatch, api_settings):
    def fake_request(self, method, url, **kwargs):
        return httpx.Response(200, text="not-json")

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    result = await rag_query_tool("hi", workspace_slug="ws-1")
    assert result.structured_content["error"]["code"] == "BACKEND_INVALID_RESPONSE"


@pytest.mark.asyncio
async def test_mcp_tool_registration_and_schema():
    tool_list = await server_module.server.list_tools()
    names = {tool.name for tool in tool_list}
    assert {"list_workspaces", "upload_document", "rag_query"} <= names

    rag = next(t for t in tool_list if t.name == "rag_query")
    schema = rag.input_schema
    assert "message" in schema["properties"]
    assert "workspace_slug" in schema["properties"]
    assert "mode" in schema["properties"]

    upload = next(t for t in tool_list if t.name == "upload_document")
    schema = upload.input_schema
    assert "file_path" in schema["properties"]
    assert "auto_embed" in schema["properties"]


@pytest.mark.asyncio
async def test_stateless_mcp_http_flow_and_health():
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:9001") as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json()["service"] == "anythingllm_mcp"

            discover_headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "server/discover",
            }
            discover_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": {},
                        "io.modelcontextprotocol/clientInfo": {"name": "pytest-client", "version": "1.0.0"},
                    }
                },
            }
            discover = await client.post("/mcp", headers=discover_headers, json=discover_payload)
            assert discover.status_code == 200, discover.text
            assert "Mcp-Session-Id" not in discover.headers
            body = discover.json()
            assert body["result"]["capabilities"] is not None

            call_headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/call",
                "Mcp-Name": "rag_query",
            }
            call_payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "rag_query",
                    "arguments": {"message": ""},
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": {},
                        "io.modelcontextprotocol/clientInfo": {"name": "pytest-client", "version": "1.0.0"},
                    },
                },
            }
            call = await client.post("/mcp", headers=call_headers, json=call_payload)
            assert call.status_code == 200, call.text
            assert "Mcp-Session-Id" not in call.headers
            result = call.json()
            assert result["result"]["isError"] is True
