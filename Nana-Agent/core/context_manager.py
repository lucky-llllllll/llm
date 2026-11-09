"""上下文管理模块：Token 用量监控、原始上下文归档、/compact 压缩、recallContext 检索。

设计原则（保持简单，便于理解与维护）：
- 不使用向量数据库 / Embedding，检索采用本地全文关键词匹配。
- 归档文件为 JSONL，每行一条原始消息（保留 role / content / tool_calls / tool_call_id）。
- 压缩 = 归档成功后 -> 调 LLM 生成摘要 -> 用摘要替换会话历史（保留 System Prompt）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import get_config
from .llm import LLMClient, LLMError

# 项目根目录（core/ 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 上下文归档目录：当前工作目录下的 .agents/context
CONTEXT_DIR = Path.cwd() / ".agents" / "context"

_cfg = get_config()

# 默认上下文窗口：无法从配置 / API 获取时使用（默认 128k），并在 UI 标注「默认」
DEFAULT_CONTEXT_WINDOW = _cfg.default_window_tokens

# 使用量达到窗口的该比例时发出警告并暂停新提示词
CONTEXT_WARNING_RATIO = _cfg.warn_ratio

# recallContext 检索参数
RECALL_MAX_MATCHES = _cfg.recall_max_matches   # 最多返回的匹配片段数
RECALL_SNIPPET_BEFORE = 100    # 匹配位置前保留的字符数
RECALL_SNIPPET_AFTER = 300     # 匹配位置后保留的字符数
RECALL_MAX_CHARS = _cfg.recall_max_chars        # 检索结果总长度上限，避免撑爆上下文

# 压缩时送入 LLM 的历史消息单条截断上限（归档中仍是完整内容）
COMPACT_MSG_MAX_CHARS = _cfg.compact_msg_max_chars

# 压缩请求的超时时间（秒）。压缩长上下文时本地模型可能要跑几分钟
COMPACT_TIMEOUT = _cfg.compact_timeout

# 上下文压缩专用 Prompt
COMPACT_PROMPT = """你是一名 AI Agent 的上下文管理助手。当前会话即将达到上下文窗口限制，需要将历史对话压缩为一份高质量的工作摘要，以便 Agent 在新的上下文中继续执行任务。

请认真阅读提供的历史上下文，提取对后续工作仍然有价值的信息，并生成一份约 5,000 Tokens 的 Markdown 摘要。

摘要应重点保留以下内容：

1. **用户目标与要求**：用户希望完成什么任务？有哪些必须遵守的约束、偏好和验收条件？
2. **已经完成的工作**：哪些任务已经完成？实现了哪些功能？获得了哪些已经验证的结果？
3. **当前工作状态**：Agent 正在处理什么任务？当前进展到哪一步？是否存在尚未解决的问题？
4. **关键技术信息**：保留重要的文件路径、函数名称、接口定义、配置项、执行命令、错误信息和必要的代码片段。
5. **关键决策与原因**：记录用户和 Agent 已经确定的技术方案，以及重要的设计取舍。
6. **下一步任务**：明确接下来应该执行的操作，以及需要继续验证的事项。

请遵守以下要求：

- 优先保留对后续任务有直接影响的信息，删除寒暄、重复讨论、过时的尝试和无关内容。
- 对已经验证的事实与尚未验证的推测作出明确区分。
- 不要将计划完成的任务描述为已经完成。
- 重要的文件路径、函数名称、配置名称和命令应保留准确原文。
- 对失败的尝试，只保留有助于避免重复错误的信息。
- 如果信息存在冲突，应优先保留用户最新确认的要求，并简要说明仍然存在的冲突。
- 如果某些历史细节无法在摘要中完整保留，可简要说明它们已保存到原始上下文归档中，后续可以通过 `recallContext` 工具重新检索。
- 使用清晰的 Markdown 结构输出摘要，不要附加解释或无关内容。

