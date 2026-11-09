"""通用 MCP 客户端桥：把外部 MCP 服务器接入 Nana-Agent。

- 从项目根目录 mcp.json 读取服务器配置（支持 stdio 与 HTTP 两种）
- 在后台线程中维护持久会话，对外暴露同步接口
- MCP 工具转换为 OpenAI function schema，命名格式 mcp__<server>__<tool>
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Any

from .config import get_config

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import (
        create_mcp_http_client,
        streamable_http_client,
    )

    MCP_SDK_AVAILABLE = True
except ImportError:  # 未安装 mcp 包时优雅降级
    MCP_SDK_AVAILABLE = False

_cfg = get_config()

MCP_CONFIG_PATH = _cfg.mcp_config_path
MCP_CONNECT_TIMEOUT = _cfg.mcp_connect_timeout  # npx 首次下载包可能较慢
MCP_CALL_TIMEOUT = _cfg.mcp_call_timeout
MCP_MAX_OUTPUT_CHARS = _cfg.mcp_max_output_chars


def _load_config() -> dict[str, dict[str, Any]]:
    try:
        with open(MCP_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[mcp] 配置文件读取失败 {MCP_CONFIG_PATH}: {exc}", file=sys.stderr)
        return {}
    servers = data.get("mcpServers") or {}
    return servers if isinstance(servers, dict) else {}


class MCPManager:
    """后台线程 + 事件循环，维持与各 MCP 服务器的持久会话。"""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._started = False
        self._stacks: dict[str, Any] = {}      # server -> AsyncExitStack
        self._sessions: dict[str, Any] = {}    # server -> ClientSession
        self._tools: dict[str, list[Any]] = {} # server -> list[Tool]
        self._configs: dict[str, dict[str, Any]] = {}  # server -> mcp.json 配置
        self._errors: dict[str, str] = {}      # server -> 错误信息
        self._status: dict[str, str] = {}      # server -> "ok" / "failed"

    # ---------- 后台事件循环 ----------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._started:
                return self._loop  # type: ignore[return-value]
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever,
                name="nana-mcp",
                daemon=True,
            )
            thread.start()
            self._loop = loop
            self._thread = thread
            self._started = True
            return loop

    def _submit(self, coro, timeout: float):
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout)

    # ---------- 连接 ----------

    async def _open_session(self, cfg: dict[str, Any], stack):
        """建立一次会话，返回 (session, tools)。调用方负责关闭 stack。"""
        if cfg.get("url"):
            # Connection: close 强制每个请求使用新连接，
            # 规避部分无状态 MCP 服务器在连接复用时的路由缺陷
            http_client = create_mcp_http_client(
                headers={"Connection": "close"}
            )
            http_ctx = await stack.enter_async_context(
                streamable_http_client(cfg["url"], http_client=http_client)
            )
            # 兼容不同 SDK 版本: 2.x 返回 (read, write)，1.x 返回 (read, write, get_session_id)
            if len(http_ctx) == 3:
                read, write, _ = http_ctx
            else:
                read, write = http_ctx
        else:
            params = StdioServerParameters(
                command=cfg["command"],
                args=list(cfg.get("args") or []),
                env=cfg.get("env") or None,
            )
            read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await asyncio.wait_for(session.initialize(), timeout=60)
        result = await asyncio.wait_for(session.list_tools(), timeout=60)
        return session, list(result.tools or [])

    async def _connect_one(self, name: str, cfg: dict[str, Any]) -> None:
        from contextlib import AsyncExitStack

        stack = AsyncExitStack()
        try:
            session, tools = await self._open_session(cfg, stack)
            self._configs[name] = cfg
            self._sessions[name] = session
            self._tools[name] = tools
            self._stacks[name] = stack
        except Exception:
            await stack.aclose()
            raise

    def connect_all(self) -> dict[str, str]:
        """连接 mcp.json 中配置的所有服务器，返回 {server: 状态}。"""
        status: dict[str, str] = {}
        for name, cfg in _load_config().items():
            try:
                self._submit(self._connect_one(name, cfg), MCP_CONNECT_TIMEOUT)
                status[name] = f"ok ({len(self._tools.get(name, []))} tools)"
                self._status[name] = "ok"
            except Exception as exc:
                self._errors[name] = f"{type(exc).__name__}: {exc}"
                status[name] = f"failed ({self._errors[name]})"
                self._status[name] = "failed"
        return status

    def get_summary(self) -> dict[str, dict[str, Any]]:
        """供 TUI 侧边栏展示：{server: {status, tools, error}}。"""
        summary: dict[str, dict[str, Any]] = {}
        names = sorted(set(self._configs) | set(self._status))
        for name in names:
            tools = [
                getattr(tool, "name", "") or "?"
                for tool in self._tools.get(name, [])
            ]
            summary[name] = {
                "status": self._status.get(name, "pending"),
                "tools": tools,
                "error": self._errors.get(name, ""),
            }
        return summary

    # ---------- 工具发现与调用 ----------

    def get_openai_tools(self) -> list[dict[str, Any]]:
        """把所有已连接服务器的 MCP 工具转换为 OpenAI function schema。"""
        schemas: list[dict[str, Any]] = []
        for name, tools in sorted(self._tools.items()):
            for tool in tools:
                # 兼容不同 SDK 版本的属性命名 (input_schema / inputSchema)
                params = (
                    getattr(tool, "input_schema", None)
                    or getattr(tool, "inputSchema", None)
                    or {"type": "object", "properties": {}}
                )
                description = (tool.description or "").strip()
                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": f"mcp__{name}__{tool.name}",
                            "description": (
                                f"[MCP:{name}] {description}"
                                or f"[MCP:{name}] {tool.name}"
                            )[:500],
                            "parameters": params,
                        },
                    }
                )
        return schemas

    def get_errors(self) -> dict[str, str]:
        return dict(self._errors)

    async def _call(self, server: str, tool: str, arguments: dict[str, Any]):
        cfg = self._configs.get(server)
        session = self._sessions.get(server)
        if cfg is None:
            raise RuntimeError(
                f"MCP 服务器 {server} 未连接。错误: "
                f"{self._errors.get(server, '未知')}"
            )
        if not cfg.get("url"):
            # stdio 服务器：进程常驻，直接用持久会话
            return await asyncio.wait_for(
                session.call_tool(tool, arguments=arguments),
                timeout=MCP_CALL_TIMEOUT,
            )
        # HTTP 无状态服务器：连接复用偶发 -32601 Not Found，
        # 改为每次调用用一次性会话（本地开销仅毫秒级）
        from contextlib import AsyncExitStack

        async with AsyncExitStack() as stack:
            fresh, _ = await self._open_session(cfg, stack)
            return await asyncio.wait_for(
                fresh.call_tool(tool, arguments=arguments),
                timeout=MCP_CALL_TIMEOUT,
            )

    def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any] | str | None,
    ) -> str:
        """同步调用 MCP 工具，返回文本结果。"""
        try:
            if isinstance(arguments, str):
                arguments = json.loads(arguments) if arguments.strip() else {}
            if not isinstance(arguments, dict):
                arguments = {}
            result = self._submit(
                self._call(server, tool, arguments),
                MCP_CALL_TIMEOUT + 5,
            )
            texts = []
            for item in getattr(result, "content", None) or []:
                text = getattr(item, "text", None)
                if text:
                    texts.append(text)
            if getattr(result, "isError", False):
                return "错误: MCP 工具执行失败: " + "\n".join(texts or ["无详情"])
            if not texts:
                return "MCP 工具执行成功（无文本输出）"
            output = "\n".join(texts)
            if len(output) > MCP_MAX_OUTPUT_CHARS:
                output = (
                    output[:MCP_MAX_OUTPUT_CHARS] + "…（MCP 输出过长，已截断）"
                )
            return output
        except asyncio.TimeoutError:
            return f"错误: MCP 工具调用超时（>{MCP_CALL_TIMEOUT:g}s）: {server}/{tool}"
        except Exception as exc:
            return f"错误: MCP 调用失败 ({server}/{tool}): {type(exc).__name__}: {exc}"


_manager: MCPManager | None = None
_manager_lock = threading.Lock()


def get_mcp_manager() -> MCPManager | None:
    """进程级单例。mcp 包未安装或无配置时返回 None。

    注意：首次调用会阻塞完成所有服务器的连接，
    在 TUI 中应放到后台线程里调用（见 Agent.load_mcp）。
    """
    global _manager
    if not MCP_SDK_AVAILABLE:
        return None
    if not _load_config():
        return None
    with _manager_lock:
        if _manager is None:
            manager = MCPManager()
            manager.connect_all()
            _manager = manager
        return _manager


def has_mcp_config() -> bool:
    """mcp.json 是否存在且配置了服务器（不建立连接）。"""
    return bool(_load_config())
