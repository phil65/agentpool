"""Forward target models."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import ConfigDict, Field, ImportString
from schemez import Schema
from upathtools import to_upath

from agentpool_config.conditions import Condition


if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.models.function import FunctionModel
    from upathtools import UPath

    from agentpool.messaging import ChatMessage, MessageNode


ConnectionType = Literal["run", "context", "forward"]
ConnectionSessionPolicy = Literal["target", "per_source_session", "standing", "per_message"]
"""Which session of the target a connection's messages run in (``run`` and ``context``).

- ``target``: the target's own session (the default).
- ``per_source_session``: one per session of the source (``<source session>/<connection>``,
  a child of it), so each conversation of the source has its own with the target.
- ``standing``: one for the connection, kept and resumed (``connection/<connection>``).
- ``per_message``: a new session for every message.

Other than ``target`` only for agent targets: a team runs its members in sessions of its own.
"""


DEFAULT_MESSAGE_TEMPLATE = """
[{{ message.timestamp.strftime('%Y-%m-%d %H:%M:%S') }}] {{ message.name }}: {{ message.content }}
"""


class ConnectionConfig(Schema):
    """Base model for message forwarding targets."""

    model_config = ConfigDict(json_schema_extra={"title": "Connection Configuration"})

    type: str = Field(init=False)
    """Connection type."""

    connection_type: ConnectionType

    session: ConnectionSessionPolicy = Field(
        default="target",
        examples=["target", "per_source_session", "standing", "per_message"],
        title="Target session",
    )
    """Which session of the target the messages run in (see ``ConnectionSessionPolicy``)."""

    wait_for_completion: bool = Field(default=True, title="Wait for completion")
    """Whether to wait for the result before continuing.

    If True, message processing will wait for the target to complete.
    If False, message will be forwarded asynchronously.
    """

    queued: bool = Field(default=False, title="Enable message queueing")
    """Whether messages should be queued for manual processing."""

    queue_strategy: Literal["concat", "latest", "buffer"] = Field(
        default="latest",
        examples=["concat", "latest", "buffer"],
        title="Queue processing strategy",
    )
    """How to process queued messages."""

    priority: int = Field(default=0, examples=[0, 1, 5], title="Task priority")
    """Priority of the task. Lower = higher priority."""

    delay: timedelta | None = Field(default=None, title="Processing delay")
    """Delay before processing."""
    filter_condition: Condition | None = Field(
        default=None,
        title="Message filter condition",
        examples=[{"type": "word_match", "words": ["bad word"]}],
    )
    """When to filter messages (using Talk.when())."""

    stop_condition: Condition | None = Field(
        default=None,
        title="Connection stop condition",
        examples=[{"type": "cost", "max_cost": 0.1}],
    )
    """When to disconnect the connection."""

    exit_condition: Condition | None = Field(
        default=None,
        title="Application exit condition",
        examples=[{"type": "cost", "max_cost": 0.2}],
    )
    """When to exit the application (by raising SystemExit)."""

    transform: ImportString[Callable[[Any], Any | Awaitable[Any]]] | None = Field(
        default=None,
        examples=["mymodule.transform_message", "utils.filters:clean_content"],
        title="Message transform function",
    )
    """Optional function to transform messages before forwarding."""

    def connect_nodes(
        self,
        source: MessageNode[Any, Any],
        agents: Sequence[MessageNode[Any, Any]],
        name: str,
    ) -> None:
        target = self.get_node(agents)
        source.connect_to(
            target,
            connection_type=self.connection_type,
            name=name,
            priority=self.priority,
            delay=self.delay,
            queued=self.queued,
            queue_strategy=self.queue_strategy,
            transform=self.transform,
            filter_condition=self.filter_condition.check if self.filter_condition else None,
            stop_condition=self.stop_condition.check if self.stop_condition else None,
            exit_condition=self.exit_condition.check if self.exit_condition else None,
            session=self.session,
        )
        source.connections.set_wait_state(target, wait=self.wait_for_completion)

    def get_node(self, nodes: Sequence[MessageNode[Any, Any]]) -> MessageNode[Any, Any]:
        raise NotImplementedError


class NodeConnectionConfig(ConnectionConfig):
    """Forward messages to another node.

    This configuration defines how messages should flow from one node to another,
    including:
    - Basic routing (which node, what type of connection)
    - Message queueing and processing strategies
    - Timing controls (priority, delay)
    - Execution behavior (wait for completion)
    """

    model_config = ConfigDict(json_schema_extra={"title": "Node Connection Configuration"})

    type: Literal["node"] = Field("node", init=False)
    """Connection to another node."""

    name: str = Field(
        examples=["output_agent", "processor", "notification_handler"],
        title="Target node name",
    )
    """Name of target agent."""

    connection_type: ConnectionType = Field(
        default="run",
        examples=["run", "context", "forward"],
        title="Connection type",
    )
    """How messages should be handled by the target agent:
    - run: Execute message as a new run
    - context: Add message to agent's context
    - forward: Forward message to agent's outbox
    """

    def get_node(self, nodes: Sequence[MessageNode[Any, Any]]) -> MessageNode[Any, Any]:
        node_dict = {i.name: i for i in nodes}
        if self.name not in node_dict:
            raise ValueError(f"Forward target {self.name} not found")
        return node_dict[self.name]


class FileConnectionConfig(ConnectionConfig):
    """Write messages to a file using a template.

    The template receives the full message object for formatting.
    Available fields include:
    - timestamp: When the message was created
    - name: Name of the sender
    - content: Message content
    - role: Message role (user/assistant/system)
    - model: Model used (if any)
    - cost_info: Token usage and cost info
    - parent_id: ID of parent message for tracking chains
    """

    model_config = ConfigDict(json_schema_extra={"title": "File Connection Configuration"})

    type: Literal["file"] = Field("file", init=False)
    """Connection to a file."""

    connection_type: Literal["run"] = Field("run", init=False, exclude=True)
    """Connection type (fixed to "run")"""

    path: str = Field(
        examples=["logs/messages.txt", "/var/log/agent-{date}.log", "output/{agent}.md"],
        title="Output file path",
    )
    """Path to output file. Supports variables: {date}, {time}, {agent}"""

    template: str = Field(
        default=DEFAULT_MESSAGE_TEMPLATE,
        examples=[
            DEFAULT_MESSAGE_TEMPLATE,
            "{{ message.content }}",
            "[{{ message.name }}]: {{ message.content }}",
        ],
        title="Message template",
    )
    """Jinja2 template for message formatting."""

    encoding: str = Field(
        default="utf-8",
        examples=["utf-8", "ascii", "latin1"],
        title="File encoding",
    )
    """File encoding to use."""

    def get_node(self, nodes: Sequence[MessageNode[Any, Any]]) -> MessageNode[Any, Any]:
        from agentpool import Agent

        agent_name = f"file_writer_{Path(self.path).stem}"
        return Agent(model=self.get_model(), name=agent_name)

    def format_message(self, message: ChatMessage[Any]) -> str:
        """Format a message using the template."""
        from jinja2 import Template

        template: Template = Template(self.template)
        return template.render(message=message)

    def resolve_path(self, context: dict[str, str]) -> UPath:
        """Resolve path template with context variables."""
        from agentpool.utils.time_utils import get_now

        now = get_now()
        date = now.strftime("%Y-%m-%d")
        time_ = now.strftime("%H-%M-%S")
        variables = {"date": date, "time": time_, **context}
        return to_upath(self.path.format(**variables))

    def get_model(self) -> FunctionModel:
        """Get provider for file writing."""
        from jinja2 import Template
        from llmling_models import function_to_model

        path_obj = to_upath(self.path)
        template_obj = Template(self.template)

        async def write_message(message: str) -> str:
            formatted = template_obj.render(message=message)
            path_obj.write_text(formatted + "\n", encoding=self.encoding)
            return ""

        return function_to_model(write_message)


class CallableConnectionConfig(ConnectionConfig):
    """Forward messages to a callable.

    The callable can be either sync or async and should have the signature:
    def process_message(message: ChatMessage[Any], **kwargs) -> Any

    Any additional kwargs specified in the config will be passed to the callable.
    """

    model_config = ConfigDict(json_schema_extra={"title": "Callable Connection Configuration"})

    type: Literal["callable"] = Field("callable", init=False)
    """Connection to a callable imported from given import path."""

    callable: ImportString[Callable[..., Any]] = Field(
        examples=["mymodule.process_message", "handlers.notifications:send_email"],
        title="Callable import path",
    )
    """Import path to the message processing function."""

    connection_type: Literal["run"] = Field("run", init=False, exclude=True)
    """Connection type (fixed to "run")"""

    kw_args: dict[str, Any] = Field(default_factory=dict, title="Additional arguments")
    """Additional kwargs to pass to the callable."""

    def get_node(self, nodes: Sequence[MessageNode[Any, Any]]) -> MessageNode[Any, Any]:
        from agentpool import Agent
        from agentpool.utils.inspection import get_fn_name

        return Agent(model=self.get_model(), name=get_fn_name(self.callable))

    async def process_message(self, message: ChatMessage[Any]) -> Any:
        """Process a message through the callable.

        Handles both sync and async callables transparently.
        """
        from agentpool.utils.inspection import execute

        return await execute(self.callable, message, **self.kw_args)

    def get_model(self) -> FunctionModel:
        """Get provider for callable."""
        from llmling_models import function_to_model

        return function_to_model(self.callable)


ForwardingTarget = Annotated[
    NodeConnectionConfig | FileConnectionConfig | CallableConnectionConfig,
    Field(discriminator="type"),
]
