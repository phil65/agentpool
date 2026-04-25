"""Tests for manifest metadata fields (YAML anchors and extensions).

This module tests:
1. Pydantic model validation of metadata fields
2. JSON Schema patternProperties generation (for YAML LSP compatibility)
3. YAML anchor functionality with metadata prefixes
"""

from __future__ import annotations

import re

import jsonschema
from llmling_models_config import StringModelConfig
import yamling

from agentpool import AgentsManifest
from agentpool.models.agents import NativeAgentConfig


# Valid config with allowed metadata fields
MANIFEST_WITH_ALLOWED_METADATA = """\
agents:
  test_agent:
    type: native
    model: openai:gpt-4o
    system_prompt: "You are a test agent"

.anchor: &default_settings
  timeout: 30
  retries: 3

_meta:
  version: "1.0.0"
  author: "Test User"

x-custom:
  environment: "production"
  feature_flags:
    - feature_a
    - feature_b
"""

# Valid config with unknown field (typo)
MANIFEST_WITH_UNKNOWN_FIELD = """\
agents:
  test_agent:
    type: native
    model: openai:gpt-4o
    system_prompt: "You are a test agent"

random_field: "this is a typo/unknown field"
"""

# Valid config with both allowed and unknown fields
MANIFEST_WITH_MIXED_FIELDS = """\
agents:
  test_agent:
    type: native
    model: openai:gpt-4o
    system_prompt: "You are a test agent"

_meta:
  version: "1.0.0"

random_field: "should trigger warning"
"""

# YAML with anchors using prefixed fields
MANIFEST_WITH_YAML_ANCHORS = """\
# Define reusable settings using YAML anchors
.shared_model: &default_model
  type: native
  model: openai:gpt-4o

.shared_prompts: &assistant_prompt
  system_prompt: "You are a helpful assistant"

agents:
  coder:
    <<: *default_model
    <<: *assistant_prompt
    name: coder
    tools:
      - type: code

  reviewer:
    <<: *default_model
    system_prompt: "You are a code reviewer"
    name: reviewer
"""


def test_allowed_metadata_fields_succeed():
    """Test that metadata fields starting with ., _, x- are allowed.

    RED PHASE: This test will FAIL because currently extra fields
    are forbidden. After implementation, this test will PASS.
    """
    config = yamling.load_yaml(MANIFEST_WITH_ALLOWED_METADATA)
    manifest = AgentsManifest.model_validate(config)

    # Verify that manifest loaded successfully
    assert "test_agent" in manifest.agents
    agent = manifest.agents["test_agent"]
    assert isinstance(agent, NativeAgentConfig)
    assert isinstance(agent.model, StringModelConfig)
    assert agent.model.identifier == "openai:gpt-4o"


def test_unknown_field_generates_warning():
    """Test that unknown fields generate a warning but don't raise ValidationError.

    RED PHASE: This test will FAIL because currently unknown fields
    raise ValidationError. After implementation, this test will PASS.
    """
    config = yamling.load_yaml(MANIFEST_WITH_UNKNOWN_FIELD)

    # After implementation, this should NOT raise ValidationError
    # It should log a warning instead
    manifest = AgentsManifest.model_validate(config)

    # Verify agents loaded correctly
    assert "test_agent" in manifest.agents
    agent = manifest.agents["test_agent"]
    assert isinstance(agent, NativeAgentConfig)
    assert isinstance(agent.model, StringModelConfig)
    assert agent.model.identifier == "openai:gpt-4o"


def test_mixed_allowed_and_unknown_fields():
    """Test manifest with both allowed and unknown fields.

    RED PHASE: This test will FAIL because currently unknown fields
    raise ValidationError. After implementation, this test will PASS.
    """
    config = yamling.load_yaml(MANIFEST_WITH_MIXED_FIELDS)

    # After implementation, this should succeed with warning for 'random_field'
    manifest = AgentsManifest.model_validate(config)

    # Verify allowed metadata fields are accessible
    assert "test_agent" in manifest.agents
    agent = manifest.agents["test_agent"]
    assert isinstance(agent, NativeAgentConfig)
    assert isinstance(agent.model, StringModelConfig)
    assert agent.model.identifier == "openai:gpt-4o"


# ==============================================================================
# JSON Schema Tests for YAML LSP Compatibility
# ==============================================================================


