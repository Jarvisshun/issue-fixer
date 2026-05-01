"""Plugin system: allow users to extend Issue Fixer with custom logic.

Users can place Python files in ~/.issue-fixer/plugins/ to add:
- Custom analysis rules (on_analyze)
- Custom fix strategies (on_fix)
- Custom review checks (on_review)

Plugin Interface:
    Each plugin file should define one or more of these functions:

    def on_analyze(issue: dict, context: dict) -> dict:
        '''Called after issue analysis. Return modified context.'''
        return context

    def on_fix(files_to_fix: list[dict], context: dict) -> list[dict]:
        '''Called after fix generation. Return modified files.'''
        return files_to_fix

    def on_review(review_result: dict, context: dict) -> dict:
        '''Called after review. Return modified review result.'''
        return review_result

Example plugin (save as ~/.issue-fixer/plugins/my_rules.py):

    def on_analyze(issue, context):
        '''Add custom labels-based rules.'''
        if 'security' in issue.get('labels', []):
            context['priority'] = 'high'
            context['search_queries'].append('vulnerability CVE exploit')
        return context

    def on_review(review_result, context):
        '''Reject fixes with too many files changed.'''
        if len(context.get('files_to_fix', [])) > 5:
            review_result['approved'] = False
            review_result['feedback'] = 'Too many files changed (>5). Split into smaller PRs.'
        return review_result
"""

import importlib.util
import sys
from pathlib import Path

from .config import config

PLUGIN_DIR = Path.home() / ".issue-fixer" / "plugins"


def _load_plugins() -> list[dict]:
    """Discover and load all plugin modules from the plugin directory."""
    if not PLUGIN_DIR.exists():
        return []

    plugins = []
    for py_file in sorted(PLUGIN_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        try:
            spec = importlib.util.spec_from_file_location(
                f"issue_fixer_plugin_{py_file.stem}", str(py_file)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            hooks = {}
            for hook_name in ("on_analyze", "on_fix", "on_review"):
                fn = getattr(module, hook_name, None)
                if callable(fn):
                    hooks[hook_name] = fn

            if hooks:
                plugins.append({
                    "name": py_file.stem,
                    "path": str(py_file),
                    "hooks": hooks,
                })
        except Exception as e:
            print(f"Warning: Failed to load plugin {py_file.name}: {e}", file=sys.stderr)

    return plugins


class PluginManager:
    """Manages plugin discovery and execution."""

    def __init__(self):
        self._plugins: list[dict] | None = None

    @property
    def plugins(self) -> list[dict]:
        if self._plugins is None:
            self._plugins = _load_plugins()
        return self._plugins

    def reload(self):
        """Force reload all plugins."""
        self._plugins = None

    def has_plugins(self) -> bool:
        return len(self.plugins) > 0

    def run_on_analyze(self, issue: dict, context: dict) -> dict:
        """Run all on_analyze hooks."""
        for plugin in self.plugins:
            fn = plugin["hooks"].get("on_analyze")
            if fn:
                try:
                    context = fn(issue, context)
                except Exception as e:
                    print(f"Warning: Plugin {plugin['name']}.on_analyze failed: {e}", file=sys.stderr)
        return context

    def run_on_fix(self, files_to_fix: list[dict], context: dict) -> list[dict]:
        """Run all on_fix hooks."""
        for plugin in self.plugins:
            fn = plugin["hooks"].get("on_fix")
            if fn:
                try:
                    files_to_fix = fn(files_to_fix, context)
                except Exception as e:
                    print(f"Warning: Plugin {plugin['name']}.on_fix failed: {e}", file=sys.stderr)
        return files_to_fix

    def run_on_review(self, review_result: dict, context: dict) -> dict:
        """Run all on_review hooks."""
        for plugin in self.plugins:
            fn = plugin["hooks"].get("on_review")
            if fn:
                try:
                    review_result = fn(review_result, context)
                except Exception as e:
                    print(f"Warning: Plugin {plugin['name']}.on_review failed: {e}", file=sys.stderr)
        return review_result

    def list_plugins(self) -> list[dict]:
        """List loaded plugins and their hooks."""
        return [
            {"name": p["name"], "path": p["path"], "hooks": list(p["hooks"].keys())}
            for p in self.plugins
        ]


# Singleton
plugin_manager = PluginManager()
