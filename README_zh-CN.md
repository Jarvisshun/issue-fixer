# Issue Fixer

> 一个 AI Agent，自动读取 GitHub Issue，使用 RAG 分析代码库，通过 LLM 生成修复，并创建 Pull Request。

<div align="right">

[English](./README.md) | **中文**

</div>

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 功能特性

- **自动化 Issue 分析**：读取 Issue 的标题、描述、标签和评论，理解问题本质
- **Hybrid RAG 搜索**：向量搜索（ChromaDB）+ BM25 关键词搜索 + RRF 重排序，精准检索代码
- **LLM 驱动的修复生成**：使用大语言模型分析根因并生成完整的文件修复
- **Diff/补丁模式**：生成精准的 SEARCH/REPLACE 补丁（Aider、Cursor、SWE-agent 等工具使用的行业标准模式）
- **Multi-Agent 流水线**：五个专业 Agent（分析、搜索、修复、依赖检查、审查）协作生成更高质量的修复
- **反馈学习系统**：记录修复历史，使用过去的成功案例作为 few-shot 示例来改进未来修复
- **置信度评分**：基于补丁质量、审查、沙箱、依赖风险的综合评分（0-100）
- **代码沙箱验证**：在 Python/JS/TS/Go/Rust 中对修复后的代码进行语法检查
- **多文件关联分析**：检测跨文件 import，标记可能需要同步修改的依赖文件
- **本地模型支持**：通过 Ollama 接入 Llama、Qwen、DeepSeek 等本地模型，支持隐私保护和离线使用
- **GitHub Webhook**：监听 Issue 事件，自动触发修复流水线
- **自动创建 PR**：创建新分支、提交修复、打开 Pull Request
- **测试验证**：在修复前后运行项目测试套件，检测回归问题
- **增量索引**：后续运行只重新索引变化的文件（基于哈希的变更检测）
- **Web UI**：基于浏览器的可视化界面，方便操作
- **多模型支持**：兼容任何 OpenAI 格式的 API（OpenAI、DeepSeek、MiMo 等）
- **GitHub Action**：可在 GitHub Marketplace 一键安装，集成到 CI/CD 流水线
- **修复统计 Dashboard**：Web 图表展示修复成功率、Issue 类型分布、趋势等数据
- **Slack/Discord 通知**：修复完成后推送到团队聊天工具
- **插件系统**：支持自定义分析规则、修复策略、审查检查
- **多语言 Prompt 优化**：针对 Python、JS/TS、Go、Java、Rust、C/C++ 的专门 prompt

## 演示

```bash
$ issue-fixer fix https://github.com/owner/repo/issues/42

┌──────────────────────── Issue Fixer ─────────────────────────┐
│ Repository: owner/repo                                       │
│ Issue: #42                                                   │
│ URL: https://github.com/owner/repo/issues/42                 │
└──────────────────────────────────────────────────────────────┘

Fetching issue...
Issue Title: Fix null pointer in auth handler
State: open
Labels: bug

Cloning repository...
Indexed 234 code files

Analyzing issue and generating fix...
┌─────────────── Root Cause Analysis ───────────────┐
│ The auth handler doesn't check for null tokens...  │
└───────────────────────────────────────────────────┘

Generated fix for 2 file(s):
  src/auth/handler.py - Add null check for token
  tests/test_auth.py - Add test case for null token

PR created successfully!
https://github.com/owner/repo/pull/43
```

## 快速开始

### 1. 安装

```bash
git clone https://github.com/Jarvisshun/issue-fixer.git
cd issue-fixer
pip install -e .

# 如需 Web UI 支持：
pip install -e ".[web]"
```

### 2. 配置

复制 `.env.example` 为 `.env`，填入你的 API Key：

```bash
cp .env.example .env
```

```env
# LLM API（兼容 OpenAI 格式）
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o

# GitHub
GITHUB_TOKEN=ghp_xxx
```

**获取 GitHub Token**：GitHub Settings → Developer settings → Personal access tokens → Generate new token (classic)，勾选 `repo` 权限。

### 3. 使用

```bash
# 分析 Issue（不创建 PR）
issue-fixer fix https://github.com/owner/repo/issues/42 --no-pr

# 修复并创建 PR
issue-fixer fix https://github.com/owner/repo/issues/42

# 修复并运行测试验证
issue-fixer fix https://github.com/owner/repo/issues/42 --verify --no-pr

# 启动 Web UI
issue-fixer web
```

## 架构

### 单 Agent 流水线（默认）

```
Issue URL → GitHub 客户端 → 代码索引器 → LLM 分析器 → PR 创建器
```

### Multi-Agent 流水线（`--agent`）

