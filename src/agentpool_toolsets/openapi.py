"""OpenAPI toolset provider."""

from typing import TYPE_CHECKING, Any

from schemez.openapi.callable_factory import OpenAPICallableFactory
from schemez.openapi.loader import load_openapi_spec, parse_operations
from upathtools import read_path, to_upath

from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider


if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    import httpx2
    from upathtools import JoinablePathLike

    from agentpool.tools.base import Tool

logger = get_logger(__name__)


class OpenAPITools(ResourceProvider):
    """Provider for OpenAPI-based tools."""

    def __init__(
        self,
        spec: JoinablePathLike,
        base_url: str = "",
        name: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(name=name or f"openapi_{base_url}")
        self.spec_url = spec
        self.base_url = base_url
        self.headers = headers or {}
        self._client: httpx2.AsyncClient | None = None
        self._spec: dict[str, Any] | None = None
        self._schemas: dict[str, dict[str, Any]] = {}
        self._operations: dict[str, Any] = {}
        self._factory: OpenAPICallableFactory | None = None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_tools(self) -> Sequence[Tool]:
        """Get all API operations as tools."""
        if not self._spec:
            await self._load_spec()

        if not self._factory:
            return []

        tools = []
        for op_id, config in self._operations.items():
            method = self._factory.create_callable(op_id, config)
            tool = self.create_tool(method, metadata={"operation": op_id})
            tools.append(tool)
        return tools

    async def _load_spec(self) -> dict[str, Any]:
        import httpx2

        if not self._client:
            self._client = httpx2.AsyncClient(base_url=self.base_url, headers=self.headers)

        try:
            spec_str = str(self.spec_url)
            if spec_str.startswith(("http://", "https://")):
                self._spec = load_openapi_spec(spec_str)
            else:
                path = to_upath(self.spec_url)
                if path.exists():
                    self._spec = load_openapi_spec(path)
                else:
                    # Try reading via upathtools for remote/special paths
                    content = await read_path(self.spec_url)
                    import tempfile

                    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
                        f.write(content)
                        temp_path = f.name
                    try:
                        self._spec = load_openapi_spec(temp_path)
                    finally:
                        to_upath(temp_path).unlink(missing_ok=True)

            if not self._spec:
                raise ValueError(f"Empty or invalid OpenAPI spec from {self.spec_url}")  # noqa: TRY301

            self._schemas = self._spec.get("components", {}).get("schemas", {})
            self._operations = parse_operations(self._spec.get("paths", {}))
            self._factory = OpenAPICallableFactory(self._schemas, self._make_request)

            if not self._operations:
                logger.warning("No operations found in spec %s.", self.spec_url)

        except Exception as e:
            raise ValueError(f"Failed to load OpenAPI spec from {self.spec_url}") from e
        else:
            return self._spec

    async def _make_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any],
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Perform HTTP request to the API."""
        if not self._client:
            import httpx2

            self._client = httpx2.AsyncClient(base_url=self.base_url, headers=self.headers)

        response = await self._client.request(method=method, url=path, params=params, json=body)
        response.raise_for_status()
        if not response.content:  # e.g. 204 No Content
            return {}
        return response.json()  # type: ignore[no-any-return]
