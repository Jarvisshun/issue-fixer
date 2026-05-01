"""Multi-Agent system for Issue Fixer.

Four specialized agents collaborate through an orchestrator:
- AnalyzerAgent: Classifies issues, identifies root cause
- SearchAgent: Performs targeted RAG searches
- FixAgent: Generates patches/fixes
- ReviewAgent: Validates fix quality
"""

from .orchestrator import AgentOrchestrator

__all__ = ["AgentOrchestrator"]
