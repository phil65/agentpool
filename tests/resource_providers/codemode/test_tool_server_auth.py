"""The code-mode tool server on localhost serves only requests with its bearer token.

The generated client code the execution environment runs carries the token.
"""

from exxec_config import LocalExecutionEnvironmentConfig
import httpx2

from agentpool.resource_providers.codemode.code_executor import RemoteCodeExecutor
from agentpool.tools.base import FunctionTool


def add_numbers(x: int, y: int) -> int:
    """Add two numbers."""
    return x + y


def _executor() -> RemoteCodeExecutor:
    tools = [FunctionTool.from_callable(add_numbers)]
    return RemoteCodeExecutor.from_tools(tools, LocalExecutionEnvironmentConfig(), server_port=0)


async def test_tool_server_refuses_requests_without_the_token() -> None:
    executor = _executor()
    async with executor:
        handler = executor.server_handler
        assert handler is not None
        url = f"http://{handler.host}:{handler.port}/tools/add_numbers"
        params = {"x": 1, "y": 2}
        async with httpx2.AsyncClient() as client:
            missing = await client.get(url, params=params)
            wrong = await client.get(url, params=params, headers={"Authorization": "Bearer wrong"})
            right = await client.get(
                url, params=params, headers={"Authorization": f"Bearer {handler.token}"}
            )
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert right.status_code == 200


async def test_generated_client_code_calls_tools_with_the_token() -> None:
    executor = _executor()
    async with executor:
        result = await executor.execute_code(
            "async def main():\n    return await add_numbers(x=5, y=3)\n", title="add"
        )
    assert result.success, result.error
    assert result.result == 8


async def test_each_server_has_its_own_token() -> None:
    first, second = _executor(), _executor()
    assert first.server_handler is not None
    assert second.server_handler is not None
    assert first.server_handler.token != second.server_handler.token
