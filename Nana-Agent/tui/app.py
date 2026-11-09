from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import datetime

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    OptionList,
    RichLog,
    Static,
)
from textual.widgets.option_list import Option

from core.agent import Agent, AgentEvent
from core.commands import (
    Command,
    all_commands,
    completion_label,
    find_command,
    help_text,
    match_commands,
)

MAX_SLASH_ROWS = 8


def _next_event(iterator: Iterator[AgentEvent]) -> AgentEvent | None:
    try:
        return next(iterator)
    except StopIteration:
        return None


# ---------------------------------------------------------------------------
# 弹窗
# ---------------------------------------------------------------------------


class CompactResultScreen(ModalScreen[None]):
    """上下文压缩对比浮动面板：左侧压缩前原文、右侧压缩后摘要，可滚动。"""

    BINDINGS = [("escape", "close_panel", "关闭")]

    CSS = """
    CompactResultScreen {
        align: center middle;
        background: $background 60%;
    }

    #compact-dialog {
        width: 94%;
        height: 88%;
        border: round $primary;
        background: $surface;
        padding: 0 1;
    }

    #compact-title {
        height: 1;
        text-style: bold;
        color: $text;
    }

    #compact-meta {
        height: 1;
        color: $text-muted;
    }

    #compact-columns {
        height: 1fr;
        margin-top: 1;
    }

    .compact-pane {
        width: 1fr;
        border: round $secondary;
        padding: 0 1;
    }

    #right-pane {
        margin-left: 1;
    }

    .pane-body {
        width: 1fr;
    }

    #compact-actions {
        height: 3;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    def __init__(self, payload: dict) -> None:
        super().__init__()
        self.payload = payload or {}

    def compose(self) -> ComposeResult:
        payload = self.payload
        before = str(payload.get("before_text") or "（没有可展示的历史消息）")
        after = str(payload.get("after_text") or "（空摘要）")
        before_tokens = int(payload.get("before_tokens") or 0)
        after_tokens = int(payload.get("after_tokens") or 0)
        meta = (
            f"触发方式: {payload.get('trigger', '-')}  |  "
            f"归档编号: {payload.get('archive_id', '-')}  |  "
            f"压缩前约 {before_tokens / 1000:.1f}k tokens → "
            f"压缩后约 {after_tokens / 1000:.1f}k tokens  |  "
            f"累计第 {payload.get('count', 1)} 次"
        )
        with Vertical(id="compact-dialog"):
            yield Static(
                Text("上下文压缩结果对比", style="bold"),
                id="compact-title",
            )
            yield Static(Text(meta), id="compact-meta")
            with Horizontal(id="compact-columns"):
                with VerticalScroll(id="left-pane", classes="compact-pane"):
                    # 用 Text 渲染，避免摘要中的 [xxx] 被当作 Rich 标签吞掉
                    yield Static(Text(before), classes="pane-body")
                with VerticalScroll(id="right-pane", classes="compact-pane"):
                    yield Static(Text(after), classes="pane-body")
            with Horizontal(id="compact-actions"):
                yield Button("关闭 (Esc)", id="close-btn", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#left-pane", VerticalScroll).border_title = (
            "压缩前 · 原始上下文"
        )
        self.query_one("#right-pane", VerticalScroll).border_title = (
            "压缩后 · 摘要"
        )
        self.query_one("#left-pane", VerticalScroll).focus()

    @on(Button.Pressed, "#close-btn")
    def _on_close(self) -> None:
        self.dismiss()

    def action_close_panel(self) -> None:
        self.dismiss()


class SessionListScreen(ModalScreen[str | None]):
    """历史会话列表面板：↑↓ 选择、回车恢复、Esc 取消。"""

    BINDINGS = [("escape", "cancel", "取消")]

    CSS = """
    SessionListScreen {
        align: center middle;
        background: $background 60%;
    }

    #session-dialog {
        width: 80%;
        height: 70%;
        border: round $primary;
        background: $surface;
        padding: 0 1;
    }

    #session-title {
        height: 1;
        text-style: bold;
    }

    #session-hint {
        height: 1;
        color: $text-muted;
    }

    #session-list {
        height: 1fr;
        margin-top: 1;
        background: $surface;
    }
    """

    def __init__(self, sessions: list) -> None:
        super().__init__()
        self.sessions = sessions

    def compose(self) -> ComposeResult:
        with Vertical(id="session-dialog"):
            yield Static(
                Text(f"历史会话（{len(self.sessions)} 个）", style="bold"),
                id="session-title",
            )
            yield Static(
                Text("↑↓ 选择 · 回车恢复 · Esc 取消", style="dim"),
                id="session-hint",
            )
            options = [
                Option(
                    Text(
                        f"{info.updated_at}  {info.title}",
                    )
                    + Text(f"   [{info.id}] {info.message_count} 条", style="dim"),
                    id=info.id,
                )
                for info in self.sessions
            ]
            yield OptionList(*options, id="session-list")

    def on_mount(self) -> None:
        self.query_one("#session-list", OptionList).focus()

    @on(OptionList.OptionSelected, "#session-list")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(str(event.option.id))

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    """斜杠命令帮助面板。"""

    BINDINGS = [("escape", "close_panel", "关闭")]

    CSS = """
    HelpScreen {
        align: center middle;
        background: $background 60%;
    }

    #help-dialog {
        width: 86%;
        height: 80%;
        border: round $primary;
        background: $surface;
        padding: 0 1;
    }

    #help-title {
        height: 1;
        text-style: bold;
    }

    #help-body {
        height: 1fr;
        margin-top: 1;
    }

    #help-actions {
        height: 3;
        align-horizontal: right;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Static(Text("斜杠命令帮助", style="bold"), id="help-title")
            with VerticalScroll(id="help-body"):
                yield Static(Text(help_text()))
            with Horizontal(id="help-actions"):
                yield Button("关闭 (Esc)", id="help-close", variant="primary")

    @on(Button.Pressed, "#help-close")
    def _on_close(self) -> None:
        self.dismiss()

    def action_close_panel(self) -> None:
        self.dismiss()


