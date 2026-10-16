import configparser, json, urllib.request, sys
sys.stdout.reconfigure(encoding="utf-8")

c = configparser.ConfigParser()
c.read("config.ini", encoding="utf-8")
s = c["llm"]

messages = []
while True:
    msg = input("你: ")
    messages.append({"role": "user", "content": msg})
    req = urllib.request.Request(
        s["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps({
            "model": s["model"],
            "messages": messages,
            "stream": True,
        }).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + s["api_key"],
        },
    )
    print("模型: ", end="", flush=True)
    reply = ""
    for line in urllib.request.urlopen(req):
        if line.startswith(b"data: ") and b"[DONE]" not in line:
            d = json.loads(line[6:])["choices"][0]["delta"].get("content", "")
            reply += d
            print(d, end="", flush=True)
    print()
    messages.append({"role": "assistant", "content": reply})
