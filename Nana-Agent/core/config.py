"""配置中心：所有可调参数集中在这里，支持默认值 + config.ini 覆盖。

- 配置文件固定放在「当前工作目录」下的 config.ini（可用环境变量 NANA_CONFIG 指定）
- 程序启动时若 config.ini 不存在，会自动生成一份带 mock 值的完整模板
- 代码中不再硬编码参数，统一通过 get_config() 读取，读取失败则回退默认值
"""
from __future__ import annotations

import configparser
import os
from pathlib import Path
from typing import Any

APP_NAME = "Nana-Agent"

# 所有配置项的默认值（config.ini 缺失某项时使用）
DEFAULTS: dict[str, dict[str, str]] = {
    "llm": {
        "base_url": "http://127.0.0.1:1234/v1",
        "api_key": "lm-studio",
        "model": "qwen/qwen3.5-9b",
        "timeout": "120",
        "max_tool_rounds": "24",
    },
    "context": {
        # 0 表示未知：使用 128k 默认值，并在 TUI 中标注「默认」
        "window_tokens": "0",
        "warn_ratio": "0.7",
        "auto_compact": "true",
        "compact_timeout": "600",
        "compact_msg_max_chars": "20000",
        "default_window_tokens": "131072",
        "recall_max_matches": "20",
        "recall_max_chars": "6000",
    },
    "tools": {
        "read_max_chars": "6000",
        "runcli_timeout": "30",
        "runcli_max_chars": "4000",
        "fetch_timeout": "30",
        "fetch_max_chars": "8000",
        "search_timeout": "30",
        "skill_max_chars": "65536",
    },
    "mcp": {
        "config_file": "mcp.json",
        "connect_timeout": "120",
        "call_timeout": "60",
        "max_output_chars": "8000",
    },
    "session": {
        "dir": ".agents/sessions",
        "auto_save": "true",
        "agents_md": "AGENTS.md",
        "agents_md_max_chars": "20000",
    },
    "search": {
        "baidu_qianfan_api_key": "",
        "qianfan_token_url": "https://aip.baidubce.com/oauth/2.0/token",
        "qianfan_search_url": "https://qianfan.baidubce.com/v2/ai_search/web_search",
    },
    "xby": {
        "api_key": "",
        "base_url": "https://mcp.xiaobenyang.com",
        "mcp_id": "1777316659203075",
        "timeout": "30",
    },
}

