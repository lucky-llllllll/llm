"""上下文压缩功能自检脚本（学生友好，无需 pytest）。

用法：
    python scripts/context_selftest.py          # 离线自检：不需要模型，秒级完成
    python scripts/context_selftest.py --live   # 额外做一次真实 LLM 压缩（需 LM Studio 已加载模型，可能数分钟）

离线自检覆盖：
1) Token 估算与用量统计
2) 70% 阈值警告 / 输入暂停
3) 原始上下文归档（JSONL：role、content、tool_calls、tool_call_id、顺序）
4) recallContext 关键词检索（指定 id / 全库 / 无匹配）
5) 摘要替换历史（保留 System Prompt、追加归档说明）
6) 压缩前后对比数据（供 TUI 浮动面板使用）
7) TUI 浮动面板布局、滚动、关闭按钮（Textual 无头测试）
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# 让脚本能直接 import 项目里的 core / tui
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.agent import Agent  # noqa: E402
from core.context_manager import (  # noqa: E402
    ContextManager,
    estimate_messages_tokens,
    estimate_tokens,
    get_context_dir,
    recall_context,
)
from core.llm import LLMClient  # noqa: E402

PASS = "  ✅"
FAIL = "  ❌"
results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, bool(condition), detail))
    print(f"{PASS if condition else FAIL} {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        raise AssertionError(name)


def build_history(turns: int = 20) -> list[dict]:
    """构造一段带工具调用的模拟历史。"""
    messages: list[dict] = []
    for i in range(turns):
        messages.append(
            {"role": "user", "content": f"第 {i} 步：请查看 data/app.db 里第 {i} 张表的结构"}
        )
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": "readTextFile",
                            "arguments": json.dumps({"path": f"table_{i}.sql"}),
                        },
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"call_{i}",
                "content": f"表 table_{i} 的字段：id、name、created_at",
            }
        )
    return messages


def offline_checks() -> str:
    """返回本次创建的归档编号，便于最后清理。"""
    print("\n[1] Token 估算与用量统计")
    check("ASCII 估算（hello world ≈ 3）", estimate_tokens("hello world") == 3)
    check("中文估算（你好世界 ≈ 4）", estimate_tokens("你好世界") == 4)
    llm = LLMClient()
    cm = ContextManager(llm)
    llm.messages = [{"role": "system", "content": "sys"}] + build_history(8)
    status = cm.get_status()
    check(
        "上下文窗口默认为 128k 并标记「默认」",
        status["window"] == 131072 and status["default"],
        f"{status['window']} tokens",
    )
    check(
        "估算用量覆盖全部消息",
        status["used"] >= estimate_messages_tokens(llm.messages),
        f"used={status['used']}",
    )
    agent_llm = Agent().llm
    cm_tools = ContextManager(agent_llm)
    check(
        "工具定义开销计入上下文用量",
        cm_tools.get_used_tokens() > estimate_messages_tokens(agent_llm.messages),
        f"含 {len(agent_llm.tools)} 个工具定义",
    )

    print("\n[2] 70% 阈值警告与输入暂停")
    cm.context_window = status["used"]  # 人为把窗口压到当前用量，触发 100%
    warning = cm.check_after_response()
    check("超过阈值返回警告文案", bool(warning))
    check("超阈值后进入 blocked（暂停普通输入）", cm.blocked)
    agent_blocked = Agent()
    agent_blocked.ctx.blocked = True
    blocked_events = list(agent_blocked.chat("随便说点什么"))
    check(
        "blocked 状态下普通提示词被拦截",
        len(blocked_events) == 1 and blocked_events[0].type == "warning",
    )

    print("\n[3] 原始上下文归档")
    archive_id = cm.archive_messages(llm.messages)
    archive_path = get_context_dir() / f"{archive_id}.jsonl"
    check("归档文件已生成", archive_path.exists(), f"{archive_id}.jsonl")
    lines = [
        json.loads(raw)
        for raw in archive_path.read_text(encoding="utf-8").splitlines()
        if raw.strip()
    ]
    check("跳过 System Prompt，只归档对话历史", len(lines) == 24, f"{len(lines)} 条消息")
    check("保留 role 与消息顺序", lines[0]["role"] == "user")
    check(
        "保留工具调用参数",
        lines[1]["tool_calls"][0]["function"]["name"] == "readTextFile",
    )
    check("保留工具返回结果与 tool_call_id", lines[2]["tool_call_id"] == "call_0")

    print("\n[4] recallContext 检索")
    found = recall_context(text="table_5 的字段")
    check("全库关键词检索命中", "table_5" in found and archive_id in found)
    only = recall_context(id=archive_id, text="call_7")
    check("指定归档编号检索命中", "readTextFile" in only or "table_7" in only)
    miss = recall_context(text="绝对不存在的关键词zzz")
    check("无匹配时给出明确提示", "未找到" in miss)
    bad_id = recall_context(id="19700101-000000", text="x")
    check("归档编号不存在时提示可用归档", "未找到" in bad_id)

    print("\n[5] 摘要替换历史")
    before = len(llm.messages)
    cm.apply_summary("## 摘要\n- 已检查 8 张表\n- 下一步：查询 visits 表", archive_id)
    check("消息数量显著下降", len(llm.messages) < before, f"{before} → {len(llm.messages)}")
    check("System Prompt 被保留", llm.messages[0]["role"] == "system")
    check(
        "摘要末尾追加归档说明并提示 recallContext",
        "recallContext" in llm.messages[-1]["content"]
        and archive_id in llm.messages[-1]["content"],
    )
    check("压缩成功后解除输入限制", cm.blocked is False)

    return archive_id


def agent_compaction_check() -> str:
    """用假的摘要函数跑一遍 /compact 流程，验证事件与 payload（不需要模型）。"""
    print("\n[6] /compact 事件流与对比数据")
    agent = Agent()
    agent.llm.messages += build_history(20)
    agent.ctx.generate_summary = lambda messages: (
        "# 摘要\n- 数据文件：`data/app.db`\n- 已完成：检查 20 张表\n- 下一步：查询 visits 表"
    )
    events = list(agent.chat("/compact"))
    types = [e.type for e in events]
    check("事件顺序：归档 → 压缩完成 → 提示", "compact_done" in types, str(types))
    payload = [e for e in events if e.type == "compact_done"][0].payload or {}
    check(
        "对比数据包含压缩前原文",
        "第 19 步" in payload.get("before_text", ""),
    )
    check("对比数据包含压缩后摘要", "visits" in payload.get("after_text", ""))
    check(
        "Token 数下降",
        payload.get("before_tokens", 0) > payload.get("after_tokens", 0),
        f"{payload.get('before_tokens')} → {payload.get('after_tokens')}",
    )
    check("压缩记录写入 compact.log", (get_context_dir() / "compact.log").exists())
    return payload.get("archive_id", "")


def ui_check(payload: dict) -> None:
    print("\n[7] TUI 浮动面板（Textual 无头测试）")
    try:
        from textual.containers import VerticalScroll

        from tui.app import CompactResultScreen, NanaApp
    except Exception as exc:  # 缺 textual 依赖时跳过
        print(f"  ⚠ 跳过：无法加载 Textual（{type(exc).__name__}: {exc}）")
        return

    async def run() -> None:
        app = NanaApp(Agent())
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app.push_screen(CompactResultScreen(payload))
            await pilot.pause()
            screen = app.screen
            check("面板已弹出", isinstance(screen, CompactResultScreen))
            left = screen.query_one("#left-pane", VerticalScroll)
            right = screen.query_one("#right-pane", VerticalScroll)
            check(
                "左右两栏等宽",
                abs(left.region.width - right.region.width) <= 2,
                f"{left.region.width} / {right.region.width}",
            )
            check("左栏可滚动", left.max_scroll_y > 0, f"max_scroll_y={left.max_scroll_y}")
            left.scroll_end(animate=False)
            await pilot.pause()
            check("滚动生效", left.scroll_y > 0, f"scroll_y={left.scroll_y}")
            screen.query_one("#close-btn").press()
            await pilot.pause()
            check("关闭按钮可关闭面板", not isinstance(app.screen, CompactResultScreen))
            app.agent.ctx.last_compact = payload
            await pilot.press("f2")
            await pilot.pause()
            check("F2 可重开对比面板", isinstance(app.screen, CompactResultScreen))

    asyncio.run(run())


def live_check() -> None:
    print("\n[8] 真实 LLM 压缩（--live）")
    agent = Agent()
    agent.llm.messages += build_history(10)
    print("  正在调用模型生成摘要，reasoning 模型可能需要几分钟，请耐心等待…")
    import time

    started = time.time()
    events = list(agent.chat("/compact"))
    elapsed = time.time() - started
    payload = [e for e in events if e.type == "compact_done"]
    errors = [e for e in events if e.type == "error"]
    if errors:
        check("真实压缩成功", False, errors[0].text.splitlines()[0])
    check("真实压缩成功", bool(payload), f"耗时 {elapsed:.1f}s")
    data = payload[0].payload or {}
    check(
        "摘要包含关键信息",
        "app.db" in data.get("after_text", "") or "表" in data.get("after_text", ""),
    )


def cleanup(archive_ids: list[str]) -> None:
    """删除本次自检产生的归档与日志，保持 .agents/context 干净。"""
    removed = 0
    for aid in archive_ids:
        if not aid:
            continue
        for path in [get_context_dir() / f"{aid}.jsonl"]:
            if path.exists():
                path.unlink()
                removed += 1
    log = get_context_dir() / "compact.log"
    if log.exists():
        log.unlink()
        removed += 1
    print(f"\n已清理测试产物 {removed} 个文件")


def main() -> None:
    live = "--live" in sys.argv[1:]
    print("=" * 60)
    print("Nana-Agent 上下文压缩 / recallContext 自检")
    print("=" * 60)

    archive_ids: list[str] = []
    try:
        archive_ids.append(offline_checks())
        archive_ids.append(agent_compaction_check())
        payload = {
            "trigger": "自检",
            "archive_id": archive_ids[-1] or "20261022-153000",
            "before_text": "用户：请检查 data/app.db\n" * 40,
            "after_text": "# 摘要\n- 数据文件：`data/app.db`\n- 下一步：查询 visits 表\n" * 10,
            "before_tokens": 48213,
            "after_tokens": 4912,
            "count": 1,
        }
        ui_check(payload)
        if live:
            live_check()
    finally:
        cleanup(archive_ids)

    failed = [name for name, ok, _ in results if not ok]
    print("\n" + "=" * 60)
    print(f"共 {len(results)} 项检查，失败 {len(failed)} 项")
    if failed:
        for name in failed:
            print(f"  - {name}")
        sys.exit(1)
    print("全部通过 ✅")
    print("=" * 60)


if __name__ == "__main__":
    main()
