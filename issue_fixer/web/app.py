"""FastAPI backend for Issue Fixer Web UI."""

import sys
import os
from pathlib import Path

# Fix Windows encoding
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import config, REPO_CACHE_DIR
from ..github_client import GitHubClient, parse_issue_url
from ..code_indexer import CodeIndexer
from ..analyzer import Analyzer
from ..test_runner import verify_fix

app = FastAPI(title="Issue Fixer", version="0.1.0")

# Store active sessions
_sessions: dict[str, dict] = {}


class FixRequest(BaseModel):
    issue_url: str
    max_files: int = 5
    run_tests: bool = False


class FixResponse(BaseModel):
    status: str
    issue: dict | None = None
    analysis: str | None = None
    files_fixed: list[dict] | None = None
    test_result: dict | None = None
    pr_url: str | None = None
    error: str | None = None


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main HTML page."""
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/api/config")
async def get_config():
    """Return current configuration status."""
    return {
        "model": config.openai_model,
        "base_url": config.openai_base_url,
        "has_openai_key": bool(config.openai_api_key),
        "has_github_token": bool(config.github_token),
    }


@app.post("/api/fix")
async def fix_issue(req: FixRequest):
    """Analyze and fix a GitHub issue."""
    # Validate config
    errors = config.validate()
    if errors:
        raise HTTPException(status_code=400, detail={"error": "Configuration error", "details": errors})

    # Parse URL
    try:
        owner, repo_name, number = parse_issue_url(req.issue_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})

    github = GitHubClient()

    # Step 1: Fetch issue
    try:
        issue = github.get_issue(owner, repo_name, number)
    except Exception as e:
        raise HTTPException(status_code=404, detail={"error": f"Failed to fetch issue: {e}"})

    # Step 2: Clone repo
    try:
        repo_dir = github.clone_repo(owner, repo_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"Failed to clone repo: {e}"})

    # Step 3: Index code
    code_files = github.list_code_files(repo_dir)
    indexer = CodeIndexer()
    indexer.index_files(repo_dir, code_files)

    # Step 4: Analyze
    try:
        analyzer = Analyzer(indexer)
        result = analyzer.analyze_issue(issue)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"LLM analysis failed: {e}"})

    files_to_fix = result.get("files_to_fix", [])[:req.max_files]

    # Step 5: Optionally run tests
    test_result = None
    if req.run_tests and files_to_fix:
        file_changes = {f["path"]: f["fixed_content"] for f in files_to_fix if f.get("fixed_content")}
        if file_changes:
            try:
                tr = verify_fix(repo_dir, file_changes)
                test_result = {
                    "verdict": tr["verdict"],
                    "baseline_success": tr["baseline"].success,
                    "baseline_framework": tr["baseline"].framework,
                    "baseline_output": tr["baseline"].output[:500],
                    "after_fix_success": tr["after_fix"].success,
                    "after_fix_framework": tr["after_fix"].framework,
                    "after_fix_output": tr["after_fix"].output[:500],
                }
            except Exception as e:
                test_result = {"verdict": "error", "error": str(e)}

    return FixResponse(
        status="success",
        issue={
            "number": issue["number"],
            "title": issue["title"],
            "state": issue["state"],
            "labels": issue["labels"],
            "body": issue["body"][:500],
            "url": issue["html_url"],
        },
        analysis=result.get("analysis", ""),
        files_fixed=[
            {"path": f["path"], "reason": f.get("reason", ""), "preview": f.get("fixed_content", "")[:300]}
            for f in files_to_fix
        ],
        test_result=test_result,
    )


@app.post("/api/pr")
async def create_pr(req: FixRequest):
    """Fix issue and create a PR."""
    errors = config.validate()
    if errors:
        raise HTTPException(status_code=400, detail={"error": "Configuration error", "details": errors})

    try:
        owner, repo_name, number = parse_issue_url(req.issue_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})

    github = GitHubClient()

    try:
        issue = github.get_issue(owner, repo_name, number)
    except Exception as e:
        raise HTTPException(status_code=404, detail={"error": f"Failed to fetch issue: {e}"})

    try:
        repo_dir = github.clone_repo(owner, repo_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"Failed to clone repo: {e}"})

    code_files = github.list_code_files(repo_dir)
    indexer = CodeIndexer()
    indexer.index_files(repo_dir, code_files)

    try:
        analyzer = Analyzer(indexer)
        result = analyzer.analyze_issue(issue)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"LLM analysis failed: {e}"})

    files_to_fix = result.get("files_to_fix", [])[:req.max_files]
    if not files_to_fix:
        return {"status": "no_fix", "message": "No fix could be generated"}

    file_changes = {f["path"]: f["fixed_content"] for f in files_to_fix if f.get("fixed_content")}
    branch_name = f"fix/issue-{number}"
    pr_title = result.get("pr_title", f"Fix #{number}: {issue['title']}")
    pr_body = result.get("pr_body", f"Automated fix for #{number}")
    pr_body += "\n\n---\n*This PR was auto-generated by [Issue Fixer](https://github.com/issue-fixer)*"

    try:
        pr_url = github.create_pull_request(
            owner=owner,
            repo_name=repo_name,
            branch_name=branch_name,
            title=pr_title,
            body=pr_body,
            file_changes=file_changes,
        )
        return {"status": "success", "pr_url": pr_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"Failed to create PR: {e}"})
