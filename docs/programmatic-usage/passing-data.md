---
title: Passing Data to the Agent
description: Data passing patterns
icon: material/arrow-left-right
---

# Passing Data to the Agent

AgentPool agents can handle various types of input data in their `run()` and `run_stream()` methods. The actual support depends on the model and provider capabilities.

## Basic Text Input

The simplest form is passing text strings:

```python
await agent.run("Analyze this text")
await agent.run("First message", "Second message")  # Multiple prompts combined
```

## Path-Like Objects

The agent automatically handles path-like objects and converts them based on type:

```python
# Local files
await agent.run(Path("document.pdf"))
await agent.run(UPath("data/image.png"))

# Any UPath-supported protocol
await agent.run(UPath("s3://bucket/document.pdf"))
await agent.run(UPath("https://example.com/image.jpg"))
```

## Images

Images can be passed in several ways:

```python
# As path to image file
await agent.run(Path("photo.jpg"))

# As URL or binary content
from pydantic_ai import ImageUrl, BinaryImage
img_content = BinaryImage(data=binary_data, media_type="image/jpeg")
await agent.run(img_content)
```

## PDF Documents

PDF documents are supported by some models (e.g., Claude 3):

```python
await agent.run(Path("document.pdf"))
await agent.run("Analyze this:", Path("document.pdf"))
```

## Mixed Content

You can combine different types of input:

```python
await agent.run(
    "Analyze this image:",
    Path("chart.png"),
    "And compare it with this document:",
    Path("report.pdf")
)
```

## Streaming Response

The same input types work with streaming:

```python
async for event in agent.run_stream(
    "Describe this image:",
    Path("photo.jpg")
) :
    print(event)  # pydantic-ai events
```

## Input Conversion

The agent automatically converts inputs to the appropriate format for each provider:

- Paths are read and converted to base64 or URLs as needed
- Images are resized/encoded according to model requirements
- Multiple inputs are combined with appropriate separators
- Provider-specific content formats are generated

You rarely need to handle these conversions manually - just pass the raw inputs and let the agent handle the details.

## Response Types

Depending on the input, responses may include:

- Text analysis of documents
- Image descriptions
- Comparisons between different content types
- Extracted information in structured format

For structured output, use `output_type`:

```python
from pydantic import BaseModel

class ImageAnalysis(BaseModel):
    description: str
    objects: list[str]
    sentiment: str

agent = Agent("my-agent", output_type=ImageAnalysis)
result = await agent.run(Path("photo.jpg"))
print(result.content.objects)  # List of detected objects
```

This handling of multiple content types, combined with automatic conversion and provider-specific support checks,
makes it easy to work with rich input data while maintaining a clean API.
