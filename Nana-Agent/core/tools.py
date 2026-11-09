import configparser
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import get_config
from .context_manager import recall_context

# 全部可调参数来自 config.ini（缺失时用内置默认值）
_cfg = get_config()

_CONFIG_PATH = _cfg.path

QIANFAN_TOKEN_URL = _cfg.get(
    "search", "qianfan_token_url", "https://aip.baidubce.com/oauth/2.0/token"
)
QIANFAN_SEARCH_URL = _cfg.get(
    "search",
    "qianfan_search_url",
    "https://qianfan.baidubce.com/v2/ai_search/web_search",
)
SEARCH_TIMEOUT = _cfg.get_float("tools", "search_timeout", 30.0)

FETCH_TIMEOUT = _cfg.get_float("tools", "fetch_timeout", 30.0)
FETCH_MAX_CHARS = _cfg.get_int("tools", "fetch_max_chars", 8000)

RUNCLI_TIMEOUT = _cfg.get_float("tools", "runcli_timeout", 30.0)
RUNCLI_MAX_CHARS = _cfg.get_int("tools", "runcli_max_chars", 4000)

# readTextFile 单次返回的最大字符数，超出则截断并提示续读
READ_MAX_CHARS = _cfg.get_int("tools", "read_max_chars", 6000)

def _get_skills_dirs() -> list:
    """候选 skills 根目录（按优先级）：
    1. 当前工作目录下的 .agents/skills（运行期用户放置的 skill）
    2. 代码所在项目根目录下的 .agents/skills（与 skill_loader 的发现逻辑一致）
    """
    project_root = Path(__file__).resolve().parent.parent
    dirs = [Path.cwd() / ".agents" / "skills", project_root / ".agents" / "skills"]
    # 去重（从项目根目录启动时两者相同）
    unique, seen = [], set()
    for d in dirs:
        r = d.resolve()
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


def _find_skill_dir(name):
    """在候选 skills 根目录中查找名为 name 的 skill 目录，返回 (skill_dir, skills_root) 或 (None, None)。"""
    for root in _get_skills_dirs():
        skill_dir = (root / name).resolve()
        # skill 目录本身也必须位于该 skills 根目录内（防 name 带 .. 越出根目录）
        try:
            skill_dir.relative_to(root)
        except ValueError:
            continue
        if skill_dir.is_dir():
            return skill_dir, root
    return None, None
SKILL_MAX_CHARS = _cfg.get_int("tools", "skill_max_chars", 65536)

# skill 名称规范：小写字母、数字和连字符，且不能以连字符开头/结尾
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

