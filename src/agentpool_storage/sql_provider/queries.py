"""Some pre-defined Queries."""

DELETE_AGENT_MESSAGES = """\
DELETE FROM message
WHERE session_id IN (
    SELECT id FROM conversation WHERE agent_name = :agent
)
"""
DELETE_AGENT_CONVERSATIONS = "DELETE FROM conversation WHERE agent_name = :agent"

DELETE_ALL_MESSAGES = "DELETE FROM message"
DELETE_ALL_CONVERSATIONS = "DELETE FROM conversation"
