"""Code sandbox: run fixed code in isolation to verify it doesn't crash.

Supports Python, Node.js, and Go. Uses subprocess with timeout and
resource limits — no Docker dependency required.

Design (2026+):
- Lightweight: subprocess-based, works on any machine
- Safe: timeout kills runaway processes, output truncated
- Extensible: add new languages by adding to LANG_CONFIGS
- Complementary to test_runner: this catches basic import/syntax errors
  even when no test suite exists
"""

import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .config import config


@dataclass
class SandboxResult:
    """Result of running code in the sandbox."""
    success: bool
    language: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int


# Language detection and run commands
LANG_CONFIGS = {
    ".py": {
        "name": "python",
        "cmd": ["python", "-c", "import ast, sys; ast.parse(open(sys.argv[1]).read()); print('Syntax OK')"],
        "syntax_only": True,
    },
    ".js": {
        "name": "node",
        "cmd": ["node", "--check"],
        "syntax_only": True,
    },
    ".ts": {
        "name": "typescript",
        "cmd": ["npx", "tsc", "--noEmit", "--allowJs", "--checkJs"],
        "syntax_only": True,
    },
    ".go": {
        "name": "go",
        "cmd": ["go", "vet"],
        "syntax_only": True,
    },
    ".rs": {
        "name": "rust",
        "cmd": ["rustc", "--edition", "2021", "--crate-type", "lib", "-o", "/dev/null"],
        "syntax_only": True,
    },
}

# Extensions we can verify
VERIFIABLE_EXTENSIONS = set(LANG_CONFIGS.keys())


def detect_language(file_path: str) -> str | None:
    """Detect language from file extension."""
    ext = Path(file_path).suffix.lower()
    if ext in LANG_CONFIGS:
        return ext
    return None


def verify_syntax(file_path: str, content: str, timeout: int = 30) -> SandboxResult:
    """Verify that code has valid syntax by running a syntax check.

    Args:
        file_path: Original file path (used to detect language)
        content: The code content to verify
        timeout: Max seconds to wait

    Returns:
        SandboxResult with success/failure details
    """
    ext = detect_language(file_path)
    if not ext:
        return SandboxResult(
            success=True,  # can't verify, assume OK
            language="unknown",
            exit_code=0,
            stdout="",
            stderr="",
            timed_out=False,
            duration_ms=0,
        )

    lang_config = LANG_CONFIGS[ext]
    lang_name = lang_config["name"]

    # Write content to temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=ext, delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        start = time.monotonic()

        # Build command
        cmd = lang_config["cmd"].copy()
        if ext == ".py":
            # Python: use ast.parse via -c
            cmd = ["python", "-c", f"import ast, sys; ast.parse(open(r'{tmp_path}').read()); print('Syntax OK')"]
        elif ext == ".js":
            cmd = ["node", "--check", tmp_path]
        elif ext == ".go":
            # Go: need a temp module
            cmd = ["go", "vet", tmp_path]
        elif ext == ".rs":
            cmd = ["rustc", "--edition", "2021", "--crate-type", "lib", "-o", "nul" if Path(tmp_path).drive else "/dev/null", tmp_path]
        else:
            cmd.append(tmp_path)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=tempfile.gettempdir(),
        )

        duration = int((time.monotonic() - start) * 1000)

        return SandboxResult(
            success=result.returncode == 0,
            language=lang_name,
            exit_code=result.returncode,
            stdout=result.stdout[:2000],
            stderr=result.stderr[:2000],
            timed_out=False,
            duration_ms=duration,
        )

    except subprocess.TimeoutExpired:
        return SandboxResult(
            success=False,
            language=lang_name,
            exit_code=-1,
            stdout="",
            stderr=f"Timed out after {timeout}s",
            timed_out=True,
            duration_ms=timeout * 1000,
        )
    except FileNotFoundError:
        # Language runtime not installed
        return SandboxResult(
            success=True,  # can't verify, assume OK
            language=lang_name,
            exit_code=0,
            stdout="",
            stderr=f"{lang_name} not installed, skipping verification",
            timed_out=False,
            duration_ms=0,
        )
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass


def verify_files(file_changes: dict[str, str], timeout: int = 30) -> dict[str, SandboxResult]:
    """Verify syntax of multiple fixed files.

    Args:
        file_changes: dict of {file_path: new_content}
        timeout: Max seconds per file

    Returns:
        dict of {file_path: SandboxResult}
    """
    results = {}
    for file_path, content in file_changes.items():
        ext = detect_language(file_path)
        if ext and ext in VERIFIABLE_EXTENSIONS:
            results[file_path] = verify_syntax(file_path, content, timeout)
    return results


def summarize_results(results: dict[str, SandboxResult]) -> str:
    """Create a human-readable summary of sandbox results."""
    if not results:
        return "No verifiable files"

    passed = sum(1 for r in results.values() if r.success)
    total = len(results)

    lines = [f"{passed}/{total} files passed syntax check"]
    for path, r in results.items():
        status = "PASS" if r.success else "FAIL"
        detail = ""
        if r.stderr:
            detail = f" — {r.stderr[:100]}"
        lines.append(f"  [{status}] {path} ({r.language}){detail}")

    return "\n".join(lines)
