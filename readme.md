# LLM Agent & MCP 项目合集
本仓库包含多个基于MCP协议的AI Agent服务，用于大模型工具调用、自主任务执行。

## 项目模块
- **AnythingLLMMCP**：AnythingLLM 的 MCP 连接器
- **AnythingLLMServer**：AnythingLLM 后端服务
- **Nana-Agent**：Nana 自主智能体
- **PetHospitalMCP**：宠物医院业务MCP服务

## 环境依赖
- Python / Node.js
- Ollama 或者大模型API
- MCP 协议支持

## 启动说明
1. 准备好模型密钥，配置对应模块内的 `.env` / `config.ini`
2. 分别进入各个子项目目录，按照各自文档启动服务
3. Agent 可通过 MCP 协议调用各个工具服务

## 安全提醒
- 不要上传 API Key、密钥、数据库密码
- 本仓库已配置 .gitignore 忽略敏感配置、缓存文件
