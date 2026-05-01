"""Cross-file dependency analysis for multi-file coordinated fixes.

When a function signature, class interface, or constant changes in one file,
this module identifies all files that import from it — so the Fix Agent can
generate coordinated patches across the dependency chain.

Supports: Python, JavaScript/TypeScript, Go
"""

import re
from collections import defaultdict
from pathlib import Path


def _parse_python_imports(content: str, file_path: str) -> list[str]:
    """Extract import dependencies from Python source.

    Returns list of imported module paths (relative, without extension).
    Handles:
        import foo.bar
        from foo.bar import Baz
        from . import sibling
        from ..package import thing
    """
    imports = []
    lines = content.split("\n")

    for line in lines:
        line = line.strip()
        if line.startswith("#"):
            continue

        # from foo.bar import ...
        m = re.match(r'from\s+([\w.]+)\s+import', line)
        if m:
            module = m.group(1)
            if not module.startswith("."):
                # Absolute import: foo.bar.baz -> foo/bar/baz
                imports.append(module.replace(".", "/"))
            else:
                # Relative import: resolve against current file
                parts = module.split(".")
                dots = sum(1 for p in parts if p == "")
                name_parts = [p for p in parts if p != ""]
                base_dir = str(Path(file_path).parent)
                for _ in range(dots - 1):
                    base_dir = str(Path(base_dir).parent)
                if name_parts:
                    imports.append(f"{base_dir}/{'/'.join(name_parts)}")
                else:
                    imports.append(base_dir)
            continue

        # import foo.bar
        m = re.match(r'import\s+([\w.]+)', line)
        if m:
            module = m.group(1)
            imports.append(module.replace(".", "/"))

    return imports


def _parse_js_imports(content: str, file_path: str) -> list[str]:
    """Extract import dependencies from JS/TS source.

    Handles:
        import X from './foo'
        import { X } from '../bar'
        require('./foo')
    """
    imports = []

    # ES module imports
    for m in re.finditer(r'''(?:import|from)\s+.*?['"]([./][^'"]+)['"]''', content):
        dep = m.group(1)
        # Remove file extension
        dep = re.sub(r'\.(js|ts|jsx|tsx|mjs)$', '', dep)
        base_dir = str(Path(file_path).parent)
        resolved = str(Path(base_dir) / dep)
        imports.append(resolved)

    # CommonJS require
    for m in re.finditer(r'''require\s*\(\s*['"]([./][^'"]+)['"]\s*\)''', content):
        dep = m.group(1)
        dep = re.sub(r'\.(js|ts|jsx|tsx|mjs)$', '', dep)
        base_dir = str(Path(file_path).parent)
        resolved = str(Path(base_dir) / dep)
        imports.append(resolved)

    return imports


def _parse_go_imports(content: str) -> list[str]:
    """Extract import dependencies from Go source.

    Handles import blocks and single imports.
    """
    imports = []

    # import block
    for block in re.finditer(r'import\s*\((.*?)\)', content, re.DOTALL):
        for m in re.finditer(r'"([^"]+)"', block.group(1)):
            imp = m.group(1)
            if not imp.startswith(("std", "fmt", "os")):  # skip stdlib-like
                imports.append(imp)

    # Single import
    for m in re.finditer(r'import\s+"([^"]+)"', content):
        imp = m.group(1)
        imports.append(imp)

    return imports


# Parser dispatch
_PARSERS = {
    ".py": lambda content, fp: _parse_python_imports(content, fp),
    ".js": lambda content, fp: _parse_js_imports(content, fp),
    ".ts": lambda content, fp: _parse_js_imports(content, fp),
    ".jsx": lambda content, fp: _parse_js_imports(content, fp),
    ".tsx": lambda content, fp: _parse_js_imports(content, fp),
    ".go": lambda content, fp: _parse_go_imports(content),
}


def build_dependency_graph(repo_dir: Path, code_files: list[Path]) -> dict[str, set[str]]:
    """Build a reverse dependency graph: file -> set of files that import it.

    Returns:
        dict mapping file_path -> set of file_paths that depend on it
    """
    # Forward deps: file -> set of modules it imports
    forward: dict[str, list[str]] = {}
    # Map module paths to actual file paths
    file_set: set[str] = set()

    for fp in code_files:
        rel = str(fp.relative_to(repo_dir))
        file_set.add(rel)
        ext = fp.suffix.lower()
        if ext not in _PARSERS:
            continue
        try:
            content = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        forward[rel] = _PARSERS[ext](content, rel)

    # Build reverse: for each file, find who imports it
    reverse: dict[str, set[str]] = defaultdict(set)

    for importer, deps in forward.items():
        for dep in deps:
            # Try to match dep to an actual file
            for candidate in file_set:
                # Exact match or suffix match
                if candidate == dep or candidate == dep + ".py" or candidate == dep + "/__init__.py":
                    reverse[candidate].add(importer)
                    break
                # Partial match for relative paths
                if dep in candidate or candidate in dep:
                    reverse[candidate].add(importer)
                    break

    return dict(reverse)


def find_affected_files(
    changed_files: list[str],
    dep_graph: dict[str, set[str]],
    max_depth: int = 2,
) -> dict[str, list[str]]:
    """Given changed files, find all files that might need coordinated updates.

    Args:
        changed_files: List of file paths that are being modified
        dep_graph: Reverse dependency graph from build_dependency_graph
        max_depth: How many levels of transitive deps to follow

    Returns:
        dict mapping changed_file -> list of dependent files
    """
    affected: dict[str, list[str]] = {}

    for changed in changed_files:
        dependents = set()
        frontier = {changed}
        visited = set()

        for _ in range(max_depth):
            next_frontier = set()
            for f in frontier:
                if f in visited:
                    continue
                visited.add(f)
                deps = dep_graph.get(f, set())
                for d in deps:
                    if d not in changed_files:  # don't re-report already-changed files
                        dependents.add(d)
                        next_frontier.add(d)
            frontier = next_frontier

        if dependents:
            affected[changed] = sorted(dependents)

    return affected


def format_dependency_report(affected: dict[str, list[str]]) -> str:
    """Format a human-readable dependency report."""
    if not affected:
        return "No cross-file dependencies detected."

    lines = ["Cross-file dependencies:"]
    for changed, deps in affected.items():
        lines.append(f"  {changed} affects:")
        for d in deps:
            lines.append(f"    -> {d}")
    return "\n".join(lines)
