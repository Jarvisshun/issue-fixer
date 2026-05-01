"""Review Agent: Validates fix quality and provides feedback.

Fourth stage of the pipeline. Reviews the generated fixes for:
- Correctness: Does the fix address the root cause?
- Completeness: Are all affected files covered?
- Safety: Could the fix introduce regressions?
- Style: Does the fix follow the codebase conventions?
"""

import json
import re

from .base import BaseAgent
from .context import AgentContext

REVIEW_PROMPT = """You are a senior code reviewer evaluating an automated fix.

## Task
Review the proposed fix against the original issue and source code.
Decide if the fix is GOOD ENOUGH to ship, or needs revision.

## Output Format — valid JSON only:
```json
{
  "approved": true/false,
  "score": 0-100,
  "summary": "Brief overall assessment",
  "checks": {
    "addresses_root_cause": {"pass": true/false, "notes": "..."},
    "minimal_changes": {"pass": true/false, "notes": "..."},
    "no_regressions": {"pass": true/false, "notes": "..."},
    "code_quality": {"pass": true/false, "notes": "..."}
  },
  "feedback": "Specific issues to fix if not approved. Empty string if approved."
}
```

## Scoring Guide
- 90-100: Excellent fix, ship it
- 70-89: Good fix, minor concerns but acceptable
- 50-69: Needs improvement, specific feedback provided
- 0-49: Poor fix, major issues

## Rules
- Be pragmatic — perfect is the enemy of good
- Focus on correctness over style
- A fix that works but isn't perfect is better than no fix
- Score >= 70 means approved
- If approved, leave feedback empty
"""


class ReviewAgent(BaseAgent):
    """Reviews fix quality and decides if it's ready to ship."""

    def run(self, ctx: AgentContext) -> AgentContext:
        if not ctx.files_to_fix:
            ctx.review_approved = False
            ctx.review_feedback = "No files to fix were generated."
            ctx.review_score = 0
            return ctx

        # Build review context
        fix_summary = self._format_fixes(ctx.files_to_fix)
        issue_text = self._format_issue(ctx.issue)

        user_content = (
            f"## Original Issue\n\n{issue_text}\n\n"
            f"## Root Cause Analysis\n{ctx.root_cause}\n\n"
            f"## Proposed Fix Strategy\n{ctx.fix_strategy}\n\n"
            f"## Proposed Changes\n\n{fix_summary}"
        )

        messages = [
            {"role": "system", "content": REVIEW_PROMPT},
            {"role": "user", "content": user_content},
        ]

        raw = self._call_llm(messages)
        result = self._extract_json(raw)

        ctx.review_approved = result.get("approved", False)
        ctx.review_score = result.get("score", 0)
        ctx.review_feedback = result.get("feedback", "")

        # Override: if score >= 70, force approve
        if ctx.review_score >= 70:
            ctx.review_approved = True

        return ctx

    def _format_fixes(self, files_to_fix: list[dict]) -> str:
        parts = []
        for f in files_to_fix:
            part = f"### `{f['path']}`\n**Reason:** {f.get('reason', 'N/A')}\n"
            if f.get("diff"):
                part += f"```diff\n{f['diff'][:1000]}\n```"
            elif f.get("fixed_content"):
                part += f"```\n{f['fixed_content'][:1000]}\n```"
            parts.append(part)
        return "\n\n".join(parts)

    def _format_issue(self, issue: dict) -> str:
        text = f"**Title:** {issue['title']}\n\n**Body:**\n{issue['body']}"
        if issue.get("labels"):
            text += f"\n\n**Labels:** {', '.join(issue['labels'])}"
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
