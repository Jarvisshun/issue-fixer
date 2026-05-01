# Issue Fixer

> An AI Agent that automatically reads GitHub Issues, analyzes the codebase using RAG, generates fixes with LLM, and creates Pull Requests.

<div align="right">

**English** | [中文](./README_zh-CN.md)

</div>

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Features

- **Automated Issue Analysis**: Reads issue title, body, labels, and comments to understand the problem
- **RAG-based Code Search**: Indexes the repository code with ChromaDB and finds relevant files using semantic search
- **LLM-powered Fix Generation**: Uses large language models to analyze root causes and generate complete file fixes
- **Automatic PR Creation**: Creates a new branch, commits the fix, and opens a Pull Request
- **Test Verification**: Runs the project's test suite before and after the fix to detect regressions
- **Incremental Indexing**: Only re-indexes changed files on subsequent runs (hash-based change detection)
- **Web UI**: Browser-based interface for easy issue fixing
- **Multi-model Support**: Works with any OpenAI-compatible API (OpenAI, DeepSeek, MiMo, etc.)

## Demo

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

## Quick Start

### 1. Install

```bash
git clone https://github.com/YOUR_USERNAME/issue-fixer.git
cd issue-fixer
pip install -e .

# For Web UI support:
pip install -e ".[web]"
```

### 2. Configure

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
```

```env
# LLM API (OpenAI-compatible)
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o

# GitHub
GITHUB_TOKEN=ghp_xxx
```

**Getting a GitHub Token**: Go to GitHub Settings > Developer settings > Personal access tokens > Generate new token (classic). Select `repo` scope.

### 3. Use

```bash
# Analyze an issue (no PR created)
issue-fixer fix https://github.com/owner/repo/issues/42 --no-pr

# Fix and create PR
issue-fixer fix https://github.com/owner/repo/issues/42

# Fix with test verification
issue-fixer fix https://github.com/owner/repo/issues/42 --verify --no-pr

# Start Web UI
issue-fixer web
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Issue Fixer                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  GitHub Issue URL                                           │
│       │                                                     │
│       ▼                                                     │
│  ┌──────────────┐     ┌───────────────┐                    │
│  │    GitHub     │     │     Code      │                    │
│  │    Client     │────▶│   Indexer     │                    │
│  │              │     │ (RAG/ChromaDB) │                    │
│  └──────┬───────┘     └───────┬───────┘                    │
│         │                     │                             │
│         ▼                     ▼                             │
│  ┌───────────────────────────────────┐                     │
│  │         LLM Analyzer              │                     │
│  │  ┌──────────┐  ┌──────────────┐   │                     │
│  │  │  Issue    │  │ Code Search  │   │                     │
│  │  │ Analysis  │  │   Results    │   │                     │
│  │  └────┬─────┘  └──────┬───────┘   │                     │
│  │       └───────┬───────┘           │                     │
│  │               ▼                   │                     │
│  │       Fix Generation              │                     │
│  └───────────────┬───────────────────┘                     │
│                  │                                          │
│                  ▼                                          │
│  ┌───────────────────────────────────┐                     │
│  │    Test Runner (optional)         │                     │
│  │  Baseline tests -> Apply fix ->   │                     │
│  │  After-fix tests -> Verdict       │                     │
│  └───────────────┬───────────────────┘                     │
│                  │                                          │
│                  ▼                                          │
│  ┌───────────────────────────────────┐                     │
│  │       PR Creator                  │                     │
│  │  Create branch -> Commit -> PR    │                     │
│  └───────────────────────────────────┘                     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Project Structure

```
issue-fixer/
├── issue_fixer/
│   ├── __init__.py          # Package init
│   ├── config.py            # Configuration management
│   ├── github_client.py     # GitHub API client (issues, repos, PRs)
│   ├── code_indexer.py      # RAG code indexer with incremental updates
│   ├── analyzer.py          # LLM-powered issue analyzer
│   ├── test_runner.py       # Test verification framework
│   ├── main.py              # CLI entry point
│   └── web/
│       ├── __init__.py
│       ├── app.py           # FastAPI backend
│       └── index.html       # Web UI frontend
├── pyproject.toml           # Project configuration
├── .env.example             # Environment variable template
├── .gitignore
├── LICENSE
├── README.md                # English
└── README_zh-CN.md          # Chinese
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `issue-fixer fix <URL>` | Fix an issue and create PR |
| `issue-fixer fix <URL> --no-pr` | Analyze only, no PR |
| `issue-fixer fix <URL> --verify` | Run test verification |
| `issue-fixer fix <URL> --max-files 10` | Process up to 10 files |
| `issue-fixer web` | Start Web UI |
| `issue-fixer info` | Show current configuration |

## Configuration

All settings can be configured via environment variables or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | (required) | API key for LLM service |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | API endpoint (any OpenAI-compatible) |
| `OPENAI_MODEL` | `gpt-4o` | Model name |
| `GITHUB_TOKEN` | (required) | GitHub Personal Access Token |

## Supported Models

Any OpenAI-compatible API works:

| Provider | Base URL | Model Example |
|----------|----------|---------------|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| MiMo (Xiaomi) | `https://token-plan-sgp.xiaomimimo.com/v1` | `mimo-v2.5-pro` |
| Moonshot | `https://api.moonshot.cn/v1` | `moonshot-v1-128k` |
| Zhipu GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4` |

## Tech Stack

| Component | Technology |
|-----------|------------|
| LLM | OpenAI API (compatible with any provider) |
| Vector Database | ChromaDB (local, no external service needed) |
| GitHub API | PyGithub |
| Code Chunking | tiktoken + custom chunking strategy |
| CLI | Click + Rich |
| Git Operations | GitPython |
| Web UI | FastAPI + vanilla HTML/JS |

## Development

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/issue-fixer.git
cd issue-fixer

# Install in development mode
pip install -e ".[web]"

# Run tests
python -m pytest tests/

# Start web UI for development
issue-fixer web --port 8000
```

## How It Works

1. **Issue Parsing**: Extracts issue details (title, body, labels, comments) from GitHub API
2. **Repository Cloning**: Shallow-clones the target repository to local cache
3. **Code Indexing**: Splits code files into chunks and indexes them in ChromaDB for semantic search
4. **Context Retrieval**: Uses RAG to find the most relevant code snippets for the issue
5. **LLM Analysis**: Sends the issue + relevant code to LLM for root cause analysis and fix generation
6. **Test Verification** (optional): Runs project tests before and after applying the fix
7. **PR Creation**: Creates a new branch, commits the fix, and opens a Pull Request

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [LangChain](https://github.com/langchain-ai/langchain) for RAG patterns
- [SWE-agent](https://github.com/Princeton-NLP/SWE-agent) for coding agent inspiration
- [ChromaDB](https://github.com/chroma-core/chroma) for vector storage
