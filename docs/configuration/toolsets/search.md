---
title: Search Toolset
description: Web and news search capabilities
icon: material/magnify
---

# Search Toolset

The Search toolset provides web and news search capabilities using various search providers.

## Basic Usage

```yaml
agents:
  researcher:
    model: openai:gpt-5-mini
    tools:
      - type: search
        web_search: tavily  # provider name; the API key is read from TAVILY_API_KEY
```

`web_search` and `news_search` take either a provider name or a provider configuration
(`type` selects the provider):

```yaml
agents:
  researcher:
    model: openai:gpt-5-mini
    tools:
      - type: search
        web_search:
          type: brave
          api_key: ${BRAVE_API_KEY}
        news_search: serper
```

## Supported Providers

### Web Search

/// mknodes
{{ "agentpool_config.toolsets.WebSearchProviderConfig" | config_union_table }}
///

### News Search

/// mknodes
{{ "agentpool_config.toolsets.NewsSearchProviderConfig" | config_union_table }}
///

## Available Tools

```python exec="true"
from agentpool_config.toolsets import SearchToolsetConfig
from agentpool.docs.utils import generate_tool_docs

provider = {"type": "tavily", "api_key": "placeholder"}  # no request is made
config = SearchToolsetConfig(web_search=provider, news_search=provider)
print(generate_tool_docs(config.get_provider()))
```

## Configuration Reference

/// mknodes
{{ "agentpool_config.toolsets.SearchToolsetConfig" | schema_to_markdown(display_mode="yaml", header_style="pymdownx", wrapped_in="tools", header_level=3) }}
///

## Provider Reference

/// mknodes
{{ "agentpool_config.toolsets.WebSearchProviderConfig" | union_to_markdown(display_mode="yaml", header_style="pymdownx", as_listitem=False, wrapped_in="web_search", header_level=3) }}
///

API keys default to the provider's environment variable (for example `TAVILY_API_KEY`,
`BRAVE_API_KEY`, `SERPER_API_KEY`) when not set in the config.
