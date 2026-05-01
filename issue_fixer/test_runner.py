"""Test runner for verifying fixes against project test suites."""

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TestResult:
    success: bool
    framework: str
    output: str
    exit_code: int


def detect_test_framework(repo_dir: Path) -> list[dict]:
    """Detect which test frameworks are available in the project."""
    available = []
    files = {f.name for f in repo_dir.iterdir() if f.is_file()}

    # Check for pytest
    if any((repo_dir / f).exists() for f in ["pytest.ini", "conftest.py"]) or "pyproject.toml" in files:
        # Verify pytest is installed
        try:
            subprocess.run(
                ["python", "-m", "pytest", "--version"],
                capture_output=True, timeout=10,
            )
            available.append({"cmd": ["python", "-m", "pytest", "--tb=short", "-q", "--timeout=60"], "name": "pytest"})
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # Check for package.json with test script
    pkg_json = repo_dir / "package.json"
    if pkg_json.exists():
        try:
            import json
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            if "test" in pkg.get("scripts", {}):
                available.append({"cmd": ["npm", "test"], "name": "npm test"})
        except Exception:
            pass

    # Check for go.mod
    if (repo_dir / "go.mod").exists():
        available.append({"cmd": ["go", "test", "./..."], "name": "go test"})

    # Check for Cargo.toml
    if (repo_dir / "Cargo.toml").exists():
        available.append({"cmd": ["cargo", "test", "--quiet"], "name": "cargo test"})

    # Fallback: try pytest anyway for Python projects
    if not available:
        py_files = list(repo_dir.rglob("test_*.py")) + list(repo_dir.rglob("*_test.py"))
        if py_files:
            available.append({"cmd": ["python", "-m", "pytest", "--tb=short", "-q", "--timeout=60"], "name": "pytest"})

    return available


def run_tests(repo_dir: Path, framework: dict | None = None) -> TestResult:
    """Run tests in the repo using the detected or specified framework."""
    if framework is None:
        frameworks = detect_test_framework(repo_dir)
        if not frameworks:
            return TestResult(
                success=True,
                framework="none",
                output="No test framework detected. Skipping test verification.",
                exit_code=0,
            )
        framework = frameworks[0]

    try:
        result = subprocess.run(
            framework["cmd"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
        return TestResult(
            success=result.returncode == 0,
            framework=framework["name"],
            output=(result.stdout + "\n" + result.stderr).strip()[-2000:],
            exit_code=result.returncode,
        )
    except subprocess.TimeoutExpired:
        return TestResult(
            success=False,
            framework=framework["name"],
            output="Test execution timed out (120s limit)",
            exit_code=-1,
        )
    except FileNotFoundError:
        return TestResult(
            success=False,
            framework=framework["name"],
            output=f"Command not found: {framework['cmd'][0]}",
            exit_code=-1,
        )


def verify_fix(repo_dir: Path, file_changes: dict[str, str]) -> dict:
    """Apply changes temporarily, run tests, then report results.

    Returns dict with: baseline (TestResult), after_fix (TestResult), verdict.
    """
    # Run baseline tests
    baseline = run_tests(repo_dir)

    # Apply changes
    backups = {}
    for file_path, new_content in file_changes.items():
        full_path = repo_dir / file_path
        if full_path.exists():
            backups[file_path] = full_path.read_text(encoding="utf-8", errors="ignore")
        else:
            backups[file_path] = None  # New file

        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(new_content, encoding="utf-8")

    # Run tests after fix
    after_fix = run_tests(repo_dir)

    # Restore original files
    for file_path, original in backups.items():
        full_path = repo_dir / file_path
        if original is None:
            full_path.unlink(missing_ok=True)
        else:
            full_path.write_text(original, encoding="utf-8")

    verdict = "pass"
    if not baseline.success and after_fix.success:
        verdict = "improved"  # Fix resolved failing tests
    elif baseline.success and after_fix.success:
        verdict = "pass"  # Tests still pass
    elif baseline.success and not after_fix.success:
        verdict = "regression"  # Fix broke tests
    else:
        verdict = "still_failing"  # Tests were failing before and still failing

    return {
        "baseline": baseline,
        "after_fix": after_fix,
        "verdict": verdict,
    }