```
Issue URL → GitHub 客户端 → 代码索引器
                                    │
                    ┌───────────────┘
                    ▼
         ┌─────────────────┐
         │   分析 Agent     │  分类 Issue，识别根因
         └────────┬────────┘
                  ▼
         ┌─────────────────┐
         │   搜索 Agent     │  多策略 RAG 搜索
         └────────┬────────┘
                  ▼
         ┌─────────────────┐     ┌──────────────┐
         │   修复 Agent     │────▶│  审查 Agent   │
         │  生成补丁/修复   │◀───│  验证质量     │
         └────────┬────────┘     └──────────────┘
                  │                  （重试循环）
                  ▼
            PR 创建 + 反馈学习
```

### 完整架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Issue Fixer                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  GitHub Issue URL  ──── 或 ────  GitHub Webhook                 │
│       │                              │                          │
│       ▼                              ▼                          │
│  ┌──────────────┐            ┌──────────────┐                  │
│  │   GitHub     │            │   Webhook    │                  │
│  │   客户端     │            │   处理器     │                  │
│  └──────┬───────┘            └──────┬───────┘                  │
│         │                           │                           │
│         ▼                           ▼                           │
│  ┌──────────────┐     ┌───────────────────────┐                │
│  │    代码       │     │  Multi-Agent 流水线    │                │
│  │   索引器     │────▶│  ┌───────────────┐   │                │
│  │ (RAG/ChromaDB)│     │  │  分析 Agent   │   │                │
│  └───────────────┘     │  ├───────────────┤   │                │
│                        │  │  搜索 Agent   │   │                │
│                        │  ├───────────────┤   │                │
│                        │  │  修复 Agent   │◀──┤                │
│                        │  ├───────────────┤   │ (审查循环)     │
│                        │  │  审查 Agent   │───┘                │
│                        │  └───────┬───────┘   │                │
│                        └──────────┼───────────┘                │
│                                   │                             │
│                                   ▼                             │
│  ┌───────────────────────────────────────────────┐             │
│  │  反馈学习系统                                   │             │
│  │  记录结果 → Few-shot 示例 → 持续改进           │             │
│  └───────────────────────────┬───────────────────┘             │
│                               │                                 │
│                               ▼                                 │
│  ┌───────────────────────────────────────────────┐             │
│  │  测试运行器（可选）|  PR 创建器                 │             │
│  └───────────────────────────────────────────────┘             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 项目结构

```
issue-fixer/
├── issue_fixer/
│   ├── __init__.py          # 包初始化
│   ├── config.py            # 配置管理（OpenAI + Ollama）
│   ├── github_client.py     # GitHub API 客户端（Issue、仓库、PR）
│   ├── code_indexer.py      # Hybrid RAG：向量 + BM25 + RRF 重排序
│   ├── analyzer.py          # LLM 驱动的 Issue 分析器（单 Agent）
│   ├── patcher.py           # Diff/补丁引擎（SEARCH/REPLACE）
│   ├── feedback.py          # 反馈学习系统
│   ├── dependency.py        # 跨文件依赖分析
│   ├── sandbox.py           # 代码语法验证沙箱
│   ├── scoring.py           # 置信度评分（0-100）
│   ├── test_runner.py       # 测试验证框架
│   ├── main.py              # CLI 入口
│   ├── agents/              # Multi-Agent 系统
│   │   ├── __init__.py
│   │   ├── context.py       # 共享 Agent 上下文
│   │   ├── base.py          # Agent 基类
│   │   ├── analyzer_agent.py
│   │   ├── search_agent.py
│   │   ├── fix_agent.py
│   │   ├── review_agent.py
│   │   └── orchestrator.py  # 流水线编排器
│   └── web/
│       ├── __init__.py
│       ├── app.py           # FastAPI 后端 + Webhook 处理
│       └── index.html       # Web UI 前端
├── pyproject.toml           # 项目配置
├── .env.example             # 环境变量模板
├── .gitignore
├── LICENSE
├── README.md                # 英文文档
└── README_zh-CN.md          # 中文文档（本文件）
```

## CLI 命令

| 命令 | 说明 |
|------|------|
| `issue-fixer fix <URL>` | 修复 Issue 并创建 PR |
| `issue-fixer fix <URL> --no-pr` | 仅分析，不创建 PR |
| `issue-fixer fix <URL> --verify` | 运行测试验证 |
| `issue-fixer fix <URL> --max-files 10` | 最多处理 10 个文件 |
| `issue-fixer fix <URL> --mode diff` | 使用 diff/补丁模式（默认） |
| `issue-fixer fix <URL> --mode full` | 使用完整文件重写模式 |
| `issue-fixer fix <URL> --agent` | 使用 Multi-Agent 流水线（含审查循环） |
| `issue-fixer fix <URL> --sandbox` | 沙箱语法验证 |
| `issue-fixer web` | 启动 Web UI |
| `issue-fixer stats` | 查看修复历史和成功率统计 |
| `issue-fixer info` | 显示当前配置 |

