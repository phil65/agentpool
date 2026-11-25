---
title: Notifications Toolset
description: Send notifications via various channels
icon: material/bell
---

# Notifications Toolset

Send notifications to named channels (Slack, Telegram, email, webhooks, ...) via Apprise.

## Basic Usage

```yaml
agents:
  notifier:
    model: openai:gpt-5-mini
    tools:
      - type: notifications
        channels:  # named channels, each one or more Apprise URLs
          team: slack://${SLACK_TOKEN_A}/${SLACK_TOKEN_B}/${SLACK_TOKEN_C}/#alerts
          ops:
            - mailto://${EMAIL_USER}:${EMAIL_PASS}@gmail.com
            - tgram://${TELEGRAM_BOT_TOKEN}/${TELEGRAM_CHAT_ID}
```

## Available Tools

```python exec="true"
from agentpool_config.toolsets import NotificationsToolsetConfig
from agentpool.docs.utils import generate_tool_docs

config = NotificationsToolsetConfig(channels={"team": "json://localhost"})
print(generate_tool_docs(config.get_provider()))
```

## Supported Channels

Notifications are sent with [Apprise](https://github.com/caronc/apprise), so every service
Apprise supports works (Slack, Telegram, Discord, email, ntfy, webhooks and many more): a channel
is configured by its [Apprise URL](https://github.com/caronc/apprise/wiki#notification-services).

## Configuration Reference

/// mknodes
{{ "agentpool_config.toolsets.NotificationsToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///
