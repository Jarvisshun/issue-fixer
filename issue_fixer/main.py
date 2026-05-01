"""Main CLI entry point - orchestrates the full Issue → Fix → PR pipeline."""

import os
import sys
from pathlib import Path

# Fix Windows console encoding for Unicode characters
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, TextColumn

from .config import config
from .github_client import GitHubClient, parse_issue_url
from .code_indexer import CodeIndexer
from .analyzer import Analyzer
from .agents import AgentOrchestrator
from .test_runner import verify_fix
from .sandbox import verify_files, summarize_results
from .feedback import feedback_store, FixRecord
from .notifier import send_notifications

console = Console(force_terminal=True)


def _validate_config():
    errors = config.validate()
    if errors:
        for e in errors:
            console.print(f"[red]Error:[/red] {e}")
        console.print("\n[yellow]Set these in .env file or environment variables.[/yellow]")
        console.print("See .env.example for reference.")
        sys.exit(1)


@click.group()
def cli():
    """Issue Fixer - AI Agent that auto-fixes GitHub Issues."""
    pass


@cli.command()
@click.argument("issue_url")
@click.option("--no-pr", is_flag=True, help="Analyze only, don't create PR")
@click.option("--max-files", default=5, help="Max files to examine in detail")
@click.option("--verify", is_flag=True, help="Run project tests to verify the fix")
@click.option("--mode", type=click.Choice(["diff", "full"]), default="diff", help="Fix mode: diff (patch) or full (rewrite)")
@click.option("--agent", is_flag=True, help="Use Multi-Agent pipeline (Analyzer→Search→Fix→Review)")
@click.option("--sandbox", is_flag=True, help="Run syntax verification in sandbox after fix")
def fix(issue_url: str, no_pr: bool, max_files: int, verify: bool, mode: str, agent: bool, sandbox: bool):
    """Fix a GitHub Issue and optionally create a PR.

    Modes:
      diff  - Generate targeted SEARCH/REPLACE patches (default, safer)
      full  - Generate complete file rewrites (fallback)

    Agent:
      --agent  Uses Multi-Agent pipeline with review loop for higher quality fixes
    """
    _validate_config()

    try:
        owner, repo_name, number = parse_issue_url(issue_url)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    pipeline = "Multi-Agent" if agent else "Single-Agent"
    console.print(Panel(
        f"[bold]Repository:[/bold] {owner}/{repo_name}\n"
        f"[bold]Issue:[/bold] #{number}\n"
        f"[bold]Mode:[/bold] {mode}\n"
        f"[bold]Pipeline:[/bold] {pipeline}\n"
        f"[bold]URL:[/bold] {issue_url}",
        title="Issue Fixer",
        border_style="blue",
    ))

    github = GitHubClient()

    # Step 1: Fetch issue
    console.print("[bold green]Fetching issue...[/bold green]")
    issue = github.get_issue(owner, repo_name, number)

    console.print(f"\n[bold]Issue Title:[/bold] {issue['title']}")
    console.print(f"[bold]State:[/bold] {issue['state']}")
    if issue['labels']:
        console.print(f"[bold]Labels:[/bold] {', '.join(issue['labels'])}")
    console.print(f"[bold]Description:[/bold] {issue['body'][:300]}...")
    if issue['comments']:
        console.print(f"[bold]Comments:[/bold] {len(issue['comments'])} comment(s)")

    # Step 2: Clone repo
    console.print("[bold green]Cloning repository...[/bold green]")
    repo_dir = github.clone_repo(owner, repo_name)
    console.print(f"[green]Repository cloned to {repo_dir}[/green]")

    # Step 3: Index code
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Indexing codebase...", total=None)
        code_files = github.list_code_files(repo_dir)
        progress.update(task, description=f"Indexing {len(code_files)} code files...")

        indexer = CodeIndexer()
        indexer.index_files(repo_dir, code_files)
        progress.update(task, description=f"Indexed {len(code_files)} files into vector store")

    console.print(f"[green]Indexed {len(code_files)} code files[/green]")

    # Step 4: Analyze issue with LLM
    if agent:
        console.print(f"[bold green]Running Multi-Agent pipeline (mode={mode})...[/bold green]")
        orchestrator = AgentOrchestrator(indexer)
        result = orchestrator.run(issue, repo_dir=repo_dir, mode=mode)
    else:
        console.print(f"[bold green]Analyzing issue (mode={mode})...[/bold green]")
        analyzer = Analyzer(indexer)
        result = analyzer.analyze_issue(issue, repo_dir=repo_dir, mode=mode)

    # Display analysis
    console.print(Panel(
        result.get("analysis", "No analysis provided"),
        title="Root Cause Analysis",
        border_style="yellow",
    ))

    files_to_fix = result.get("files_to_fix", [])
    if not files_to_fix:
        console.print("[yellow]No fix could be generated from the available context.[/yellow]")
        console.print("[dim]Try providing more details in the issue, or check if the issue requires architectural changes.[/dim]")
        # Record failed attempt
        feedback_store.record_fix(FixRecord(
            issue_url=issue_url,
            issue_title=issue["title"],
            issue_type=result.get("issue_type", ""),
            repo=f"{owner}/{repo_name}",
            files_changed=[],
            mode=mode,
            pipeline="multi-agent" if agent else "single-agent",
            success=False,
            review_score=result.get("review_score", 0),
            model=config.openai_model,
        ))
        return

    # Show fix summary
    console.print(f"\n[bold green]Generated fix for {len(files_to_fix)} file(s):[/bold green]")
    for f in files_to_fix[:max_files]:
        patch_info = ""
        if f.get("patch_stats"):
            ps = f["patch_stats"]
            patch_info = f" [{ps['applied']} patches applied, {ps['failed']} failed]"
        elif f.get("patch_error"):
            patch_info = f" [yellow]patch failed: {f['patch_error']}[/yellow]"
        console.print(f"  [cyan]{f['path']}[/cyan] - {f.get('reason', '')}{patch_info}")

    if len(files_to_fix) > max_files:
        console.print(f"[yellow]Limiting to {max_files} files. Use --max-files to adjust.[/yellow]")
        files_to_fix = files_to_fix[:max_files]

    # Show diffs in diff mode
    if mode == "diff":
        for f in files_to_fix:
            if f.get("diff"):
                console.print(Panel(
                    f["diff"][:1500],
                    title=f"Diff: {f['path']}",
                    border_style="cyan",
                ))

    # Step 5: Optionally verify fix with tests
    if verify:
        console.print("[bold green]Running test verification...[/bold green]")
        file_changes = {f["path"]: f["fixed_content"] for f in files_to_fix if f.get("fixed_content")}
        if file_changes:
            result_tr = verify_fix(repo_dir, file_changes)
            verdict = result_tr["verdict"]
            verdict_color = {"pass": "green", "improved": "green", "regression": "red", "still_failing": "yellow"}.get(verdict, "yellow")
            console.print(f"  Baseline: {'Pass' if result_tr['baseline'].success else 'Fail'} ({result_tr['baseline'].framework})")
            console.print(f"  After fix: {'Pass' if result_tr['after_fix'].success else 'Fail'} ({result_tr['after_fix'].framework})")
            console.print(f"  [{verdict_color}]Verdict: {verdict}[/{verdict_color}]")
            if verdict == "regression":
                console.print("[red]Warning: Fix may have introduced regressions![/red]")

    # Step 5b: Optionally verify syntax in sandbox
    if sandbox:
        console.print("[bold green]Running sandbox syntax verification...[/bold green]")
        file_changes = {f["path"]: f["fixed_content"] for f in files_to_fix if f.get("fixed_content")}
        if file_changes:
            sandbox_results = verify_files(file_changes)
            summary = summarize_results(sandbox_results)
            console.print(f"  {summary}")
            for path, r in sandbox_results.items():
                if not r.success:
                    console.print(f"  [red]FAIL[/red] {path}: {r.stderr[:200]}")

    # Step 6: Create PR or just show results
    changed_files = [f["path"] for f in files_to_fix if f.get("fixed_content")]

    if no_pr:
        console.print("\n[bold]Fix generated (no PR created):[/bold]")
        for f in files_to_fix:
            if f.get("fixed_content"):
                console.print(Panel(
                    f.get("fixed_content", "")[:1000] + "...",
                    title=f"Fix for {f['path']}",
                    border_style="green",
                ))
        # Record successful analysis (no PR)
        feedback_store.record_fix(FixRecord(
            issue_url=issue_url,
            issue_title=issue["title"],
            issue_type=result.get("issue_type", ""),
            repo=f"{owner}/{repo_name}",
            files_changed=changed_files,
            mode=mode,
            pipeline="multi-agent" if agent else "single-agent",
            success=True,
            pr_created=False,
            review_score=result.get("review_score", 0),
            model=config.openai_model,
        ))
        return

    # Create PR
    console.print("\n[bold green]Creating Pull Request...[/bold green]")

    branch_name = f"fix/issue-{number}"
    pr_title = result.get("pr_title", f"Fix #{number}: {issue['title']}")
    pr_body = result.get("pr_body", f"Automated fix for #{number}")

    # Add diff summary to PR body
    if mode == "diff":
        diff_summary = "\n\n## Changes\n\n"
        for f in files_to_fix:
            if f.get("diff"):
                diff_summary += f"### `{f['path']}`\n{f.get('reason', '')}\n\n```diff\n{f['diff'][:500]}\n```\n\n"
        pr_body += diff_summary

    pr_body += f"\n\n---\n*This PR was auto-generated by [Issue Fixer](https://github.com/issue-fixer)*"

    file_changes = {f["path"]: f["fixed_content"] for f in files_to_fix if f.get("fixed_content")}
    try:
        pr_url = github.create_pull_request(
            owner=owner,
            repo_name=repo_name,
            branch_name=branch_name,
            title=pr_title,
            body=pr_body,
            file_changes=file_changes,
        )
        console.print(f"\n[bold green]PR created successfully![/bold green]")
        console.print(f"[link={pr_url}]{pr_url}[/link]")
        # Record success with PR
        feedback_store.record_fix(FixRecord(
            issue_url=issue_url,
            issue_title=issue["title"],
            issue_type=result.get("issue_type", ""),
            repo=f"{owner}/{repo_name}",
            files_changed=changed_files,
            mode=mode,
            pipeline="multi-agent" if agent else "single-agent",
            success=True,
            pr_created=True,
            pr_url=pr_url,
            review_score=result.get("review_score", 0),
            model=config.openai_model,
        ))
        # Send notifications
        notif_results = send_notifications(
            issue_title=issue["title"],
            issue_url=issue_url,
            files_changed=changed_files,
            confidence=result.get("confidence", 0),
            pr_url=pr_url,
            success=True,
        )
        if notif_results:
            for ch, ok in notif_results.items():
                status = "sent" if ok else "failed"
                console.print(f"  [dim]Notification ({ch}): {status}[/dim]")
    except Exception as e:
        console.print(f"\n[red]Failed to create PR: {e}[/red]")
        console.print("[yellow]You may need write access to the repository.[/yellow]")
        console.print("\n[dim]Fix content:[/dim]")
        for f in files_to_fix:
            if f.get("fixed_content"):
                console.print(Panel(
                    f.get("fixed_content", "")[:500],
                    title=f"Fix for {f['path']}",
                    border_style="green",
                ))
        # Record PR failure
        feedback_store.record_fix(FixRecord(
            issue_url=issue_url,
            issue_title=issue["title"],
            issue_type=result.get("issue_type", ""),
            repo=f"{owner}/{repo_name}",
            files_changed=changed_files,
            mode=mode,
            pipeline="multi-agent" if agent else "single-agent",
            success=False,
            pr_created=False,
            error=str(e),
            review_score=result.get("review_score", 0),
            model=config.openai_model,
        ))


