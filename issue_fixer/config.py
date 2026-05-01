"""Configuration management."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_CACHE_DIR = Path(".repos")


@dataclass
class Config:
    # LLM provider: "openai" (any OpenAI-compatible API) or "ollama" (local)
    llm_provider: str = field(default_factory=lambda: os.environ.get("LLM_PROVIDER", "openai"))

    # OpenAI-compatible API settings (also used for DeepSeek, MiMo, etc.)
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    openai_base_url: str = field(default_factory=lambda: os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    openai_model: str = field(default_factory=lambda: os.environ.get("OPENAI_MODEL", "gpt-4o"))

    # Ollama local model settings
    ollama_base_url: str = field(default_factory=lambda: os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b"))

    github_token: str = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))
    github_webhook_secret: str = field(default_factory=lambda: os.environ.get("GITHUB_WEBHOOK_SECRET", ""))

    # RAG settings
    chunk_size: int = 1500
    chunk_overlap: int = 200
    top_k: int = 10

    # Agent settings
    max_iterations: int = 5

    @property
    def llm_api_key(self) -> str:
        """Get the API key for the active provider."""
        if self.llm_provider == "ollama":
            return "ollama"  # Ollama doesn't need a real key
        return self.openai_api_key

    @property
    def llm_base_url(self) -> str:
        """Get the base URL for the active provider."""
        if self.llm_provider == "ollama":
            return f"{self.ollama_base_url}/v1"
        return self.openai_base_url

    @property
    def llm_model(self) -> str:
        """Get the model name for the active provider."""
        if self.llm_provider == "ollama":
            return self.ollama_model
        return self.openai_model

    def validate(self) -> list[str]:
        errors = []
        if self.llm_provider == "ollama":
            # Ollama doesn't need API key, just check URL is reachable at runtime
            pass
        elif not self.openai_api_key:
            errors.append("OPENAI_API_KEY is not set (or set LLM_PROVIDER=ollama for local models)")
        if not self.github_token:
            errors.append("GITHUB_TOKEN is not set")
        return errors


config = Config()
