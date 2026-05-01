"""Fix Agent: Generates patches/fixes for identified issues.

Third stage of the pipeline. Uses the analysis and search results to:
- Generate SEARCH/REPLACE patches (diff mode) or full file rewrites
- Produce PR title and description
- Handle multiple files with coordinated changes
"""

import json
import re
from pathlib import Path

from .base import BaseAgent
from .context import AgentContext
from ..patcher import PatchBlock, apply_patch
from ..feedback import feedback_store
from ..lang_prompts import get_language_guidelines

DIFF_PROMPT = """You are a senior software engineer generating targeted code fixes.

## Context
You've been given:
1. A GitHub Issue with root cause analysis
2. Relevant source code from the repository
3. Test code from the repository

## Task
Generate SEARCH/REPLACE patches to fix the issue.

## Output Format — valid JSON only:
```json
{
  "fix_strategy": "Why this is the minimal safe change",
  "files_to_fix": [
    {
      "path": "relative/path/to/file.ext",
      "reason": "What's wrong and what you're changing",
      "patches": [
        {
          "search": "The EXACT original code block (copy character-for-character from context)",
          "replace": "The new code to replace it with"
        }
      ]
    }
  ],
  "pr_title": "fix: conventional commit title",
  "pr_body": "Summary of changes"
}
```

## Critical Rules
1. SEARCH must match the original code EXACTLY (copy from context)
2. Include 3-5 lines of surrounding context in SEARCH for unique matching
3. Each patch = one logical change
4. Order patches top-to-bottom within each file
5. Don't overlap SEARCH blocks in the same file
6. Minimal changes — don't refactor unrelated code
7. If you cannot generate precise patches, return empty `files_to_fix`
"""

FULL_PROMPT = """You are a senior software engineer generating code fixes.

## Context
You've been given:
1. A GitHub Issue with root cause analysis
2. Relevant source code from the repository
3. Test code from the repository

## Task
Generate complete fixed file content for each file that needs changes.

## Output Format — valid JSON only:
```json
{
  "fix_strategy": "Why this is the minimal safe change",
  "files_to_fix": [
    {
      "path": "relative/path/to/file.ext",
      "reason": "What's wrong and what you're changing",
      "fixed_content": "The COMPLETE new content of this file"
    }
  ],
  "pr_title": "fix: conventional commit title",
  "pr_body": "Summary of changes"
}
```

## Rules
- `fixed_content` = ENTIRE file, not a snippet
- Minimal changes only
- Preserve existing code style
- If context is insufficient, return empty `files_to_fix`
"""


class FixAgent(BaseAgent):
    """Generates code fixes based on analysis and search results."""

    def run(self, ctx: AgentContext) -> AgentContext:
        # Build context from search results
        code_context = self._format_chunks(ctx.relevant_chunks, "Relevant Code")
        test_context = self._format_chunks(ctx.test_chunks, "Test Code")

        # Build the prompt
        issue_text = self._format_issue(ctx.issue)

        user_content = (
            f"## Root Cause Analysis\n\n"
            f"**Type:** {ctx.issue_type}\n"
            f"**Root Cause:** {ctx.root_cause}\n"
            f"**Affected Areas:** {', '.join(ctx.affected_areas)}\n\n"
            f"## GitHub Issue\n\n{issue_text}\n\n"
            f"## Relevant Source Code\n\n{code_context}"
        )
        if test_context:
            user_content += f"\n\n## Related Test Code\n\n{test_context}"

        # Add language-specific guidelines
        candidate_files = [c["file"] for c in ctx.relevant_chunks + ctx.test_chunks]
        lang_guidelines = get_language_guidelines(candidate_files)
        if lang_guidelines:
            user_content += f"\n\n{lang_guidelines}"

        # Add few-shot examples from feedback learning
        examples = feedback_store.format_examples_for_prompt(ctx.issue_type, limit=2)
        if examples:
            user_content += f"\n\n{examples}"

        # Add review feedback if this is a retry
        if ctx.review_feedback:
            user_content += (
                f"\n\n## Previous Review Feedback\n\n"
                f"The previous fix was rejected. Please address these issues:\n"
                f"{ctx.review_feedback}"
            )

        system_prompt = DIFF_PROMPT if ctx.mode == "diff" else FULL_PROMPT

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        raw = self._call_llm(messages)
        result = self._extract_json(raw)

        ctx.fix_strategy = result.get("fix_strategy", "")
        ctx.files_to_fix = result.get("files_to_fix", [])

        # If diff mode, apply patches to get final content
        if ctx.mode == "diff" and ctx.repo_dir and ctx.files_to_fix:
            ctx.files_to_fix = self._apply_patches(ctx.files_to_fix, ctx.repo_dir)

        return ctx

    def _apply_patches(self, files_to_fix: list[dict], repo_dir: Path) -> list[dict]:
        """Apply diff patches to get final file content."""
        resolved = []
        for file_info in files_to_fix:
            file_path = file_info.get("path", "")
            patches_raw = file_info.get("patches", [])

            if file_info.get("fixed_content") and not patches_raw:
                resolved.append(file_info)
                continue

            full_path = repo_dir / file_path
            if not full_path.exists():
                if file_info.get("fixed_content"):
                    resolved.append(file_info)
                continue

            original_content = full_path.read_text(encoding="utf-8", errors="ignore")

            patches = [
                PatchBlock(search=p["search"], replace=p["replace"], file_path=file_path)
                for p in patches_raw
                if p.get("search") is not None and p.get("replace") is not None
            ]

            if not patches:
                continue

            patch_result = apply_patch(original_content, patches, fuzzy=True)

            file_info["fixed_content"] = patch_result.new_content
            file_info["diff"] = patch_result.diff
            file_info["patch_stats"] = {
                "applied": patch_result.applied_blocks,
                "failed": patch_result.failed_blocks,
            }

            if not patch_result.success:
                file_info["patch_error"] = patch_result.error

            resolved.append(file_info)

        return resolved

    def _format_chunks(self, chunks: list[dict], label: str) -> str:
        if not chunks:
            return ""
        parts = []
        for i, chunk in enumerate(chunks, 1):
            parts.append(
                f"### {label} Chunk {i}: {chunk['file']} "
                f"(lines {chunk['start_line']}-{chunk['end_line']})\n"
                f"```\n{chunk['text']}\n```"
            )
        return "\n\n".join(parts)

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
