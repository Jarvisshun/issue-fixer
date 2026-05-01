"""Analyzer Agent: Classifies issues and identifies root cause.

First stage of the pipeline. Examines the issue title, body, labels,
and comments to produce:
- Issue type classification
- Root cause hypothesis
- Affected areas in the codebase
- Targeted search queries for the Search Agent
"""

import json
import re

from .base import BaseAgent
from .context import AgentContext

ANALYZER_PROMPT = """You are an expert issue analyst for a software engineering team.

## Task
Analyze this GitHub Issue and produce a structured assessment.

## Output Format — valid JSON only:
```json
{
  "issue_type": "bug|feature|docs|security|performance|refactor|other",
  "severity": "critical|high|medium|low",
  "root_cause_hypothesis": "Your analysis of what likely causes this issue",
  "affected_areas": [
    "list of likely affected directories/modules, e.g. src/auth/, src/api/handlers"
  ],
  "search_queries": [
    "targeted search query 1 to find relevant code",
    "targeted search query 2",
    "targeted search query 3"
  ],
  "keywords": ["important", "technical", "terms"],
  "confidence": 0.0 to 1.0
}
```

## Rules
- `search_queries` should be specific enough to find the relevant code (not generic)
- Include 2-5 search queries targeting different aspects of the issue
- `affected_areas` should list directory prefixes or module names
- Be specific about the root cause — don't just restate the symptoms
- If the issue is unclear, set confidence below 0.5 and explain in root_cause_hypothesis
"""


class AnalyzerAgent(BaseAgent):
    """Classifies issues and generates targeted analysis."""

    def run(self, ctx: AgentContext) -> AgentContext:
        issue = ctx.issue
        issue_text = self._format_issue(issue)

        messages = [
            {"role": "system", "content": ANALYZER_PROMPT},
            {"role": "user", "content": f"## GitHub Issue\n\n{issue_text}"},
        ]

        raw = self._call_llm(messages)
        result = self._extract_json(raw)

        ctx.issue_type = result.get("issue_type", "other")
        ctx.root_cause = result.get("root_cause_hypothesis", "")
        ctx.affected_areas = result.get("affected_areas", [])
        ctx.search_queries = result.get("search_queries", [])

        # Ensure we have at least one search query
        if not ctx.search_queries:
            ctx.search_queries = [
                f"{issue['title']}",
                f"test {issue['title']}",
            ]

        return ctx

    def _format_issue(self, issue: dict) -> str:
        text = f"**Title:** {issue['title']}\n\n**Body:**\n{issue['body']}"
        if issue.get("labels"):
            text += f"\n\n**Labels:** {', '.join(issue['labels'])}"
        if issue.get("comments"):
            text += "\n\n**Comments:**"
            for c in issue["comments"][:5]:
                text += f"\n- @{c['author']}: {c['body'][:500]}"
        return text

    def _extract_json(self, text: str) -> dict:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
        # Brace matching fallback
        depth, start = 0, -1
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        start = -1
        return {}
