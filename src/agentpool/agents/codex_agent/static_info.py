from agentpool.agents.modes import ModeInfo


POLICY_MODES = [
    ModeInfo(
        value="never",
        name="Auto-Execute",
        description="Execute tools without approval (default for programmatic use)",
        category_id="mode",
    ),
    ModeInfo(
        value="on-request",
        name="On Request",
        description="Ask for approval only when tool explicitly requests it",
        category_id="mode",
    ),
    ModeInfo(
        value="untrusted",
        name="Always Confirm",
        description="Request approval before executing any tool",
        category_id="mode",
    ),
]


SANDBOX_MODES = [
    ModeInfo(
        value="read-only",
        name="Read Only",
        description="Sandbox with read-only file access",
        category_id="sandbox",
    ),
    ModeInfo(
        value="workspace-write",
        name="Workspace Write",
        description="Can write files within workspace directory",
        category_id="sandbox",
    ),
    ModeInfo(
        value="danger-full-access",
        name="Full Access",
        description="Full filesystem access (dangerous)",
        category_id="sandbox",
    ),
    # ModeInfo(
    #     value="externalSandbox",
    #     name="External Sandbox",
    #     description="Use external sandbox environment",
    #     category_id="sandbox",
    # ),
]


EFFORT_MODES = [
    ModeInfo(
        value="low",
        name="Low Effort",
        description="Fast responses with lighter reasoning",
        category_id="thought_level",
    ),
    ModeInfo(
        value="medium",
        name="Medium Effort",
        description="Balanced reasoning depth for everyday tasks",
        category_id="thought_level",
    ),
    ModeInfo(
        value="high",
        name="High Effort",
        description="Deep reasoning for complex problems",
        category_id="thought_level",
    ),
    ModeInfo(
        value="xhigh",
        name="Extra High Effort",
        description="Maximum reasoning depth for complex problems",
        category_id="thought_level",
    ),
]


PERSONALITY_MODES = [
    ModeInfo(
        value="none",
        name="None",
        description="No personality preset",
        category_id="personality",
    ),
    ModeInfo(
        value="friendly",
        name="Friendly",
        description="Warm and approachable tone",
        category_id="personality",
    ),
    ModeInfo(
        value="pragmatic",
        name="Pragmatic",
        description="Direct and efficient communication",
        category_id="personality",
    ),
]
