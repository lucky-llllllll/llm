from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from .commands import Command, all_commands, find_command, help_text
from .config import config_summary, get_config
from .context_manager import ContextManager, serialize_for_compact
from .llm import LLMClient, StreamChunk
from .mcp_client import get_mcp_manager, has_mcp_config
from .sessions import SessionStore, build_agents_md_message, read_agents_md
from .skill_loader import discover_skills
from .tools import (
    TOOLS,
    create_dir,
    create_file,
    delete_dir,
    delete_file,
    edit_text_file,
    get_cwd,
    get_date,
    get_file_type,
    is_leap_year,
    list_dir,
    read_text_file,
    recall_context_tool,
    rename_path,
    search_web,
    fetch_web,
    run_cli,
    load_skill,
    create_skill,
    set_xby_api_key,
    get_zodiac_info,
    get_daily_horoscope,
    get_compatibility,
    get_all_zodiacs,
    get_zodiac_by_date,
    get_rising_sign,
    get_rising_sign_info,
)

AgentEventType = Literal[
    "text",
    "tool_start",
    "tool_result",
    "done",
    "error",
    "warning",
    "system",
    "compact_done",
    "session",
]

# loadSkill 的 function 描述（补充进 agent 的工具 schema 数组）
LOAD_SKILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "loadSkill",
        "description": (
            "读取指定 skill 目录内的文件内容（如 SKILL.md 或其附属文件）。"
            "严格限制：只能访问 .agents/skills/<name>/ 目录内的相对路径文件，"
            "绝对路径、.. 路径穿越等越界访问一律拒绝"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "skill 名称，对应 .agents/skills 下的子文件夹名",
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "skill 目录内的相对文件路径，默认 SKILL.md，"
                        "例如 SKILL.md、scripts/run.py"
                    ),
                },
            },
            "required": ["name"],
        },
    },
}


def _compose_tools() -> list[dict[str, Any]]:
    """组装 agent 使用的工具 schema 数组，确保包含 loadSkill（去重）。"""
    tools = list(TOOLS)
    names = {tool["function"]["name"] for tool in tools}
    if "loadSkill" not in names:
        tools.append(LOAD_SKILL_SCHEMA)
    return tools


def _build_system_prompt() -> str:
    """扫描 .agents/skills，把可用 skill 列表拼接为 system prompt 片段。"""
    lines = [
        "重要规则：生成 Word(.docx)、Excel(.xlsx)、PPT(.pptx) 文档时，"
        "必须使用 officecli 技能中的命令（通过 RunCli 工具调用），"
        "禁止把纯文本内容直接写入这些后缀的文件。",
    ]
    skills = discover_skills()
    if not skills:
        return "\n".join(lines)
    lines.append("")
    lines.append(
        "你可以使用以下技能（技能详情通过 loadSkill 工具读取）："
    )
    for skill in skills:
        line = f"- {skill['name']}: {skill['description']}".rstrip(": ")
        lines.append(line)
    return "\n".join(lines)


@dataclass(slots=True)
class AgentEvent:
    type: AgentEventType
    text: str = ""
    tool: str | None = None
    arguments: dict[str, Any] | None = field(default=None)
    # 附加数据（如 compact_done 的压缩前后文本），供 UI 展示
    payload: dict[str, Any] | None = field(default=None)


def _parse_arguments(
    arguments: str | dict[str, Any] | None,
) -> dict[str, Any]:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, str):
        parsed = json.loads(arguments)
    else:
        parsed = arguments
    if not isinstance(parsed, dict):
        raise ValueError("工具参数必须是 JSON 对象")
    return parsed


