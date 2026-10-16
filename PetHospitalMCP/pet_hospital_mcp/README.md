# Pet Hospital MCP Service

This project exposes the existing Go pet hospital REST API as an independent Python MCP service using the official SDK 2.x `MCPServer` and the modern `2026-07-28` protocol.

## Prerequisites

- Python 3.11+
- The Go pet hospital REST API must already be running on `http://127.0.0.1:8080` or another configured host.
- `mcp==2.0.0`

## Install

```bash
cd pet_hospital_mcp
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -U pip
python -m pip install -e .[dev]
```

## Start the Go REST API first

The MCP service is only an adapter. The upstream Go service must be started before the MCP server is used.

```bash
# from the Go project root
# starts on default http://127.0.0.1:8080
./pethospital
```

## Configure MCP and upstream URL

The MCP server reads these environment variables:

- `MCP_HOST` — default `127.0.0.1`
- `MCP_PORT` — default `9000`
- `PET_HOSPITAL_BASE_URL` — default `http://127.0.0.1:8080`

Example:

```bash
export MCP_HOST=127.0.0.1
export MCP_PORT=9000
export PET_HOSPITAL_BASE_URL=http://127.0.0.1:8080
```

## Start the MCP service

```bash
cd pet_hospital_mcp
python -m pet_hospital_mcp
```

Or with uvicorn directly:

```bash
cd pet_hospital_mcp
MCP_HOST=127.0.0.1 MCP_PORT=9000 PET_HOSPITAL_BASE_URL=http://127.0.0.1:8080 uvicorn pet_hospital_mcp.server:app --host 127.0.0.1 --port 9000
```

## MCP endpoints

- Streamable HTTP endpoint: `http://127.0.0.1:9000/mcp`
- Health check: `http://127.0.0.1:9000/health`

This server uses the official Python SDK `mcp==2.0.0` and the modern protocol revision `2026-07-28`.
It uses `MCPServer` and the stateless Streamable HTTP transport, without legacy `initialize` or `Mcp-Session-Id` session handling.

## Validate with MCP Inspector or a 2.x client

1. Start the Go API.
2. Start the MCP service.
3. Open MCP Inspector and connect to `http://127.0.0.1:9000/mcp`.
4. Send a `server/discover` probe with `MCP-Protocol-Version: 2026-07-28`.
5. Call the tool `list_pets`.

Example request using raw HTTP:

```bash
curl -s http://127.0.0.1:9000/health
```

```bash
curl -s -X POST http://127.0.0.1:9000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/call' \
  -H 'Mcp-Name: list_pets' \
  -d '{
    "jsonrpc":"2.0",
    "id":1,
    "method":"tools/call",
    "params":{
      "name":"list_pets",
      "arguments":{
        "page":1,
        "pageSize":10,
        "species":"犬",
        "status":"待就诊",
        "sortBy":"totalCost",
        "order":"desc"
      },
      "_meta":{
        "io.modelcontextprotocol/protocolVersion":"2026-07-28",
        "io.modelcontextprotocol/clientCapabilities":{},
        "io.modelcontextprotocol/clientInfo":{"name":"demo-client","version":"1.0.0"}
      }
    }
  }'
```

## Tool: list_pets

`list_pets` adapts `GET /api/v1/pets` and supports only these query arguments:

- `q`
- `name`
- `ownerName`
- `ownerPhone`
- `species`
- `doctor`
- `disease`
- `status`
- `min`
- `max`
- `sortBy`
- `order`
- `page`
- `pageSize`

It returns the backend `data` object, which contains:

- `items`
- `total`
- `page`
- `pageSize`
- `totalPages`
- `totalCost`

## Health check

```bash
curl -s http://127.0.0.1:9000/health
```

Example response:

```json
{"status":"ok","service":"pet_hospital_mcp"}
```

## Unit tests

```bash
cd pet_hospital_mcp
pytest -q
```

Expected result: all tests pass.

## Notes

- This project intentionally implements only the `list_pets` tool.
- It does not implement stage-two tools.
- All backend calls use HTTP timeouts and a small retry policy.
- Tool input validation rejects unknown fields, NaN, Infinity, and incorrect types.
