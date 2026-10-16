import json
import math
from typing import Any

import httpx
import pytest
from mcp.server import MCPServer

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.errors import BackendApiError
from pet_hospital_mcp.server import app, list_pets_tool


TEST_OK_PAYLOAD = {
    "items": [
        {
            "id": "PET-000001",
            "name": "旺财",
            "species": "犬",
            "ownerName": "张三",
            "ownerPhone": "13800001111",
            "doctor": "李医生",
            "disease": "急性肠胃炎",
            "status": "待就诊",
            "records": [],
            "charges": [],
            "totalCost": 0,
            "visitCount": 0,
        }
    ],
    "total": 1,
    "page": 1,
    "pageSize": 10,
    "totalPages": 1,
    "totalCost": 0,
}


@pytest.fixture
def backend_transport(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_request(self, method, url, **kwargs):
        captured["method"] = method
        captured["url"] = str(url)
        captured["params"] = kwargs.get("params")
        return httpx.Response(200, json={"code": 200, "message": "ok", "data": TEST_OK_PAYLOAD})

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    return captured


@pytest.mark.asyncio
async def test_list_pets_forwards_query_params(backend_transport):
    from pet_hospital_mcp.server import server

    result = await server.call_tool(
        "list_pets",
        {
            "q": "肠胃炎",
            "name": "旺财",
            "ownerName": "张三",
            "ownerPhone": "13800001111",
            "species": "犬",
            "doctor": "李医生",
            "disease": "急性肠胃炎",
            "status": "待就诊",
            "min": 100,
            "max": 500,
            "sortBy": "totalCost",
            "order": "desc",
            "page": 2,
            "pageSize": 10,
        },
    )
    assert result.is_error is False
    assert backend_transport["method"] == "GET"
    assert backend_transport["url"].endswith("/api/v1/pets")
    params = backend_transport["params"]
    assert params["q"] == "肠胃炎"
    assert params["name"] == "旺财"
    assert params["ownerName"] == "张三"
    assert params["ownerPhone"] == "13800001111"
    assert params["species"] == "犬"
    assert params["doctor"] == "李医生"
    assert params["disease"] == "急性肠胃炎"
    assert params["status"] == "待就诊"
    assert params["min"] == 100
    assert params["max"] == 500
    assert params["sortBy"] == "totalCost"
    assert params["order"] == "desc"
    assert params["page"] == 2
    assert params["pageSize"] == 10


@pytest.mark.asyncio
async def test_list_pets_validation_error():
    result = await list_pets_tool(page=0, pageSize=0)
    assert result.is_error is True
    payload = result.structured_content
    assert payload["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_list_pets_backend_4xx_errors(monkeypatch):
    from pet_hospital_mcp.server import server

    def fake_request(self, method, url, **kwargs):
        return httpx.Response(503, json={"code": 503, "message": "backend down"})

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    result = await server.call_tool("list_pets", {"page": 1, "pageSize": 10})
    payload = result.structured_content
    assert payload["error"]["code"] in {"BACKEND_API_ERROR", "BACKEND_UNAVAILABLE", "INTERNAL_ERROR"}


@pytest.mark.asyncio
async def test_list_pets_timeout_and_network_errors(monkeypatch):
    from pet_hospital_mcp.server import server

    def timeout_request(self, method, url, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx.Client, "request", timeout_request)
    result = await server.call_tool("list_pets", {"page": 1, "pageSize": 10})
    payload = result.structured_content
    assert payload["error"]["code"] == "BACKEND_TIMEOUT"

    def network_request(self, method, url, **kwargs):
        raise httpx.ConnectError("no backend")

    monkeypatch.setattr(httpx.Client, "request", network_request)
    result = await server.call_tool("list_pets", {"page": 1, "pageSize": 10})
    payload = result.structured_content
    assert payload["error"]["code"] == "BACKEND_UNAVAILABLE"


@pytest.mark.asyncio
async def test_list_pets_invalid_json(monkeypatch):
    def fake_request(self, method, url, **kwargs):
        return httpx.Response(200, text="not-json")

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    result = await list_pets_tool(page=1, pageSize=10)
    payload = result.structured_content
    assert payload["error"]["code"] == "BACKEND_INVALID_RESPONSE"


@pytest.mark.asyncio
async def test_mcp_tool_registration_and_schema():
    tools = await app if False else None
    # direct MCPServer check
    from pet_hospital_mcp.server import server

    tool_list = await server.list_tools()
    names = {tool.name for tool in tool_list}
    assert "list_pets" in names
    tool = next(t for t in tool_list if t.name == "list_pets")
    schema = tool.input_schema
    assert "page" in schema["properties"]
    assert "pageSize" in schema["properties"]
    assert "species" in schema["properties"]
    assert "status" in schema["properties"]
    assert "sortBy" in schema["properties"]
    assert "order" in schema["properties"]
    assert schema["properties"]["page"]["minimum"] == 1
    assert schema["properties"]["pageSize"]["minimum"] == 1
    assert schema["properties"]["pageSize"]["maximum"] == 500


@pytest.mark.asyncio
async def test_stateless_mcp_http_flow_and_health():
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:9000") as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json()["service"] == "pet_hospital_mcp"

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
                "Mcp-Name": "list_pets",
            }
            call_payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "list_pets",
                    "arguments": {"page": 1, "pageSize": 10},
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": {},
                        "io.modelcontextprotocol/clientInfo": {"name": "pytest-client", "version": "1.0.0"},
                    },
                },
            }
            with pytest.MonkeyPatch.context() as m:
                def fake_request(self, method, url, **kwargs):
                    return httpx.Response(200, json={"code": 200, "message": "ok", "data": TEST_OK_PAYLOAD})

                m.setattr(httpx.Client, "request", fake_request)
                call = await client.post("/mcp", headers=call_headers, json=call_payload)
            assert call.status_code == 200, call.text
            assert "Mcp-Session-Id" not in call.headers
            result = call.json()
            assert result["result"]["isError"] is False or result["result"].get("isError") is False
