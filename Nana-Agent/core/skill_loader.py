"""轻量 skill 扫描器。

扫描项目根目录下 .agents/skills 的每个子文件夹，
读取其中 SKILL.md 的 YAML frontmatter，提取 name 与 description。
只读文件头部（frontmatter 区域），不读取正文。
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_ROOT / ".agents" / "skills"

# frontmatter 通常不超过几 KB，只读头部即可，避免加载正文
_HEAD_CHARS = 8192

# 匹配文件开头第一个 `--- ... ---` 块
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)
# 匹配 `key: value` 形式的一行
_KEY_RE = re.compile(r"^([A-Za-z][\w-]*)[ \t]*:[ \t]*(.*)$")
# YAML 块标量标记（| 或 > 及其变体）
_BLOCK_SCALARS = {"|", ">", "|-", ">-", "|+", ">+"}


def _parse_frontmatter(text: str) -> dict[str, str]:
    """解析 markdown 头部的 YAML frontmatter，返回简单 key: value 字典。

    支持单行值、引号包裹的值，以及 `|` / `>` 块标量（折叠为一行）。
    不做完整 YAML 解析，够提取 name / description 即可。
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}

    meta: dict[str, str] = {}
    lines = match.group(1).splitlines()
    i = 0
    while i < len(lines):
        m = _KEY_RE.match(lines[i])
        if not m:
            i += 1
            continue

        key = m.group(1)
        value = m.group(2).strip()

        if value in _BLOCK_SCALARS:
            # 块标量：收集后续缩进（或空）行，折叠为一行
            collected: list[str] = []
            i += 1
            while i < len(lines) and (
                not lines[i].strip() or lines[i][:1] in (" ", "\t")
            ):
                collected.append(lines[i].strip())
                i += 1
            value = " ".join(part for part in collected if part)
        else:
            i += 1

        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\"", "'"):
            value = value[1:-1]

        meta[key] = value
    return meta


def _candidate_skills_dirs() -> list[Path]:
    """候选 skills 根目录（按优先级，与 core.tools.loadSkill 保持一致）：
    1. 当前工作目录下的 .agents/skills
    2. 代码所在项目根目录下的 .agents/skills
    """
    dirs = [
        Path.cwd() / ".agents" / "skills",
        PROJECT_ROOT / ".agents" / "skills",
    ]
    unique: list[Path] = []
    seen: set[Path] = set()
    for d in dirs:
        r = d.resolve()
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


def discover_skills(skills_dir: str | Path | None = None) -> list[dict[str, str]]:
    """扫描 skills 目录，返回各 skill 的元信息列表。

    每个 skill 是 .agents/skills 下的一个子文件夹，其中含 SKILL.md。
    默认合并扫描「工作目录」与「项目根目录」两处的 .agents/skills，
    同名 skill 以先出现的（工作目录）为准。
    返回项形如 {"name": ..., "description": ..., "path": <SKILL.md 路径>}；
    frontmatter 缺失时 name 回退为文件夹名，description 为空串。
    """
    roots = (
        [Path(skills_dir)]
        if skills_dir is not None
        else _candidate_skills_dirs()
    )
    skills: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for root in roots:
        if not root.is_dir():
            continue

        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if child.name in seen_names:
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.is_file():
                continue

            try:
                with skill_md.open("r", encoding="utf-8", errors="replace") as f:
                    head = f.read(_HEAD_CHARS)
            except OSError:
                continue

            meta = _parse_frontmatter(head)
            seen_names.add(child.name)
            skills.append(
                {
                    "name": meta.get("name", child.name),
                    "description": meta.get("description", ""),
                    "path": str(skill_md),
                }
            )
    return skills
