"""Search Agent: Performs targeted RAG searches for relevant code.

Second stage of the pipeline. Uses the Analyzer Agent's output to:
- Run multiple targeted searches with different queries
- Filter and rank results by relevance
- Identify candidate files for modification
"""

from .base import BaseAgent
from .context import AgentContext


class SearchAgent(BaseAgent):
    """Performs multi-strategy RAG search to find relevant code."""

    def __init__(self, indexer):
        super().__init__()
        self.indexer = indexer

    def run(self, ctx: AgentContext) -> AgentContext:
        all_chunks = []
        seen_ids = set()

        # Strategy 1: Use analyzer-generated search queries
        for query in ctx.search_queries:
            chunks = self.indexer.search(query, top_k=5)
            for chunk in chunks:
                chunk_id = f"{chunk['file']}:{chunk['start_line']}"
                if chunk_id not in seen_ids:
                    seen_ids.add(chunk_id)
                    all_chunks.append(chunk)

        # Strategy 2: Search by affected areas
        for area in ctx.affected_areas:
            chunks = self.indexer.search(area, top_k=3)
            for chunk in chunks:
                chunk_id = f"{chunk['file']}:{chunk['start_line']}"
                if chunk_id not in seen_ids:
                    seen_ids.add(chunk_id)
                    all_chunks.append(chunk)

        # Strategy 3: Search for test files
        test_queries = [
            f"test {ctx.issue.get('title', '')}",
            f"test {ctx.issue_type}",
        ]
        test_chunks = []
        for query in test_queries:
            chunks = self.indexer.search(query, top_k=3)
            for chunk in chunks:
                chunk_id = f"{chunk['file']}:{chunk['start_line']}"
                if chunk_id not in seen_ids:
                    seen_ids.add(chunk_id)
                    test_chunks.append(chunk)

        # Sort all chunks by distance (lower = more relevant)
        all_chunks.sort(key=lambda c: c.get("distance", 999))
        test_chunks.sort(key=lambda c: c.get("distance", 999))

        # Limit to top results
        ctx.relevant_chunks = all_chunks[:15]
        ctx.test_chunks = test_chunks[:5]

        # Extract candidate file paths
        candidate_files = set()
        for chunk in ctx.relevant_chunks + ctx.test_chunks:
            candidate_files.add(chunk["file"])
        ctx.candidate_files = sorted(candidate_files)

        return ctx