# 缺失时生成的模板（mock 值，需用户修改后重启）
TEMPLATE = """# Nana-Agent 配置文件
# 修改本文件后需要重启程序才能生效。
# 所有配置项都可以删除，删除后使用程序内置默认值。

[llm]
# OpenAI 兼容接口地址（LM Studio 默认 http://127.0.0.1:1234/v1）
base_url = http://127.0.0.1:1234/v1
# API Key；本地 LM Studio 填任意值即可
api_key = sk-mock-key
# 模型名称
model = mock-model-name
# 单次请求超时（秒）
timeout = 120
# 一轮对话中最多执行多少次工具调用
max_tool_rounds = 24

[context]
# 模型上下文窗口大小（tokens）；0 表示未知，使用 128k 默认值并标注「默认」
window_tokens = 0
# 上下文使用量达到该比例时警告 / 自动压缩
warn_ratio = 0.7
# 达到阈值后是否自动压缩（false = 仅警告，等待手动 /compact）
auto_compact = true
# 压缩摘要请求的超时时间（秒），本地大模型可能很慢
compact_timeout = 600
# 压缩时单条历史消息的最大字符数（归档中仍是完整内容）
compact_msg_max_chars = 20000
# 无法获取窗口大小时使用的默认值
default_window_tokens = 131072
# recallContext 单次最多返回的片段数 / 总字符数
recall_max_matches = 20
recall_max_chars = 6000

[tools]
# readTextFile 单次返回的最大字符数
read_max_chars = 6000
# RunCli 的超时时间（秒）与输出上限
runcli_timeout = 30
runcli_max_chars = 4000
# fetchWeb 的超时时间（秒）与内容上限
fetch_timeout = 30
fetch_max_chars = 8000
# 联网搜索请求超时（秒）
search_timeout = 30
# 加载 skill 文件的大小上限
skill_max_chars = 65536

[mcp]
# MCP 服务器配置文件（相对当前工作目录）
config_file = mcp.json
# 连接单个服务器的超时时间（秒），npx 首次下载可能较慢
connect_timeout = 120
# 调用 MCP 工具的超时时间（秒）
call_timeout = 60
# MCP 工具输出字符上限
max_output_chars = 8000

[session]
# 会话事件日志目录（相对当前工作目录）
dir = .agents/sessions
# 每轮对话结束后自动保存会话
auto_save = true
# 项目说明文件名（放在当前工作目录，首次加载会话时注入一次）
agents_md = AGENTS.md
# AGENTS.md 注入的最大字符数
agents_md_max_chars = 20000

[search]
# 百度千帆联网搜索 API Key（留空则 searchWeb 不可用）
baidu_qianfan_api_key = YOUR_QIANFAN_API_KEY
qianfan_token_url = https://aip.baidubce.com/oauth/2.0/token
qianfan_search_url = https://qianfan.baidubce.com/v2/ai_search/web_search

[xby]
# 小笨羊 MCP API Key（留空则星座相关工具不可用）
api_key = YOUR_XBY_API_KEY
base_url = https://mcp.xiaobenyang.com
mcp_id = 1777316659203075
timeout = 30
"""