def execute_tool(
    name: str,
    arguments: str | dict[str, Any] | None,
) -> str:
    try:
        args = _parse_arguments(arguments)

        # MCP 桥接工具: mcp__<server>__<tool>
        if name.startswith("mcp__"):
            try:
                _, server, tool = name.split("__", 2)
            except ValueError:
                return f"错误: 非法的 MCP 工具名: {name}"
            manager = get_mcp_manager()
            if manager is None:
                return "错误: MCP 未启用（缺少 mcp 包或 mcp.json 无配置）"
            return manager.call_tool(server, tool, args)

        if name == "getDate":
            result = get_date()
        elif name == "isLeapYear":
            year = int(args["year"])
            result = (
                f"{year} 年是闰年"
                if is_leap_year(year)
                else f"{year} 年不是闰年"
            )
        elif name == "getCwd":
            result = get_cwd()
        elif name == "listDir":
            result = list_dir(args.get("path", "."))
        elif name == "getFileType":
            result = get_file_type(args["path"])
        elif name == "readTextFile":
            result = read_text_file(
                args["path"],
                args.get("encoding", "utf-8"),
                args.get("offset", 1),
                args.get("limit"),
            )
        elif name == "createFile":
            result = create_file(
                args["directory"],
                args["filename"],
                args.get("content", ""),
            )
        elif name == "editTextFile":
            result = edit_text_file(
                args["path"],
                args["content"],
                args.get("append", False),
                args.get("encoding", "utf-8"),
            )
        elif name == "createDir":
            result = create_dir(args["path"])
        elif name == "deleteDir":
            result = delete_dir(
                args["path"],
                args.get("recursive", True),
            )
        elif name == "deleteFile":
            result = delete_file(args["path"])
        elif name == "renamePath":
            result = rename_path(args["source"], args["target"])
        elif name == "searchWeb":
            result = search_web(args.get("query", ""))
        elif name == "fetchWeb":
            result = fetch_web(args.get("url", ""))
        elif name == "RunCli":
            result = run_cli(args.get("command", ""), args.get("args"))
        elif name == "loadSkill":
            result = load_skill(args.get("name", ""), args.get("filename", "SKILL.md"))
        elif name == "createSkill":
            result = create_skill(args.get("name", ""), args.get("skillContext", ""))
        elif name == "setXbyApiKey":
            result = set_xby_api_key(args.get("api_key", ""))
        elif name == "getZodiacInfo":
            result = get_zodiac_info(args.get("zodiac", ""))
        elif name == "getDailyHoroscope":
            result = get_daily_horoscope(
                args.get("zodiac", ""), args.get("category")
            )
        elif name == "getCompatibility":
            result = get_compatibility(
                args.get("zodiac1", ""), args.get("zodiac2", "")
            )
        elif name == "getAllZodiacs":
            result = get_all_zodiacs()
        elif name == "getZodiacByDate":
            result = get_zodiac_by_date(args.get("month"), args.get("day"))
        elif name == "getRisingSign":
            result = get_rising_sign(
                args.get("birth_year"),
                args.get("birth_month"),
                args.get("birth_day"),
                args.get("birth_hour"),
                args.get("birth_minute"),
                args.get("latitude"),
                args.get("longitude"),
            )
        elif name == "getRisingSignInfo":
            result = get_rising_sign_info(args.get("rising_sign", ""))
        elif name == "recallContext":
            result = recall_context_tool(
                args.get("id"),
                args.get("text", ""),
            )
        else:
            result = f"未知工具: {name}"

        return result if isinstance(result, str) else str(result)
    except Exception as exc:
        return f"错误: {type(exc).__name__}: {exc}"


