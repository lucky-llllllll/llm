"""斜杠命令注册表。

每个命令都带有简短的 description，供输入框上方的补全引导面板展示。
UI 类命令（ui=True）由 TUI 直接处理（需要弹窗/退出），
其余命令交给 Agent 执行并发回事件。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    description: str
    usage: str = ""
    ui: bool = False

    @property
    def usage_text(self) -> str:
        return self.usage or self.name


COMMANDS: tuple[Command, ...] = (
    Command("/help", "显示全部斜杠命令与用法", ui=True),
    Command("/new", "新建会话（自动保存当前会话）"),
    Command("/resume", "打开历史会话列表，上下键选择、回车恢复、Esc 取消", ui=True),
    Command("/save", "保存当前会话到 .agents/sessions/"),
    Command("/compact", "压缩当前上下文：归档原文 + 生成摘要 + 替换历史"),
    Command("/context", "查看上下文用量、归档目录与压缩次数"),
    Command("/mcp", "查看已连接的 MCP 服务器与可用工具"),
    Command("/skills", "列出 .agents/skills 中可用的技能"),
    Command("/tools", "列出全部 Function Calling 工具（含 MCP）"),
    Command("/config", "查看当前生效的配置项与配置文件路径"),
    Command("/clear", "清空当前会话的对话历史（保留会话编号）"),
    Command("/quit", "退出 Nana-Agent", ui=True),
)

_ALIASES: dict[str, str] = {
    "/exit": "/quit",
    "/q": "/quit",
    "/?": "/help",
    "/h": "/help",
    "/sessions": "/resume",
    "/history": "/resume",
}

_COMMAND_MAP: dict[str, Command] = {c.name: c for c in COMMANDS}


def all_commands() -> tuple[Command, ...]:
    return COMMANDS


def find_command(text: str) -> tuple[Command | None, str]:
    """解析一行输入，返回 (命令, 剩余参数)。不是命令时返回 (None, "")。"""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None, ""
    head, _, rest = stripped.partition(" ")
    head = head.lower()
    name = _ALIASES.get(head, head)
    command = _COMMAND_MAP.get(name)
    return command, rest.strip()


def match_commands(prefix: str) -> list[Command]:
    """按前缀匹配命令，用于补全面板（prefix 含前导 /）。"""
    prefix = prefix.strip().lower()
    if not prefix.startswith("/"):
        return []
    exact: list[Command] = []
    partial: list[Command] = []
    for command in COMMANDS:
        if command.name == prefix:
            exact.append(command)
        elif command.name.startswith(prefix):
            partial.append(command)
    # 完全匹配的排在最前面，方便回车直接执行
    return exact + partial


def help_text() -> str:
    """给 /help 弹窗与纯文本模式用的命令清单。"""
    width = max(len(c.usage_text) for c in COMMANDS)
    lines = ["可用斜杠命令：", ""]
    for command in COMMANDS:
        lines.append(f"  {command.usage_text.ljust(width)}   {command.description}")
    lines.append("")
    lines.append("提示：输入 / 会自动弹出补全引导面板，↑↓ 选择、Tab/回车补全、Esc 关闭。")
    return "\n".join(lines)


def completion_label(command: Command) -> str:
    """补全面板中每行的显示文本。"""
    return f"{command.name:<10} {command.description}"
