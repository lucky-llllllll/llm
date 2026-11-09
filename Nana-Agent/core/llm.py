from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from .config import get_config


class LLMError(RuntimeError):
    pass


@dataclass(slots=True)
class StreamChunk:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None


class LLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        timeout: float | None = None,
    ) -> None:
        # 未显式传入时，全部从 config.ini 读取（缺失则用内置默认值）
        cfg = get_config()
        self.base_url = (base_url or cfg.llm_base_url).rstrip("/")
        self.api_key = api_key or cfg.llm_api_key
        self.model = model or cfg.llm_model
        self.tools = tools or []
        self.timeout = cfg.llm_timeout if timeout is None else timeout
        self.messages: list[dict[str, Any]] = []
        # 最近一次响应的 Token 用量（由模型 API 返回），形如
        # {"prompt_tokens": int, "completion_tokens": int, ...}
        # 服务器未提供时为 None，调用方需回退到估算
        self.last_usage: dict[str, Any] | None = None

    def clear_history(self) -> None:
        self.messages.clear()

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(
        self,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        self.messages.append(message)

    def add_tool_message(self, tool_call_id: str, content: str) -> None:
        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            }
        )

    def _post_chat(self, payload: dict[str, Any]) -> Any:
        """发送一次 chat/completions 请求并返回解析后的 JSON。

        若服务器不认识 stream_options 字段（HTTP 400），自动去掉该字段重试一次。
        """
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
            method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            # 部分服务器不支持 stream_options，去掉后重试一次
            if (
                exc.code == 400
                and "stream_options" in payload
                and ("stream_options" in detail or "Unrecognized" in detail)
            ):
                retry_payload = {
                    k: v for k, v in payload.items() if k != "stream_options"
                }
                return self._post_chat(retry_payload)
            raise LLMError(
                f"模型请求失败: HTTP {exc.code}: {detail}"
            ) from exc
        except Exception as exc:
            raise LLMError(f"模型请求失败: {type(exc).__name__}: {exc}") from exc
        return response

    def stream_chat(
        self,
        messages: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamChunk]:
        # 每次请求前重置 usage，避免拿到上一次的过期数据
        self.last_usage = None
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages if messages is None else messages,
            "stream": True,
            # 请求服务器在流式响应末尾附带 Token 用量
            "stream_options": {"include_usage": True},
        }
        if self.tools:
            payload["tools"] = self.tools

        response = self._post_chat(payload)

        calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None

        with response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    if data == "[DONE]":
                        break
                    continue

                event = json.loads(data)
                # 流式模式下 usage 通常出现在最后一个单独的 chunk 里
                if event.get("usage"):
                    self.last_usage = event["usage"]
                choices = event.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}

                content = delta.get("content") or ""
                if content:
                    yield StreamChunk(content=content)

                for tool_call in delta.get("tool_calls") or []:
                    index = tool_call.get("index")
                    if index is None:
                        index = 0
                    call = calls.setdefault(
                        index,
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    if tool_call.get("id"):
                        call["id"] = tool_call["id"]
                    function = tool_call.get("function") or {}
                    if function.get("name"):
                        call["function"]["name"] += function["name"]
                    if function.get("arguments"):
                        call["function"]["arguments"] += function["arguments"]

                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

        yield StreamChunk(
            tool_calls=[calls[index] for index in sorted(calls)],
            finish_reason=finish_reason,
        )

    def run_round(self) -> Iterator[StreamChunk]:
        content_parts: list[str] = []
        final = StreamChunk()

        for chunk in self.stream_chat():
            if chunk.content:
                content_parts.append(chunk.content)
                yield StreamChunk(content=chunk.content)
            if chunk.tool_calls or chunk.finish_reason is not None:
                final = chunk

        self.add_assistant_message(
            "".join(content_parts),
            final.tool_calls or None,
        )
        yield final

    def chat(self, user_input: str) -> Iterator[StreamChunk]:
        self.add_user_message(user_input)
        yield from self.run_round()

    def complete(
        self,
        messages: list[dict[str, Any]],
        timeout: float | None = None,
    ) -> str:
        """非流式补全：发送一次请求，返回完整文本。

        供上下文压缩等内部流程使用（不携带工具定义，模型只输出文本）。
        timeout 不传时使用客户端默认值；压缩长上下文时应传更大的值。
        """
        self.last_usage = None
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        effective_timeout = self.timeout if timeout is None else timeout
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
            method="POST",
        )
        try:
            response = urllib.request.urlopen(
                request, timeout=effective_timeout
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMError(
                f"模型请求失败: HTTP {exc.code}: {detail}"
            ) from exc
        except Exception as exc:
            raise LLMError(f"模型请求失败: {type(exc).__name__}: {exc}") from exc
        with response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        if data.get("usage"):
            self.last_usage = data["usage"]
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("模型返回为空，无法生成结果")
        message = choices[0].get("message") or {}
        return message.get("content") or ""