class ConfigNoticeScreen(ModalScreen[bool]):
    """config.ini 刚被创建时的提示对话框。"""

    BINDINGS = [("escape", "later", "稍后")]

    CSS = """
    ConfigNoticeScreen {
        align: center middle;
        background: $background 60%;
    }

    #config-dialog {
        width: 80%;
        height: auto;
        border: round $warning;
        background: $surface;
        padding: 1 2;
    }

    #config-title {
        height: auto;
        text-style: bold;
    }

    #config-body {
        height: auto;
        margin: 1 0;
    }

    #config-actions {
        height: 3;
        align-horizontal: right;
    }

    #config-actions Button {
        margin-left: 2;
    }
    """

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path

    def compose(self) -> ComposeResult:
        body = Text()
        body.append("首次启动：已自动创建配置文件\n\n", style="bold")
        body.append(f"{self.path}\n\n", style="bold cyan")
        body.append(
            "文件中的模型地址、模型名称、API Key 等都是占位（mock）信息，"
            "直接使用将无法正常工作。\n\n"
            "请修改以下至少一项后重启程序：\n", style=""
        )
        body.append(
            "  [llm] base_url / api_key / model\n"
            "  [search] baidu_qianfan_api_key（可选）\n"
            "  [xby] api_key（可选）\n\n",
            style="dim",
        )
        body.append("修改完成并重新启动后，本提示不再出现。", style="yellow")
        with Vertical(id="config-dialog"):
            yield Static(body, id="config-title")
            with Horizontal(id="config-actions"):
                yield Button("稍后再说", id="config-later")
                yield Button("退出程序", id="config-quit", variant="error")

    @on(Button.Pressed, "#config-quit")
    def _quit(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#config-later")
    def _later(self) -> None:
        self.dismiss(False)

    def action_later(self) -> None:
        self.dismiss(False)


# ---------------------------------------------------------------------------
# 主界面
# ---------------------------------------------------------------------------


class NanaApp(App[None]):
    TITLE = "Nana Agent"
    BINDINGS = [
        Binding("ctrl+q", "quit", "退出"),
        Binding("f2", "show_last_compact", "压缩对比"),
        Binding("f1", "show_help", "帮助"),
        # 补全面板打开时优先拦截方向键/回车/Tab/Esc
        Binding("up", "slash_prev", show=False, priority=True),
        Binding("down", "slash_next", show=False, priority=True),
        Binding("tab", "slash_accept", show=False, priority=True),
        Binding("enter", "slash_accept_or_submit", show=False, priority=True),
        Binding("escape", "slash_dismiss", show=False, priority=True),
    ]

    CSS = """
    #body {
        height: 1fr;
        layout: horizontal;
        padding: 0 1;
    }

    #chat-panel {
        width: 2fr;
        border: round $primary;
        padding: 0 1;
    }

    #sidebar {
        width: 1fr;
        margin-left: 1;
    }

    #mcp-panel {
        height: 1fr;
        border: round $secondary;
        padding: 0 1;
    }

    #mcp-body {
        width: 1fr;
    }

    #tool-panel {
        height: 1fr;
        border: round $secondary;
        margin-top: 1;
        padding: 0 1;
    }

    .message {
        width: 100%;
        margin-bottom: 1;
        padding: 0 1;
    }

    .user-message {
        background: $boost;
        border-left: thick $accent;
    }

    .assistant-message {
        background: $panel;
        border-left: thick $success;
    }

    .error-message {
        color: $error;
        border-left: thick $error;
    }

    .system-message {
        color: $text-muted;
    }

    .warning-message {
        color: $warning;
        border-left: thick $warning;
    }

    #slash-panel {
        height: 0;
        display: none;
        margin: 0 1;
        border: round $accent;
        background: $panel;
    }

    #status-bar {
        height: 1;
        margin: 0 1;
        color: $text-muted;
    }

    #prompt {
        margin: 1;
    }
    """

    def __init__(self, agent: Agent, config_created: bool = False) -> None:
        super().__init__()
        self.agent = agent
        self.config_created = config_created
        self.sub_title = f"model: {agent.llm.model}"
        self._busy = False
        self._streaming_message: Static | None = None
        self._streaming_text: list[str] = []
        self._slash_items: list[Command] = []
        self._slash_visible = False
        self._suppress_suggest = False
        self._mcp_error = ""

    # ---------------- 布局 ----------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with VerticalScroll(id="chat-panel"):
                yield Static(
                    Text("Nana Agent 已就绪。输入消息开始对话，输入 / 查看命令。"),
                    classes="message system-message",
                )
            with Vertical(id="sidebar"):
                with VerticalScroll(id="mcp-panel"):
                    yield Static(Text("正在加载 MCP…", style="dim"), id="mcp-body")
                yield RichLog(
                    id="tool-panel",
                    markup=False,
                    highlight=False,
                    wrap=True,
                    auto_scroll=True,
                )
        yield OptionList(id="slash-panel")
        yield Input(
            placeholder="输入消息，Enter 发送；/ 查看命令（↑↓ 选择，Tab/回车补全）",
            id="prompt",
        )
        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#chat-panel", VerticalScroll).border_title = "对话"
        self.query_one("#mcp-panel", VerticalScroll).border_title = "MCP 服务器与工具"
        tool_panel = self.query_one("#tool-panel", RichLog)
        tool_panel.border_title = "Function Calls"
        tool_panel.write(Text("等待工具调用…", style="dim"))

        self._start_session()
        self._refresh_status()
        self.query_one("#prompt", Input).focus()

        if self.config_created:
            self.push_screen(
                ConfigNoticeScreen(str(self.agent.cfg.path)),
                self._after_config_notice,
            )

        # MCP 异步加载：先进入界面，加载完成后刷新边栏
        self._load_mcp()

    def _start_session(self) -> None:
        info = self.agent.begin_session()
        hint = "，已注入 AGENTS.md" if info.get("agents_md") else ""
        self._mount_system_message(
            f"新会话 {info['id']}{hint}。输入 / 查看全部斜杠命令。"
        )

    def _after_config_notice(self, quit_now: bool | None) -> None:
        if quit_now:
            self.exit()
            return
        self._log_event("配置", "配置文件为自动生成的 mock 内容，请修改后重启", "bold yellow")

    # ---------------- MCP 侧边栏 ----------------

    @work(exclusive=True, group="mcp")
    async def _load_mcp(self) -> None:
        self._render_mcp_panel(loading=True)
        try:
            # 阻塞的连接过程放到线程里，避免卡住界面
            summary = await asyncio.to_thread(self.agent.load_mcp)
        except Exception as exc:
            self._mcp_error = f"{type(exc).__name__}: {exc}"
            self._render_mcp_panel()
            self._log_event("MCP", f"加载失败：{self._mcp_error}", "bold red")
            return
        self._render_mcp_panel(summary=summary)
        if summary:
            total_tools = sum(len(item["tools"]) for item in summary.values())
            self._log_event(
                "MCP",
                f"加载完成：{len(summary)} 个服务器，{total_tools} 个工具",
                "bold cyan",
            )
        else:
            self._log_event("MCP", "没有可用的 MCP 服务器", "bold yellow")

    def _render_mcp_panel(
        self,
        loading: bool = False,
        summary: dict | None = None,
    ) -> None:
        """把 MCP 服务器与工具列表渲染到边栏（支持异步加载完成后传入结果）。"""
        text = Text()
        if summary is None:
            summary = getattr(self.agent, "mcp_summary", {}) or {}
        if loading:
            text.append("正在后台连接 MCP 服务器…\n", style="bold yellow")
            text.append("界面可正常使用，加载完成后自动刷新。", style="dim")
            self._mcp_body.update(text)
            return
        if not summary:
            text.append("未连接 MCP 服务器\n\n", style="bold red")
            for name, detail in (self.agent.mcp_status or {}).items():
                text.append(f"{name}: {detail}\n", style="dim")
            text.append("\n配置方式：在 mcp.json 的 mcpServers 中添加服务器。", style="dim")
            self._mcp_body.update(text)
            return
        for name, item in sorted(summary.items()):
            ok = item["status"] == "ok"
            text.append("● " if ok else "○ ", style="green" if ok else "red")
            text.append(f"{name}", style="bold green" if ok else "bold red")
            text.append(f"  {len(item['tools'])} tools\n", style="dim")
            if not ok and item.get("error"):
                text.append(f"    {item['error']}\n", style="red")
            for tool in item["tools"]:
                text.append(f"    • {tool}\n", style="")
            text.append("\n")
        self._mcp_body.update(text)

    @property
    def _mcp_body(self) -> Static:
        return self.query_one("#mcp-body", Static)

    # ---------------- 斜杠命令补全 ----------------

    @property
    def _prompt(self) -> Input:
        return self.query_one("#prompt", Input)

    @property
    def _slash_panel(self) -> OptionList:
        return self.query_one("#slash-panel", OptionList)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """只在补全面板打开时接管方向键/回车/Tab/Esc。"""
        if action in ("slash_prev", "slash_next", "slash_accept", "slash_dismiss"):
            return self._slash_visible
        if action == "slash_accept_or_submit":
            if not self._slash_visible:
                return False
            selected = self._selected_command()
            # 已经精确输入完整命令时，回车应直接提交而不是补全
            return bool(selected) and self._prompt.value.strip() != selected.name
        return True

    def _selected_command(self) -> Command | None:
        panel = self._slash_panel
        index = panel.highlighted
        if index is None or not (0 <= index < len(self._slash_items)):
            return None
        return self._slash_items[index]

    @on(Input.Changed, "#prompt")
    def _on_prompt_changed(self, event: Input.Changed) -> None:
        # 补全写入的值不再触发面板，避免刚补全又弹出来
        if self._suppress_suggest:
            self._suppress_suggest = False
            self._hide_slash()
            return
        text = event.value
        stripped = text.strip()
        if not stripped.startswith("/") or " " in stripped:
            self._hide_slash()
            return
        matches = match_commands(stripped)
        if not matches:
            self._hide_slash()
            return
        self._slash_items = matches
        panel = self._slash_panel
        panel.clear_options()
        panel.add_options(
            [Option(completion_label(cmd), id=cmd.name) for cmd in matches]
        )
        panel.highlighted = 0
        panel.styles.height = min(len(matches), MAX_SLASH_ROWS)
        panel.styles.display = "block"
        self._slash_visible = True

    def _hide_slash(self) -> None:
        self._slash_visible = False
        self._slash_items = []
        panel = self._slash_panel
        panel.styles.display = "none"
        panel.styles.height = 0

    def _move_slash(self, delta: int) -> None:
        if not self._slash_items:
            return
        panel = self._slash_panel
        current = panel.highlighted or 0
        total = len(self._slash_items)
        panel.highlighted = (current + delta) % total

    def action_slash_prev(self) -> None:
        self._move_slash(-1)

    def action_slash_next(self) -> None:
        self._move_slash(1)

    def action_slash_accept(self) -> None:
        selected = self._selected_command()
        if selected is None:
            return
        self._hide_slash()
        self._suppress_suggest = True
        self._prompt.value = selected.name + " "
        self._prompt.cursor_position = len(self._prompt.value)
        self._prompt.focus()

    def action_slash_accept_or_submit(self) -> None:
        # check_action 已保证这里处于「未精确匹配」状态
        self.action_slash_accept()

    def action_slash_dismiss(self) -> None:
        self._hide_slash()
        self._prompt.focus()

    # ---------------- 输入与对话 ----------------

    @on(Input.Submitted, "#prompt")
    async def handle_prompt(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or self._busy:
            return

        self._hide_slash()
        event.input.value = ""

        # UI 类命令（需要弹窗/退出）由界面自己处理
        command, _ = find_command(text)
        if command is not None and command.ui:
            await self._mount_message("user", text)
            self._handle_ui_command(command)
            return

        event.input.disabled = True
        self._busy = True
        self._streaming_message = None
        self._streaming_text.clear()
        await self._mount_message("user", text)
        self._run_chat(text)

    def _handle_ui_command(self, command: Command) -> None:
        if command.name == "/quit":
            self._save_before_exit()
            self.exit()
            return
        if command.name == "/help":
            self.push_screen(HelpScreen())
            return
        if command.name == "/resume":
            sessions = self.agent.list_sessions()
            if not sessions:
                self._mount_system_message(
                    "还没有历史会话。会话会在每轮对话后自动保存到 "
                    f"{self.agent.sessions.directory}"
                )
                return
            self.push_screen(SessionListScreen(sessions), self._resume_selected)
            return

    def _save_before_exit(self, log: bool = True) -> None:
        """退出前保存当前会话，避免丢失最后一轮内容。"""
        try:
            self.agent.save_session()
            if log:
                self._log_event("会话", "退出前已保存当前会话", "bold cyan")
        except Exception as exc:
            if log:
                self._log_event("会话", f"退出前保存失败：{exc}", "bold red")

    def on_unmount(self) -> None:
        # 此时控件已卸载，不能再写日志面板
        self._save_before_exit(log=False)

    def _resume_selected(self, session_id: str | None) -> None:
        if not session_id:
            self._log_event("会话", "已取消恢复", "dim")
            return
        try:
            info = self.agent.resume_session(session_id)
        except Exception as exc:
            self._mount_system_message(f"恢复会话失败：{type(exc).__name__}: {exc}")
            return
        self._replay_history(info)

    @work(exclusive=True, group="chat")
    async def _replay_history(self, info: dict) -> None:
        """清空对话区并重放已恢复的会话消息。"""
        await self._clear_chat()
        await self._mount_message(
            "system",
            f"已恢复会话 {info.get('id')}（{info.get('message_count', 0)} 条消息）",
        )
        for message in self.agent.messages:
            role = message.get("role")
            if role == "system":
                continue
            content = message.get("content") or ""
            if role == "user":
                if content.startswith("以下是本项目 AGENTS.md"):
                    await self._mount_message(
                        "system", "（本项目 AGENTS.md 规则已随会话加载）"
                    )
                    continue
                await self._mount_message("user", content)
            elif role == "assistant":
                if content:
                    await self._mount_message("assistant", content)
                for call in message.get("tool_calls") or []:
                    name = (call.get("function") or {}).get("name", "?")
                    await self._mount_message("system", f"→ 调用工具 {name}")
            elif role == "tool":
                text = content if len(content) <= 500 else content[:500] + "…"
                await self._mount_message("system", f"工具返回: {text}")
        self._refresh_status()

    @work(exclusive=True, group="chat")
    async def _run_chat(self, text: str) -> None:
        try:
            iterator = self.agent.chat(text)
            while True:
                event = await asyncio.to_thread(_next_event, iterator)
                if event is None:
                    break
                await self._handle_agent_event(event)
        except Exception as exc:
            await self._handle_agent_event(
                AgentEvent(
                    "error",
                    text=f"请求失败：{type(exc).__name__}: {exc}",
                )
            )
        finally:
            self._finish_turn()

    async def _handle_agent_event(self, event: AgentEvent) -> None:
        if event.type == "text":
            await self._append_stream_text(event.text)
        elif event.type == "tool_start":
            self._streaming_message = None
            self._streaming_text.clear()
            self._log_tool_call(event)
        elif event.type == "tool_result":
            self._log_tool_result(event)
        elif event.type == "system":
            self._streaming_message = None
            self._streaming_text.clear()
            await self._mount_message("system", event.text)
            self._log_event("系统", event.text, "bold yellow")
            self._refresh_status()
        elif event.type == "warning":
            self._streaming_message = None
            self._streaming_text.clear()
            await self._mount_message("warning", event.text)
            self._log_event("警告", event.text, "bold yellow")
            self._refresh_status()
        elif event.type == "compact_done":
            self._streaming_message = None
            self._streaming_text.clear()
            payload = event.payload or {}
            self._log_event(
                "压缩",
                (
                    f"压缩完成，归档 {payload.get('archive_id', '-')}，"
                    f"{payload.get('before_tokens', 0)} → "
                    f"{payload.get('after_tokens', 0)} tokens，已打开对比面板"
                ),
                "bold cyan",
            )
            self.push_screen(CompactResultScreen(payload))
            self._refresh_status()
        elif event.type == "session":
            payload = event.payload or {}
            if payload.get("cleared"):
                await self._clear_chat()
                hint = (
                    "，已注入 AGENTS.md" if payload.get("agents_md") else ""
                )
                await self._mount_message(
                    "system",
                    f"已切换到会话 {payload.get('id')}{hint}",
                )
            self._refresh_status()
        elif event.type == "error":
            await self._mount_message("error", event.text)
            self._log_event("错误", event.text, "bold red")

    async def _append_stream_text(self, text: str) -> None:
        self._streaming_text.append(text)
        content = "".join(self._streaming_text)
        if self._streaming_message is None:
            self._streaming_message = await self._mount_message(
                "assistant",
                content,
            )
        else:
            self._streaming_message.update(self._render_message("assistant", content))
            self._chat_panel.scroll_end(animate=False)

    async def _mount_message(self, role: str, text: str) -> Static:
        widget = Static(
            self._render_message(role, text),
            classes=f"message {role}-message",
        )
        await self._chat_panel.mount(widget)
        self._chat_panel.scroll_end(animate=False)
        return widget

    def _mount_system_message(self, text: str) -> None:
        self.run_worker(self._mount_message("system", text), group="misc")

    async def _clear_chat(self) -> None:
        panel = self._chat_panel
        for child in list(panel.children):
            await child.remove()
        self._streaming_message = None
        self._streaming_text.clear()

    def _render_message(self, role: str, text: str) -> Text:
        label, style = {
            "user": ("You", "bold cyan"),
            "assistant": ("Nana", "bold green"),
            "error": ("Error", "bold red"),
            "system": ("System", "bold yellow"),
            "warning": ("⚠ 警告", "bold yellow"),
        }.get(role, (role.title(), "bold"))
        renderable = Text()
        renderable.append(f"{label}\n", style=style)
        renderable.append(text)
        return renderable

    def _log_tool_call(self, event: AgentEvent) -> None:
        arguments = json.dumps(
            event.arguments or {},
            ensure_ascii=False,
            default=str,
        )
        line = Text()
        line.append(f"[{self._timestamp()}] ", style="dim")
        line.append(event.tool or "?", style="bold yellow")
        line.append("(")
        line.append(arguments, style="cyan")
        line.append(")")
        self._tool_log.write(line)

    def _log_tool_result(self, event: AgentEvent) -> None:
        result = event.text or ""
        if len(result) > 1200:
            result = f"{result[:1200]}…（已截断）"
        line = Text()
        line.append(f"[{self._timestamp()}] ", style="dim")
        line.append(f"{event.tool or '?'} → ", style="bold green")
        line.append(result, style="white")
        self._tool_log.write(line)

    def _log_event(self, label: str, text: str, style: str) -> None:
        """把系统事件、MCP 加载、会话操作等写入 Function Calls 面板。"""
        message = (text or "").strip()
        if len(message) > 800:
            message = f"{message[:800]}…（已截断）"
        line = Text()
        line.append(f"[{self._timestamp()}] ", style="dim")
        line.append(f"{label} · ", style=style)
        line.append(message, style="white")
        try:
            self._tool_log.write(line)
        except Exception:
            # 界面正在卸载时写日志失败可忽略
            pass

    def _finish_turn(self) -> None:
        self._busy = False
        self._streaming_message = None
        self._streaming_text.clear()
        self._refresh_status()
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = False
        prompt.focus()

    def _refresh_status(self) -> None:
        """底部状态栏：会话编号 + 上下文用量（长度k / 窗口k / 百分比）。"""
        status = self.agent.get_context_status()
        used_k = status["used"] / 1000
        window_k = status["window"] / 1000
        session_id = self.agent.session_id or "-"
        text = (
            f"会话 {session_id}  |  上下文: {used_k:.1f}k / {window_k:.0f}k "
            f"({status['ratio'] * 100:.1f}%)"
        )
        if status["default"]:
            text += "（默认）"
        if status["blocked"]:
            text += "  ⚠ 已超 70% 阈值，普通输入已暂停，请执行 /compact"
        self.query_one("#status-bar", Static).update(text)

    # ---------------- 快捷键 ----------------

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_show_last_compact(self) -> None:
        """F2：重新打开最近一次压缩的对比面板。"""
        payload = getattr(self.agent.ctx, "last_compact", None)
        if payload:
            self.push_screen(CompactResultScreen(payload))
        else:
            self._log_event("压缩", "暂无可显示的压缩记录", "bold yellow")

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().strftime("%H:%M:%S")

    @property
    def _chat_panel(self) -> VerticalScroll:
        return self.query_one("#chat-panel", VerticalScroll)

    @property
    def _tool_log(self) -> RichLog:
        return self.query_one("#tool-panel", RichLog)


__all__ = [
    "NanaApp",
    "CompactResultScreen",
    "SessionListScreen",
    "HelpScreen",
    "ConfigNoticeScreen",
    "all_commands",
]
