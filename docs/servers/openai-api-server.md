---
title: OpenAI API Server
description: OpenAI-compatible API server for AgentPool agents
icon: material/api
---

# OpenAI API Server

The OpenAI API server provides an OpenAI-compatible API that makes your AgentPool agents accessible through the standard OpenAI API format. This enables integration with any tool or library that supports the OpenAI API.

## Overview

The server implements the OpenAI API specification, exposing agents as models:

```
GET  /v1/models              -> List available agents
POST /v1/chat/completions    -> Chat completions (streaming supported)
POST /v1/responses           -> Responses API
```

## Quick Start

```bash
# Run with default settings
agentpool serve-api config.yml

# Custom host and port
agentpool serve-api config.yml --host 0.0.0.0 --port 8000
```

See [`serve-api`](../cli/serve-api.md) for all CLI options.

## Programmatic Usage

```python
import anyio
from agentpool import AgentPool
from agentpool_server.openai_api_server import OpenAIAPIServer


async def main():
    pool = AgentPool()
    await pool.add_agent(Agent("gpt-4-custom", model="openai:gpt-4"))
    
    server = OpenAIAPIServer(
        pool,
        host="0.0.0.0",
        port=8000,
        api_key="your-secret-key",  # Optional authentication
        cors=True,
        docs=True,
    )
    
    async with server, server.run_context():
        await anyio.sleep_forever()


anyio.run(main)
```

## API Endpoints

### List Models

```
GET /v1/models
```

Returns available agents as OpenAI-compatible models:

```json
{
  "object": "list",
  "data": [
    {
      "id": "assistant",
      "object": "model",
      "created": 0,
      "owned_by": "agentpool"
    },
    {
      "id": "coder",
      "object": "model",
      "created": 0,
      "owned_by": "agentpool"
    }
  ]
}
```

### Chat Completions

```
POST /v1/chat/completions
```

Standard OpenAI chat completions format:

```json
{
  "model": "assistant",
  "messages": [
    {"role": "user", "content": "Hello!"}
  ],
  "stream": false
}
```

Response:

```json
{
  "id": "msg_123",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "assistant",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! How can I help you today?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 15,
    "total_tokens": 25
  }
}
```

#### Streaming

Set `"stream": true` for server-sent events:

```
data: {"id":"msg_123","choices":[{"delta":{"content":"Hello"}}]}
data: {"id":"msg_123","choices":[{"delta":{"content":"!"}}]}
data: [DONE]
```

### Responses API

```
POST /v1/responses
```

Alternative responses API format:

```json
{
  "model": "assistant",
  "input": "Tell me a story about a robot."
}
```

## Authentication

The server supports optional API key authentication:

```python
server = OpenAIAPIServer(
    pool,
    api_key="your-secret-key"
)
```

Clients must include the key in the `Authorization` header:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"model": "assistant", "messages": [{"role": "user", "content": "Hi"}]}'
```

## Client Integration

### OpenAI Python Client

Use the official OpenAI Python client with your AgentPool server:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-secret-key"
)

response = client.chat.completions.create(
    model="assistant",  # Your agent name
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)
```

### Streaming with OpenAI Client

```python
stream = client.chat.completions.create(
    model="assistant",
    messages=[{"role": "user", "content": "Tell me a story"}],
    stream=True
)

for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

### LangChain Integration

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-secret-key",
    model="assistant"
)

response = llm.invoke("What is the meaning of life?")
```

### curl Examples

```bash
# List models
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer your-secret-key"

# Chat completion
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "assistant",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'

# Streaming
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer your-secret-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "assistant",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

## Configuration

### Agent Configuration

```yaml
# config.yml
agents:
  # Agents become models with their names as IDs
  gpt-4-custom:
    type: native
    model: openai:gpt-5-mini
    system_prompt: "You are a helpful assistant."
    
  claude-coder:
    type: claude_code
    model: sonnet
    tools:
      - type: file_access
      - type: process_management
```

Agents are accessible as models: `gpt-4-custom`, `claude-coder`, etc.

## API Documentation

When `docs=True` (default), the server provides interactive API documentation:

- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

## Use Cases

### Drop-in Replacement

Replace OpenAI API calls with your own agents without changing client code:

```python
# Before: Using OpenAI directly
client = OpenAI()

# After: Using your AgentPool server
client = OpenAI(base_url="http://localhost:8000/v1", api_key="key")

# Same API, your agents
response = client.chat.completions.create(
    model="my-agent",
    messages=[{"role": "user", "content": "Hello"}]
)
```

### Local Development

Run agents locally for development without API costs:

```yaml
agents:
  dev-assistant:
    model: ollama:llama3
    system_prompt: "You are a development assistant."
```

### API Gateway

Expose multiple backend models through a unified API:

```yaml
agents:
  fast:
    model: openai:gpt-4o-mini
  smart:
    model: anthropic:claude-sonnet-4-20250514
  local:
    model: ollama:llama3
```

Clients choose the appropriate model for their use case.
