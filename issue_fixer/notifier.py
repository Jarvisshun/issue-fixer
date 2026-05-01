"""Notification system: push fix results to Slack, Discord, or custom webhooks.

Sends a formatted message when a fix completes, including:
- Issue title and URL
- Files changed
- Confidence score
- PR link (if created)

Supports:
- Slack Incoming Webhooks
- Discord Webhooks
- Generic webhook (JSON POST)
"""

import json
import os
from urllib.request import Request, urlopen
from urllib.error import URLError

from .config import config


def _post_json(url: str, payload: dict, timeout: int = 10) -> bool:
    """POST JSON payload to a URL. Returns True on success."""
    try:
        data = json.dumps(payload).encode("utf-8")
        req = Request(url, data=data, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.status in (200, 204)
    except (URLError, OSError, TimeoutError):
        return False


def notify_slack(
    webhook_url: str,
    issue_title: str,
    issue_url: str,
    files_changed: list[str],
    confidence: int,
    pr_url: str = "",
    success: bool = True,
) -> bool:
    """Send a Slack notification about a fix result.

    Uses Slack Block Kit for rich formatting.
    """
    status_emoji = "white_check_mark" if success else "x"
    status_text = "Fixed" if success else "Failed"
    color = "#36a64f" if success else "#dc3545"

    files_text = "\n".join(f"• `{f}`" for f in files_changed[:5])
    if len(files_changed) > 5:
        files_text += f"\n• ... and {len(files_changed) - 5} more"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f":{status_emoji}: Issue {status_text}: {issue_title[:80]}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Issue:*\n<{issue_url}|{issue_title[:50]}>"},
                {"type": "mrkdwn", "text": f"*Confidence:*\n{confidence}/100"},
            ],
        },
    ]

    if files_changed:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Files Changed:*\n{files_text}"},
        })

    if pr_url:
        blocks.append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "View PR"},
                "url": pr_url,
                "style": "primary",
            }],
        })

    blocks.append({"type": "divider"})

    payload = {
        "attachments": [{"color": color, "blocks": blocks}],
    }

    return _post_json(webhook_url, payload)


def notify_discord(
    webhook_url: str,
    issue_title: str,
    issue_url: str,
    files_changed: list[str],
    confidence: int,
    pr_url: str = "",
    success: bool = True,
) -> bool:
    """Send a Discord notification about a fix result.

    Uses Discord embeds for rich formatting.
    """
    status_emoji = "✅" if success else "❌"
    status_text = "Fixed" if success else "Failed"
    color = 0x36A64F if success else 0xDC3545

    files_text = "\n".join(f"• `{f}`" for f in files_changed[:5])
    if len(files_changed) > 5:
        files_text += f"\n• ... and {len(files_changed) - 5} more"

    fields = [
        {"name": "Issue", "value": f"[{issue_title[:50]}]({issue_url})", "inline": True},
        {"name": "Confidence", "value": f"{confidence}/100", "inline": True},
    ]

    if files_changed:
        fields.append({"name": "Files Changed", "value": files_text[:1024], "inline": False})

    description = f"{status_emoji} **Issue {status_text}**"
    if pr_url:
        description += f"\n[View Pull Request]({pr_url})"

    embed = {
        "title": f"Issue Fixer: {issue_title[:80]}",
        "description": description,
        "color": color,
        "fields": fields,
    }

    payload = {"embeds": [embed]}
    return _post_json(webhook_url, payload)


def notify_generic(
    webhook_url: str,
    issue_title: str,
    issue_url: str,
    files_changed: list[str],
    confidence: int,
    pr_url: str = "",
    success: bool = True,
) -> bool:
    """Send a generic JSON webhook notification."""
    payload = {
        "event": "issue_fix_completed",
        "success": success,
        "issue": {
            "title": issue_title,
            "url": issue_url,
        },
        "files_changed": files_changed,
        "confidence": confidence,
        "pr_url": pr_url,
    }
    return _post_json(webhook_url, payload)


# Notification channel config
def get_notification_channels() -> list[dict]:
    """Read notification channels from environment variables.

    Supported env vars:
        SLACK_WEBHOOK_URL   - Slack incoming webhook
        DISCORD_WEBHOOK_URL - Discord webhook
        CUSTOM_WEBHOOK_URL  - Generic JSON webhook
    """
    channels = []

    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if slack_url:
        channels.append({"type": "slack", "url": slack_url})

    discord_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if discord_url:
        channels.append({"type": "discord", "url": discord_url})

    custom_url = os.environ.get("CUSTOM_WEBHOOK_URL", "")
    if custom_url:
        channels.append({"type": "generic", "url": custom_url})

    return channels


def send_notifications(
    issue_title: str,
    issue_url: str,
    files_changed: list[str],
    confidence: int,
    pr_url: str = "",
    success: bool = True,
) -> dict[str, bool]:
    """Send notifications to all configured channels.

    Returns dict of {channel_type: success}.
    """
    channels = get_notification_channels()
    results = {}

    dispatchers = {
        "slack": notify_slack,
        "discord": notify_discord,
        "generic": notify_generic,
    }

    for ch in channels:
        fn = dispatchers.get(ch["type"])
        if fn:
            ok = fn(
                webhook_url=ch["url"],
                issue_title=issue_title,
                issue_url=issue_url,
                files_changed=files_changed,
                confidence=confidence,
                pr_url=pr_url,
                success=success,
            )
            results[ch["type"]] = ok

    return results
