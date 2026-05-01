"""Orchestrator: Coordinates the Multi-Agent pipeline.

Pipeline flow:
  Analyzer → Search → Fix → DepCheck → Review
                        ↑               │
                        └───────────────┘  (retry if not approved)

Each agent is independent and communicates through AgentContext.
The orchestrator manages the flow, retries, and error handling.
"""

from pathlib import Path

from rich.console import Console

from .context import AgentContext
from .analyzer_agent import AnalyzerAgent
from .search_agent import SearchAgent
from .fix_agent import FixAgent
from .review_agent import ReviewAgent
from ..code_indexer import CodeIndexer
from ..dependency import build_dependency_graph, find_affected_files, format_dependency_report
from ..scoring import compute_confidence, format_confidence
from ..plugins import plugin_manager

console = Console(force_terminal=True)


class AgentOrchestrator:
    """Coordinates the Multi-Agent fix pipeline.

    Architecture:
    1. Analyzer Agent → classify issue, generate search queries
    2. Search Agent → RAG search with multiple strategies
    3. Fix Agent → generate patches/fixes
    4. Review Agent → validate fix quality
    5. If review fails → loop back to Fix Agent with feedback (max 2 iterations)
    """

    def __init__(self, indexer: CodeIndexer):
        self.analyzer = AnalyzerAgent()
        self.search = SearchAgent(indexer)
        self.fix = FixAgent()
        self.review = ReviewAgent()

    def run(self, issue: dict, repo_dir=None, mode: str = "diff") -> dict:
        """Execute the full Multi-Agent pipeline.

        Returns:
            dict compatible with the existing interface:
            {analysis, files_to_fix, pr_title, pr_body}
        """
        ctx = AgentContext(
            issue=issue,
            repo_dir=repo_dir,
            mode=mode,
            max_iterations=2,
        )

        # Stage 1: Analyze
        console.print("  [dim]→ Analyzer Agent: classifying issue...[/dim]")
        ctx = self.analyzer.run(ctx)
        console.print(
            f"  [dim]  Type: {ctx.issue_type}, "
            f"Confidence queries: {len(ctx.search_queries)}[/dim]"
        )

        # Plugin hook: on_analyze
        if plugin_manager.has_plugins():
            plugin_ctx = {
                "issue_type": ctx.issue_type,
                "root_cause": ctx.root_cause,
                "search_queries": ctx.search_queries,
                "affected_areas": ctx.affected_areas,
            }
            plugin_ctx = plugin_manager.run_on_analyze(ctx.issue, plugin_ctx)
            ctx.search_queries = plugin_ctx.get("search_queries", ctx.search_queries)
            ctx.affected_areas = plugin_ctx.get("affected_areas", ctx.affected_areas)
            console.print(f"  [dim]  Plugins: {len(plugin_manager.plugins)} active[/dim]")

        # Stage 2: Search
        console.print("  [dim]→ Search Agent: finding relevant code...[/dim]")
        ctx = self.search.run(ctx)
        console.print(
            f"  [dim]  Found {len(ctx.relevant_chunks)} code chunks, "
            f"{len(ctx.test_chunks)} test chunks, "
            f"{len(ctx.candidate_files)} candidate files[/dim]"
        )

        # Stage 3+4: Fix → Review loop
        while ctx.iteration < ctx.max_iterations:
            ctx.iteration += 1

            # Stage 3: Fix
            console.print(
                f"  [dim]→ Fix Agent: generating patches "
                f"(iteration {ctx.iteration})...[/dim]"
            )
            ctx = self.fix.run(ctx)

            # Plugin hook: on_fix
            if plugin_manager.has_plugins():
                ctx.files_to_fix = plugin_manager.run_on_fix(ctx.files_to_fix, {
                    "issue": ctx.issue,
                    "issue_type": ctx.issue_type,
                    "root_cause": ctx.root_cause,
                })

            console.print(
                f"  [dim]  Generated fixes for {len(ctx.files_to_fix)} file(s)[/dim]"
            )

            if not ctx.files_to_fix:
                console.print("  [yellow]  No fix could be generated.[/yellow]")
                break

            # Stage 3b: Dependency analysis
            if ctx.repo_dir and ctx.files_to_fix:
                console.print("  [dim]→ Dependency Agent: checking cross-file deps...[/dim]")
                changed = [f["path"] for f in ctx.files_to_fix]
                code_files = list(Path(ctx.repo_dir).rglob("*.*"))
                dep_graph = build_dependency_graph(ctx.repo_dir, code_files)
                affected = find_affected_files(changed, dep_graph)
                if affected:
                    report = format_dependency_report(affected)
                    console.print(f"  [yellow]  {report}[/yellow]")
                    # Add affected files info to context for the Review Agent
                    ctx.review_feedback = (
                        f"Cross-file dependencies detected:\n{report}\n\n"
                        + (ctx.review_feedback or "")
                    )

            # Stage 4: Review
            console.print("  [dim]→ Review Agent: validating fix quality...[/dim]")
            ctx = self.review.run(ctx)

            # Plugin hook: on_review
            if plugin_manager.has_plugins():
                review_data = {
                    "approved": ctx.review_approved,
                    "score": ctx.review_score,
                    "feedback": ctx.review_feedback,
                }
                review_data = plugin_manager.run_on_review(review_data, {
                    "issue": ctx.issue,
                    "files_to_fix": ctx.files_to_fix,
                    "iteration": ctx.iteration,
                })
                ctx.review_approved = review_data.get("approved", ctx.review_approved)
                ctx.review_score = review_data.get("score", ctx.review_score)
                ctx.review_feedback = review_data.get("feedback", ctx.review_feedback)

            console.print(
                f"  [dim]  Score: {ctx.review_score}/100, "
                f"Approved: {ctx.review_approved}[/dim]"
            )

            if ctx.review_approved:
                console.print(
                    f"  [green]  Review passed (score: {ctx.review_score})[/green]"
                )
                break
            else:
                if ctx.iteration < ctx.max_iterations:
                    console.print(
                        f"  [yellow]  Review feedback: "
                        f"{ctx.review_feedback[:100]}...[/yellow]"
                    )
                    console.print(
                        "  [yellow]  Retrying with feedback...[/yellow]"
                    )
                else:
                    console.print(
                        f"  [yellow]  Max iterations reached. "
                        f"Using best effort (score: {ctx.review_score})[/yellow]"
                    )

        # Stage 5: Confidence scoring
        console.print("  [dim]→ Scoring Agent: computing confidence...[/dim]")
        confidence = compute_confidence(
            files_to_fix=ctx.files_to_fix,
            review_score=ctx.review_score,
            affected_files=sum(len(v) for v in (
                find_affected_files(
                    [f["path"] for f in ctx.files_to_fix],
                    build_dependency_graph(ctx.repo_dir, list(Path(ctx.repo_dir).rglob("*.*")))
                ).values() if ctx.repo_dir else []
            )),
        )
        console.print(f"  {format_confidence(confidence)}")

        # Build result dict (compatible with existing interface)
        analysis_parts = [
            f"**Type:** {ctx.issue_type}",
            f"**Root Cause:** {ctx.root_cause}",
        ]
        if ctx.fix_strategy:
            analysis_parts.append(f"**Fix Strategy:** {ctx.fix_strategy}")
        if ctx.review_feedback:
            analysis_parts.append(f"**Review:** {ctx.review_feedback}")
        analysis_parts.append(f"**Confidence:** {confidence.total}/100 ({confidence.label})")

        return {
            "analysis": "\n\n".join(analysis_parts),
            "files_to_fix": ctx.files_to_fix,
            "pr_title": f"fix: {issue['title'][:60]}",
            "pr_body": ctx.root_cause,
            # Extra metadata
            "issue_type": ctx.issue_type,
            "review_score": ctx.review_score,
            "review_approved": ctx.review_approved,
            "iterations": ctx.iteration,
            "confidence": confidence.total,
            "confidence_label": confidence.label,
            "needs_review": confidence.needs_review,
        }
