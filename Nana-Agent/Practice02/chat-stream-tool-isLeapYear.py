import configparser, json, urllib.request, sys, subprocess, platform
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
            if name == "getDate":
                result = get_date()
            elif name == "isLeapYear":
                args = json.loads(tc["function"]["arguments"] or "{}")
                year = int(args["year"])
                result = f"{year} 年是闰年" if is_leap_year(year) else f"{year} 年不是闰年"
            else:
                result = f"未知工具: {name}"
            print(f"[调用工具 {name}] {result}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })
