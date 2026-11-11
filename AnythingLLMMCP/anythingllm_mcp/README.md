# AnythingLLM MCP

AnythingLLM 的 MCP（Model Context Protocol）适配服务，与 `PetHospitalMCP` 同构：Python + `mcp` + httpx + pydantic，通过 AnythingLLM 的 REST API（`http://127.0.0.1:3001/api/v1`）把三类能力暴露给任意 MCP 客户端。

## 工具列表

| 工具 | 上游接口 | 说明 |
| --- | --- | --- |
| `list_workspaces` | `GET /api/v1/workspaces` | 列出所有工作区（slug、名称、文档数、线程数、chatMode） |
| `upload_document` | `POST /api/v1/document/upload` + `POST /api/v1/workspace/{slug}/update-embeddings` | 上传本地文档并向量化到指定工作区（不传 slug 时默认第一个工作区） |
| `rag_query` | `POST /api/v1/workspace/{slug}/chat` | 检索问答；`mode=query` 仅基于已嵌入文档作答，`mode=chat` 允许模型自由发挥 |

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ANYTHINGLLM_API_KEY` | （必填） | AnythingLLM 后台生成的 Developer API Key |
| `ANYTHINGLLM_BASE_URL` | `http://127.0.0.1:3001` | AnythingLLM 服务地址 |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `9001` | MCP HTTP 服务监听地址 |
| `ANYTHINGLLM_TIMEOUT_SECONDS` | `30` | 上游请求超时 |
| `ANYTHINGLLM_MAX_RETRIES` | `2` | 408/429/5xx 重试次数 |
| `ANYTHINGLLM_DEFAULT_CHAT_MODE` | `query` | 未指定 mode 时 rag_query 的默认模式 |

API Key 只从环境变量读取，不要写进代码或前端页面提交到仓库。

## 启动

```bash
cd AnythingLLMMCP/anythingllm_mcp
pip install -e ".[dev]"
set ANYTHINGLLM_API_KEY=你的Key
python -m anythingllm_mcp
```

- MCP 端点：`http://127.0.0.1:9001/mcp`（streamable HTTP，无状态）
- 健康检查：`http://127.0.0.1:9001/health`

## 测试

```bash
cd AnythingLLMMCP/anythingllm_mcp
python -m pytest
```

测试通过 monkeypatch 模拟上游 REST，不需要本地起 AnythingLLM。
