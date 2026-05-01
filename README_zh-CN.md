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
- **RAG 代码搜索**：使用 ChromaDB 索引仓库代码，通过语义搜索找到相关文件
- **LLM 驱动的修复生成**：使用大语言模型分析根因并生成完整的文件修复
- **自动创建 PR**：创建新分支、提交修复、打开 Pull Request
- **测试验证**：在修复前后运行项目测试套件，检测回归问题
- **增量索引**：后续运行只重新索引变化的文件（基于哈希的变更检测）
- **Web UI**：基于浏览器的可视化界面，方便操作
- **多模型支持**：兼容任何 OpenAI 格式的 API（OpenAI、DeepSeek、MiMo 等）

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

```
┌─────────────────────────────────────────────────────────────┐
│                      Issue Fixer                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  GitHub Issue URL                                           │
│       │                                                     │
│       ▼                                                     │
│  ┌──────────────┐     ┌───────────────┐                    │
│  │    GitHub     │     │     代码      │                    │
│  │    客户端     │────▶│    索引器     │                    │
│  │              │     │ (RAG/ChromaDB) │                    │
│  └──────┬───────┘     └───────┬───────┘                    │
│         │                     │                             │
│         ▼                     ▼                             │
│  ┌───────────────────────────────────┐                     │
│  │         LLM 分析器                │                     │
│  │  ┌──────────┐  ┌──────────────┐   │                     │
│  │  │  Issue    │  │  代码搜索    │   │                     │
│  │  │  分析     │  │    结果      │   │                     │
│  │  └────┬─────┘  └──────┬───────┘   │                     │
│  │       └───────┬───────┘           │                     │
│  │               ▼                   │                     │
│  │       修复生成                     │                     │
│  └───────────────┬───────────────────┘                     │
│                  │                                          │
│                  ▼                                          │
│  ┌───────────────────────────────────┐                     │
│  │    测试运行器（可选）              │                     │
│  │  基准测试 -> 应用修复 ->           │                     │
│  │  修复后测试 -> 判定结果            │                     │
│  └───────────────┬───────────────────┘                     │
│                  │                                          │
│                  ▼                                          │
│  ┌───────────────────────────────────┐                     │
│  │       PR 创建器                    │                     │
│  │  创建分支 -> 提交 -> 创建 PR       │                     │
│  └───────────────────────────────────┘                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 项目结构

```
issue-fixer/
├── issue_fixer/
│   ├── __init__.py          # 包初始化
│   ├── config.py            # 配置管理
│   ├── github_client.py     # GitHub API 客户端（Issue、仓库、PR）
│   ├── code_indexer.py      # RAG 代码索引器（支持增量更新）
│   ├── analyzer.py          # LLM 驱动的 Issue 分析器
│   ├── test_runner.py       # 测试验证框架
│   ├── main.py              # CLI 入口
│   └── web/
│       ├── __init__.py
│       ├── app.py           # FastAPI 后端
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
| `issue-fixer web` | 启动 Web UI |
| `issue-fixer info` | 显示当前配置 |

## 配置项

所有配置可通过环境变量或 `.env` 文件设置：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENAI_API_KEY` | （必填） | LLM 服务的 API Key |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | API 端点（任何 OpenAI 兼容格式） |
| `OPENAI_MODEL` | `gpt-4o` | 模型名称 |
| `GITHUB_TOKEN` | （必填） | GitHub Personal Access Token |

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

1. **Issue 解析**：从 GitHub API 提取 Issue 详情（标题、描述、标签、评论）
2. **仓库克隆**：浅克隆目标仓库到本地缓存
3. **代码索引**：将代码文件分块并索引到 ChromaDB 进行语义搜索
4. **上下文检索**：使用 RAG 找到与 Issue 最相关的代码片段
5. **LLM 分析**：将 Issue + 相关代码发送给 LLM 进行根因分析和修复生成
6. **测试验证**（可选）：在应用修复前后运行项目测试
7. **PR 创建**：创建新分支、提交修复、打开 Pull Request

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