XBY_BASE_URL = _cfg.get("xby", "base_url", "https://mcp.xiaobenyang.com")
XBY_MCP_ID = _cfg.get("xby", "mcp_id", "1777316659203075")
XBY_TIMEOUT = _cfg.get_float("xby", "timeout", 30.0)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "recallContext",
            "description": (
                "检索历史上下文归档（.agents/context/ 目录下的 JSONL 归档文件）。"
                "当上下文压缩后摘要中缺少某些历史细节时，"
                "用本工具按关键词找回原始对话片段（含文件路径、命令、工具返回等）。"
                "id 不传时在所有归档中检索"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": (
                            "归档编号（可选），例如 20261022-153000。"
                            "提供时只在该归档文件中检索"
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": "要查找的关键词或文本内容（必填）",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getDate",
            "description": "获取当前电脑的日期和时间",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "isLeapYear",
            "description": "判断指定年份是否为闰年",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {
                        "type": "integer",
                        "description": "要判断的年份，例如 2024",
                    }
                },
                "required": ["year"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getCwd",
            "description": "获取当前工作目录",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listDir",
            "description": "列出指定目录下所有文件和子目录",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径，默认为当前目录",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getFileType",
            "description": "获取指定文件或目录的类型",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件或目录路径"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "readTextFile",
            "description": (
                "读取指定纯文本文件内容，支持分页读取大文件。"
                "内容超过单次上限时自动截断并返回续读提示，"
                "把返回信息中的 offset 作为下一次调用的 offset 即可继续向后读取，"
                "如此反复直到读完整个文件"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "encoding": {
                        "type": "string",
                        "description": "文件编码，默认为 utf-8，解码失败时自动回退到系统编码（如 GBK）",
                    },
                    "offset": {
                        "type": "integer",
                        "description": (
                            "起始行号（从 1 开始），默认 1。"
                            "续读大文件时传入上次返回信息中提示的 offset"
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "本次最多读取的行数，默认不限（仍受单次字符数上限约束）。"
                            "小文件可不传"
                        ),
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createFile",
            "description": "在指定目录下创建新文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "目标目录路径"},
                    "filename": {"type": "string", "description": "新文件名"},
                    "content": {
                        "type": "string",
                        "description": "文件初始内容，默认为空",
                    },
                },
                "required": ["directory", "filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "editTextFile",
            "description": "编辑纯文本文件内容，可覆盖或追加",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "要写入的内容"},
                    "append": {
                        "type": "boolean",
                        "description": "为 true 时追加内容，否则覆盖，默认为 false",
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码，默认为 utf-8",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "createDir",
            "description": "创建新目录，可包含多级父目录",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要创建的目录路径"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteDir",
            "description": "删除目录，默认递归删除非空目录",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要删除的目录路径"},
                    "recursive": {
                        "type": "boolean",
                        "description": "为 false 时仅删除空目录，默认为 true",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deleteFile",
            "description": "删除指定文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要删除的文件路径"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "renamePath",
            "description": "重命名文件或目录",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "原路径"},
                    "target": {"type": "string", "description": "新路径"},
                },
                "required": ["source", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "searchWeb",
            "description": (
                "联网搜索：调用百度千帆 AI 搜索，根据关键词搜索互联网，"
                "返回网页搜索结果（标题、网址、摘要）"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，例如：qwen3.5 介绍",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetchWeb",
            "description": (
                "抓取指定网页并提取正文：用 curl 下载页面，"
                "用 trafilatura 提取文章正文并过滤广告、导航栏、页脚等无关内容，"
                "返回纯文本正文。通常配合 searchWeb 使用"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要抓取的网页链接，须以 http:// 或 https:// 开头",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "RunCli",
            "description": (
                "在本地执行命令行程序并返回输出（stdout/stderr/退出码）。"
                "不经 shell 解析，不支持管道 | 和通配符展开，"
                "参数须逐个放入 args 列表"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的可执行命令，例如 ls、git、py",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "命令参数列表，例如 ls -l 对应 [\"-l\"]；可为空",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
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
    },
    {
        "type": "function",
        "function": {
            "name": "createSkill",
            "description": (
                "根据用户的自然语言要求创建新的 Skill："
                "在 .agents/skills/<name>/ 目录下生成符合 Agent Skills 规范的 SKILL.md"
                "（含 YAML Frontmatter 的 name、description 及正文）。"
                "name 必须由小写字母、数字和连字符组成（如 my-cool-skill）。"
                "若 skill 已存在则拒绝创建，不会覆盖。"
                "如需补充 references/、scripts/ 等目录或其他辅助文件，"
                "请继续调用 createDir、createFile 等现有文件工具完成"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Skill 名称，只能由小写字母、数字和连字符组成，"
                            "例如 weather-helper，对应 .agents/skills 下的子文件夹名"
                        ),
                    },
                    "skillContext": {
                        "type": "string",
                        "description": (
                            "要写入 SKILL.md 的完整内容，必须以 YAML Frontmatter 开头"
                            "（--- 包裹，含 name 与 description 字段），后接正文"
                        ),
                    },
                },
                "required": ["name", "skillContext"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "setXbyApiKey",
            "description": (
                "设置小笨羊星座服务的 APIKEY 并保存到 config.ini 的 [xby] 配置段，"
                "之后星座相关工具即可正常使用，重启后仍然有效"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "api_key": {
                        "type": "string",
                        "description": "小笨羊 (xiaobenyang.com) 的 APIKEY",
                    }
                },
                "required": ["api_key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getZodiacInfo",
            "description": "获取指定星座的详细信息，包括性格特征、守护星、元素等",
            "parameters": {
                "type": "object",
                "properties": {
                    "zodiac": {
                        "type": "string",
                        "description": "星座名称（中文或英文），例如：白羊座 / Aries",
                    }
                },
                "required": ["zodiac"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getDailyHoroscope",
            "description": "获取指定星座的今日运势（整体、爱情、事业、财运等）",
            "parameters": {
                "type": "object",
                "properties": {
                    "zodiac": {
                        "type": "string",
                        "description": "星座名称（中文或英文）",
                    },
                    "category": {
                        "type": "string",
                        "description": "运势类别（可选），例如：整体 / 爱情 / 事业 / 财运",
                    },
                },
                "required": ["zodiac"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getCompatibility",
            "description": "获取两个星座之间的配对指数和关系分析",
            "parameters": {
                "type": "object",
                "properties": {
                    "zodiac1": {
                        "type": "string",
                        "description": "第一个星座名称（中文或英文）",
                    },
                    "zodiac2": {
                        "type": "string",
                        "description": "第二个星座名称（中文或英文）",
                    },
                },
                "required": ["zodiac1", "zodiac2"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getAllZodiacs",
            "description": "获取全部 12 个星座的基本信息列表",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getZodiacByDate",
            "description": "根据出生月份和日期判断所属星座",
            "parameters": {
                "type": "object",
                "properties": {
                    "month": {
                        "type": "integer",
                        "description": "出生月份（1-12）",
                    },
                    "day": {
                        "type": "integer",
                        "description": "出生日期（1-31）",
                    },
                },
                "required": ["month", "day"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getRisingSign",
            "description": "根据完整出生日期、时间和地点经纬度计算上升星座",
            "parameters": {
                "type": "object",
                "properties": {
                    "birth_year": {
                        "type": "integer",
                        "description": "出生年份（1900-2100）",
                    },
                    "birth_month": {
                        "type": "integer",
                        "description": "出生月份（1-12）",
                    },
                    "birth_day": {
                        "type": "integer",
                        "description": "出生日期（1-31）",
                    },
                    "birth_hour": {
                        "type": "integer",
                        "description": "出生小时（0-23），24 小时制",
                    },
                    "birth_minute": {
                        "type": "integer",
                        "description": "出生分钟（0-59）",
                    },
                    "latitude": {
                        "type": "number",
                        "description": "出生地纬度（-90 到 90），例如北京 39.9",
                    },
                    "longitude": {
                        "type": "number",
                        "description": "出生地经度（-180 到 180），例如北京 116.4",
                    },
                },
                "required": [
                    "birth_year",
                    "birth_month",
                    "birth_day",
                    "birth_hour",
                    "birth_minute",
                    "latitude",
                    "longitude",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "getRisingSignInfo",
            "description": "获取指定上升星座的详细解读信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "rising_sign": {
                        "type": "string",
                        "description": "上升星座名称（中文或英文）",
                    }
                },
                "required": ["rising_sign"],
            },
        },
    },
]


def recall_context_tool(id=None, text=""):
    """recallContext 工具入口（实现在 context_manager.recall_context）。"""
    return recall_context(id=id, text=text)


def get_date():
    if platform.system() == "Windows":
        date = subprocess.run(
            ["cmd", "/c", "date", "/t"],
            capture_output=True,
            text=True,
            encoding="mbcs",
        ).stdout.strip()
        time = subprocess.run(
            ["cmd", "/c", "time", "/t"],
            capture_output=True,
            text=True,
            encoding="mbcs",
        ).stdout.strip()
        return f"{date} {time}"
    return subprocess.run(["date"], capture_output=True, text=True).stdout.strip()


def is_leap_year(year):
    return (year % 4 == 0 and year % 100 != 0) or year % 400 == 0


def get_cwd():
    try:
        return os.getcwd()
    except Exception as e:
        return f"错误: {e}"


def list_dir(path="."):
    try:
        names = sorted(os.listdir(path))
        if not names:
            return f"目录 {os.path.abspath(path)} 为空"
        lines = []
        for name in names:
            full = os.path.join(path, name)
            kind = "目录" if os.path.isdir(full) else "文件"
            lines.append(f"{kind}: {name}")
        return "\n".join(lines)
    except Exception as e:
        return f"错误: {e}"


def get_file_type(path):
    try:
        if not os.path.exists(path):
            return f"错误: 路径不存在: {path}"
        if os.path.islink(path):
            kind = "符号链接"
        elif os.path.isdir(path):
            kind = "目录"
        else:
            kind = "文件"
        ext = os.path.splitext(path)[1]
        return f"{kind}，扩展名: {ext or '无'}"
    except Exception as e:
        return f"错误: {e}"


def _read_text_lines(path, encoding):
    """按指定编码读出所有行；utf-8 等解码失败时自动回退到系统本地编码。"""
    try:
        with open(path, "r", encoding=encoding, errors="strict") as f:
            return f.readlines(), encoding
    except (UnicodeDecodeError, ValueError):
        if encoding and encoding.lower().replace("-", "") in ("utf8", "utf8mb4"):
            fallback = "mbcs" if platform.system() == "Windows" else "gbk"
            with open(path, "r", encoding=fallback, errors="replace") as f:
                return f.readlines(), fallback
        with open(path, "r", encoding=encoding, errors="replace") as f:
            return f.readlines(), encoding
    except LookupError:
        # 无效编码名，按系统默认重试
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines(), "utf-8"


def read_text_file(path, encoding="utf-8", offset=1, limit=None):
    """分页读取文本文件，超长自动截断并提示续读 offset。"""
    try:
        if not path or not str(path).strip():
            return "错误: 文件路径 (path) 不能为空"
        path = str(path).strip()
        if not os.path.exists(path):
            return f"错误: 文件不存在: {path}"
        if not os.path.isfile(path):
            return f"错误: 路径不是文件: {path}"

        try:
            offset = int(offset) if offset is not None else 1
        except (TypeError, ValueError):
            offset = 1
        if offset < 1:
            offset = 1
        try:
            limit = int(limit) if limit is not None else None
        except (TypeError, ValueError):
            limit = None
        if limit is not None and limit < 1:
            limit = None

        try:
            lines, used_encoding = _read_text_lines(path, encoding)
        except OSError as exc:
            return f"错误: 读取文件失败: {exc}"

        total = len(lines)
        start = min(offset - 1, total)
        end = total if limit is None else min(start + limit, total)

        chunks: list[str] = []
        used = 0
        truncated = False
        shown_end = end  # 本次实际显示到的行号（1-based，含）
        for i in range(start, end):
            line = lines[i]
            if used + len(line) > READ_MAX_CHARS and i > start:
                truncated = True
                shown_end = i  # 0-based i 未显示，1-based 最后一行是 i
                break
            chunks.append(line)
            used += len(line)

        if not chunks:
            if start >= total:
                return (
                    f"错误: offset={offset} 超出文件范围"
                    f"（文件共 {total} 行），请用更小的 offset"
                )
            # 单行超长：硬截断，本行无法续读
            text = lines[start][:READ_MAX_CHARS]
            shown_end = start + 1
            header = (
                f"[{path} | 共 {total} 行 | "
                f"本次显示第 {start + 1}-{shown_end} 行（该行超长已截断）"
                f" | 编码 {used_encoding}]"
            )
            return f"{header}\n\n{text}\n\n[单行超过 {READ_MAX_CHARS} 字符，已截断]"
        text = "".join(chunks).rstrip("\n")

        header = (
            f"[{path} | 共 {total} 行 | "
            f"本次显示第 {start + 1}-{shown_end} 行"
            f" | 编码 {used_encoding}]"
        )
        if truncated:
            return (
                f"{header}\n\n{text}\n\n"
                f"…（内容过长，已截断。未读完，请再次调用 readTextFile，"
                f"传 offset={shown_end + 1} 继续读取后续内容）"
            )
        if end < total:
            return (
                f"{header}\n\n{text}\n\n"
                f"…（后面还有 {total - end} 行未显示。请再次调用 readTextFile，"
                f"传 offset={end + 1} 继续读取）"
            )
        return f"{header}\n\n{text}\n\n[已读到文件末尾]"
    except Exception as exc:
        return f"错误: {type(exc).__name__}: {exc}"


def create_file(directory, filename, content=""):
    try:
        path = os.path.join(directory, filename)
        with open(path, "x", encoding="utf-8") as f:
            f.write(content)
        return f"已创建文件: {os.path.abspath(path)}"
    except Exception as e:
        return f"错误: {e}"


def edit_text_file(path, content, append=False, encoding="utf-8"):
    try:
        with open(path, "a" if append else "w", encoding=encoding) as f:
            f.write(content)
        action = "追加" if append else "覆盖写入"
        return f"已{action}文件: {os.path.abspath(path)}"
    except Exception as e:
        return f"错误: {e}"


def create_dir(path):
    try:
        os.makedirs(path)
        return f"已创建目录: {os.path.abspath(path)}"
    except Exception as e:
        return f"错误: {e}"


def delete_dir(path, recursive=True):
    try:
        if not os.path.isdir(path):
            return f"错误: 目录不存在: {path}"
        if recursive:
            shutil.rmtree(path)
        else:
            os.rmdir(path)
        return f"已删除目录: {os.path.abspath(path)}"
    except Exception as e:
        return f"错误: {e}"


def delete_file(path):
    try:
        os.remove(path)
        return f"已删除文件: {os.path.abspath(path)}"
    except Exception as e:
        return f"错误: {e}"


def rename_path(source, target):
    try:
        shutil.move(source, target)
        return f"已重命名: {source} -> {target}"
    except Exception as e:
        return f"错误: {e}"


def _load_qianfan_api_key():
    """从 config.ini [search] 读取千帆 API Key，返回 (key, 错误信息)。"""
    config = get_config()
    api_key = config.get("search", "baidu_qianfan_api_key", "").strip()
    if not api_key or api_key.startswith("YOUR_"):
        return None, (
            "错误: config.ini 中 baidu_qianfan_api_key 未配置。"
            f"请编辑 {config.path} 的 [search] 段后重启程序"
        )
    return api_key, None


def _get_qianfan_access_token(api_key):
    """获取千帆搜索 access_token。

    千帆 AI 搜索的 bce-v3 格式 API Key 可直接作为 access_token（Bearer）使用；
    其他格式视为 "AK|SK"（用 | 分隔），走 OAuth2.0 client_credentials 换取 token。
    """
    api_key = api_key.strip()
    if api_key.startswith("bce-v3/"):
        return api_key

    parts = api_key.split("|", 1)
    if len(parts) != 2:
        raise ValueError(
            "无效的千帆密钥格式: bce-v3 密钥可直接使用，"
            "或配置为 'AK|SK' 格式用于换取 access_token"
        )
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": parts[0],
            "client_secret": parts[1],
        }
    ).encode("utf-8")
    request = urllib.request.Request(QIANFAN_TOKEN_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=SEARCH_TIMEOUT) as resp:
            token_resp = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(
            f"token 接口返回 HTTP {exc.code}: {detail}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(
            f"网络请求失败: {type(exc).__name__}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"token 接口返回内容不是有效 JSON: {exc}") from exc

    token = token_resp.get("access_token")
    if not token:
        raise RuntimeError(f"token 接口未返回 access_token: {token_resp}")
    return token


def search_web(query):
    """调用百度千帆 AI 搜索，返回格式化的网页搜索结果。"""
    try:
        if not query or not query.strip():
            return "错误: 搜索关键词不能为空"
        query = query.strip()

        api_key, err = _load_qianfan_api_key()
        if err:
            return err

        try:
            access_token = _get_qianfan_access_token(api_key)
        except Exception as exc:
            return f"错误: 获取千帆 access_token 失败: {exc}"

        payload = {
            "messages": [{"role": "user", "content": query}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": 5}],
        }
        request = urllib.request.Request(
            QIANFAN_SEARCH_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + access_token,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=SEARCH_TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code in (401, 403):
                return (
                    f"错误: 千帆鉴权失败 (HTTP {exc.code})，"
                    f"请检查 config.ini 中 baidu_qianfan_api_key 是否有效。详情: {detail}"
                )
            if exc.code == 429:
                return f"错误: 千帆搜索调用额度超限 (HTTP 429)。详情: {detail}"
            return f"错误: 千帆搜索接口返回 HTTP {exc.code}: {detail}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return (
                "错误: 网络请求失败，无法连接千帆搜索接口: "
                f"{type(exc).__name__}: {exc}"
            )
        except json.JSONDecodeError as exc:
            return f"错误: 千帆搜索返回内容不是有效 JSON: {exc}"

        if isinstance(body, dict) and body.get("error"):
            desc = body.get("error_description") or body.get("error")
            return f"错误: 千帆搜索接口返回错误: {desc}"

        results = (
            body.get("references")
            or body.get("search_result")
            or body.get("results")
            or []
        )
        if not results:
            return f"未搜索到与“{query}”相关的结果"

        lines = [f"搜索“{query}”的结果:"]
        for index, item in enumerate(results, 1):
            title = item.get("title") or "无标题"
            url = item.get("url") or ""
            content = (item.get("snippet") or item.get("content") or "").strip()
            if len(content) > 300:
                content = content[:300] + "…"
            website = item.get("website") or ""
            date = item.get("date") or ""
            meta = " / ".join(part for part in (website, date) if part)
            lines.append(f"{index}. {title}")
            if meta:
                lines.append(f"   来源: {meta}")
            if url:
                lines.append(f"   网址: {url}")
            if content:
                lines.append(f"   摘要: {content}")
        return "\n".join(lines)
    except Exception as exc:
        return f"错误: {type(exc).__name__}: {exc}"


def fetch_web(url):
    """用 curl 抓取网页（shell=False），再用 trafilatura 提取正文。"""
    try:
        url = (url or "").strip()
        if not url:
            return "错误: URL 不能为空"
        if not (url.startswith("http://") or url.startswith("https://")):
            return f"错误: 仅支持 http/https 链接: {url}"

        try:
            # shell=False（默认）：以参数列表方式调用 curl，不经 shell 解析
            result = subprocess.run(
                [
                    "curl",
                    "-sSL",
                    "--max-time", "20",
                    "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NanaAgent/1.0",
                    url,
                ],
                capture_output=True,
                timeout=FETCH_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return f"错误: 抓取网页超时（超过 {FETCH_TIMEOUT:g} 秒）: {url}"
        except FileNotFoundError:
            return "错误: 未找到 curl 命令，请确认系统已安装 curl"

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            return (
                f"错误: curl 抓取失败 (exit {result.returncode}): "
                f"{stderr or url}"
            )

        html = result.stdout.decode("utf-8", errors="replace")
        if not html.strip():
            return f"错误: 网页内容为空: {url}"

        try:
            import trafilatura
        except ImportError:
            return (
                "错误: 缺少 trafilatura 依赖，"
                "请先执行: pip install trafilatura"
            )

        # trafilatura 自动过滤广告、导航栏、页脚等模板内容，仅保留正文
        text = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
        if not text:
            return (
                "错误: 未能从网页中提取到正文"
                "（可能是非文章页面或被反爬拦截）: "
                f"{url}"
            )

        if len(text) > FETCH_MAX_CHARS:
            text = text[:FETCH_MAX_CHARS] + "…（正文过长，已截断）"
        return f"已抓取网页正文: {url}\n\n{text}"
    except Exception as exc:
        return f"错误: {type(exc).__name__}: {exc}"


def _runcli_decode(data):
    """优先按 utf-8 解码命令输出，失败时回退到系统本地编码(GBK/mbcs)。"""
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("mbcs", errors="replace")
        except LookupError:
            return data.decode("gbk", errors="replace")


def _runcli_truncate(text):
    if len(text) > RUNCLI_MAX_CHARS:
        return text[:RUNCLI_MAX_CHARS] + f"…（输出过长，已截断，共 {len(text)} 字符）"
    return text


def run_cli(command, args=None):
    """以参数列表方式执行本地命令（shell=False），返回 stdout/stderr/退出码。"""
    try:
        command = (command or "").strip()
        if not command:
            return "错误: command 不能为空"

        if args is None or args == "":
            args = []
        elif isinstance(args, str):
            # 模型偶尔把 args 当作整串传入，这里做一次安全拆分
            args = shlex.split(args)
        if not isinstance(args, list):
            return "错误: args 必须是字符串列表"

        try:
            # shell=False（默认）：不经 shell 解析，无注入风险
            result = subprocess.run(
                [command, *args],
                capture_output=True,
                timeout=RUNCLI_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            partial = _runcli_decode(exc.stdout or b"")
            partial = _runcli_truncate(partial)
            return (
                f"错误: 命令执行超时（超过 {RUNCLI_TIMEOUT:g} 秒）: "
                f"{command} {' '.join(args)}\n已产生的输出: {partial or '无'}"
            )
        except FileNotFoundError:
            return (
                f"错误: 未找到命令: {command}。"
                "注意不经 shell，cmd 内置命令（如 dir）不可用，"
                "请改用可执行程序（如 ls、git、py）"
            )

        stdout = _runcli_truncate(_runcli_decode(result.stdout))
        stderr = _runcli_truncate(_runcli_decode(result.stderr))

        lines = [f"命令: {command} {' '.join(args)}".rstrip()]
        lines.append(f"退出码: {result.returncode}")
        lines.append(f"stdout:\n{stdout or '（空）'}")
        if stderr:
            lines.append(f"stderr:\n{stderr}")
        return "\n".join(lines)
    except Exception as exc:
        return f"错误: {type(exc).__name__}: {exc}"


def load_skill(name, filename="SKILL.md"):
    """读取 .agents/skills/<name>/ 下指定文件，严格限制在该 skill 目录内。"""
    try:
        name = (name or "").strip().strip("/\\")
        filename = (filename or "").strip()
        if not name:
            return "错误: skill 名称 (name) 不能为空"
        if not filename:
            return "错误: 文件名 (filename) 不能为空"

        skill_dir, skills_root = _find_skill_dir(name)
        if skill_dir is None:
            searched = "、".join(str(d) for d in _get_skills_dirs())
            return f"错误: skill 不存在: {name}（已搜索: {searched}）"

        # 拒绝绝对路径 / 盘符路径
        candidate = Path(filename)
        if candidate.is_absolute() or (len(filename) >= 2 and filename[1] == ":"):
            return f"错误: filename 必须是 skill 目录内的相对路径: {filename}"

        # 路径穿越检查：无论文件是否存在，解析后必须仍位于 skill 目录内
        target = (skill_dir / candidate).resolve()
        try:
            target.relative_to(skill_dir)
        except ValueError:
            return f"错误: 路径越界，禁止访问 skill 目录之外的文件: {filename}"

        if not target.is_file():
            return f"错误: 文件不存在: {name}/{filename}"

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"错误: 读取文件失败: {exc}"

        if len(content) > SKILL_MAX_CHARS:
            content = content[:SKILL_MAX_CHARS] + "…（内容过长，已截断）"
        return f"skill[{name}] 文件 {filename} 内容:\n\n{content}"
    except Exception as exc:
        return f"错误: {type(exc).__name__}: {exc}"


def create_skill(name, skill_context):
    """在 .agents/skills/<name>/ 下创建符合规范的 SKILL.md，已存在则拒绝覆盖。

    返回创建结果说明；新建的 skill 需重启 Agent 后才会出现在 system prompt
    的技能列表中（技能列表在 Agent 初始化时扫描生成）。
    """
    try:
        name = (name or "").strip().strip("/\\").lower()
        if not name:
            return (
                "错误: skill 名称 (name) 不能为空。"
                "名称须由小写字母、数字和连字符组成，例如 weather-helper"
            )
        if not _SKILL_NAME_RE.match(name):
            return (
                f"错误: skill 名称不合法: {name}。"
                "名称只能由小写字母、数字和连字符组成，且不能以连字符开头或结尾"
            )
        if len(name) > 64:
            return f"错误: skill 名称过长（最多 64 字符）: {name}"

        skill_context = skill_context or ""
        if not skill_context.strip():
            return "错误: skillContext 不能为空，需提供完整的 SKILL.md 内容"
        if len(skill_context) > SKILL_MAX_CHARS:
            return (
                f"错误: skillContext 过长（{len(skill_context)} 字符，"
                f"上限 {SKILL_MAX_CHARS}），请精简内容后重试"
            )

        # 校验 YAML Frontmatter：必须以 --- 开头，且包含 name / description
        stripped = skill_context.lstrip("\ufeff\n\r\t ")
        if not stripped.startswith("---"):
            return (
                "错误: skillContext 不符合 Agent Skills 规范："
                "SKILL.md 必须以 YAML Frontmatter 开头"
                "（首行为 ---，随后是 name、description 等字段，再以 --- 结束），"
                "之后才是正文"
            )
        frontmatter = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        fm_block = frontmatter.split("\n---", 1)[0].lower()
        if "name:" not in fm_block or "description:" not in fm_block:
            return (
                "错误: skillContext 的 YAML Frontmatter 缺少 name 或 description "
                "字段，请补全后重试"
            )

        # frontmatter 中如声明了 name，须与目录名一致，避免加载后名称混乱
        fm_name = None
        for line in frontmatter.split("\n---", 1)[0].splitlines():
            line = line.strip()
            if line.lower().startswith("name:"):
                fm_name = line.split(":", 1)[1].strip().strip("\"'").lower()
                break
        if fm_name and fm_name != name:
            return (
                f"错误: Frontmatter 中的 name ({fm_name}) 与目标 skill 名称 ({name}) "
                "不一致，请统一后重试"
            )

        # 目标目录：优先级最高的 skills 根目录（与 skill_loader 发现逻辑一致）
        skills_root = _get_skills_dirs()[0]
        skill_dir = skills_root / name
        if skill_dir.exists():
            return (
                f"错误: skill 已存在: {name}（目录: {skill_dir}），"
                "为避免覆盖原有内容，本次未做任何修改。"
                "如需更新，请先与用户确认，再用 loadSkill 读取现有内容后"
                "通过 editTextFile 修改"
            )

        skill_dir.mkdir(parents=True, exist_ok=False)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(skill_context, encoding="utf-8")
        return (
            f"已创建 skill: {name}\n"
            f"SKILL.md 路径: {skill_md}\n"
            "注意: 新 skill 将在 Agent 重启后加入可用技能列表。"
            "如需补充 references/、scripts/ 等目录或辅助文件，"
            "请继续调用 createDir、createFile 等文件工具在该 skill 目录内创建"
        )
    except FileExistsError:
        return f"错误: skill 已存在，未覆盖: {name}"
    except Exception as exc:
        return f"错误: {type(exc).__name__}: {exc}"


# ── 小笨羊星座服务（xiaobenyang.com）──────────────────────────────


def _load_xby_api_key():
    """从 config.ini [xby] 读取小笨羊 APIKEY，返回 (key, 错误信息)。"""
    config = get_config()
    api_key = config.get("xby", "api_key", "").strip()
    if not api_key or api_key.startswith("YOUR_"):
        return None, (
            "错误: config.ini 中 [xby] api_key 未配置。"
            "可在聊天中告诉我你的小笨羊 APIKEY，我会调用 setXbyApiKey 保存。"
        )
    return api_key, None


def _extract_xby_text(data):
    """适配小笨羊 API 多种返回格式，提取可读文本。"""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("text", "result", "content", "data"):
            value = data.get(key)
            if isinstance(value, str):
                return value
        inner = data.get("data")
        if isinstance(inner, dict):
            for key in ("text", "result", "content"):
                value = inner.get(key)
                if isinstance(value, str):
                    return value
        return json.dumps(data, ensure_ascii=False, indent=2)
    return str(data)


def _xby_call(func_name, params):
    """调用小笨羊星座平台 API，返回文本；失败时返回错误信息字符串。"""
    api_key, err = _load_xby_api_key()
    if err:
        return err

    request = urllib.request.Request(
        f"{XBY_BASE_URL}/api",
        data=json.dumps(params or {}, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "XBY-APIKEY": api_key,
            "func": func_name,
            "mcpid": XBY_MCP_ID,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=XBY_TIMEOUT) as resp:
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        if exc.code in (401, 403):
            return (
                f"错误: 小笨羊 API 鉴权失败 (HTTP {exc.code})，"
                f"请检查 APIKEY 是否有效。详情: {detail}"
            )
        return f"错误: 小笨羊 API 返回 HTTP {exc.code}: {detail}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return (
            "错误: 网络请求失败，无法连接小笨羊 API: "
            f"{type(exc).__name__}: {exc}"
        )

    if "application/json" in content_type:
        try:
            return _extract_xby_text(json.loads(body))
        except json.JSONDecodeError:
            return f"错误: 小笨羊 API 返回内容不是有效 JSON: {body[:300]}"
    return body


def set_xby_api_key(api_key):
    """把小笨羊 APIKEY 写入 config.ini 的 [xby] 配置段并持久化。"""
    try:
        api_key = (api_key or "").strip()
        if not api_key:
            return "错误: api_key 不能为空"

        config = get_config()
        if not config.path.exists():
            return f"错误: 配置文件不存在: {config.path}（请重新启动程序自动生成）"
        config.set("xby", "api_key", api_key)
        return (
            f"APIKEY 已设置并保存到 {config.path}，"
            "现在可以正常使用星座工具了。"
        )
    except Exception as exc:
        return f"错误: {type(exc).__name__}: {exc}"


def get_zodiac_info(zodiac):
    try:
        zodiac = (zodiac or "").strip()
        if not zodiac:
            return "错误: 星座名称不能为空"
        return _xby_call("get_zodiac_info", {"zodiac": zodiac})
    except Exception as exc:
        return f"错误: {type(exc).__name__}: {exc}"


def get_daily_horoscope(zodiac, category=None):
    try:
        zodiac = (zodiac or "").strip()
        if not zodiac:
            return "错误: 星座名称不能为空"
        params = {"zodiac": zodiac}
        if category and str(category).strip():
            params["category"] = str(category).strip()
        return _xby_call("get_daily_horoscope", params)
    except Exception as exc:
        return f"错误: {type(exc).__name__}: {exc}"


def get_compatibility(zodiac1, zodiac2):
    try:
        zodiac1 = (zodiac1 or "").strip()
        zodiac2 = (zodiac2 or "").strip()
        if not zodiac1 or not zodiac2:
            return "错误: 两个星座名称都不能为空"
        return _xby_call(
            "get_compatibility", {"zodiac1": zodiac1, "zodiac2": zodiac2}
        )
    except Exception as exc:
        return f"错误: {type(exc).__name__}: {exc}"


def get_all_zodiacs():
    try:
        return _xby_call("get_all_zodiacs", {})
    except Exception as exc:
        return f"错误: {type(exc).__name__}: {exc}"


def get_zodiac_by_date(month, day):
    try:
        month = int(month)
        day = int(day)
        if not (1 <= month <= 12):
            return f"错误: 月份必须在 1-12 之间，收到: {month}"
        if not (1 <= day <= 31):
            return f"错误: 日期必须在 1-31 之间，收到: {day}"
        return _xby_call("get_zodiac_by_date", {"month": month, "day": day})
    except Exception as exc:
        return f"错误: {type(exc).__name__}: {exc}"


def get_rising_sign(
    birth_year, birth_month, birth_day, birth_hour, birth_minute, latitude, longitude
):
    try:
        params = {
            "birthYear": int(birth_year),
            "birthMonth": int(birth_month),
            "birthDay": int(birth_day),
            "birthHour": int(birth_hour),
            "birthMinute": int(birth_minute),
            "latitude": float(latitude),
            "longitude": float(longitude),
        }
        if not (1 <= params["birthMonth"] <= 12):
            return f"错误: 月份必须在 1-12 之间，收到: {params['birthMonth']}"
        if not (1 <= params["birthDay"] <= 31):
            return f"错误: 日期必须在 1-31 之间，收到: {params['birthDay']}"
        if not (0 <= params["birthHour"] <= 23):
            return f"错误: 小时必须在 0-23 之间，收到: {params['birthHour']}"
        if not (0 <= params["birthMinute"] <= 59):
            return f"错误: 分钟必须在 0-59 之间，收到: {params['birthMinute']}"
        if not (-90 <= params["latitude"] <= 90):
            return f"错误: 纬度必须在 -90 到 90 之间，收到: {params['latitude']}"
        if not (-180 <= params["longitude"] <= 180):
            return f"错误: 经度必须在 -180 到 180 之间，收到: {params['longitude']}"
        return _xby_call("get_rising_sign", params)
    except Exception as exc:
        return f"错误: {type(exc).__name__}: {exc}"


def get_rising_sign_info(rising_sign):
    try:
        rising_sign = (rising_sign or "").strip()
        if not rising_sign:
            return "错误: 上升星座名称不能为空"
        return _xby_call("get_rising_sign_info", {"risingSign": rising_sign})
    except Exception as exc:
        return f"错误: {type(exc).__name__}: {exc}"


TOOL_MAP = {
    "getDate": get_date,
    "isLeapYear": is_leap_year,
    "getCwd": get_cwd,
    "listDir": list_dir,
    "getFileType": get_file_type,
    "readTextFile": read_text_file,
    "createFile": create_file,
    "editTextFile": edit_text_file,
    "createDir": create_dir,
    "deleteDir": delete_dir,
    "deleteFile": delete_file,
    "renamePath": rename_path,
    "searchWeb": search_web,
    "fetchWeb": fetch_web,
    "RunCli": run_cli,
    "loadSkill": load_skill,
    "createSkill": create_skill,
    "setXbyApiKey": set_xby_api_key,
    "getZodiacInfo": get_zodiac_info,
    "getDailyHoroscope": get_daily_horoscope,
    "getCompatibility": get_compatibility,
    "getAllZodiacs": get_all_zodiacs,
    "getZodiacByDate": get_zodiac_by_date,
    "getRisingSign": get_rising_sign,
    "getRisingSignInfo": get_rising_sign_info,
}
