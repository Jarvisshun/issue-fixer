"""Language-specific prompt engineering for multi-language code fixing.

Each language has its own common bug patterns, idioms, and best practices.
This module provides tailored prompts that improve fix quality by injecting
language-specific knowledge into the LLM context.

Supported: Python, JavaScript/TypeScript, Go, Java, Rust, C/C++
"""

from pathlib import Path

# Language detection by extension
EXT_TO_LANG = {
    ".py": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".cs": "csharp",
}

# Language-specific bug patterns and fix guidelines
LANGUAGE_GUIDELINES = {
    "python": """## Python-Specific Guidelines
- Check for `None` before attribute access (AttributeError is the #1 Python bug)
- Use `isinstance()` instead of `type()` for type checking
- Ensure context managers (`with` statements) are used for file/resource handling
- Check for mutable default arguments in function definitions (`def f(x=[])` is a bug)
- Verify exception handling: catch specific exceptions, not bare `except:`
- Check for f-string vs .format() consistency
- Ensure async functions use `await` on coroutines
- Watch for `==` vs `is` comparison (use `is` for None, True, False)
- Check list/dict comprehension syntax and scope leaks
- Verify indentation consistency (Python is whitespace-sensitive)""",

    "javascript": """## JavaScript/TypeScript-Specific Guidelines
- Check for `null`/`undefined` before property access (use `?.` optional chaining)
- Verify `===` vs `==` (prefer strict equality)
- Check for `var` vs `let`/`const` (prefer block-scoped)
- Ensure Promises are properly awaited or `.then()`-ed
- Watch for `this` binding issues in callbacks and arrow functions
- Check for missing `return` in array methods (`.map()`, `.filter()`)
- Verify `JSON.parse()` is wrapped in try-catch
- Check for prototype pollution in object operations
- Ensure event listeners are cleaned up to prevent memory leaks
- TypeScript: check for type assertion safety (`as` vs `satisfies`)""",

    "typescript": """## TypeScript-Specific Guidelines
- Check for `null`/`undefined` before property access (use `?.` optional chaining)
- Verify type narrowing in conditional branches
- Ensure generic constraints are properly defined
- Check for `any` type usage — prefer `unknown` with type guards
- Verify discriminated unions have exhaustive switch cases
- Watch for `as` type assertions that bypass safety
- Check for proper error typing in catch blocks
- Ensure async return types are `Promise<T>` not just `T`
- Verify mapped types and conditional types are correct
- Check for missing `readonly` on immutable data structures""",

    "go": """## Go-Specific Guidelines
- ALWAYS check `err != nil` after function calls that return error
- Ensure `defer` statements are placed correctly (before error returns)
- Check for nil pointer dereference on pointer types
- Verify goroutine synchronization (channels, sync.WaitGroup, mutexes)
- Watch for goroutine leaks (missing channel close or context cancel)
- Check for proper `context.Context` propagation
- Ensure `io.Reader`/`io.Writer` are properly closed
- Check for race conditions with shared state
- Verify struct field tags match JSON/DB column names
- Check for unused variables/imports (Go compiler rejects these)""",

    "java": """## Java-Specific Guidelines
- Check for NullPointerException (use Optional or null checks)
- Ensure resources are closed (try-with-resources pattern)
- Check for proper exception handling (catch specific exceptions)
- Verify thread safety in concurrent code (synchronized, volatile, concurrent collections)
- Watch for autoboxing pitfalls (Integer cache, == vs .equals())
- Check for proper equals() and hashCode() implementation
- Ensure generics type safety (avoid raw types)
- Check for memory leaks (unclosed streams, static collections)
- Verify access modifiers (private fields, public methods)
- Check for proper toString(), compareTo() implementations""",

    "rust": """## Rust-Specific Guidelines
- Check for proper error handling with `Result<T, E>` (no unwrap() in production)
- Verify lifetime annotations are correct
- Check for borrowing conflicts (mutable vs immutable references)
- Ensure `Option<T>` is properly handled (no unwrap on None)
- Watch for unnecessary cloning (prefer borrowing)
- Check for proper `Drop` implementation for custom types
- Verify `Send`/`Sync` bounds for concurrent code
- Check for integer overflow (use checked_add/saturating_add)
- Ensure unsafe blocks have safety comments
- Verify pattern matching exhaustiveness in `match`""",

    "c": """## C-Specific Guidelines
- Check for buffer overflow (use strncpy, snprintf instead of strcpy, sprintf)
- Verify malloc/free pairing (every malloc needs a free)
- Check for NULL pointer dereference
- Ensure array bounds are checked
- Watch for use-after-free bugs
- Check for memory leaks in error paths
- Verify integer overflow in arithmetic operations
- Check for proper string null termination
- Ensure file handles are closed
- Check for uninitialized variables""",

    "cpp": """## C++-Specific Guidelines
- Use smart pointers (unique_ptr, shared_ptr) instead of raw pointers
- Check for rule of three/five (copy/move constructors, assignment, destructor)
- Verify no dangling references after container modification
- Check for proper virtual destructor in base classes
- Watch for slicing in polymorphic assignments
- Check for exception safety guarantees (basic/strong/noexcept)
- Ensure RAII pattern for resource management
- Check for proper move semantics (std::move, rvalue references)
- Verify const correctness
- Check for data races in multi-threaded code""",

    "ruby": """## Ruby-Specific Guidelines
- Check for nil before method calls (use `&.` safe navigation)
- Verify exception handling (rescue specific exceptions)
- Check for proper use of `freeze` on constants
- Watch for mutation of shared objects
- Ensure blocks are properly yielded
- Check for `==` vs `eql?` vs `equal?` usage
- Verify proper use of `require` vs `require_relative`
- Check for N+1 query patterns in ActiveRecord
- Ensure proper cleanup in `ensure` blocks""",

    "php": """## PHP-Specific Guidelines
- Check for null before property access (use `?->` nullsafe operator)
- Verify strict types declaration (`declare(strict_types=1)`)
- Check for proper input validation and sanitization
- Watch for SQL injection (use prepared statements)
- Check for XSS vulnerabilities (use htmlspecialchars)
- Ensure proper error handling (try-catch with specific exceptions)
- Verify array key existence before access (isset/array_key_exists)
- Check for proper session security""",

    "swift": """## Swift-Specific Guidelines
- Check for nil with optional binding (`if let` / `guard let`)
- Verify proper use of `weak`/`unowned` to prevent retain cycles
- Check for main thread UI updates
- Watch for force unwrapping (`!`) — prefer safe unwrapping
- Check for proper error handling with `do-try-catch`
- Verify protocol conformance completeness
- Check for proper memory management in closures""",

    "kotlin": """## Kotlin-Specific Guidelines
- Check for null safety (`?.`, `!!`, `?:` usage)
- Verify coroutine scope and cancellation handling
- Check for proper flow collection (collect vs first)
- Watch for memory leaks in coroutine launches
- Check for proper sealed class when expressions
- Verify extension function null receiver handling""",
}


