"""FastAPI backend for Issue Fixer Web UI + GitHub Webhook handler."""

import asyncio
import hashlib
import hmac
import json
import sys
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Fix Windows encoding
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..config import config, REPO_CACHE_DIR
from ..github_client import GitHubClient, parse_issue_url
from ..code_indexer import CodeIndexer
from ..analyzer import Analyzer
from ..agents import AgentOrchestrator
from ..test_runner import verify_fix
from ..feedback import feedback_store

app = FastAPI(title="Issue Fixer", version="0.4.0")

# ─── Webhook job tracking ────────────────────────────────────────────────────
_jobs: dict[str, dict] = {}

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


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Serve the statistics dashboard."""
    html_path = Path(__file__).parent / "dashboard.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/api/stats")
async def get_stats():
    """Return fix statistics for the dashboard."""
    stats = feedback_store.get_stats()
    records = feedback_store.get_all_records()

    # Build timeline data (last 30 entries)
    timeline = []
    for r in records[-30:]:
        timeline.append({
            "timestamp": r.timestamp,
            "success": r.success,
            "issue_type": r.issue_type,
            "pipeline": r.pipeline,
            "mode": r.mode,
            "review_score": r.review_score,
            "model": r.model,
        })

    # Top repos
    repo_counts: dict[str, int] = {}
    for r in records:
        repo_counts[r.repo] = repo_counts.get(r.repo, 0) + 1
    top_repos = sorted(repo_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "total_fixes": stats.total_fixes,
        "successful_fixes": stats.successful_fixes,
        "success_rate": stats.success_rate,
        "by_type": stats.by_type,
        "by_pipeline": stats.by_pipeline,
        "top_repos": [{"repo": r, "count": c} for r, c in top_repos],
        "timeline": timeline,
    }


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


# ─── GitHub Webhook Endpoint ─────────────────────────────────────────────────

def _verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    if not secret:
        return True  # No secret configured, skip verification
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _process_issue_webhook(payload: dict, job_id: str):
    """Background task: process a GitHub issue webhook event."""
    issue_data = payload.get("issue", {})
    repo_data = payload.get("repository", {})
    action = payload.get("action", "")

    owner = repo_data.get("owner", {}).get("login", "")
    repo_name = repo_data.get("name", "")
    number = issue_data.get("number", 0)
    title = issue_data.get("title", "")
    body = issue_data.get("body", "") or ""
    labels = [l.get("name", "") for l in issue_data.get("labels", [])]
    html_url = issue_data.get("html_url", "")

    _jobs[job_id]["status"] = "processing"
    _jobs[job_id]["issue_title"] = title
    _jobs[job_id]["issue_url"] = html_url

    try:
        # Clone and index
        github = GitHubClient()
        repo_dir = github.clone_repo(owner, repo_name)
        code_files = github.list_code_files(repo_dir)
        indexer = CodeIndexer()
        indexer.index_files(repo_dir, code_files)

        issue = {
            "number": number,
            "title": title,
            "body": body,
            "state": "open",
            "labels": labels,
            "comments": [],
            "html_url": html_url,
        }

        # Run multi-agent pipeline
        orchestrator = AgentOrchestrator(indexer)
        result = orchestrator.run(issue, repo_dir=repo_dir, mode="diff")

        files_to_fix = result.get("files_to_fix", [])
        if not files_to_fix:
            _jobs[job_id]["status"] = "no_fix"
            _jobs[job_id]["message"] = "No fix could be generated"
            return

        # Create PR
        file_changes = {f["path"]: f["fixed_content"] for f in files_to_fix if f.get("fixed_content")}
        branch_name = f"fix/issue-{number}"
        pr_title = result.get("pr_title", f"Fix #{number}: {title}")
        pr_body = result.get("pr_body", f"Automated fix for #{number}")
        pr_body += "\n\n---\n*This PR was auto-generated by [Issue Fixer Webhook](https://github.com/issue-fixer)*"

        pr_url = github.create_pull_request(
            owner=owner,
            repo_name=repo_name,
            branch_name=branch_name,
            title=pr_title,
            body=pr_body,
            file_changes=file_changes,
        )

        _jobs[job_id]["status"] = "success"
        _jobs[job_id]["pr_url"] = pr_url
        _jobs[job_id]["files_changed"] = [f["path"] for f in files_to_fix]

    except Exception as e:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = str(e)


@app.post("/api/webhook")
async def webhook_handler(request: Request, background_tasks: BackgroundTasks):
    """Handle GitHub webhook events for issues.

    Setup:
    1. Go to repo Settings → Webhooks → Add webhook
    2. Payload URL: https://your-server:8000/api/webhook
    3. Content type: application/json
    4. Secret: (set GITHUB_WEBHOOK_SECRET env var to match)
    5. Events: select "Issues"
    """
    # Get raw body for signature verification
    body = await request.body()

    # Verify signature
    signature = request.headers.get("X-Hub-Signature-256", "")
    webhook_secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if webhook_secret and not _verify_webhook_signature(body, signature, webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Parse payload
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Only process issue events
    event_type = request.headers.get("X-GitHub-Event", "")
    if event_type != "issues":
        return {"status": "ignored", "event": event_type}

    action = payload.get("action", "")
    if action not in ("opened", "referred"):
        return {"status": "ignored", "action": action}

    # Create job
    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "repo": payload.get("repository", {}).get("full_name", ""),
        "issue_number": payload.get("issue", {}).get("number"),
    }

    # Process in background
    background_tasks.add_task(_process_issue_webhook, payload, job_id)

    return {"status": "accepted", "job_id": job_id}


@app.get("/api/webhook/jobs")
async def list_jobs():
    """List all webhook processing jobs."""
    return {"jobs": list(_jobs.values())}


@app.get("/api/webhook/jobs/{job_id}")
async def get_job(job_id: str):
    """Get status of a specific webhook job."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return _jobs[job_id]
