"""会话存储：每个会话一个 JSONL 事件日志，保存在 .agents/sessions/。

文件格式（每行一个 JSON 事件）：
    第一行  {"type": "meta", "id": ..., "created_at": ..., "title": ..., "agents_md": true}
    之后每行 {"type": "message", "role": ..., "content": ..., "tool_calls": ..., ...}

这样既能快速列出会话（只读 meta），又能完整恢复对话（按行重放）。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import get_config

# 会话标题取自首条用户消息
TITLE_MAX_CHARS = 40


def sessions_dir() -> Path:
    """会话目录（配置项 [session] dir，相对当前工作目录）。"""
    directory = get_config().sessions_dir
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def agents_md_path() -> Path:
    """AGENTS.md 路径（当前工作目录下，文件名可配置）。"""
    return Path.cwd() / get_config().agents_md_name


def read_agents_md() -> str | None:
    """读取 AGENTS.md；不存在或为空时返回 None。"""
    path = agents_md_path()
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text:
        return None
    limit = get_config().agents_md_max_chars
    if len(text) > limit:
        text = text[:limit] + "\n…（内容过长已截断）"
    return text


def build_agents_md_message(content: str) -> dict[str, Any]:
    """把 AGENTS.md 包装成一条用户消息（只注入一次，后续直接复用）。"""
    return {
        "role": "user",
        "content": (
            "以下是本项目 AGENTS.md 中约定的长期规则，请在本次会话中始终遵守：\n\n"
            f"{content}"
        ),
    }


@dataclass(slots=True)
class SessionInfo:
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int
    path: Path

    @property
    def preview(self) -> str:
        return f"{self.id} | {self.title} | {self.updated_at} | {self.message_count} 条"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _message_to_event(message: dict[str, Any]) -> dict[str, Any]:
    event: dict[str, Any] = {"type": "message", "ts": _now()}
    for key in ("role", "content", "tool_calls", "tool_call_id", "name"):
        if key in message:
            event[key] = message[key]
    return event


class SessionStore:
    """会话文件读写。一个实例可管理多个会话。"""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or sessions_dir()
        self.directory.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return (
            datetime.now().strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid.uuid4().hex[:4]
        )

    def path_for(self, session_id: str) -> Path:
        return self.directory / f"{session_id}.jsonl"

    def exists(self, session_id: str) -> bool:
        return self.path_for(session_id).is_file()

    # ---------- 写入 ----------

    def save(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        created_at: str | None = None,
        agents_md: bool = False,
        title: str | None = None,
    ) -> Path:
        """整体重写会话文件（先写临时文件再替换，避免半截文件）。"""
        path = self.path_for(session_id)
        created = created_at or self._read_meta(session_id).get("created_at") or _now()
        title = title or self._make_title(messages) or "（空会话）"
        meta = {
            "type": "meta",
            "id": session_id,
            "created_at": created,
            "updated_at": _now(),
            "title": title,
            "agents_md": agents_md,
            "message_count": len([m for m in messages if m.get("role") != "system"]),
        }
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for message in messages:
                if message.get("role") == "system":
                    continue  # System Prompt 每次启动重新生成，不落盘
                f.write(json.dumps(_message_to_event(message), ensure_ascii=False) + "\n")
        tmp.replace(path)
        return path

    def _make_title(self, messages: list[dict[str, Any]]) -> str:
        for message in messages:
            if message.get("role") != "user":
                continue
            content = message.get("content") or ""
            if not isinstance(content, str) or not content.strip():
                continue
            if "AGENTS.md" in content[:60]:
                continue  # 跳过注入的项目说明
            title = " ".join(content.split())
            return title[:TITLE_MAX_CHARS] + ("…" if len(title) > TITLE_MAX_CHARS else "")
        return ""

    # ---------- 读取 ----------

    def _read_meta(self, session_id: str) -> dict[str, Any]:
        path = self.path_for(session_id)
        if not path.is_file():
            return {}
        try:
            with path.open("r", encoding="utf-8") as f:
                first = f.readline()
            event = json.loads(first)
            return event if isinstance(event, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def load(self, session_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """返回 (消息列表, meta)。消息已剔除 system 与事件包装字段。"""
        path = self.path_for(session_id)
        messages: list[dict[str, Any]] = []
        meta: dict[str, Any] = {}
        if not path.is_file():
            raise FileNotFoundError(f"会话不存在: {session_id}")
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "meta":
                    meta = event
                    continue
                if event.get("type") != "message":
                    continue
                if event.get("role") == "system":
                    continue
                message: dict[str, Any] = {"role": event.get("role", "user")}
                if "content" in event:
                    message["content"] = event["content"]
                if event.get("tool_calls"):
                    message["tool_calls"] = event["tool_calls"]
                if event.get("tool_call_id"):
                    message["tool_call_id"] = event["tool_call_id"]
                messages.append(message)
        return messages, meta

    def list(self) -> list[SessionInfo]:
        infos: list[SessionInfo] = []
        for path in self.directory.glob("*.jsonl"):
            meta = self._read_meta(path.stem)
            if not meta:
                meta = {"id": path.stem, "title": "（无元信息）"}
            stat = path.stat()
            infos.append(
                SessionInfo(
                    id=path.stem,
                    title=str(meta.get("title") or "（空会话）"),
                    created_at=str(meta.get("created_at") or "-"),
                    updated_at=str(meta.get("updated_at") or _stat_time(stat.st_mtime)),
                    message_count=int(meta.get("message_count") or 0),
                    path=path,
                )
            )
        infos.sort(key=lambda info: info.updated_at, reverse=True)
        return infos

    def delete(self, session_id: str) -> bool:
        path = self.path_for(session_id)
        if path.is_file():
            path.unlink()
            return True
        return False


def _stat_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
