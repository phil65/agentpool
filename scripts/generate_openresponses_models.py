"""Generate Pydantic models from OpenResponses OpenAPI specification.

This script downloads the OpenAPI spec from the OpenResponses repository
and generates Pydantic models using datamodel-codegen.

Requirements:
    uv tool install datamodel-code-generator

Usage:
    python scripts/generate_openresponses_models.py
    # Or with uv:
    uv run python scripts/generate_openresponses_models.py
"""

from pathlib import Path
import subprocess
import sys


# URLs and paths
OPENAPI_URL = (
    "https://raw.githubusercontent.com/openresponses/openresponses/main/public/openapi/openapi.json"
)
PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "src" / "agentpool" / "models" / "openresponses"
OUTPUT_FILE = OUTPUT_DIR / "models.py"
TEMP_OPENAPI = "/tmp/openresponses_openapi.json"


def download_openapi_spec() -> None:
    """Download the OpenAPI specification."""
    print(f"Downloading OpenAPI spec from {OPENAPI_URL}...")
    result = subprocess.run(
        ["curl", "-fsSL", OPENAPI_URL, "-o", TEMP_OPENAPI],
        check=True,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error downloading spec: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(f"  Downloaded to {TEMP_OPENAPI}")


def generate_models() -> None:
    """Generate Pydantic models using datamodel-codegen."""
    print("Generating Pydantic models...")

    cmd = [
        "uv",
        "tool",
        "run",
        "--from",
        "datamodel-code-generator",
        "datamodel-codegen",
        "--input",
        TEMP_OPENAPI,
        "--input-file-type",
        "openapi",
        "--output",
        str(OUTPUT_FILE),
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--target-python-version",
        "3.13",
        "--target-pydantic-version",
        "2",
        "--use-standard-collections",
        "--use-annotated",
        "--field-constraints",
        "--use-schema-description",
        "--use-field-description",
        "--reuse-model",
        "--enum-field-as-literal",
        "all",
        "--use-one-literal-as-default",
        "--collapse-root-models",
        "--base-class",
        "agentpool.models.openresponses.base.OpenResponsesBase",
        "--formatters",
        "ruff-check",
        "ruff-format",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if result.returncode != 0:
        print(f"Error generating models: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    # Show warnings but don't fail
    if result.stderr:
        print(f"Warnings: {result.stderr}")

    print(f"  Generated models to {OUTPUT_FILE}")


def verify_output() -> None:
    """Verify the generated file exists and has content."""
    if not OUTPUT_FILE.exists():
        print(f"Error: Output file {OUTPUT_FILE} not found!", file=sys.stderr)
        sys.exit(1)

    content = OUTPUT_FILE.read_text()
    lines = content.count("\n")
    print(f"  Verified: {lines} lines generated")

    model_count = content.count("class ")
    print(f"  Generated {model_count} model classes")


def main() -> None:
    """Main entry point."""
    print("=" * 60)
    print("OpenResponses Model Generation")
    print("=" * 60)

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        download_openapi_spec()
        generate_models()
        verify_output()

        print("\n" + "=" * 60)
        print("Success! Models generated successfully.")
        print("=" * 60)
        print(f"\nGenerated models: {OUTPUT_FILE}")
        print("\nNext steps:")
        print("  1. Review the generated models")
        print("  2. Update __init__.py if needed")
        print("  3. Run tests to verify imports work")

    except subprocess.CalledProcessError as e:
        print(f"\nError: Command failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"\nUnexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
