"""Config creation toolset with schema validation."""

from typing import TYPE_CHECKING, Any, Literal

import anyenv
from schemez.helpers import json_schema_to_pydantic_code
from upathtools import is_directory_sync, to_upath
from upathtools.filesystems.file_filesystems import JsonSchemaFileSystem

from agentpool.resource_providers import StaticResourceProvider


if TYPE_CHECKING:
    import jsonschema
    from upathtools import JoinablePathLike


MarkupType = Literal["yaml", "json", "toml"]


def _format_validation_error(error: jsonschema.ValidationError) -> str:
    """Format a validation error for user-friendly display."""
    path = " -> ".join(str(p) for p in error.absolute_path) if error.absolute_path else "root"
    return f"At '{path}': {error.message}"


class ConfigCreationTools(StaticResourceProvider):
    """Provider for config creation and validation tools."""

    def __init__(
        self,
        schema_path: JoinablePathLike,
        markup: MarkupType = "yaml",
        name: str = "config_creation",
    ) -> None:
        """Initialize the config creation toolset.

        Args:
            schema_path: Path to the JSON schema file
            markup: Markup language for configs (yaml, json, toml)
            name: Namespace for the tools
        """
        super().__init__(name=name)
        self._schema_path = to_upath(schema_path)
        self._markup: MarkupType = markup
        self._schema: dict[str, Any] | None = None

        self.add_tool(
            self.create_tool(
                self.create_config,
                category="edit",
                read_only=False,
                idempotent=True,
                description_override=(
                    f"Create and validate a {markup.upper()} configuration. "
                    "Returns validation result and any errors."
                ),
            )
        )
        self.add_tool(
            self.create_tool(
                self.show_schema_as_code, category="read", read_only=True, idempotent=True
            )
        )
        self.add_tool(
            self.create_tool(self.list_schema, category="read", read_only=True, idempotent=True)
        )
        self.add_tool(
            self.create_tool(
                self.read_schema_node, category="read", read_only=True, idempotent=True
            )
        )

    def _load_schema(self) -> dict[str, Any]:
        """Load and cache the JSON schema."""
        if self._schema is None:
            content = self._schema_path.read_text()
            self._schema = anyenv.load_json(content, return_type=dict)
        return self._schema

    def _get_schema_fs(self) -> JsonSchemaFileSystem:
        """Get or create the JSON schema filesystem."""
        return JsonSchemaFileSystem(schema_url=str(self._schema_path))

    async def create_config(self, content: str) -> str:
        """Create and validate a configuration.

        Args:
            content: The configuration content in the configured markup format

        Returns:
            Validation result message
        """
        import jsonschema
        import yamling

        schema = self._load_schema()
        try:
            data = yamling.load(content, self._markup, verify_type=dict)
        except Exception as e:  # noqa: BLE001
            return f"Failed to parse {self._markup.upper()}: {e}"

        validator = jsonschema.Draft202012Validator(schema)
        if errors := [_format_validation_error(e) for e in validator.iter_errors(data)]:
            error_list = "\n".join(f"- {e}" for e in errors[:10])
            suffix = f"\n... and {len(errors) - 10} more errors" if len(errors) > 10 else ""  # noqa: PLR2004
            return f"Validation failed with {len(errors)} error(s):\n{error_list}{suffix}"

        return "Configuration is valid! Successfully validated against schema."

    async def show_schema_as_code(self) -> str:
        """Show the JSON schema as Python Pydantic code.

        Returns:
            Python code representation of the schema
        """
        schema = self._load_schema()
        return json_schema_to_pydantic_code(schema, class_name="Config")

    async def list_schema(self, path: str = "/") -> str:
        """List contents at a path in the JSON schema.

        Args:
            path: Path to list (e.g. '/', '/$defs', '/$defs/AgentConfig/properties')

        Returns:
            Formatted listing of schema contents at the path
        """
        fs = self._get_schema_fs()
        try:
            items = fs.ls(path, detail=True)
        except FileNotFoundError:
            return f"Path not found: {path}"

        if not items:
            return f"No contents at: {path}"

        lines = [f"Contents of {path}:\n"]
        for item in items:
            name = item["name"]
            fs = self._get_schema_fs()
            full_path = f"{path.rstrip('/')}/{name}" if path != "/" else f"/{name}"
            is_dir = is_directory_sync(fs, full_path, entry_type=item.get("type"))
            icon = "📁" if is_dir else "📄"
            parts = [f"{icon} {name}"]
            if schema_type := item.get("schema_type"):
                parts.append(f"[{schema_type}]")
            if item.get("required"):
                parts.append("(required)")
            if desc := item.get("description"):
                # Truncate long descriptions
                desc_short = desc[:60] + "..." if len(desc) > 60 else desc  # noqa: PLR2004
                parts.append(f"- {desc_short}")
            lines.append("  " + " ".join(parts))

        return "\n".join(lines)

    async def read_schema_node(self, path: str) -> str:
        """Read the JSON schema at a specific path.

        Args:
            path: Path to read (e.g. '/$defs/AgentConfig', '/properties/agents')

        Returns:
            JSON schema content at the path
        """
        fs = self._get_schema_fs()
        try:
            content: str = fs.cat(path)
            # Parse and re-format for readability
            schema_data = anyenv.load_json(content)
            return anyenv.dump_json(schema_data, indent=True)
        except FileNotFoundError:
            return f"Path not found: {path}"
        except anyenv.JsonLoadError as e:
            return f"Failed to parse schema at {path}: {e}"