**最终目标：使一个从未阅读过原始对话的 Agent，仅凭这份摘要，就能够理解当前任务、继承关键决策，并继续开展工作。**"""


def get_context_dir() -> Path:
    """返回归档目录，并确保其存在。"""
    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    return CONTEXT_DIR


# ---------------------------------------------------------------------------
# Token 估算
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """粗略估算一段文本的 Token 数。

    规则（够用即可，不追求精确）：
    - CJK 等全角字符按约 1 字符 ≈ 1 Token；
    - 其余（ASCII 等）按约 4 字符 ≈ 1 Token。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """按消息列表估算 Token（包含对话历史、工具调用参数与工具返回结果）。"""
    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif content:
            total += estimate_tokens(json.dumps(content, ensure_ascii=False))
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            total += estimate_tokens(json.dumps(tool_calls, ensure_ascii=False))
        total += 4  # 每条消息的固定开销（role 等元数据）
    return total


def _load_context_window() -> tuple[int, bool]:
    """从 config.ini [context] window_tokens 读取上下文窗口大小。

    返回 (窗口大小, 是否为默认值)。
    """
    config = get_config()
    value = config.context_window
    if value and value > 0:
        return value, False
    return config.default_window_tokens, True


def _load_auto_compact() -> bool:
    """从 config.ini [context] auto_compact 读取是否自动压缩，默认开启。"""
    return get_config().auto_compact


# ---------------------------------------------------------------------------
# ContextManager
# ---------------------------------------------------------------------------