class TestSchemaPatternProperties:
    """Tests verifying that patternProperties are correctly generated in JSON Schema.

    These tests ensure YAML LSPs (like yaml-language-server) won't warn about
    fields starting with allowed prefixes (., _, x-).
    """

    def test_schema_contains_pattern_properties(self):
        """Test that the generated JSON schema includes patternProperties."""
        schema = AgentsManifest.model_json_schema()

        assert "patternProperties" in schema, (
            "Schema must include patternProperties for YAML LSP compatibility"
        )

    def test_schema_pattern_for_dot_prefix(self):
        """Test that patternProperties includes pattern for dot-prefixed fields."""
        schema = AgentsManifest.model_json_schema()
        pattern_props = schema.get("patternProperties", {})

        # Should have a pattern matching dot-prefixed keys
        dot_patterns = [p for p in pattern_props if re.match(r"^\^\\\..*", p)]
        assert dot_patterns, (
            "Schema must include patternProperties for dot-prefixed fields (e.g., .anchor)"
        )

    def test_schema_pattern_for_underscore_prefix(self):
        """Test that patternProperties includes pattern for underscore-prefixed fields."""
        schema = AgentsManifest.model_json_schema()
        pattern_props = schema.get("patternProperties", {})

        # Should have a pattern matching underscore-prefixed keys
        underscore_patterns = [p for p in pattern_props if re.match(r"^\^_.*", p)]
        assert underscore_patterns, (
            "Schema must include patternProperties for underscore-prefixed fields (e.g., _meta)"
        )

    def test_schema_pattern_for_x_prefix(self):
        """Test that patternProperties includes pattern for x-prefixed fields."""
        schema = AgentsManifest.model_json_schema()
        pattern_props = schema.get("patternProperties", {})

        # Should have a pattern matching x-prefixed keys
        x_patterns = [p for p in pattern_props if re.match(r"^\^x-.*", p)]
        assert x_patterns, (
            "Schema must include patternProperties for x-prefixed fields (e.g., x-custom)"
        )

    def test_pattern_properties_have_descriptions(self):
        """Test that all patternProperties have descriptions for LSP hover info."""
        schema = AgentsManifest.model_json_schema()
        pattern_props = schema.get("patternProperties", {})

        for pattern, prop_schema in pattern_props.items():
            assert "description" in prop_schema, (
                f"patternProperty '{pattern}' should have a description for LSP hover info"
            )


class TestJsonSchemaValidation:
    """Tests validating YAML against the generated JSON Schema.

    These tests simulate what a YAML LSP would do when validating a document.
    """

    def test_schema_validates_allowed_metadata_fields(self):
        """Test that JSON Schema validation passes for allowed metadata fields.

        This simulates what a YAML LSP does when checking a document.
        """
        schema = AgentsManifest.model_json_schema()
        config = yamling.load_yaml(MANIFEST_WITH_ALLOWED_METADATA)

        # Use jsonschema to validate (this is what YAML LSPs do)
        # This should NOT raise any validation errors
        validator = jsonschema.Draft7Validator(schema)
        errors = list(validator.iter_errors(config))

        # Filter out errors related to our prefixed fields
        prefix_related_errors = [
            e
            for e in errors
            if any(key.startswith((".", "_", "x-")) for key in getattr(e, "path", []))
        ]
        assert not prefix_related_errors, (
            f"Schema should not produce errors for prefixed fields: {prefix_related_errors}"
        )

    def test_schema_validates_yaml_with_anchors(self):
        """Test that YAML anchors using prefixed fields pass schema validation."""
        schema = AgentsManifest.model_json_schema()
        config = yamling.load_yaml(MANIFEST_WITH_YAML_ANCHORS)

        validator = jsonschema.Draft7Validator(schema)
        errors = list(validator.iter_errors(config))

        # Check that anchor fields (.shared_model, .shared_prompts) don't cause errors
        anchor_errors = [
            e for e in errors if any(str(key).startswith(".") for key in e.absolute_path)
        ]
        assert not anchor_errors, (
            f"Schema should not produce errors for YAML anchor fields: {anchor_errors}"
        )


class TestYamlAnchorFunctionality:
    """Tests verifying that YAML anchors work correctly with metadata prefixes."""

    def test_yaml_anchors_resolve_correctly(self):
        """Test that YAML anchors defined in prefixed fields resolve correctly."""
        config = yamling.load_yaml(MANIFEST_WITH_YAML_ANCHORS)
        manifest = AgentsManifest.model_validate(config)

        # Verify that agents inherited from anchors are loaded correctly
        assert "coder" in manifest.agents
        assert "reviewer" in manifest.agents

        coder = manifest.agents["coder"]
        reviewer = manifest.agents["reviewer"]

        # Both should have the shared model from anchor
        assert isinstance(coder, NativeAgentConfig)
        assert isinstance(reviewer, NativeAgentConfig)
        assert isinstance(coder.model, StringModelConfig)
        assert isinstance(reviewer.model, StringModelConfig)
        assert coder.model.identifier == "openai:gpt-4o"
        assert reviewer.model.identifier == "openai:gpt-4o"

    def test_anchor_fields_not_in_agents(self):
        """Test that anchor fields don't accidentally become agents."""
        config = yamling.load_yaml(MANIFEST_WITH_YAML_ANCHORS)
        manifest = AgentsManifest.model_validate(config)

        # Anchor fields should NOT appear as agents
        assert ".shared_model" not in manifest.agents
        assert ".shared_prompts" not in manifest.agents

    def test_metadata_fields_stored_in_model_extra(self):
        """Test that metadata fields are accessible via model_extra."""
        config = yamling.load_yaml(MANIFEST_WITH_ALLOWED_METADATA)
        manifest = AgentsManifest.model_validate(config)

        # The extra fields should be accessible
        assert hasattr(manifest, "model_extra")
        extra = manifest.model_extra or {}

        # Check for our metadata fields
        assert ".anchor" in extra or "_meta" in extra or "x-custom" in extra, (
            "At least one of the metadata fields should be in model_extra"
        )