def detect_language(file_path: str) -> str | None:
    """Detect programming language from file extension."""
    ext = Path(file_path).suffix.lower()
    return EXT_TO_LANG.get(ext)


def get_language_guidelines(file_paths: list[str]) -> str:
    """Get combined language-specific guidelines for a set of files.

    Returns a prompt section with guidelines for all detected languages.
    """
    detected = set()
    for fp in file_paths:
        lang = detect_language(fp)
        if lang:
            detected.add(lang)

    if not detected:
        return ""

    sections = []
    for lang in sorted(detected):
        if lang in LANGUAGE_GUIDELINES:
            sections.append(LANGUAGE_GUIDELINES[lang])

    if not sections:
        return ""

    return "\n\n".join(sections)


def get_primary_language(file_paths: list[str]) -> str | None:
    """Get the most common language across a set of files."""
    from collections import Counter
    langs = []
    for fp in file_paths:
        lang = detect_language(fp)
        if lang:
            langs.append(lang)
    if not langs:
        return None
    return Counter(langs).most_common(1)[0][0]


# Language-specific test framework detection patterns
TEST_FRAMEWORKS = {
    "python": {
        "pytest": ["pytest.ini", "pyproject.toml", "conftest.py", "test_*.py"],
        "unittest": ["unittest", "TestCase"],
    },
    "javascript": {
        "jest": ["jest.config", "package.json"],
        "vitest": ["vitest.config"],
        "mocha": [".mocharc"],
    },
    "typescript": {
        "jest": ["jest.config"],
        "vitest": ["vitest.config"],
    },
    "go": {
        "go test": ["*_test.go"],
    },
    "java": {
        "junit": ["pom.xml", "build.gradle"],
        "testng": ["testng.xml"],
    },
    "rust": {
        "cargo test": ["Cargo.toml"],
    },
}
