"""Orchestrator: Coordinates the Multi-Agent pipeline.

Pipeline flow:
  Analyzer → Search → Fix → Review
                        ↑        │
                        └────────┘  (retry if not approved)

Each agent is independent and communicates through AgentContext.
The orchestrator manages the flow, retries, and error handling.
"""

from rich.console import Console

from .context import AgentContext
from .analyzer_agent import AnalyzerAgent
from .search_agent import SearchAgent
from .fix_agent import FixAgent
from .review_agent import ReviewAgent
from ..code_indexer import CodeIndexer

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
            console.print(
                f"  [dim]  Generated fixes for {len(ctx.files_to_fix)} file(s)[/dim]"
            )

            if not ctx.files_to_fix:
                console.print("  [yellow]  No fix could be generated.[/yellow]")
                break

            # Stage 4: Review
            console.print("  [dim]→ Review Agent: validating fix quality...[/dim]")
            ctx = self.review.run(ctx)
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

        # Build result dict (compatible with existing interface)
        analysis_parts = [
            f"**Type:** {ctx.issue_type}",
            f"**Root Cause:** {ctx.root_cause}",
        ]
        if ctx.fix_strategy:
            analysis_parts.append(f"**Fix Strategy:** {ctx.fix_strategy}")
        if ctx.review_feedback:
            analysis_parts.append(f"**Review:** {ctx.review_feedback}")

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
        }
