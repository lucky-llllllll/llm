import configparser, json, urllib.request, sys, subprocess, platform, os, shutil
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

c = configparser.ConfigParser()
c.read(Path(__file__).with_name("config.ini"), encoding="utf-8")
s = c["llm"]

tools = [
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
            "description": "读取指定纯文本文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "encoding": {
                        "type": "string",
                        "description": "文件编码，默认为 utf-8",
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
]


def get_date():
    if platform.system() == "Windows":
        date = subprocess.run(
            ["cmd", "/c", "date", "/t"],
            capture_output=True, text=True, encoding="mbcs",
        ).stdout.strip()
        time = subprocess.run(
            ["cmd", "/c", "time", "/t"],
            capture_output=True, text=True, encoding="mbcs",
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


def read_text_file(path, encoding="utf-8"):
    try:
        with open(path, "r", encoding=encoding) as f:
            return f.read()
    except Exception as e:
        return f"错误: {e}"


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


def stream_chat(messages):
    req = urllib.request.Request(
        s["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps({
            "model": s["model"],
            "messages": messages,
            "tools": tools,
            "stream": True,
        }).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + s["api_key"],
        },
    )
    print("模型: ", end="", flush=True)
    reply = ""
    calls = {}
    for line in urllib.request.urlopen(req):
        if line.startswith(b"data: ") and b"[DONE]" not in line:
            delta = json.loads(line[6:])["choices"][0].get("delta", {})
            content = delta.get("content") or ""
            reply += content
            print(content, end="", flush=True)
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                call = calls.setdefault(idx, {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                })
                if tc.get("id"):
                    call["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    call["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    call["function"]["arguments"] += fn["arguments"]
    if reply:
        print()
    return reply, [calls[i] for i in sorted(calls)]


messages = []
while True:
    msg = input("你: ")
    messages.append({"role": "user", "content": msg})
    while True:
        reply, tool_calls = stream_chat(messages)
        if not tool_calls:
            messages.append({"role": "assistant", "content": reply})
            break
        messages.append({
            "role": "assistant",
            "content": reply,
            "tool_calls": tool_calls,
        })
        for tc in tool_calls:
            name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"] or "{}")
            if name == "getDate":
                result = get_date()
            elif name == "isLeapYear":
                year = int(args["year"])
                result = f"{year} 年是闰年" if is_leap_year(year) else f"{year} 年不是闰年"
            elif name == "getCwd":
                result = get_cwd()
            elif name == "listDir":
                result = list_dir(args.get("path", "."))
            elif name == "getFileType":
                result = get_file_type(args["path"])
            elif name == "readTextFile":
                result = read_text_file(args["path"], args.get("encoding", "utf-8"))
            elif name == "createFile":
                result = create_file(
                    args["directory"], args["filename"], args.get("content", "")
                )
            elif name == "editTextFile":
                result = edit_text_file(
                    args["path"],
                    args["content"],
                    args.get("append", False),
                    args.get("encoding", "utf-8"),
                )
            elif name == "createDir":
                result = create_dir(args["path"])
            elif name == "deleteDir":
                result = delete_dir(args["path"], args.get("recursive", True))
            elif name == "deleteFile":
                result = delete_file(args["path"])
            elif name == "renamePath":
                result = rename_path(args["source"], args["target"])
            else:
                result = f"未知工具: {name}"
            print(f"[调用工具 {name}] {result}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })
