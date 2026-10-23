# Agent 工具调用练习项目

本项目是一个基于 OpenAI 兼容接口的本地大模型 Agent 工具调用练习，通过流式响应和 function calling 技术实现智能体与用户的交互。项目采用迭代开发模式，逐步引入新的文件操作工具，帮助开发者掌握 LLM 工具调用的核心概念和最佳实践。

## 项目概述

本项目演示了如何在本地环境中利用支持 OpenAI 格式的大语言模型（如 Qwen、Llama3 等）实现智能体的工具调用能力。通过流式响应技术，用户可以看到模型推理过程的实时输出，同时借助 function calling 机制，智能体能够自动识别用户需求并调用相应的工具完成操作。

### 核心技术特性

- **OpenAI 兼容接口**：支持所有符合 OpenAI API 规范的本地大模型
- **流式响应（Streaming）**：实现低延迟的交互式对话体验
- **Function Calling**：自动将自然语言请求映射到具体工具函数
- **迭代开发模式**：从基础对话到复杂工具集，循序渐进地扩展能力

## 项目结构

```
Agent/
├── Practice01/
│   ├── chat.py                        # 基础流式对话版本
│   └── config.ini                     # 模型服务配置
└── Practice02/
    ├── chat.py                        # 基于 Practice01 的流式工具调用版本
    ├── chat-stream-tool-isLeapYear.py # 新增 isLeapYear 工具
    ├── chat-stream-tool-fileOps.py    # 新增完整文件操作工具集
    ├── config.ini                     # 模型服务配置
    ├── architecture.mmd               # Mermaid 架构图
    └── README.md                      # 本文档
```

## 架构图

Mermaid 架构图保存在 [architecture.mmd](architecture.mmd)，展示三个版本的迭代关系、12 个工具以及 `stream_chat` 流式调用主流程。

## 迭代版本说明

### Practice01: 基础流式对话 (`chat.py`)

**特点**：项目的起点，专注于实现最基本的流式对话功能。

**功能**：
- 建立与本地大模型的连接
- 实现完整的消息序列处理（system/user/assistant）
- 展示流式响应效果，用户可实时看到模型输出
- 提供基本的对话交互框架

**适用场景**：
- 快速测试本地大模型的可用性
- 验证 OpenAI 接口配置正确性
- 学习基础的 API 调用流程

### Practice02: 工具调用增强版

#### `chat-stream-tool-isLeapYear.py` - 首个工具版本

**新增功能**：
- 引入 `isLeapYear` 判断闰年工具
- 演示函数签名定义与参数映射机制
- 展示模型如何识别"2024 是闰年吗？"这类问题并自动调用工具

**学习重点**：
- Tool Definition 的结构和字段含义
- Parameters 的类型定义规则
- 工具返回结果的处理方式

#### `chat-stream-tool-fileOps.py` - 完整文件操作工具集

**新增功能**：引入完整的文件操作能力，包含以下 **12 个工具**。其中 10 个文件工具均基于 Python 内置的 `os` / `shutil` 实现，并带有异常捕获，失败时返回错误信息。

| 序号 | 工具名称 | 功能描述 | 输入参数示例 |
|------|----------|----------|--------------|
| 1 | `getDate` | 获取当前电脑的日期和时间 | - |
| 2 | `isLeapYear` | 判断指定年份是否为闰年 | `year: integer` (如 2024) |
| 3 | `getCwd` | 获取当前工作目录 | - |
| 4 | `listDir` | 列出指定目录下所有文件和子目录 | `path: string` (目录路径，默认当前目录) |
| 5 | `getFileType` | 获取指定文件或目录的类型 | `path: string` (文件或目录路径) |
| 6 | `readTextFile` | 读取指定纯文本文件内容 | `path: string`, `encoding: string` (默认为 utf-8) |
| 7 | `createFile` | 在指定目录下创建新文件 | `directory: string`, `filename: string`, `content: string` (默认空) |
| 8 | `editTextFile` | 编辑纯文本文件内容，可覆盖或追加 | `path: string`, `content: string`, `append: boolean` (默认 false), `encoding: string` |
| 9 | `createDir` | 创建新目录，可包含多级父目录 | `path: string` |
| 10 | `deleteDir` | 删除目录，默认递归删除非空目录 | `path: string`, `recursive: boolean` (默认为 true) |
| 11 | `deleteFile` | 删除指定文件 | `path: string` |
| 12 | `renamePath` | 重命名文件或目录 | `source: string`, `target: string` |

