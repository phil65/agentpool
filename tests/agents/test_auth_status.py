"""Tests for converting backend account info to AuthStatus."""

from aclientp.schema import AuthAccount as ACPAuthAccount, AuthStatusResponse
from clawd_code_sdk.models.server_info import ClaudeCodeAccountInfo
from codexed.models import GetAccountResponse
from codexed.models.v2_protocol import ApiKeyAccount, ChatgptAccount

from agentpool.agents.acp_agent.acp_converters import acp_to_native_auth_status
from agentpool.agents.auth import AuthAccount, AuthStatus
from agentpool.agents.claude_code_agent.converters import to_auth_status as claude_auth
from agentpool.agents.codex_agent.codex_converters import to_auth_status as codex_auth


def test_claude_subscription_account() -> None:
    account = ClaudeCodeAccountInfo(
        email="me@example.com",
        subscription_type="Claude Max",
        token_source="claude.ai",
        organization="Acme",
        api_provider="firstParty",
    )
    expected = AuthAccount(
        label="Claude Max",
        description="claude.ai",
        active=True,
        email="me@example.com",
        organization="Acme",
    )
    assert claude_auth(account) == AuthStatus(state="authenticated", accounts=[expected])


def test_claude_states() -> None:
    assert claude_auth(None).state == "unknown"
    assert claude_auth(ClaudeCodeAccountInfo(api_provider="firstParty")).state == "unauthenticated"
    bedrock = claude_auth(ClaudeCodeAccountInfo(api_provider="bedrock"))
    assert bedrock.state == "not_required"
    assert bedrock.accounts[0].label == "bedrock"
    api_key = claude_auth(ClaudeCodeAccountInfo(api_key_source="ANTHROPIC_API_KEY"))
    assert api_key.accounts[0].label == "Anthropic API key"


def test_codex_accounts() -> None:
    chatgpt = ChatgptAccount(email="me@example.com", plan_type="pro", workspace_name="Acme")
    status = codex_auth(GetAccountResponse(account=chatgpt, requires_openai_auth=True))
    assert status == AuthStatus(
        state="authenticated",
        accounts=[
            AuthAccount(
                label="ChatGPT pro", active=True, email="me@example.com", organization="Acme"
            )
        ],
    )
    api_key = codex_auth(GetAccountResponse(account=ApiKeyAccount(), requires_openai_auth=True))
    assert api_key.accounts[0].label == "OpenAI API key"


def test_codex_without_account() -> None:
    required = GetAccountResponse(account=None, requires_openai_auth=True)
    assert codex_auth(required).state == "unauthenticated"
    optional = GetAccountResponse(account=None, requires_openai_auth=False)
    assert codex_auth(optional).state == "not_required"


def test_acp_auth_status_unknown_state() -> None:
    response = AuthStatusResponse(state="expired", accounts=[ACPAuthAccount(label="Team")])
    assert acp_to_native_auth_status(response) == AuthStatus(
        state="unknown", accounts=[AuthAccount(label="Team")]
    )