class ContextManager:
    """负责上下文用量监控、归档、压缩与状态维护。"""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm
        self.context_dir = get_context_dir()
        self.context_window, self.window_is_default = _load_context_window()
        self.auto_compact = _load_auto_compact()
        self.threshold = CONTEXT_WARNING_RATIO
        # 达到阈值后置为 True：暂停接受新的普通用户提示词，直到压缩成功
        self.blocked = False
        # 压缩进行中的标志（压缩期间不接受其他任务）
        self.compacting = False
        self.compact_count = 0
        # 最近一次压缩的对比结果（压缩前原文 / 压缩后摘要），供 UI 展示
        self.last_compact: dict[str, Any] | None = None
        # 压缩日志（每次压缩追加一行，便于回溯）
        self.log_path = self.context_dir / "compact.log"

    # ---------------- 用量监控 ----------------

    def get_used_tokens(self) -> int:
        """当前上下文 Token 用量：优先用模型 API 返回的 usage，否则估算。"""
        usage = getattr(self.llm, "last_usage", None)
        if usage:
            prompt_tokens = usage.get("prompt_tokens")
            if prompt_tokens is not None:
                completion = usage.get("completion_tokens") or 0
                return int(prompt_tokens) + int(completion)
        # 估算：对话历史 + System Prompt（已在 messages 里）+ 工具定义开销
        total = estimate_messages_tokens(self.llm.messages)
        tools = getattr(self.llm, "tools", None)
        if tools:
            total += estimate_tokens(json.dumps(tools, ensure_ascii=False))
        return total

    def get_status(self) -> dict[str, Any]:
        used = self.get_used_tokens()
        window = self.context_window
        return {
            "used": used,
            "window": window,
            "ratio": (used / window) if window > 0 else 0.0,
            "default": self.window_is_default,
            "blocked": self.blocked,
        }

    def check_after_response(self) -> str | None:
        """每次 LLM 响应结束后检查用量，达到阈值则返回警告文案并暂停输入。"""
        status = self.get_status()
        if status["ratio"] >= self.threshold:
            self.blocked = True
            if self.auto_compact:
                action = "Nana-Agent 将在本轮结束后自动压缩上下文。"
            else:
                action = (
                    "Nana-Agent 已暂停接受新的普通提示词，"
                    "请执行 /compact 压缩上下文后再继续。"
                )
            return (
                f"⚠ 上下文使用量已达 {status['used'] / 1000:.1f}k / "
                f"{status['window'] / 1000:.0f}k（{status['ratio'] * 100:.1f}%），"
                f"超过 {self.threshold:.0%} 警告阈值。\n{action}"
            )
        return None

    # ---------------- 压缩日志 ----------------

    def log_compaction(
        self,
        archive_id: str,
        before_tokens: int,
        after_tokens: int,
        trigger: str,
    ) -> None:
        """把一次压缩的摘要信息追加到 .agents/context/compact.log。"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = (
            f"{timestamp} | {trigger} | 归档 {archive_id} | "
            f"{before_tokens} -> {after_tokens} tokens | "
            f"累计第 {self.compact_count} 次\n"
        )
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass  # 日志失败不影响压缩本身

    # ---------------- 原始上下文归档 ----------------

    def archive_messages(self, messages: list[dict[str, Any]]) -> str:
        """把需要压缩的历史消息完整保存为 JSONL 归档，返回归档编号。

        - 跳过 System Prompt（压缩后仍会保留，无需归档）；
        - 保留 role、content、tool_calls、tool_call_id 与消息顺序；
        - 先写临时文件再重命名，保证归档完整落盘。
        失败时抛出异常，调用方必须放弃压缩并保留原上下文。
        """
        archive_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.context_dir / f"{archive_id}.jsonl"
        tmp = self.context_dir / f"{archive_id}.jsonl.tmp"
        count = 0
        try:
            with tmp.open("w", encoding="utf-8") as f:
                for msg in messages:
                    if msg.get("role") == "system":
                        continue
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                    count += 1
                f.flush()
            tmp.replace(target)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        if count == 0:
            # 没有任何可归档内容（例如刚清空历史），视为无需压缩
            target.unlink(missing_ok=True)
            raise RuntimeError("当前会话没有可归档的历史消息，无需压缩。")
        return archive_id

    # ---------------- 调用 LLM 压缩 ----------------

    def generate_summary(self, messages: list[dict[str, Any]]) -> str:
        """用压缩专用 Prompt 调用 LLM，生成约 5,000 Tokens 的工作摘要。"""
        transcript = serialize_for_compact(messages)
        prompt = (
            COMPACT_PROMPT
            + "\n\n---\n以下是本会话需要压缩的完整历史上下文：\n\n"
            + transcript
        )
        compact_messages = [{"role": "user", "content": prompt}]
        # 仅调用 LLM 生成摘要，不允许任何工具调用
        summary = self.llm.complete(compact_messages, timeout=COMPACT_TIMEOUT)
        return summary.strip()

    def apply_summary(self, summary: str, archive_id: str) -> None:
        """用压缩摘要替换会话历史：保留 System Prompt 与工具定义。"""
        archived_at = datetime.strptime(archive_id, "%Y%m%d-%H%M%S")
        timestamp = archived_at.strftime("%Y-%m-%d %H:%M:%S")
        note = (
            f"\n\n---\n此前上下文已于 {timestamp} 完成压缩，"
            f"原始上下文保存在编号为 {archive_id} 的归档文件中。"
            "如需查找历史信息，请使用 recallContext 工具。"
        )
        system_msgs = [m for m in self.llm.messages if m.get("role") == "system"]
        summary_msg = {"role": "user", "content": summary + note}
        self.llm.messages = system_msgs + [summary_msg]
        # 用量信息已失效，清空后回到估算模式
        self.llm.last_usage = None
        self.blocked = False


# ---------------------------------------------------------------------------
# 压缩用序列化
# ---------------------------------------------------------------------------

def serialize_for_compact(
    messages: list[dict[str, Any]],
    max_chars: int | None = COMPACT_MSG_MAX_CHARS,
) -> str:
    """把历史消息序列化为可读文本。

    - max_chars=COMPACT_MSG_MAX_CHARS：用于送给 LLM 压缩，超长内容截断以免请求本身爆窗；
    - max_chars=None：用于 UI 展示压缩前原文（不截断）。
    归档里保存的始终是完整原始内容。
    """
    lines: list[str] = []
    for index, msg in enumerate(messages, 1):
        role = msg.get("role", "?")
        if role == "system":
            continue
        lines.append(f"### 消息 {index} [role={role}]")
        if role == "assistant" and msg.get("tool_calls"):
            for call in msg["tool_calls"]:
                function = call.get("function") or {}
                lines.append(
                    f"工具调用 {function.get('name', '?')}，参数: "
                    f"{function.get('arguments', '')}"
                )
        content = msg.get("content")
        if isinstance(content, str) and content:
            text = content
            if max_chars and len(text) > max_chars:
                text = (
                    text[:max_chars]
                    + "…（内容过长已截断，完整内容见归档，可用 recallContext 检索）"
                )
            lines.append(text)
        if role == "tool" and msg.get("tool_call_id"):
            lines.append(f"[tool_call_id={msg['tool_call_id']}]")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# recallContext 检索实现
# ---------------------------------------------------------------------------

def _message_search_text(msg: dict[str, Any]) -> str:
    """把一条消息拼成可检索的纯文本（含工具调用参数与 tool_call_id）。"""
    parts: list[str] = []
    content = msg.get("content")
    if isinstance(content, str) and content:
        parts.append(content)
    elif content:
        parts.append(json.dumps(content, ensure_ascii=False))
    for call in msg.get("tool_calls") or []:
        function = call.get("function") or {}
        parts.append(f"{function.get('name', '')} {function.get('arguments', '')}")
    if msg.get("tool_call_id"):
        parts.append(f"[tool_call_id={msg['tool_call_id']}]")
    return "\n".join(parts)


def _make_snippet(text: str, start: int, length: int) -> str:
    """截取匹配位置附近的片段。"""
    lo = max(0, start - RECALL_SNIPPET_BEFORE)
    hi = min(len(text), start + length + RECALL_SNIPPET_AFTER)
    snippet = text[lo:hi].replace("\n", " ")
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


def _search_files(files: list[Path], keywords: list[str]) -> list[dict[str, Any]]:
    """在给定归档文件中做大小写不敏感的关键词匹配。"""
    matches: list[dict[str, Any]] = []
    for path in files:
        file_id = path.stem
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            searchable = _message_search_text(msg)
            lowered = searchable.lower()
            keyword = next((k for k in keywords if k in lowered), None)
            if keyword is None:
                continue
            pos = lowered.find(keyword)
            matches.append(
                {
                    "id": file_id,
                    "line": line_no,
                    "role": msg.get("role", "?"),
                    "snippet": _make_snippet(searchable, pos, len(keyword)),
                }
            )
            if len(matches) >= RECALL_MAX_MATCHES:
                return matches
    return matches


def recall_context(id: str | None = None, text: str = "") -> str:
    """recallContext 工具实现：在历史归档中按关键词检索原始上下文片段。"""
    text = (text or "").strip()
    if not text:
        return "错误: 必须提供 text 参数（要查找的关键词或文本）。"

    context_dir = get_context_dir()
    if id:
        archive_path = context_dir / f"{id.strip()}.jsonl"
        if not archive_path.exists():
            available = sorted(p.stem for p in context_dir.glob("*.jsonl"))
            listing = ", ".join(available) if available else "无"
            return f"错误: 未找到编号为 {id} 的归档。当前可用归档: {listing}"
        files = [archive_path]
    else:
        files = sorted(context_dir.glob("*.jsonl"))
        if not files:
            return "暂无历史归档（.agents/context/ 目录下没有归档文件）。"

    # 优先匹配完整 text；无结果时回退为按空白拆分的词组匹配
    keywords = [text.lower()]
    matches = _search_files(files, keywords)
    if not matches:
        tokens = [
            t.lower()
            for t in re.split(r"\s+", text)
            if len(t) >= 2 and t.lower() != text.lower()
        ]
        if tokens:
            matches = _search_files(files, tokens)

    if not matches:
        scope = f"归档 {id}" if id else f"全部 {len(files)} 份归档"
        return f"在{scope}中未找到与「{text}」相关的内容。"

    output: list[str] = []
    total_chars = 0
    truncated = False
    for m in matches:
        block = (
            f"[归档 {m['id']} | 第 {m['line']} 条消息 | role={m['role']}]\n"
            f"{m['snippet']}"
        )
        if total_chars + len(block) > RECALL_MAX_CHARS:
            truncated = True
            break
        output.append(block)
        total_chars += len(block)

    header = f"共找到 {len(matches)} 处匹配：\n"
    body = "\n\n".join(output)
    footer = "\n\n（如需更多细节，可用 recallContext 指定归档编号并换关键词继续检索。）"
    if truncated:
        footer = "\n\n（匹配较多，仅显示部分结果；可指定归档编号并换更精确的关键词继续检索。）"
    return header + "\n\n" + body + footer