def config_path() -> Path:
    """配置文件路径：默认当前工作目录下的 config.ini。"""
    override = os.environ.get("NANA_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.cwd() / "config.ini"


class Config:
    """配置访问器：先取 config.ini 的值，缺失则回退 DEFAULTS。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_path()
        self.parser = configparser.ConfigParser(
            inline_comment_prefixes=("#", ";"),
            allow_no_value=True,
        )
        self.load()

    def load(self) -> None:
        self.parser = configparser.ConfigParser(
            inline_comment_prefixes=("#", ";"),
            allow_no_value=True,
        )
        try:
            self.parser.read(self.path, encoding="utf-8")
        except Exception:
            # 配置损坏时不影响启动，全部使用默认值
            self.parser = configparser.ConfigParser(
                inline_comment_prefixes=("#", ";"),
            )

    def reload(self) -> None:
        self.load()

    # ---------- 基础读取 ----------

    def get(self, section: str, key: str, default: str | None = None) -> str:
        fallback = DEFAULTS.get(section, {}).get(key, default)
        try:
            value = self.parser.get(section, key, fallback=None)
        except Exception:
            value = None
        if value is None:
            return "" if fallback is None else str(fallback)
        return value.strip()

    def get_int(self, section: str, key: str, default: int = 0) -> int:
        raw = self.get(section, key, str(default))
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return default

    def get_float(self, section: str, key: str, default: float = 0.0) -> float:
        raw = self.get(section, key, str(default))
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError):
            return default

    def get_bool(self, section: str, key: str, default: bool = True) -> bool:
        raw = self.get(section, key, "true" if default else "false").lower()
        return raw in ("1", "true", "yes", "on")

    def set(self, section: str, key: str, value: str) -> None:
        """写入配置并落盘（保留其它已有配置项）。"""
        if not self.parser.has_section(section):
            self.parser.add_section(section)
        self.parser.set(section, key, str(value))
        with self.path.open("w", encoding="utf-8") as f:
            self.parser.write(f)

    # ---------- 常用项快捷属性 ----------

    @property
    def llm_base_url(self) -> str:
        return self.get("llm", "base_url")

    @property
    def llm_api_key(self) -> str:
        return self.get("llm", "api_key")

    @property
    def llm_model(self) -> str:
        return self.get("llm", "model")

    @property
    def llm_timeout(self) -> float:
        return self.get_float("llm", "timeout", 120.0)

    @property
    def max_tool_rounds(self) -> int:
        return self.get_int("llm", "max_tool_rounds", 24)

    @property
    def context_window(self) -> int:
        """配置的窗口大小；0 表示未配置。"""
        return self.get_int("context", "window_tokens", 0)

    @property
    def default_window_tokens(self) -> int:
        return self.get_int("context", "default_window_tokens", 131072)

    @property
    def warn_ratio(self) -> float:
        return self.get_float("context", "warn_ratio", 0.7)

    @property
    def auto_compact(self) -> bool:
        return self.get_bool("context", "auto_compact", True)

    @property
    def compact_timeout(self) -> float:
        return self.get_float("context", "compact_timeout", 600.0)

    @property
    def compact_msg_max_chars(self) -> int:
        return self.get_int("context", "compact_msg_max_chars", 20000)

    @property
    def recall_max_matches(self) -> int:
        return self.get_int("context", "recall_max_matches", 20)

    @property
    def recall_max_chars(self) -> int:
        return self.get_int("context", "recall_max_chars", 6000)

    @property
    def sessions_dir(self) -> Path:
        """会话目录（相对当前工作目录）。"""
        raw = Path(self.get("session", "dir", ".agents/sessions"))
        return raw if raw.is_absolute() else Path.cwd() / raw

    @property
    def auto_save(self) -> bool:
        return self.get_bool("session", "auto_save", True)

    @property
    def agents_md_name(self) -> str:
        return self.get("session", "agents_md", "AGENTS.md")

    @property
    def agents_md_max_chars(self) -> int:
        return self.get_int("session", "agents_md_max_chars", 20000)

    @property
    def mcp_config_path(self) -> Path:
        raw = Path(self.get("mcp", "config_file", "mcp.json"))
        path = raw if raw.is_absolute() else Path.cwd() / raw
        if not path.exists():
            # 兼容从非项目根目录启动的情况
            fallback = Path(__file__).resolve().parent.parent / "mcp.json"
            if fallback.exists():
                return fallback
        return path

    @property
    def mcp_connect_timeout(self) -> float:
        return self.get_float("mcp", "connect_timeout", 120.0)

    @property
    def mcp_call_timeout(self) -> float:
        return self.get_float("mcp", "call_timeout", 60.0)

    @property
    def mcp_max_output_chars(self) -> int:
        return self.get_int("mcp", "max_output_chars", 8000)

    def as_dict(self) -> dict[str, dict[str, str]]:
        """当前生效的全部配置（含默认值），用于 /config 展示。"""
        result: dict[str, dict[str, str]] = {}
        for section, defaults in DEFAULTS.items():
            result[section] = {
                key: self.get(section, key, value) for key, value in defaults.items()
            }
        return result


_config: Config | None = None


def get_config() -> Config:
    """进程级配置单例。"""
    global _config
    if _config is None:
        _config = Config()
    return _config


def reload_config() -> Config:
    global _config
    _config = Config()
    return _config


def ensure_config() -> bool:
    """若当前目录没有 config.ini，则生成带 mock 值的完整模板。

    返回 True 表示本次新建了配置文件（调用方应提示用户修改并重启）。
    """
    path = config_path()
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE, encoding="utf-8")
    return True


def config_summary() -> str:
    """给 /config 命令用的简要文本（隐藏敏感 Key）。"""
    cfg = get_config()
    lines = [f"配置文件: {cfg.path}", ""]
    for section, items in cfg.as_dict().items():
        lines.append(f"[{section}]")
        for key, value in items.items():
            if "key" in key.lower() and value:
                value = value[:4] + "***" if len(value) > 4 else "***"
            lines.append(f"  {key} = {value}")
        lines.append("")
    return "\n".join(lines).rstrip()
