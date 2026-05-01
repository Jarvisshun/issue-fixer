"""GitHub API client for Issue fetching, repo cloning, and PR creation."""

import os
import re
import shutil
import stat
from pathlib import Path

from github import Github, Repository

from .config import config, REPO_CACHE_DIR

# Code file extensions to index
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
    ".rb", ".php", ".c", ".cpp", ".h", ".hpp", ".cs", ".swift",
    ".kt", ".scala", ".sh", ".sql", ".yaml", ".yml", ".json",
    ".toml", ".cfg", ".ini", ".md", ".txt", ".dockerfile",
}


def parse_issue_url(url: str) -> tuple[str, str, int]:
    """Parse 'https://github.com/owner/repo/issues/123' into (owner, repo, number)."""
    pattern = r"github\.com/([^/]+)/([^/]+)/issues/(\d+)"
    match = re.search(pattern, url)
    if not match:
        raise ValueError(f"Invalid GitHub Issue URL: {url}")
    return match.group(1), match.group(2), int(match.group(3))


class GitHubClient:
    def __init__(self):
        self.gh = Github(config.github_token)

    def get_issue(self, owner: str, repo_name: str, number: int) -> dict:
        """Fetch issue title, body, labels, and comments."""
        repo = self.gh.get_repo(f"{owner}/{repo_name}")
        issue = repo.get_issue(number)

        comments = []
        for comment in issue.get_comments():
            comments.append({
                "author": comment.user.login,
                "body": comment.body,
                "created_at": comment.created_at.isoformat(),
            })

        return {
            "number": issue.number,
            "title": issue.title,
            "body": issue.body or "",
            "labels": [l.name for l in issue.labels],
            "state": issue.state,
            "comments": comments,
            "repo_full_name": f"{owner}/{repo_name}",
            "html_url": issue.html_url,
        }

    def clone_repo(self, owner: str, repo_name: str) -> Path:
        """Clone repo to local cache. Skip if already exists."""
        repo_dir = REPO_CACHE_DIR / f"{owner}_{repo_name}"
        if repo_dir.exists():
            # Windows: git files may be locked, handle gracefully
            def _remove_readonly(func, path, _exc_info):
                os.chmod(path, stat.S_IWRITE)
                func(path)
            shutil.rmtree(repo_dir, onerror=_remove_readonly)

        REPO_CACHE_DIR.mkdir(exist_ok=True)
        repo_url = f"https://x-access-token:{config.github_token}@github.com/{owner}/{repo_name}.git"

        from git import Repo as GitRepo
        GitRepo.clone_from(repo_url, str(repo_dir), depth=1)
        return repo_dir

    def list_code_files(self, repo_dir: Path) -> list[Path]:
        """List all code files in the repo, excluding common non-code dirs."""
        skip_dirs = {
            "node_modules", ".git", "__pycache__", "venv", ".venv",
            "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
            "vendor", "packages", ".next", ".nuxt", "coverage",
        }

        code_files = []
        for path in repo_dir.rglob("*"):
            if not path.is_file():
                continue
            if any(part in skip_dirs for part in path.parts):
                continue
            if path.suffix.lower() in CODE_EXTENSIONS:
                code_files.append(path)
        return code_files

    def create_pull_request(
        self,
        owner: str,
        repo_name: str,
        branch_name: str,
        title: str,
        body: str,
        file_changes: dict[str, str],
    ) -> str:
        """Create a PR with the given file changes."""
        repo = self.gh.get_repo(f"{owner}/{repo_name}")
        default_branch = repo.default_branch
        base_sha = repo.get_branch(default_branch).commit.sha

        # Create new branch
        repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_sha)

        # Commit each file change
        for file_path, new_content in file_changes.items():
            try:
                existing = repo.get_contents(file_path, ref=branch_name)
                repo.update_file(
                    path=file_path,
                    message=f"fix: update {file_path}",
                    content=new_content,
                    sha=existing.sha,
                    branch=branch_name,
                )
            except Exception as e:
                # File doesn't exist yet, create it
                if "Not Found" in str(e) or "404" in str(e):
                    repo.create_file(
                        path=file_path,
                        message=f"fix: create {file_path}",
                        content=new_content,
                        branch=branch_name,
                    )
                else:
                    raise

        # Create PR
        pr = repo.create_pull(
            title=title,
            body=body,
            head=branch_name,
            base=default_branch,
        )
        return pr.html_url
