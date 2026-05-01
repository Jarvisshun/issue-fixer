"""Feedback Learning System: records fix history and improves future fixes.

Persists fix outcomes to a local JSON store. Over time, the system uses
successful past fixes as few-shot examples in LLM prompts, and tracks
success rates by issue type to guide strategy selection.

Design rationale (2026+):
- Few-shot learning from past successes is the most cost-effective way
  to improve LLM output quality without fine-tuning
- Issue-type-specific metrics help the orchestrator decide when to use
  multi-agent vs single-agent pipeline
- All data is local (no external service), suitable for individual use
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import config

# Default store location
FEEDBACK_DIR = Path.home() / ".issue-fixer"
FEEDBACK_FILE = FEEDBACK_DIR / "feedback.json"


@dataclass
class FixRecord:
    """A single fix attempt record."""

    # Issue info
    issue_url: str = ""
    issue_title: str = ""
    issue_type: str = ""

    # Fix info
    repo: str = ""
    files_changed: list[str] = field(default_factory=list)
    mode: str = "diff"
    pipeline: str = "single-agent"

    # Outcome
    success: bool = False
    pr_created: bool = False
    pr_url: str = ""
    review_score: int = 0
    error: str = ""

    # Metadata
    timestamp: str = ""
    model: str = ""
    iteration_count: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FixRecord":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class FeedbackStats:
    """Aggregated feedback statistics."""

    total_fixes: int = 0
    successful_fixes: int = 0
    success_rate: float = 0.0
    by_type: dict = field(default_factory=dict)  # issue_type -> {total, success, rate}
    by_pipeline: dict = field(default_factory=dict)  # pipeline -> {total, success, rate}


class FeedbackStore:
    """Persistent feedback storage and retrieval.

    Stores fix records in ~/.issue-fixer/feedback.json.
    Provides retrieval of relevant past fixes for few-shot learning.
    """

    def __init__(self, store_path: Path | None = None):
        self.store_path = store_path or FEEDBACK_FILE
        self._records: list[FixRecord] = []
        self._load()

    def _load(self):
        """Load records from disk."""
        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                self._records = [FixRecord.from_dict(r) for r in data]
            except (json.JSONDecodeError, TypeError):
                self._records = []

    def _save(self):
        """Persist records to disk."""
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        data = [r.to_dict() for r in self._records]
        self.store_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def record_fix(self, record: FixRecord):
        """Record a fix attempt."""
        if not record.timestamp:
            record.timestamp = datetime.now(timezone.utc).isoformat()
        if not record.model:
            record.model = config.openai_model
        self._records.append(record)
        self._save()

    def get_successful_examples(
        self, issue_type: str = "", limit: int = 3
    ) -> list[FixRecord]:
        """Get past successful fixes for few-shot learning.

        Prioritizes:
        1. Same issue type
        2. Most recent
        3. Highest review score
        """
        successful = [r for r in self._records if r.success]

        if issue_type:
            typed = [r for r in successful if r.issue_type == issue_type]
            # If we have enough of the same type, use those
            if len(typed) >= limit:
                typed.sort(key=lambda r: (r.review_score, r.timestamp), reverse=True)
                return typed[:limit]
            # Otherwise mix typed + general
            remaining = [r for r in successful if r.issue_type != issue_type]
            remaining.sort(key=lambda r: (r.review_score, r.timestamp), reverse=True)
            return (typed + remaining)[:limit]

        successful.sort(key=lambda r: (r.review_score, r.timestamp), reverse=True)
        return successful[:limit]

    def get_stats(self) -> FeedbackStats:
        """Compute aggregated feedback statistics."""
        stats = FeedbackStats()
        stats.total_fixes = len(self._records)

        if not self._records:
            return stats

        stats.successful_fixes = sum(1 for r in self._records if r.success)
        stats.success_rate = (
            stats.successful_fixes / stats.total_fixes if stats.total_fixes else 0
        )

        # By issue type
        for r in self._records:
            t = r.issue_type or "unknown"
            if t not in stats.by_type:
                stats.by_type[t] = {"total": 0, "success": 0, "rate": 0.0}
            stats.by_type[t]["total"] += 1
            if r.success:
                stats.by_type[t]["success"] += 1

        for t, data in stats.by_type.items():
            data["rate"] = data["success"] / data["total"] if data["total"] else 0

        # By pipeline
        for r in self._records:
            p = r.pipeline or "single-agent"
            if p not in stats.by_pipeline:
                stats.by_pipeline[p] = {"total": 0, "success": 0, "rate": 0.0}
            stats.by_pipeline[p]["total"] += 1
            if r.success:
                stats.by_pipeline[p]["success"] += 1

        for p, data in stats.by_pipeline.items():
            data["rate"] = data["success"] / data["total"] if data["total"] else 0

        return stats

    def format_examples_for_prompt(
        self, issue_type: str = "", limit: int = 2
    ) -> str:
        """Format past successful fixes as few-shot examples for LLM prompt."""
        examples = self.get_successful_examples(issue_type, limit)
        if not examples:
            return ""

        parts = ["## Past Successful Fixes (for reference)\n"]
        for i, ex in enumerate(examples, 1):
            parts.append(
                f"### Example {i}: {ex.issue_title}\n"
                f"- **Type:** {ex.issue_type}\n"
                f"- **Files:** {', '.join(ex.files_changed[:3])}\n"
                f"- **Mode:** {ex.mode}\n"
                f"- **Review Score:** {ex.review_score}/100\n"
            )
        return "\n".join(parts)

    def get_all_records(self) -> list[FixRecord]:
        """Return all records (for display/export)."""
        return list(self._records)

    def clear(self):
        """Clear all records."""
        self._records = []
        self._save()


# Singleton
feedback_store = FeedbackStore()
