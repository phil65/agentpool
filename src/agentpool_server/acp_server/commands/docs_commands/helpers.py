"""Helper functions and constants for documentation commands."""

from __future__ import annotations


# Scripts run where the session runs its tools (client terminal or this host)
SCHEMA_EXTRACTION_SCRIPT = """
import sys
import json
import importlib

def import_callable(path):
    parts = path.split(".")
    for i in range(len(parts), 0, -1):
        try:
            module_path = ".".join(parts[:i])
            module = importlib.import_module(module_path)
            obj = module
            for part in parts[i:]:
                obj = getattr(obj, part)
            return obj
        except (ImportError, AttributeError):
            continue
    raise ImportError(f"Could not import %s" % path)

try:
    model_class = import_callable("{input_path}")
    schema = model_class.model_json_schema()
    print(json.dumps(schema, indent=2))
except Exception as e:
    print(f"Error: %s" % e, file=sys.stderr)
    sys.exit(1)
"""
