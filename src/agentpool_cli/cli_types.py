"""Type definitions for CLI options."""

from typing import Literal


# Log levels
LogLevel = Literal["debug", "info", "warning", "error"]

# Output detail levels
DetailLevel = Literal["simple", "detailed", "markdown"]

# Statistics grouping options
GroupBy = Literal["agent", "model", "hour", "day"]

OutputFormat = Literal["json", "yaml", "table", "text"]

# Provider types
Provider = Literal["pydantic_ai"]

# Merge methods for pull requests
MergeMethod = Literal["merge", "squash", "rebase"]
