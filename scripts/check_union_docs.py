"""Check that all members of discriminated unions have corresponding doc files.

Usage:
    python scripts/check_union_docs.py
"""

from pathlib import Path
import sys

from agentpool.docs.utils import check_docs_for_union, discriminator_to_filename


def main() -> int:
    """Run checks for known union types."""
    # Import here to avoid import errors if dependencies missing
    from agentpool_config.toolsets import ToolsetConfig

    checks = [
        (ToolsetConfig, Path("docs/configuration/toolsets"), "Toolsets"),
        # Add more checks here as needed:
        # (EventSourceConfig, Path("docs/configuration/event-sources"), "Event Sources"),
    ]

    exit_code = 0

    for union_type, docs_dir, name in checks:
        print(f"\nChecking {name}...")
        print(f"  Union: {union_type}")
        print(f"  Docs:  {docs_dir}")

        try:
            missing, extra = check_docs_for_union(union_type, docs_dir)

            if missing:
                exit_code = 1
                print(f"\n  Missing docs for {len(missing)} {name.lower()}:")
                for discriminator, model_cls in sorted(missing.items()):
                    expected = discriminator_to_filename(discriminator)
                    print(f"    - {expected}.md  (for {model_cls.__name__})")

            if extra:
                print(f"\n  Extra docs without corresponding type ({len(extra)}):")
                for filename in sorted(extra):
                    print(f"    - {filename}.md")

            if not missing and not extra:
                from agentpool.docs.utils import get_discriminator_values

                count = len(get_discriminator_values(union_type))
                print(f"  All {count} types documented")

        except Exception as e:  # noqa: BLE001
            exit_code = 1
            print(f"  Error: {e}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
