"""LLM-powered Issue analyzer with diff-based patching and full-file fallback."""

import json
import re
from pathlib import Path

from openai import OpenAI

from .config import config
from .code_indexer import CodeIndexer
from .patcher import PatchBlock, apply_patch, generate_diff

# ─── Full-file mode prompt (legacy, fallback) ───────────────────────────────

FULL_FILE_PROMPT = """You are a senior software engineer performing automated code review and bug fixing.

## Your Task
Analyze a GitHub Issue and the relevant source code, then produce a precise fix.

## Analysis Process (think step by step)
1. **Classify**: bug / feature / docs / security / performance / other
2. **Root cause**: What specific code behavior causes the problem?
3. **Locate**: Which files need changes?
4. **Design**: What minimal change resolves it?
5. **Generate**: Write the FULL updated content for each file.

## Response Format — valid JSON only:
```json
{
  "issue_type": "bug|feature|docs|security|performance|other",
  "analysis": "Step-by-step root cause analysis",
  "fix_strategy": "Why this is the minimal safe change",
  "files_to_fix": [
    {
      "path": "relative/path/to/file.ext",
      "reason": "What's wrong and what you're changing",
      "fixed_content": "The COMPLETE new content of this file"
    }
  ],
  "pr_title": "fix: conventional commit title",
  "pr_body": "Summary, Root Cause, Fix Description, Testing Notes"
}
```

## Rules
- `fixed_content` = ENTIRE file, not a snippet
- Minimal changes only — don't refactor unrelated code
- Preserve existing code style
- If context is insufficient, return empty `files_to_fix`
"""

# ─── Diff/patch mode prompt (preferred for 2026+) ───────────────────────────

