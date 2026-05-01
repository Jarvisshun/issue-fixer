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
from .test_runner import verify_fix

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
def fix(issue_url: str, no_pr: bool, max_files: int, verify: bool):
    """Fix a GitHub Issue and optionally create a PR."""
    _validate_config()

    # Parse issue URL
    try:
        owner, repo_name, number = parse_issue_url(issue_url)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    console.print(Panel(
        f"[bold]Repository:[/bold] {owner}/{repo_name}\n"
        f"[bold]Issue:[/bold] #{number}\n"
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
    console.print("[bold green]Analyzing issue and generating fix...[/bold green]")
    analyzer = Analyzer(indexer)
    result = analyzer.analyze_issue(issue)

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
        return

    console.print(f"\n[bold green]Generated fix for {len(files_to_fix)} file(s):[/bold green]")
    for f in files_to_fix[:max_files]:
        console.print(f"  [cyan]{f['path']}[/cyan] - {f.get('reason', '')}")

    # Step 5: Optionally do a deeper check on specific files
    if len(files_to_fix) > max_files:
        console.print(f"[yellow]Limiting to {max_files} files. Use --max-files to adjust.[/yellow]")
        files_to_fix = files_to_fix[:max_files]

    # Step 6: Optionally verify fix with tests
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

    # Step 7: Create PR or just show results
    if no_pr:
        console.print("\n[bold]Fix generated (no PR created):[/bold]")
        for f in files_to_fix:
            console.print(Panel(
                f.get("fixed_content", "")[:1000] + "...",
                title=f"Fix for {f['path']}",
                border_style="green",
            ))
        return

    # Create PR
    console.print("\n[bold green]Creating Pull Request...[/bold green]")

    branch_name = f"fix/issue-{number}"
    pr_title = result.get("pr_title", f"Fix #{number}: {issue['title']}")
    pr_body = result.get("pr_body", f"Automated fix for #{number}")
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
    except Exception as e:
        console.print(f"\n[red]Failed to create PR: {e}[/red]")
        console.print("[yellow]You may need write access to the repository.[/yellow]")
        console.print("\n[dim]Fix content:[/dim]")
        for f in files_to_fix:
            console.print(Panel(
                f.get("fixed_content", "")[:500],
                title=f"Fix for {f['path']}",
                border_style="green",
            ))


@cli.command()
def info():
    """Show current configuration."""
    console.print(Panel(
        f"[bold]Model:[/bold] {config.openai_model}\n"
        f"[bold]Base URL:[/bold] {config.openai_base_url}\n"
        f"[bold]OpenAI Key:[/bold] {'Set' if config.openai_api_key else 'Not set'}\n"
        f"[bold]GitHub Token:[/bold] {'Set' if config.github_token else 'Not set'}\n"
        f"[bold]Chunk Size:[/bold] {config.chunk_size}\n"
        f"[bold]Top K:[/bold] {config.top_k}",
        title="Configuration",
    ))


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
