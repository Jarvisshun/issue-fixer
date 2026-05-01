"""Configuration management."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_CACHE_DIR = Path(".repos")


@dataclass
class Config:
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    openai_base_url: str = field(default_factory=lambda: os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    openai_model: str = field(default_factory=lambda: os.environ.get("OPENAI_MODEL", "gpt-4o"))
    github_token: str = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))

    # RAG settings
    chunk_size: int = 1500
    chunk_overlap: int = 200
    top_k: int = 10

    # Agent settings
    max_iterations: int = 5

    def validate(self) -> list[str]:
        errors = []
        if not self.openai_api_key:
            errors.append("OPENAI_API_KEY is not set")
        if not self.github_token:
            errors.append("GITHUB_TOKEN is not set")
        return errors


config = Config()