class Agent:
    def __init__(
        self,
        llm: LLMClient | None = None,
        max_tool_rounds: int | None = None,
    ) -> None:
        cfg = get_config()
        self.cfg = cfg
        self.llm = llm or LLMClient(tools=_compose_tools())
        if not self.llm.tools:
            self.llm.tools = _compose_tools()
        # 未显式传入时从 config.ini 读取
        self.max_tool_rounds = (
            cfg.max_tool_rounds if max_tool_rounds is None else max_tool_rounds
        )

        # MCP：不在构造时阻塞连接，改由 TUI 启动后台线程调用 load_mcp()
        self.mcp_status: dict[str, str] = {}
        self.mcp_summary: dict[str, dict[str, Any]] = {}
        self.mcp_loaded = False
        self._base_tool_count = len(self.llm.tools)

        self.system_prompt = _build_system_prompt()
        if self.system_prompt:
            self.llm.messages.insert(
                0,
                {"role": "system", "content": self.system_prompt},
            )

        # 上下文管理：Token 监控、归档、压缩、状态
        self.ctx = ContextManager(self.llm)

        # 会话：JSONL 事件日志 + AGENTS.md 一次性注入
        self.sessions = SessionStore()
        self.session_id = ""
        self.session_created_at = ""
        self.agents_md_injected = False
        self.auto_save = cfg.auto_save

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self.llm.messages

    def get_context_status(self) -> dict[str, Any]:
        """返回当前上下文用量状态，供 TUI 状态栏展示。"""
        return self.ctx.get_status()

    # ------------------------------------------------------------------
    # 会话管理
    # ------------------------------------------------------------------

    def begin_session(self) -> dict[str, Any]:
        """启动时建立新会话（含 AGENTS.md 一次性注入）。"""
        return self.new_session(save_current=False, announce=False)

    def new_session(
        self,
        save_current: bool = True,
        announce: bool = True,
    ) -> dict[str, Any]:
        """新建会话：先把当前会话落盘，再重置消息并注入 AGENTS.md。"""
        if save_current and self.session_id:
            self.save_session()

        self.session_id = self.sessions.new_id()
        self.session_created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.agents_md_injected = False
        self.ctx.blocked = False

        # 只保留 System Prompt
        system_msgs = [m for m in self.llm.messages if m.get("role") == "system"]
        if not system_msgs and self.system_prompt:
            system_msgs = [{"role": "system", "content": self.system_prompt}]
        self.llm.messages = system_msgs

        injected = self._inject_agents_md()
        # 立即落盘，确保会话一建立就存在于磁盘上
        self.save_session()

        return {
            "id": self.session_id,
            "agents_md": injected,
            "created_at": self.session_created_at,
            "announce": announce,
        }

    def _inject_agents_md(self) -> bool:
        """会话首次加载时把 AGENTS.md 作为一条消息追加到历史里。

        「一次注入，全程复用」：注入后消息始终留在历史中，
        并随会话事件日志持久化，后续轮次不再重复注入，
        从而保持前缀稳定、缓存有效。
        """
        if self.agents_md_injected:
            return False
        content = read_agents_md()
        self.agents_md_injected = True
        if not content:
            return False
        self.llm.messages.append(build_agents_md_message(content))
        return True

    def save_session(self) -> str:
        """把当前会话写入 .agents/sessions/<id>.jsonl，返回文件路径。"""
        if not self.session_id:
            self.session_id = self.sessions.new_id()
            self.session_created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        path = self.sessions.save(
            self.session_id,
            self.llm.messages,
            created_at=self.session_created_at,
            agents_md=self.agents_md_injected,
        )
        return str(path)

    def resume_session(self, session_id: str) -> dict[str, Any]:
        """恢复历史会话：先保存当前会话，再重放事件日志。"""
        if self.session_id and self.session_id != session_id:
            self.save_session()
        messages, meta = self.sessions.load(session_id)
        system_msgs = [m for m in self.llm.messages if m.get("role") == "system"]
        if not system_msgs and self.system_prompt:
            system_msgs = [{"role": "system", "content": self.system_prompt}]
        self.llm.messages = system_msgs + messages
        self.session_id = session_id
        self.session_created_at = str(
            meta.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        # 归档中已包含 AGENTS.md 消息时不再重复注入
        self.agents_md_injected = bool(meta.get("agents_md", True))
        self.ctx.blocked = False
        self.llm.last_usage = None
        return {
            "id": session_id,
            "message_count": len(messages),
            "title": meta.get("title", ""),
        }

    def list_sessions(self) -> list[Any]:
        return self.sessions.list()

    def clear_conversation(self) -> None:
        """清空当前会话的对话历史（保留会话编号与 System Prompt）。"""
        system_msgs = [m for m in self.llm.messages if m.get("role") == "system"]
        if not system_msgs and self.system_prompt:
            system_msgs = [{"role": "system", "content": self.system_prompt}]
        self.llm.messages = system_msgs
        self.agents_md_injected = False
        self.ctx.blocked = False
        self.llm.last_usage = None
        self._inject_agents_md()
        self.save_session()

    # ------------------------------------------------------------------
    # MCP（异步加载：由 TUI 在后台线程调用）
    # ------------------------------------------------------------------

    def load_mcp(self) -> dict[str, dict[str, Any]]:
        """连接 mcp.json 中所有服务器并注册工具，返回边栏摘要。

        该方法是阻塞的，调用方应放到后台线程（如 asyncio.to_thread），
        这样 TUI 可以先显示界面、加载完成后再刷新边栏。
        """
        if self.mcp_loaded:
            return self.mcp_summary
        summary: dict[str, dict[str, Any]] = {}
        try:
            if not has_mcp_config():
                self.mcp_loaded = True
                self.mcp_summary = {}
                self.mcp_status = {"（未配置）": "mcp.json 中没有 mcpServers"}
                return summary
            manager = get_mcp_manager()
        except Exception as exc:
            self.mcp_status = {"（初始化失败）": f"{type(exc).__name__}: {exc}"}
            self.mcp_loaded = True
            return summary
        if manager is None:
            self.mcp_status = {"（不可用）": "缺少 mcp 包或配置为空"}
            self.mcp_loaded = True
            return summary

        summary = manager.get_summary()
        self.mcp_summary = summary
        self.mcp_status = {
            name: ("ok" if item["status"] == "ok" else item.get("error", "failed"))
            for name, item in summary.items()
        }
        schemas = manager.get_openai_tools()
        if schemas:
            existing = {tool["function"]["name"] for tool in self.llm.tools}
            new_tools = [
                tool
                for tool in schemas
                if tool["function"]["name"] not in existing
            ]
            if new_tools:
                self.llm.tools = list(self.llm.tools) + new_tools
        self.mcp_loaded = True
        return summary

    def clear_history(self) -> None:
        self.llm.clear_history()
        # 清空对话后保留 system prompt（skill 列表）
        if self.system_prompt:
            self.llm.messages.append(
                {"role": "system", "content": self.system_prompt}
            )

    def _refresh_system_prompt(self) -> None:
        """每轮对话前重新扫描技能列表，新加的 skill 无需重启即可生效。"""
        prompt = _build_system_prompt()
        if prompt == self.system_prompt:
            return
        self.system_prompt = prompt
        messages = self.llm.messages
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = prompt
        else:
            messages.insert(0, {"role": "system", "content": prompt})

    def chat(self, user_input: str) -> Iterator[AgentEvent]:
        text = user_input.strip()
        if not text:
            return

        # 斜杠命令：统一交给命令分发器（/compact 也在其中）
        if text.startswith("/"):
            command, argument = find_command(text)
            if command is None:
                yield AgentEvent(
                    "error",
                    text=(
                        f"未知命令: {text.split()[0]}\n"
                        "输入 /help 查看全部命令，或输入 / 打开补全面板。"
                    ),
                )
                return
            yield from self.run_command(command, argument)
            return

        # 达到阈值后暂停普通提示词，直到压缩成功完成
        if self.ctx.blocked:
            yield AgentEvent(
                "warning",
                text=(
                    "当前上下文使用量已超过警告阈值，Nana-Agent 已暂停接受新的普通提示词。\n"
                    "请先执行 /compact 压缩上下文，压缩成功后即可继续对话。"
                ),
            )
            return

        self._refresh_system_prompt()
        self.llm.add_user_message(text)
        max_requests = max(1, self.max_tool_rounds + 1)

        for request_index in range(max_requests):
            final = StreamChunk()
            content_parts: list[str] = []

            for chunk in self.llm.run_round():
                if chunk.content:
                    content_parts.append(chunk.content)
                    yield AgentEvent("text", text=chunk.content)
                if chunk.tool_calls or chunk.finish_reason is not None:
                    final = chunk

            if not final.tool_calls:
                yield AgentEvent("done", text="".join(content_parts))
                self._auto_save()
                # LLM 响应结束后检查上下文用量，必要时警告并自动压缩
                yield from self._check_context_and_compact()
                return

            for call in final.tool_calls:
                if not call.get("id"):
                    call["id"] = f"call_{uuid.uuid4().hex[:12]}"
                call["type"] = "function"

                function = call.get("function")
                if not isinstance(function, dict):
                    function = {}
                    call["function"] = function
                name = function.get("name") or ""
                raw_arguments = function.get("arguments") or ""

                try:
                    arguments = _parse_arguments(raw_arguments)
                except (json.JSONDecodeError, ValueError):
                    arguments = {"_raw": raw_arguments}

                yield AgentEvent(
                    "tool_start",
                    tool=name,
                    arguments=arguments,
                )
                result = execute_tool(name, raw_arguments)
                self.llm.add_tool_message(call["id"], result)
                yield AgentEvent("tool_result", tool=name, text=result)

            if request_index == max_requests - 1:
                yield AgentEvent(
                    "error",
                    text=(
                        f"已达到最大工具调用轮数"
                        f"（{self.max_tool_rounds}），本轮已停止。"
                    ),
                )
                self._auto_save()
                yield from self._check_context_and_compact()
                return

        yield AgentEvent("done")
        yield from self._check_context_and_compact()

    def _auto_save(self) -> None:
        """每轮对话结束后自动保存会话（配置项 [session] auto_save）。"""
        if not self.auto_save or not self.session_id:
            return
        try:
            self.save_session()
        except Exception as exc:  # 保存失败不影响对话
            print(f"[session] 自动保存失败: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------
    # 斜杠命令分发（UI 类命令由 TUI 处理）
    # ------------------------------------------------------------------

    def run_command(
        self,
        command: Command,
        argument: str = "",
    ) -> Iterator[AgentEvent]:
        name = command.name

        if name == "/compact":
            yield from self._run_compact()
            return
        if name == "/new":
            info = self.new_session()
            hint = "，已注入 AGENTS.md" if info["agents_md"] else ""
            yield AgentEvent(
                "system",
                text=f"已新建会话: {info['id']}{hint}（原会话已自动保存）",
            )
            yield AgentEvent("session", payload={**info, "cleared": True})
            return
        if name == "/save":
            path = self.save_session()
            yield AgentEvent("system", text=f"会话已保存: {path}")
            return
        if name == "/clear":
            self.clear_conversation()
            yield AgentEvent("system", text="当前会话历史已清空（会话编号保持不变）。")
            yield AgentEvent(
                "session",
                payload={"id": self.session_id, "cleared": True, "agents_md": False},
            )
            return
        if name == "/context":
            yield AgentEvent("system", text=self.context_text())
            return
        if name == "/mcp":
            yield AgentEvent("system", text=self.mcp_text())
            return
        if name == "/skills":
            yield AgentEvent("system", text=self.skills_text())
            return
        if name == "/tools":
            yield AgentEvent("system", text=self.tools_text())
            return
        if name == "/config":
            yield AgentEvent("system", text=config_summary())
            return
        if name == "/help":
            yield AgentEvent("system", text=help_text())
            return
        # /resume 与 /quit 需要在界面中执行
        yield AgentEvent(
            "error",
            text=f"命令 {name} 需要在界面中执行（请直接在输入框输入）。",
        )

    # ---------- 各类信息文本 ----------

    def context_text(self) -> str:
        status = self.ctx.get_status()
        default_hint = "（默认）" if status["default"] else ""
        lines = [
            "上下文状态",
            f"  会话编号: {self.session_id or '-'}",
            f"  已用 tokens: {status['used']:,}",
            f"  上下文窗口: {status['window']:,}{default_hint}",
            f"  占用比例: {status['ratio'] * 100:.1f}%（阈值 {self.ctx.threshold:.0%}）",
            f"  是否暂停输入: {'是' if status['blocked'] else '否'}",
            f"  自动压缩: {'开启' if self.ctx.auto_compact else '关闭'}",
            f"  累计压缩次数: {self.ctx.compact_count}",
            f"  归档目录: {self.ctx.context_dir}",
            f"  压缩日志: {self.ctx.log_path}",
        ]
        if self.ctx.last_compact:
            last = self.ctx.last_compact
            lines.append(
                f"  最近一次压缩: {last['archive_id']}，"
                f"{last['before_tokens']} → {last['after_tokens']} tokens"
            )
        return "\n".join(lines)

    def mcp_text(self) -> str:
        if not self.mcp_loaded:
            return "MCP 仍在后台加载中，请稍后再试。"
        if not self.mcp_summary:
            detail = "\n".join(f"  {k}: {v}" for k, v in self.mcp_status.items())
            return "未连接任何 MCP 服务器。" + (f"\n{detail}" if detail else "")
        lines = ["MCP 服务器"]
        for name, item in sorted(self.mcp_summary.items()):
            ok = item["status"] == "ok"
            lines.append(
                f"  {'✅' if ok else '❌'} {name}（{len(item['tools'])} 个工具）"
            )
            if not ok and item.get("error"):
                lines.append(f"      错误: {item['error']}")
            for tool in item["tools"]:
                lines.append(f"      - {tool}")
        return "\n".join(lines)

    def skills_text(self) -> str:
        skills = discover_skills()
        if not skills:
            return "未发现可用技能（.agents/skills 目录为空）。"
        lines = [f"可用技能（{len(skills)} 个，用 loadSkill 读取详情）"]
        for skill in skills:
            lines.append(f"  - {skill['name']}: {skill['description']}".rstrip(": "))
        return "\n".join(lines)

    def tools_text(self) -> str:
        local: list[str] = []
        mcp: list[str] = []
        for tool in self.llm.tools:
            name = tool.get("function", {}).get("name", "")
            if not name:
                continue
            (mcp if name.startswith("mcp__") else local).append(name)
        lines = [f"Function Calling 工具（共 {len(local) + len(mcp)} 个）", ""]
        lines.append(f"本地工具（{len(local)}）:")
        lines.append("  " + ", ".join(sorted(local)))
        lines.append("")
        lines.append(f"MCP 工具（{len(mcp)}）:")
        lines.append("  " + (", ".join(sorted(mcp)) if mcp else "（未加载）"))
        return "\n".join(lines)

    def _check_context_and_compact(self) -> Iterator[AgentEvent]:
        """响应结束后检查上下文用量：
        超过阈值时先发出警告；若开启了 auto_compact 则立即自动压缩，
        压缩失败时保持 blocked，等待用户手动执行 /compact。
        """
        warning = self.ctx.check_after_response()
        if not warning:
            return
        yield AgentEvent("warning", text=warning)
        if self.ctx.auto_compact:
            yield from self._run_compact(trigger="70% 阈值自动压缩")

    # ------------------------------------------------------------------
    # /compact 上下文压缩流程
    # ------------------------------------------------------------------

    def _run_compact(self, trigger: str = "手动 /compact") -> Iterator[AgentEvent]:
        """压缩流程：归档 -> LLM 摘要 -> 替换历史 -> 解除输入限制。

        trigger 用于区分「手动 /compact」和「达到阈值自动压缩」。
        压缩期间只允许调用 LLM 生成摘要；任何一步失败都保留原上下文，
        用户可重新执行 /compact。
        """
        ctx = self.ctx
        if ctx.compacting:
            yield AgentEvent("error", text="上下文压缩正在进行中，请勿重复执行。")
            return

        ctx.compacting = True
        try:
            yield AgentEvent(
                "system",
                text=f"（{trigger}）开始压缩上下文：正在保存原始上下文归档…",
            )
            before_tokens = ctx.get_used_tokens()

            # 第 1 步：归档必须成功，才能继续压缩
            try:
                archive_id = ctx.archive_messages(self.messages)
            except Exception as exc:
                yield AgentEvent(
                    "error",
                    text=(
                        f"原始上下文归档保存失败，已保留原有上下文，未执行压缩：\n{exc}"
                    ),
                )
                return
            yield AgentEvent(
                "system",
                text=(
                    f"原始上下文已保存到 .agents/context/{archive_id}.jsonl，"
                    "正在调用 LLM 生成摘要（可能需要一些时间）…"
                ),
            )

            # 第 2 步：调用 LLM 生成摘要（仅此一次 LLM 调用，无工具）
            try:
                summary = ctx.generate_summary(self.messages)
            except Exception as exc:
                yield AgentEvent(
                    "error",
                    text=(
                        "摘要生成失败，已保留原有上下文，可重新执行 /compact：\n"
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
                return
            if not summary:
                yield AgentEvent(
                    "error",
                    text="模型返回了空摘要，已保留原有上下文，可重新执行 /compact。",
                )
                return

            # 第 3 步：用摘要替换历史（保留 System Prompt 与工具定义）
            # 替换前快照原文，供 UI 展示「压缩前 / 压缩后」对比
            original_text = serialize_for_compact(self.messages, max_chars=None)
            ctx.apply_summary(summary, archive_id)
            ctx.compact_count += 1
            after_tokens = ctx.get_used_tokens()
            # 持久化压缩日志（.agents/context/compact.log）
            ctx.log_compaction(archive_id, before_tokens, after_tokens, trigger)

            payload = {
                "trigger": trigger,
                "archive_id": archive_id,
                "before_text": original_text or "（没有可展示的历史消息）",
                "after_text": summary,
                "before_tokens": before_tokens,
                "after_tokens": after_tokens,
                "count": ctx.compact_count,
            }
            # 保存最近一次压缩结果（便于 UI 需要时重新打开）
            ctx.last_compact = payload
            yield AgentEvent("compact_done", text=summary, payload=payload)
            yield AgentEvent(
                "system",
                text=(
                    f"✅ 上下文压缩完成（第 {ctx.compact_count} 次，触发: {trigger}，"
                    f"归档编号 {archive_id}，"
                    f"{before_tokens} → {after_tokens} tokens）。"
                    "输入限制已解除，可以继续对话（按 F2 可重新查看压缩对比面板）。"
                ),
            )
            # 压缩后历史已变化，立即落盘
            self._auto_save()
            yield AgentEvent("done")
        finally:
            ctx.compacting = False