**学习重点**：
- 复杂参数类型的定义与验证
- 工具之间的协作调用模式
- 错误处理与安全操作（如删除前的确认）
- 实际应用场景的模拟与测试

## 运行方法

### 环境准备

1. **Python 版本要求**：Python 3.8+，仅使用标准库，无需安装第三方依赖。
2. **模型服务**：启动 LM Studio 等 OpenAI 兼容服务，并在 `config.ini` 中配置 `base_url`、`api_key` 和 `model`。
3. **运行目录**：在 `Agent` 目录下执行命令，程序会按脚本所在位置读取 `config.ini`。

### 启动命令

```bash
# Practice01: 基础对话
python Practice01/chat.py

# Practice02-版本一：测试 isLeapYear 工具
python Practice02/chat-stream-tool-isLeapYear.py

# Practice02-版本二：完整文件操作工具集（推荐）
python Practice02/chat-stream-tool-fileOps.py
```

### 交互示例

启动程序后，在终端中输入以下命令进行测试：

```
# 测试基础对话
你好！今天天气怎么样？

# 测试 isLeapYear 工具
2024 是闰年吗？
判断一下 2030 是不是闰年。

# 测试文件操作工具
- 获取当前目录：给我看看当前工作目录是什么
- 列出目录内容：显示当前目录下的所有文件和子目录
- 创建文件：请在当前目录创建一个名为 test.txt 的文件，内容写"Hello World"
- 读取文件：把刚才创建的 test.txt 内容读出来
- 编辑文件：给 test.txt 追加一行"这是第二行"
- 删除文件：删除 test.txt 文件
- 创建目录：在当前目录下创建一个 test_dir 目录
- 重命名：将 test_dir 改名为 my_folder
```

## 工具调用原理说明

### Function Calling 机制

1. **工具定义注册**：程序启动时将所有可用工具的 schema 注册给模型
2. **请求解析**：用户输入被送入模型进行意图识别
3. **工具选择**：模型根据意图选择合适的工具（可能多个工具组合）
4. **参数填充**：模型将工具调用的具体参数填入 tool_call 字段
5. **工具执行**：本地 Python 代码执行工具函数，返回结果
6. **结果反馈**：将执行结果作为消息追加，模型继续生成回复

### 流式响应优势

- **低延迟体验**：用户看到输出即表示处理正在进行
- **实时交互感**：模拟真实聊天场景的响应节奏
- **过程可见性**：可观察模型思考过程的逐步生成

## 最佳实践建议

1. **工具调用前验证**：确保工具参数符合定义类型
2. **错误处理机制**：捕获并优雅处理文件不存在的异常
3. **目录操作谨慎**：删除操作前最好确认路径正确性
4. **编码一致性**：文本文件建议使用 UTF-8 编码
5. **权限注意**：创建/删除操作考虑当前用户权限

## 常见问题

### Q: 为什么工具调用有时不准确？

A: 可能是工具定义描述不够清晰，或模型对参数理解不足。建议：
- 使用更具体的 tool_name 和 description
- 在 parameters 中添加更多示例值
- 将复杂操作拆分为多个简单工具

### Q: 流式响应卡顿怎么办？

A: 
- 检查网络连接（如使用远程模型）
- 降低生成速度参数（adjust stream timeout）
- 确保本地大模型足够强大

### Q: 如何调试工具调用过程？

A: 程序会打印 tool_call 和 tool_result 信息，查看终端输出即可。也可添加详细的 logging。

## 扩展方向

- **更多工具**：文件搜索、文本摘要、代码生成等
- **多工具组合**：支持一次调用多个工具的复杂任务
- **工具权限管理**：区分只读/可写操作的安全控制
- **人类反馈强化**：收集用户对工具使用效果的反馈

---

*最后更新：2026 年 10 月*

本项目适合学习 LLM 应用开发、Function Calling 机制和 Agent 设计模式。欢迎在此基础上继续扩展，探索更多有趣的工具调用场景！