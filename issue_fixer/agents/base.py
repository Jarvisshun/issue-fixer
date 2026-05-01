"""Base agent class for Multi-Agent system."""

from abc import ABC, abstractmethod
from openai import OpenAI

from ..config import config
from .context import AgentContext


class BaseAgent(ABC):
    """Base class for all agents in the system.

    Supports any OpenAI-compatible API (OpenAI, DeepSeek, MiMo, etc.)
    and local models via Ollama.
    """

    def __init__(self):
        self.client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
        )
        self.model = config.llm_model

    @abstractmethod
    def run(self, ctx: AgentContext) -> AgentContext:
        """Execute the agent's task and return updated context."""
        ...

    def _call_llm(self, messages: list[dict], temperature: float = 0.1) -> str:
        """Call LLM with fallback for response_format.

        Ollama models may not support json_object response_format,
        so we try with it first and fall back without.
        """
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            return resp.choices[0].message.content
        except Exception:
            pass

        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
        )
        return resp.choices[0].message.content
