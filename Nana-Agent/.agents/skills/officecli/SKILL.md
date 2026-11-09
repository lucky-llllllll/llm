---
name: officecli
description: 用 officecli 命令行工具创建和编辑 Word/Excel/PPT 文档（.docx/.xlsx/.pptx）。当用户要求生成或修改 Word 文档、Excel 表格、PPT 幻灯片，或要求"保存为本地 word/excel/ppt 文档"时使用本技能。
version: 2.0
---

# OfficeCli 文档生成技能（officecli）

officecli 是单文件命令行工具（本机已可直接执行 `officecli` 命令），无需 Office 安装、无 Python 依赖。通过 RunCli 工具调用，参数逐个放入 args 列表。

## 重要：禁止事项

1. **禁止把纯文本直接写入 .docx 后缀的文件**——那是假 Word 文档，Word 打开会报错。
2. **不要用 python-docx 写脚本**，直接用 officecli 命令。
3. **不确定语法时先查 help**（如 `officecli help docx paragraph`），不要猜，不要凭记忆编造语法。

## 生成 Word 文档的推荐流程（batch 批量，2-3 次调用完成）

**第 1 步**：用 createFile 工具写一个 JSON 命令文件（如 commands.json），把全部段落的 add 命令放进一个数组。text 中的 `\n` 会自动分段：

```json
[
  {"command":"add","parent":"/body","type":"paragraph","props":{"text":"演讲稿标题","bold":"true"}},
  {"command":"add","parent":"/body","type":"paragraph","props":{"text":"第一段正文...\n\n第二段正文...\n\n第三段正文..."}},
  {"command":"add","parent":"/body","type":"paragraph","props":{"text":"小节标题","bold":"true"}},
  {"command":"add","parent":"/body","type":"paragraph","props":{"text":"小节正文..."}}
]
```

**第 2 步**：创建文档并一次性填充（两条 RunCli 调用）：

```
RunCli(command="officecli", args=["create", "输出.docx", "--force"])
RunCli(command="officecli", args=["batch", "输出.docx", "--input", "commands.json"])
```

**第 3 步**：校验并核对：

```
RunCli(command="officecli", args=["validate", "输出.docx"])
RunCli(command="officecli", args=["view", "输出.docx", "text"])
```

写简单短文档时也可以不用 batch，直接逐条 `add`（见下），但长文档务必用 batch 控制调用次数。

## 常用单条命令（语法已验证）

```
officecli create <file>.docx [--force]                  # 创建空文档，--force 覆盖已存在的
officecli add <file> /body --type paragraph --prop text="内容" --prop style=Heading1
officecli set <file> /body/p[1] --prop font.bold=true --prop font.size=14pt
officecli set <file> / --find 旧文字 --replace 新文字      # 全文查找替换
officecli view <file> text|outline|stats|issues          # 查看
officecli validate <file>                                # 校验
officecli close <file>                                   # 释放文件（resident 占用时）
officecli help docx paragraph                            # 查段落全部属性
```

## 常见报错处理

| 报错 | 处理 |
|------|------|
| File already exists | 加 `--force` 重新 create，或换文件名 |
| file locked / resident process | 先执行 `officecli close <file>` 再操作 |
| style 未定义警告 | 无害，officecli 会自动补内置样式 |
| 命令不存在/语法错 | 运行 `officecli help` 查正确语法，禁止编造 |

## 工作提示

- 演讲稿、报告等长文档：全部内容一次写进 commands.json，一次 batch 搞定，不要逐段调用耗尽轮数。
- 中文直接放入 `--prop text=` 或 JSON 的 props，无需转义。
- 生成后用 `view text` 核对内容，并告诉用户文件的完整路径。