@cli.command()
def info():
    """Show current configuration."""
    provider_info = f"[bold]LLM Provider:[/bold] {config.llm_provider}\n"
    if config.llm_provider == "ollama":
        provider_info += (
            f"[bold]Ollama URL:[/bold] {config.ollama_base_url}\n"
            f"[bold]Ollama Model:[/bold] {config.ollama_model}\n"
        )
    else:
        provider_info += (
            f"[bold]Model:[/bold] {config.openai_model}\n"
            f"[bold]Base URL:[/bold] {config.openai_base_url}\n"
            f"[bold]API Key:[/bold] {'Set' if config.openai_api_key else 'Not set'}\n"
        )
    provider_info += (
        f"[bold]GitHub Token:[/bold] {'Set' if config.github_token else 'Not set'}\n"
        f"[bold]Chunk Size:[/bold] {config.chunk_size}\n"
        f"[bold]Top K:[/bold] {config.top_k}"
    )
    console.print(Panel(provider_info, title="Configuration"))


@cli.command()
def stats():
    """Show fix history and success rate statistics."""
    s = feedback_store.get_stats()

    if s.total_fixes == 0:
        console.print("[dim]No fix history yet. Run some fixes first![/dim]")
        return

    # Overall stats
    console.print(Panel(
        f"[bold]Total Fixes:[/bold] {s.total_fixes}\n"
        f"[bold]Successful:[/bold] {s.successful_fixes}\n"
        f"[bold]Success Rate:[/bold] {s.success_rate:.0%}",
        title="Fix Statistics",
        border_style="blue",
    ))

    # By issue type
    if s.by_type:
        console.print("\n[bold]By Issue Type:[/bold]")
        for t, data in sorted(s.by_type.items()):
            console.print(
                f"  {t}: {data['success']}/{data['total']} "
                f"({data['rate']:.0%})"
            )

    # By pipeline
    if s.by_pipeline:
        console.print("\n[bold]By Pipeline:[/bold]")
        for p, data in sorted(s.by_pipeline.items()):
            console.print(
                f"  {p}: {data['success']}/{data['total']} "
                f"({data['rate']:.0%})"
            )

    # Recent fixes
    records = feedback_store.get_all_records()
    if records:
        console.print("\n[bold]Recent Fixes:[/bold]")
        for r in records[-5:]:
            status = "[green]OK[/green]" if r.success else "[red]FAIL[/red]"
            console.print(
                f"  {status} {r.issue_title[:50]} ({r.mode}, {r.pipeline})"
            )


@cli.command()
@click.option("--host", default="127.0.0.1", help="Host to bind")
@click.option("--port", default=8000, help="Port to bind")
def web(host: str, port: int):
    """Start the Web UI server."""
    try:
        import uvicorn
        from .web.app import app
        console.print(f"[bold green]Starting Issue Fixer Web UI...[/bold green]")
        console.print(f"[dim]Open http://{host}:{port} in your browser[/dim]")
        uvicorn.run(app, host=host, port=port, log_level="info")
    except ImportError:
        console.print("[red]Web UI dependencies not installed.[/red]")
        console.print("[yellow]Run: pip install issue-fixer[web][/yellow]")
        sys.exit(1)


if __name__ == "__main__":
    cli()
