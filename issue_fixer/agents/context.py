"""Shared context for Multi-Agent communication."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentContext:
    """Shared state passed between agents during a fix pipeline run."""

    # Input
    issue: dict = field(default_factory=dict)
    repo_dir: Path | None = None
    mode: str = "diff"

    # Analyzer Agent output
    issue_type: str = ""
    root_cause: str = ""
    affected_areas: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)

    # Search Agent output
    relevant_chunks: list[dict] = field(default_factory=list)
    test_chunks: list[dict] = field(default_factory=list)
    candidate_files: list[str] = field(default_factory=list)

    # Fix Agent output
    files_to_fix: list[dict] = field(default_factory=list)
    fix_strategy: str = ""

    # Review Agent output
    review_approved: bool = False
    review_feedback: str = ""
    review_score: int = 0  # 0-100

    # Pipeline metadata
    iteration: int = 0
    max_iterations: int = 2
    errors: list[str] = field(default_factory=list)
