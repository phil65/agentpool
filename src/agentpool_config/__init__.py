"""Core data models for AgentPool."""

from typing import Annotated
from pydantic import Field

from agentpool_config.tools import ImportToolConfig, BaseToolConfig
from agentpool_config.agentpool_tools import AgentpoolToolConfig
from agentpool_config.builtin_tools import BuiltinToolConfig

from agentpool_config.forward_targets import ForwardingTarget
from agentpool_config.session import SessionQuery
from agentpool_config.teams import TeamConfig
from agentpool_config.mcp_server import (
    BaseMCPServerConfig,
    StdioMCPServerConfig,
    StreamableHTTPMCPServerConfig,
    MCPServerConfig,
    SSEMCPServerConfig,
)
from agentpool_config.event_handlers import (
    BaseEventHandlerConfig,
    StdoutEventHandlerConfig,
    CallbackEventHandlerConfig,
    EventHandlerConfig,
    resolve_handler_configs,
)
from agentpool_config.hooks import (
    BaseHookConfig,
    CallableHookConfig,
    CommandHookConfig,
    HookConfig,
    HooksConfig,
    PromptHookConfig,
)
from agentpool_config.toolsets import ToolsetConfig
from agentpool_config.resolution import (
    ConfigLayer,
    ConfigSource,
    ResolvedConfig,
    find_project_config,
    get_global_config_dir,
    get_global_config_path,
    resolve_config,
    resolve_config_for_server,
)


ToolConfig = Annotated[
    ImportToolConfig | AgentpoolToolConfig,
    Field(discriminator="type"),
]

NativeAgentToolConfig = Annotated[
    ToolConfig | BuiltinToolConfig,
    Field(discriminator="type"),
]

# Unified type for all tool configurations (single tools + toolsets)
AnyToolConfig = Annotated[
    NativeAgentToolConfig | ToolsetConfig,
    Field(discriminator="type"),
]
__all__ = [
    "AnyToolConfig",
    "BaseEventHandlerConfig",
    "BaseHookConfig",
    "BaseMCPServerConfig",
    "BaseToolConfig",
    "CallableHookConfig",
    "CallbackEventHandlerConfig",
    "CommandHookConfig",
    "ConfigLayer",
    "ConfigSource",
    "EventHandlerConfig",
    "ForwardingTarget",
    "HookConfig",
    "HooksConfig",
    "MCPServerConfig",
    "NativeAgentToolConfig",
    "PromptHookConfig",
    "ResolvedConfig",
    "SSEMCPServerConfig",
    "SessionQuery",
    "StdioMCPServerConfig",
    "StdoutEventHandlerConfig",
    "StreamableHTTPMCPServerConfig",
    "TeamConfig",
    "ToolConfig",
    "ToolsetConfig",
    "find_project_config",
    "get_global_config_dir",
    "get_global_config_path",
    "resolve_config",
    "resolve_config_for_server",
    "resolve_handler_configs",
]