DIFF_PROMPT = """You are a senior software engineer performing automated code review and bug fixing.

## Your Task
Analyze a GitHub Issue and produce **targeted patches** (not full file rewrites).

## Analysis Process
1. **Classify**: bug / feature / docs / security / performance / other
2. **Root cause**: What code behavior causes the problem?
3. **Locate**: Which files and which specific lines need changes?
4. **Patch**: Generate SEARCH/REPLACE blocks for each change.

## Response Format — valid JSON only:
```json
{
  "issue_type": "bug|feature|docs|security|performance|other",
  "analysis": "Step-by-step root cause analysis",
  "fix_strategy": "Why this is the minimal safe change",
  "files_to_fix": [
    {
      "path": "relative/path/to/file.ext",
      "reason": "What's wrong and what you're changing",
      "patches": [
        {
          "search": "The EXACT original code block to find (must match file content exactly)",
          "replace": "The new code to replace it with"
        }
      ]
    }
  ],
  "pr_title": "fix: conventional commit title",
  "pr_body": "Summary, Root Cause, Fix Description, Testing Notes"
}
```

## Critical Rules for SEARCH/REPLACE blocks
1. **SEARCH must match exactly**: Copy the original code character-for-character from the provided context
2. **Include enough context**: Include 3-5 lines before/after the change point so the match is unique
3. **Keep blocks small**: Each block should be one logical change (one function, one condition, etc.)
4. **Order matters**: Apply patches in file order (top to bottom)
5. **Don't overlap**: SEARCH blocks in the same file must not overlap
6. **If you can't generate precise patches**, return empty `files_to_fix` and explain in `analysis`

## When You Cannot Fix
If context is insufficient, return:
```json
{
  "issue_type": "...",
  "analysis": "Explain what's missing",
  "fix_strategy": "",
  "files_to_fix": [],
  "pr_title": "",
  "pr_body": ""
}
```
"""


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown code blocks."""
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

    depth = 0
    start = -1
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

    raise ValueError(f"Could not extract JSON from LLM response:\n{text[:500]}")


def _call_llm(client: OpenAI, messages: list[dict]) -> str:
    """Call LLM, trying with response_format first, falling back without it."""
    try:
        response = client.chat.completions.create(
            model=config.llm_model,
            messages=messages,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content
    except Exception:
        pass

    response = client.chat.completions.create(
        model=config.llm_model,
        messages=messages,
        temperature=0.1,
    )
    return response.choices[0].message.content


class Analyzer:
    """Analyze issues and generate fixes using LLM + RAG.

    Supports two modes:
    - diff mode (default): generates SEARCH/REPLACE patches (safer, auditable)
    - full mode: generates complete file content (fallback)
    """

    def __init__(self, indexer: CodeIndexer):
        self.client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
        )
        self.indexer = indexer

    def analyze_issue(self, issue: dict, repo_dir: Path | None = None, mode: str = "diff") -> dict:
        """Analyze an issue and return a fix plan.

        Args:
            issue: Issue dict from GitHubClient
            repo_dir: Repository directory (needed for diff mode to read original files)
            mode: "diff" for patch-based fixes, "full" for complete file rewrites

        Returns:
            dict with analysis, files_to_fix, pr_title, pr_body
        """
        # Step 1: RAG search
        query = f"{issue['title']}\n{issue['body'][:2000]}"
        relevant_chunks = self.indexer.search(query, top_k=config.top_k)
        test_chunks = self.indexer.search(f"test {issue['title']}", top_k=3)

        code_context = self._build_code_context(relevant_chunks)
        test_context = self._build_code_context(test_chunks, label="Test Files")

        issue_text = self._build_issue_text(issue)

        # Step 2: Build prompt
        user_content = f"## GitHub Issue\n\n{issue_text}\n\n## Relevant Source Code\n\n{code_context}"
        if test_context:
            user_content += f"\n\n## Related Test Code\n\n{test_context}"

        system_prompt = DIFF_PROMPT if mode == "diff" else FULL_FILE_PROMPT

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        # Step 3: Call LLM
        raw = _call_llm(self.client, messages)
        result = _extract_json(raw)

        # Step 4: Normalize
        result.setdefault("analysis", "")
        result.setdefault("files_to_fix", [])
        result.setdefault("pr_title", f"Fix: {issue['title'][:60]}")
        result.setdefault("pr_body", result.get("analysis", ""))

        # Step 5: If diff mode, apply patches to get final content
        if mode == "diff" and repo_dir and result["files_to_fix"]:
            result["files_to_fix"] = self._apply_diff_patches(
                result["files_to_fix"], repo_dir
            )

        return result

    def _apply_diff_patches(self, files_to_fix: list[dict], repo_dir: Path) -> list[dict]:
        """Apply diff patches to get final file content."""
        resolved = []
        for file_info in files_to_fix:
            file_path = file_info.get("path", "")
            patches_raw = file_info.get("patches", [])

            # If it already has fixed_content (fallback from LLM), keep it
            if file_info.get("fixed_content") and not patches_raw:
                resolved.append(file_info)
                continue

            # Read original file
            full_path = repo_dir / file_path
            if not full_path.exists():
                # File doesn't exist yet — treat as new file
                if file_info.get("fixed_content"):
                    resolved.append(file_info)
                continue

            original_content = full_path.read_text(encoding="utf-8", errors="ignore")

            # Parse patches
            patches = [
                PatchBlock(search=p["search"], replace=p["replace"], file_path=file_path)
                for p in patches_raw
                if p.get("search") is not None and p.get("replace") is not None
            ]

            if not patches:
                # No valid patches, skip
                continue

            # Apply patches
            patch_result = apply_patch(original_content, patches, fuzzy=True)

            if patch_result.success:
                file_info["fixed_content"] = patch_result.new_content
                file_info["diff"] = patch_result.diff
                file_info["patch_stats"] = {
                    "applied": patch_result.applied_blocks,
                    "failed": patch_result.failed_blocks,
                }
                resolved.append(file_info)
            else:
                # Patch failed — fall back to asking LLM for full file
                # Store the error for debugging
                file_info["patch_error"] = patch_result.error
                file_info["diff"] = patch_result.diff
                # Keep the file in results even if patch failed
                # The caller can decide to retry with full mode
                resolved.append(file_info)

        return resolved

    def analyze_issue_full(self, issue: dict) -> dict:
        """Convenience: analyze with full-file mode (legacy behavior)."""
        return self.analyze_issue(issue, mode="full")

    def refine_with_file(self, issue: dict, file_path: str, file_content: str) -> dict | None:
        """Ask LLM to look at a specific file for more context."""
        prompt = f"""I'm analyzing this issue:
Title: {issue['title']}
Body: {issue['body'][:1500]}

I need to check if this file needs changes. Here is its full content:

File: {file_path}
```
{file_content[:8000]}
```

Respond in JSON with SEARCH/REPLACE patches:
{{
  "needs_change": true/false,
  "reason": "why or why not",
  "patches": [
    {{
      "search": "exact original code to find",
      "replace": "new code to replace with"
    }}
  ]
}}"""

        messages = [
            {"role": "system", "content": "You are an expert software engineer. Respond only in valid JSON."},
            {"role": "user", "content": prompt},
        ]
        raw = _call_llm(self.client, messages)
        return _extract_json(raw)

    def _build_code_context(self, chunks: list[dict], label: str = "Code") -> str:
        """Format code chunks into context string."""
        parts = []
        for i, chunk in enumerate(chunks, 1):
            parts.append(
                f"### {label} Chunk {i}: {chunk['file']} (lines {chunk['start_line']}-{chunk['end_line']})\n"
                f"```\n{chunk['text']}\n```"
            )
        return "\n\n".join(parts)

    def _build_issue_text(self, issue: dict) -> str:
        """Format issue into text."""
        text = f"**Title:** {issue['title']}\n\n**Body:**\n{issue['body']}"
        if issue.get("labels"):
            text += f"\n\n**Labels:** {', '.join(issue['labels'])}"
        if issue.get("comments"):
            text += "\n\n**Comments:**"
            for c in issue["comments"][:5]:
                text += f"\n- @{c['author']}: {c['body'][:500]}"
        return text
