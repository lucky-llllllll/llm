"""Nana-Agent 会话 / 命令 / 配置 / 边栏 自检脚本（离线运行，不需要模型）。

用法：
    python scripts/features_selftest.py

覆盖：
1) config.ini 缺失时自动生成 mock 模板、默认值回退、Key 脱敏
2) 会话：新建 / 自动保存 / 列表 / 恢复 / 清空
3) AGENTS.md：一次注入、持久化到事件日志、恢复不重复注入
4) 斜杠命令：注册表、别名、前缀匹配、未知命令
5) TUI：MCP 边栏（异步加载）、补全面板、会话列表弹窗、帮助弹窗、配置提醒、/new、/quit
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PASS = "  ✅"
FAIL = "  ❌"
results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, bool(condition), detail))
    print(f"{PASS if condition else FAIL} {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        raise AssertionError(name)


def prepare_env() -> Path:
    """把工作目录切到临时目录，避免污染项目里的 sessions / config。"""
    tmp = Path(tempfile.mkdtemp(prefix="nana-test-"))
    os.environ["NANA_CONFIG"] = str(tmp / "config.ini")
    os.chdir(tmp)
    return tmp


def widget_text(widget) -> str:
    content = getattr(widget, "content", "")
    return getattr(content, "plain", str(content))


# ---------------------------------------------------------------------------


def check_config(tmp: Path) -> None:
    print("\n[1] 配置文件自动生成与读取")
    from core.config import DEFAULTS, ensure_config, get_config

    created = ensure_config()
    cfg = get_config()
    check("首次启动自动创建 config.ini", created and cfg.path.exists(), str(cfg.path))
    text = cfg.path.read_text(encoding="utf-8")
    check("模板包含全部配置段", all(f"[{s}]" in text for s in DEFAULTS))
    check("模板使用 mock 模型名", "mock-model-name" in text)
    check("再次启动不会重复创建", ensure_config() is False)
    check("默认值回退生效", cfg.get_int("context", "recall_max_matches", 0) > 0)
    summary = __import__("core.config", fromlist=["config_summary"]).config_summary()
    check("配置摘要对 Key 脱敏", "YOUR_QIANFAN_API_KEY" not in summary and "***" in summary)


def check_sessions(tmp: Path) -> None:
    print("\n[2] 会话：新建 / 保存 / 列表 / 恢复 / 清空")
    from core.agent import Agent
    from core.sessions import SessionStore

    agent = Agent()
    agent.sessions = SessionStore(tmp / "sessions")
    info = agent.begin_session()
    check("新会话有唯一编号", bool(info["id"]), info["id"])

    agent.llm.messages.append({"role": "user", "content": "帮我检查数据库表结构"})
    agent.llm.messages.append({"role": "assistant", "content": "已读取 pets 表"})
    path = Path(agent.save_session())
    check("会话写入 .jsonl 文件", path.exists(), path.name)

    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    check("首行是 meta", lines[0]["type"] == "meta" and lines[0]["id"] == info["id"])
    check("标题取自首条用户消息", "数据库" in lines[0]["title"], lines[0]["title"])
    check("消息按顺序保存", lines[1]["role"] == "user" and lines[2]["role"] == "assistant")
    check("System Prompt 不落盘", all(line.get("role") != "system" for line in lines[1:]))

    restored = Agent()
    restored.sessions = SessionStore(tmp / "sessions")
    result = restored.resume_session(info["id"])
    check("恢复会话消息数一致", result["message_count"] == 2, str(result))
    check(
        "恢复后保留 System Prompt",
        restored.llm.messages[0]["role"] == "system",
    )
    check("会话列表可读取", any(s.id == info["id"] for s in restored.list_sessions()))

    before = len(restored.llm.messages)
    list(restored.chat("/clear"))
    check(
        "清空后只剩 System Prompt（+AGENTS.md）",
        len(restored.llm.messages) < before,
        f"{before} → {len(restored.llm.messages)}",
    )


def check_agents_md(tmp: Path) -> None:
    print("\n[3] AGENTS.md：一次注入、持久化、不重复注入")
    from core.agent import Agent
    from core.sessions import SessionStore

    work = tmp / "project"
    work.mkdir(exist_ok=True)
    (work / "AGENTS.md").write_text(
        "# 项目规则\n\n- 回答使用中文\n- 改动前先读文件\n", encoding="utf-8"
    )
    os.chdir(work)

    agent = Agent()
    agent.sessions = SessionStore(work / ".agents" / "sessions")
    info = agent.begin_session()
    check("首次加载注入 AGENTS.md", info["agents_md"])
    md_messages = [
        m for m in agent.messages if "AGENTS.md" in (m.get("content") or "")
    ]
    check("只注入一条消息", len(md_messages) == 1)
    check("消息包含规则原文", "回答使用中文" in md_messages[0]["content"])

    agent.save_session()
    path = agent.sessions.path_for(agent.session_id)
    check(
        "AGENTS.md 已持久化到事件日志",
        "AGENTS.md" in path.read_text(encoding="utf-8"),
    )
    meta = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    check("meta.agents_md 标记为真", meta.get("agents_md") is True)

    again = Agent()
    again.sessions = SessionStore(work / ".agents" / "sessions")
    again.resume_session(info["id"])
    count = len([m for m in again.messages if "AGENTS.md" in (m.get("content") or "")])
    check("恢复会话不会重复注入", count == 1, f"{count} 条")


def check_commands() -> None:
    print("\n[4] 斜杠命令")
    from core.commands import COMMANDS, find_command, help_text, match_commands

    names = [c.name for c in COMMANDS]
    for required in ("/new", "/resume", "/save", "/quit", "/compact", "/help"):
        check(f"注册了 {required}", required in names)
    check("命令均有说明文案", all(c.description for c in COMMANDS))

    cmd, arg = find_command("/save 当前进度")
    check("解析命令与参数", cmd is not None and cmd.name == "/save" and arg == "当前进度")
    check("别名解析", find_command("/exit")[0].name == "/quit")
    check("前缀匹配（/co）", [c.name for c in match_commands("/co")] == [
        "/compact",
        "/context",
        "/config",
    ])
    check("帮助文本包含全部命令", all(n in help_text() for n in names))
    check("非命令输入返回 None", find_command("你好")[0] is None)


def check_ui(tmp: Path) -> None:
    print("\n[5] TUI：边栏 / 补全面板 / 弹窗 / 命令")
    from textual.widgets import Input, OptionList

    from core.agent import Agent
    from core.sessions import SessionStore
    from tui.app import (
        ConfigNoticeScreen,
        HelpScreen,
        NanaApp,
        SessionListScreen,
    )

    agent = Agent()
    agent.sessions = SessionStore(tmp / "ui-sessions")
    agent.load_mcp = lambda: {
        "pet-hospital": {"status": "ok", "tools": ["query_visits"], "error": ""},
        "bazi": {"status": "failed", "tools": [], "error": "连接超时"},
    }

    async def run() -> None:
        app = NanaApp(agent)
        async with app.run_test(size=(150, 46)) as pilot:
            await pilot.pause(0.4)

            sidebar = widget_text(app.query_one("#mcp-body"))
            check("边栏显示服务器名", "pet-hospital" in sidebar)
            check("边栏显示工具列表", "query_visits" in sidebar)
            check("边栏显示连接错误", "连接超时" in sidebar)
            check("状态栏显示会话编号", agent.session_id in widget_text(app.query_one("#status-bar")))

            prompt = app.query_one("#prompt", Input)
            prompt.focus()
            await pilot.press("/")
            await pilot.pause()
            panel = app.query_one("#slash-panel", OptionList)
            check("输入 / 弹出补全面板", app._slash_visible and panel.option_count == len(
                [c for c in __import__("core.commands", fromlist=["COMMANDS"]).COMMANDS]
            ))
            await pilot.press("down")
            await pilot.pause()
            check("上下键切换高亮", panel.highlighted == 1, str(panel.highlighted))
            await pilot.press("tab")
            await pilot.pause()
            check("Tab 补全并关闭面板", not app._slash_visible and prompt.value.startswith("/"))

            prompt.value = ""
            await pilot.press("/", "h", "e", "l", "p")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause(0.3)
            check("/help 打开帮助面板", isinstance(app.screen, HelpScreen))
            await pilot.press("escape")
            await pilot.pause()

            prompt.value = ""
            await pilot.press("/", "r", "e", "s", "u", "m", "e")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause(0.4)
            check("/resume 打开会话列表", isinstance(app.screen, SessionListScreen))
            await pilot.press("escape")
            await pilot.pause()

            old_id = agent.session_id
            prompt.value = "/new"
            await pilot.press("enter")
            await pilot.pause(0.8)
            check("/new 切换了会话", agent.session_id != old_id)
            check("/new 清空了对话区", len(app.query_one("#chat-panel").children) <= 1)

        agent2 = Agent()
        agent2.sessions = SessionStore(tmp / "ui-sessions")
        agent2.load_mcp = lambda: {}
        app2 = NanaApp(agent2, config_created=True)
        async with app2.run_test(size=(120, 40)) as pilot2:
            await pilot2.pause(0.4)
            check("缺少配置时弹出提醒", isinstance(app2.screen, ConfigNoticeScreen))
            await pilot2.press("escape")
            await pilot2.pause()

    asyncio.run(run())


def main() -> None:
    print("=" * 62)
    print("Nana-Agent 会话 / 命令 / 配置 / 边栏 自检")
    print("=" * 62)
    tmp = prepare_env()
    try:
        check_config(tmp)
        check_sessions(tmp)
        check_agents_md(tmp)
        check_commands()
        check_ui(tmp)
    finally:
        os.chdir(PROJECT_ROOT)

    failed = [name for name, ok, _ in results if not ok]
    print("\n" + "=" * 62)
    print(f"共 {len(results)} 项检查，失败 {len(failed)} 项")
    if failed:
        for name in failed:
            print(f"  - {name}")
        sys.exit(1)
    print("全部通过 ✅")
    print("=" * 62)


if __name__ == "__main__":
    main()