## 配置项

所有配置可通过环境变量或 `.env` 文件设置：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_PROVIDER` | `openai` | LLM 提供商：`openai` 或 `ollama` |
| `OPENAI_API_KEY` | （必填） | LLM 服务的 API Key |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | API 端点（任何 OpenAI 兼容格式） |
| `OPENAI_MODEL` | `gpt-4o` | 模型名称 |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 服务地址 |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Ollama 模型名称 |
| `GITHUB_TOKEN` | （必填） | GitHub Personal Access Token |
| `GITHUB_WEBHOOK_SECRET` | （可选） | Webhook HMAC 密钥 |

## 支持的模型

任何 OpenAI 兼容的 API 均可使用：

| 提供商 | Base URL | 模型示例 |
|--------|----------|----------|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| MiMo（小米） | `https://token-plan-sgp.xiaomimimo.com/v1` | `mimo-v2.5-pro` |
| Moonshot（月之暗面） | `https://api.moonshot.cn/v1` | `moonshot-v1-128k` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4` |

## 技术栈

| 组件 | 技术 |
|------|------|
| LLM | OpenAI API（兼容任意提供商） |
| 向量数据库 | ChromaDB（本地，无需外部服务） |
| GitHub API | PyGithub |
| 代码分块 | tiktoken + 自定义分块策略 |
| CLI | Click + Rich |
| Git 操作 | GitPython |
| Web UI | FastAPI + 原生 HTML/JS |

## 开发指南

```bash
# 克隆
git clone https://github.com/Jarvisshun/issue-fixer.git
cd issue-fixer

# 开发模式安装
pip install -e ".[web]"

# 运行测试
python -m pytest tests/

# 启动 Web UI 进行开发
issue-fixer web --port 8000
```

## 工作原理

### 单 Agent 模式（默认）
1. **Issue 解析**：从 GitHub API 提取 Issue 详情（标题、描述、标签、评论）
2. **仓库克隆**：浅克隆目标仓库到本地缓存
3. **代码索引**：将代码文件分块并索引到 ChromaDB 进行语义搜索
4. **上下文检索**：使用 RAG 找到与 Issue 最相关的代码片段
5. **LLM 分析**：将 Issue + 相关代码发送给 LLM 进行根因分析和修复生成
6. **测试验证**（可选）：在应用修复前后运行项目测试
7. **PR 创建**：创建新分支、提交修复、打开 Pull Request

### Multi-Agent 模式（`--agent`）
1. **分析 Agent**：对 Issue 进行分类，识别根因，生成针对性搜索查询
2. **搜索 Agent**：执行多策略 RAG 搜索（按查询、按影响区域、搜索测试文件）
3. **修复 Agent**：使用前面 Agent 的上下文生成 SEARCH/REPLACE 补丁
4. **审查 Agent**：验证修复质量，提供改进建议
5. **重试循环**：如果审查未通过，修复 Agent 根据反馈重新生成（最多 2 次迭代）
6. **反馈学习**：记录修复结果，使用过去的成功案例作为 few-shot 示例

### GitHub Webhook
监听 Issue 事件，自动触发 Multi-Agent 流水线。详见下方 [Webhook 配置](#webhook-配置)。

## Webhook 配置

1. 启动 Webhook 服务器：
   ```bash
   issue-fixer web --port 8000
   ```

2. 在 GitHub 仓库中，进入 **Settings → Webhooks → Add webhook**
3. 配置：
   - **Payload URL**：`https://your-server:8000/api/webhook`
   - **Content type**：`application/json`
   - **Secret**：设置与 `.env` 中 `GITHUB_WEBHOOK_SECRET` 相同的值
   - **Events**：选择 "Issues"

4. 查看任务状态：
   ```bash
   curl http://localhost:8000/api/webhook/jobs
   ```

## 贡献

欢迎贡献！请随时提交 Pull Request。

1. Fork 本仓库
2. 创建你的功能分支（`git checkout -b feature/amazing-feature`）
3. 提交你的更改（`git commit -m 'Add amazing feature'`）
4. 推送到分支（`git push origin feature/amazing-feature`）
5. 打开一个 Pull Request

## 许可证

本项目基于 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件。

## 致谢

- [LangChain](https://github.com/langchain-ai/langchain) 的 RAG 模式
- [SWE-agent](https://github.com/Princeton-NLP/SWE-agent) 的编码 Agent 灵感
- [ChromaDB](https://github.com/chroma-core/chroma) 的向量存储
