"""LLM-powered Issue analyzer and code fixer with optimized prompts."""

import json
import re

from openai import OpenAI

from .config import config
from .code_indexer import CodeIndexer

SYSTEM_PROMPT = """You are a senior software engineer performing automated code review and bug fixing. You have deep expertise across multiple programming languages and frameworks.

## Your Task

Analyze a GitHub Issue and the relevant source code, then produce a precise fix.

## Analysis Process (think step by step)

1. **Classify the issue**: Is it a bug, feature request, documentation issue, security concern, or performance problem?
2. **Identify root cause**: What specific code behavior causes the reported problem?
3. **Locate affected files**: Which files need changes? Use the provided code context.
4. **Design the fix**: What minimal change resolves the issue without breaking other functionality?
5. **Generate complete file content**: Write the FULL updated content for each file.

## Response Format

You MUST respond with valid JSON only, no other text. Use this exact structure:

```json
{
  "issue_type": "bug|feature|docs|security|performance|other",
  "analysis": "Step-by-step root cause analysis. Be specific about what code behavior causes the issue.",
  "fix_strategy": "Brief description of your fix approach and why this is the minimal safe change.",
  "files_to_fix": [
    {
      "path": "relative/path/to/file.ext",
      "reason": "Specific explanation of what's wrong and what you're changing",
      "fixed_content": "The COMPLETE new content of this file"
    }
  ],
  "pr_title": "Conventional commit style title (e.g., 'fix: resolve null pointer in auth handler')",
  "pr_body": "Detailed PR body with: Summary, Root Cause, Fix Description, Testing Notes"
}
```

## Critical Rules

1. **COMPLETE content only**: `fixed_content` must be the ENTIRE file, not a snippet or diff
2. **Minimal changes**: Only modify what's necessary to fix the issue. Don't refactor unrelated code.
3. **Preserve style**: Match existing code conventions (indentation, naming, import style)
4. **Don't invent context**: If you need a file that's not provided, say so in `analysis` and return empty `files_to_fix`
5. **Handle edge cases**: Consider null values, empty collections, boundary conditions
6. **Security first**: Never introduce security vulnerabilities (SQL injection, XSS, etc.)
7. **Backward compatibility**: Don't break existing public APIs unless that's the explicit goal

## When You Cannot Fix

If the provided code context is insufficient (missing key files, architectural changes needed, etc.), return:
```json
{
  "issue_type": "...",
  "analysis": "Explain what you found and what's missing",
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

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from ```json ... ``` or ``` ... ``` block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding the outermost { ... } block
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
            model=config.openai_model,
            messages=messages,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content
    except Exception:
        pass

    # Fallback: some models don't support response_format
    response = client.chat.completions.create(
        model=config.openai_model,
        messages=messages,
        temperature=0.1,
    )
    return response.choices[0].message.content


class Analyzer:
    """Analyze issues and generate fixes using LLM + RAG."""

    def __init__(self, indexer: CodeIndexer):
        self.client = OpenAI(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url,
        )
        self.indexer = indexer

    def analyze_issue(self, issue: dict) -> dict:
        """Analyze an issue and return a fix plan."""
        # Step 1: Use RAG to find relevant code
        query = f"{issue['title']}\n{issue['body'][:2000]}"
        relevant_chunks = self.indexer.search(query, top_k=config.top_k)

        # Step 2: Also search for test files related to the issue
        test_chunks = self.indexer.search(
            f"test {issue['title']}", top_k=3
        )

        # Step 3: Build context from relevant code
        code_context = self._build_code_context(relevant_chunks)
        test_context = self._build_code_context(test_chunks, label="Test Files")

        # Step 4: Build the full issue description
        issue_text = self._build_issue_text(issue)

        # Step 5: Call LLM with optimized prompt
        user_content = f"## GitHub Issue\n\n{issue_text}\n\n## Relevant Source Code\n\n{code_context}"
        if test_context:
            user_content += f"\n\n## Related Test Code\n\n{test_context}"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        raw = _call_llm(self.client, messages)
        result = _extract_json(raw)

        # Normalize: ensure required fields exist
        result.setdefault("analysis", "")
        result.setdefault("files_to_fix", [])
        result.setdefault("pr_title", f"Fix: {issue['title'][:60]}")
        result.setdefault("pr_body", result.get("analysis", ""))

        return result

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

Respond in JSON:
{{
  "needs_change": true/false,
  "reason": "why or why not",
  "fixed_content": "complete new file content (only if needs_change is true)"
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
