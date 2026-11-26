"""Generate JSON schema for AgentsManifest config.

Can be used:
1. As a standalone script: python scripts/generate_schema.py
2. As a pre-commit hook
3. From CI
"""

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Literal

from agentpool import AgentsManifest
from agentpool.log import configure_logging, get_logger


logger = get_logger(__name__)


def _convert_to_openapi_nullable(obj: dict[str, Any] | list[Any]) -> None:
    """Convert anyOf nullable patterns to OpenAPI nullable format (in-place)."""
    if isinstance(obj, dict):
        # Check if this is a nullable anyOf pattern
        if "anyOf" in obj:
            any_of = obj["anyOf"]
            if len(any_of) == 2:  # noqa: PLR2004
                null_item = None
                type_item = None

                for item in any_of:
                    if isinstance(item, dict):
                        if item.get("type") == "null":
                            null_item = item
                        elif "type" in item and len(item) == 1:
                            type_item = item

                # If we found a simple null + type pattern
                if null_item and type_item:
                    del obj["anyOf"]
                    obj.update(type_item)
                    obj["nullable"] = True

        # Recurse into nested objects
        for value in obj.values():
            if isinstance(value, (dict, list)):
                _convert_to_openapi_nullable(value)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                _convert_to_openapi_nullable(item)


def generate_schema_variant(
    variant: Literal["any_of", "primitive_type_array", "openapi"] = "primitive_type_array",
) -> dict[str, Any]:
    """Generate schema with specific union format.

    Args:
        variant: Union format for nullable fields
            - "any_of": Standard Pydantic anyOf pattern
            - "primitive_type_array": Use type arrays ["string", "null"]
            - "openapi": Use type + nullable property (OpenAPI 3.0 style)

    Returns:
        Generated JSON schema
    """
    if variant == "openapi":
        schema = AgentsManifest.model_json_schema(union_format="any_of")
        _convert_to_openapi_nullable(schema)
        return schema
    return AgentsManifest.model_json_schema(union_format=variant)


def generate_schema(
    output_path: str | Path | None = None,
    check_only: bool = False,
    force: bool = True,
    variant: Literal["any_of", "primitive_type_array", "openapi"] = "primitive_type_array",
) -> tuple[bool, dict[str, Any]]:
    """Generate JSON schema for config models.

    Args:
        output_path: Where to write the schema. If None, uses default location
        check_only: Just check if schema would change, don't write
        force: Force-overwrite
        variant: Union format for nullable fields

    Returns:
        Tuple of (changed: bool, schema: dict)
    """
    # Get default path if none provided
    if output_path is None:
        root = Path(__file__).parent.parent
        output_path = root / "schema" / "config-schema.json"
    else:
        output_path = Path(output_path)

    logger.info("Generating schema", output_path=output_path, variant=variant)

    # Generate new schema
    schema = generate_schema_variant(variant)
    logger.info("Generated schema", variant=variant)

    # Check if different from existing
    changed = True
    if output_path.exists():
        try:
            with output_path.open() as f:
                current = json.load(f)
            changed = current != schema
            logger.info("Schema status", status="differs" if changed else "unchanged")

        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read current schema", error=exc)

    # Write if needed
    if (changed or force) and not check_only:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w") as f:
            json.dump(schema, f, indent=2)
        logger.info("Schema written", output_path=output_path)

    return changed, schema


def main() -> int:
    """Run schema generation."""
    configure_logging()
    parser = argparse.ArgumentParser(description="Generate config schema")
    parser.add_argument(
        "--output",
        "-o",
        help="Output path (default: schema/config-schema.json)",
    )
    parser.add_argument(
        "--check",
        "-c",
        action="store_true",
        help="Check if schema would change without writing",
    )
    parser.add_argument(
        "--variant",
        "-v",
        choices=["any_of", "primitive_type_array", "openapi"],
        default="primitive_type_array",
        help="Union format for nullable fields (default: primitive_type_array)",
    )
    args = parser.parse_args()

    try:
        changed, _ = generate_schema(args.output, args.check, variant=args.variant)
        if args.check and changed:
            logger.warning("Schema would change")
            return 1
    except Exception:
        logger.exception("Schema generation failed")
        return 1
    else:
        return 0


if __name__ == "__main__":
    sys.exit(main())
