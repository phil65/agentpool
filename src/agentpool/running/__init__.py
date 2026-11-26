"""Decorator auto-injection / run package."""

from agentpool.running.decorators import with_nodes
from agentpool.running.discovery import NodeFunction, node_function
from agentpool.running.executor import execute_single, execute_functions
from agentpool.running.run_nodes import run_nodes_async, run_nodes
from agentpool.running.injection import NodeInjectionError

__all__ = [
    "NodeFunction",
    "NodeInjectionError",
    "execute_functions",
    "execute_single",
    "node_function",
    "run_nodes",
    "run_nodes_async",
    "with_nodes",
]
