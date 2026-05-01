"""Diff/patch engine: generate and apply SEARCH/REPLACE patches.

Industry-standard pattern used by Aider, Cursor, SWE-agent.
Instead of rewriting entire files, we generate targeted patches that:
1. Are auditable (human can review the exact changes)
2. Are safe (original code is preserved outside the change)
3. Support fuzzy matching (minor whitespace/formatting differences)
4. Fall back to full file rewrite if patching fails
"""

import re
from dataclasses import dataclass
from difflib import unified_diff


@dataclass
class PatchBlock:
    """A single SEARCH/REPLACE change block."""
    search: str    # Original code to find
    replace: str   # New code to replace with
    file_path: str = ""


@dataclass
class PatchResult:
    """Result of applying a patch."""
    success: bool
    file_path: str
    original_content: str
    new_content: str
    applied_blocks: int
    failed_blocks: int
    error: str | None = None
    diff: str = ""


def parse_patch_blocks(text: str) -> list[PatchBlock]:
    """Parse SEARCH/REPLACE blocks from LLM response.

    Expected format:
    path/to/file.py
    <<<<<<< SEARCH
    original code to find
    =======
    new code to replace with
    >>>>>>> REPLACE
    """
    blocks = []
    current_file = ""

    # Pattern: file path followed by SEARCH/REPLACE blocks
    # Also handles ``` fenced blocks
    lines = text.strip().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Detect file path (lines ending with common extensions, or starting with path separators)
        if re.match(r'^[\w\-./\\]+\.\w+$', line) and not line.startswith(("<<<", "===", ">>>")):
            current_file = line
            i += 1
            continue

        # Detect SEARCH block
        if line.startswith("<<<<<<<") and "SEARCH" in line:
            search_lines = []
            i += 1
            while i < len(lines):
                if lines[i].strip().startswith("======="):
                    break
                search_lines.append(lines[i])
                i += 1

            # Skip ======= line
            i += 1

            # Collect REPLACE block
            replace_lines = []
            while i < len(lines):
                if lines[i].strip().startswith(">>>>>>>") and "REPLACE" in line:
                    break
                if lines[i].strip().startswith("<<<<<<<"):
                    break
                replace_lines.append(lines[i])
                i += 1

            search_text = "\n".join(search_lines)
            replace_text = "\n".join(replace_lines)

            if search_text.strip() or replace_text.strip():
                blocks.append(PatchBlock(
                    search=search_text,
                    replace=replace_text,
                    file_path=current_file,
                ))

        i += 1

    return blocks


def apply_patch(original_content: str, blocks: list[PatchBlock], fuzzy: bool = True) -> PatchResult:
    """Apply SEARCH/REPLACE blocks to original content.

    Args:
        original_content: The original file content
        blocks: List of patch blocks to apply
        fuzzy: If True, try fuzzy matching when exact match fails

    Returns:
        PatchResult with the patched content or error info
    """
    if not blocks:
        return PatchResult(
            success=False,
            file_path="",
            original_content=original_content,
            new_content=original_content,
            applied_blocks=0,
            failed_blocks=0,
            error="No patch blocks provided",
        )

    result_content = original_content
    applied = 0
    failed = 0

    for block in blocks:
        search = block.search
        replace = block.replace

        # Try exact match first
        if search in result_content:
            result_content = result_content.replace(search, replace, 1)
            applied += 1
            continue

        # Try fuzzy match: normalize whitespace
        if fuzzy:
            # Normalize: strip trailing whitespace, normalize line endings
            normalized_search = "\n".join(line.rstrip() for line in search.split("\n"))
            normalized_content = "\n".join(line.rstrip() for line in result_content.split("\n"))

            if normalized_search in normalized_content:
                # Find the position in normalized content
                idx = normalized_content.find(normalized_search)
                if idx >= 0:
                    # Map back to original content positions
                    # Find corresponding position in original
                    original_lines = result_content.split("\n")
                    search_lines = search.split("\n")

                    # Simple approach: find the first matching line
                    for oi, orig_line in enumerate(original_lines):
                        if orig_line.rstrip() == search_lines[0].rstrip():
                            # Check if subsequent lines match
                            match = True
                            for si, sline in enumerate(search_lines):
                                if oi + si >= len(original_lines):
                                    match = False
                                    break
                                if original_lines[oi + si].rstrip() != sline.rstrip():
                                    match = False
                                    break
                            if match:
                                # Replace these lines
                                replace_lines = replace.split("\n")
                                original_lines[oi:oi + len(search_lines)] = replace_lines
                                result_content = "\n".join(original_lines)
                                applied += 1
                                break
                    else:
                        failed += 1
                    continue

        # Try indentation-flexible match
        if fuzzy:
            # Strip common leading whitespace from both search and content
            search_dedent = _dedent(search)
            content_dedent = _dedent(result_content)
            if search_dedent in content_dedent:
                # Apply with adjusted indentation
                idx = content_dedent.find(search_dedent)
                # Find the indentation of the matched block in original
                lines_before = content_dedent[:idx].count("\n")
                original_lines = result_content.split("\n")
                if lines_before < len(original_lines):
                    indent = _get_indent(original_lines[lines_before])
                    replace_lines = replace.split("\n")
                    replace_indented = "\n".join(
                        indent + line if line.strip() else line
                        for line in replace_lines
                    )
                    search_indented = "\n".join(
                        indent + line if line.strip() else line
                        for line in search.split("\n")
                    )
                    if search_indented in result_content:
                        result_content = result_content.replace(search_indented, replace_indented, 1)
                        applied += 1
                        continue

        failed += 1

    # Generate diff
    diff = ""
    if result_content != original_content:
        diff_lines = unified_diff(
            original_content.splitlines(keepends=True),
            result_content.splitlines(keepends=True),
            fromfile="original",
            tofile="fixed",
        )
        diff = "".join(diff_lines)

    return PatchResult(
        success=failed == 0 and applied > 0,
        file_path=blocks[0].file_path if blocks else "",
        original_content=original_content,
        new_content=result_content,
        applied_blocks=applied,
        failed_blocks=failed,
        error=f"{failed} block(s) failed to apply" if failed > 0 else None,
        diff=diff,
    )


def _dedent(text: str) -> str:
    """Remove common leading whitespace from all lines."""
    lines = text.split("\n")
    # Find minimum indentation (excluding empty lines)
    min_indent = float("inf")
    for line in lines:
        stripped = line.lstrip()
        if stripped:
            min_indent = min(min_indent, len(line) - len(stripped))
    if min_indent == float("inf") or min_indent == 0:
        return text
    return "\n".join(line[min_indent:] if line.strip() else "" for line in lines)


def _get_indent(line: str) -> str:
    """Get the leading whitespace of a line."""
    return line[:len(line) - len(line.lstrip())]


def generate_diff(original: str, fixed: str, file_path: str = "") -> str:
    """Generate a unified diff between original and fixed content."""
    diff_lines = unified_diff(
        original.splitlines(keepends=True),
        fixed.splitlines(keepends=True),
        fromfile=f"a/{file_path}" if file_path else "original",
        tofile=f"b/{file_path}" if file_path else "fixed",
    )
    return "".join(diff_lines)
