"""OpenCode V2 HTTP API models and async client."""

from opencode_v2_sdk._version import OPENCODE_VERSION
from opencode_v2_sdk.client import OpenCodeV2Client, OpenCodeV2Error

__all__ = [
    "OPENCODE_VERSION",
    "OpenCodeV2Client",
    "OpenCodeV2Error",
]
