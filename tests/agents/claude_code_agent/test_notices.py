"""Rate limit notices: shown once per state, not with every request."""

from clawd_code_sdk.models import RateLimitInfo

from agentpool.agents.claude_code_agent.notices import RateLimitNotices, rate_limit_notice


def _info(status: str = "allowed_warning", used: float | None = 0.86) -> RateLimitInfo:
    return RateLimitInfo.model_validate({
        "status": status,
        "rateLimitType": "seven_day",
        "utilization": used,
    })


def test_notice_text() -> None:
    notice = rate_limit_notice(_info())
    assert notice is not None
    assert (notice.title, notice.severity) == ("Rate limit close: seven day (86% used)", "warning")
    rejected = rate_limit_notice(_info("rejected", 1.0))
    assert rejected is not None
    assert rejected.severity == "error"
    assert rate_limit_notice(_info("allowed")) is None


def test_same_state_is_announced_once() -> None:
    """The CLI reports the limit with every request; T3 Code showed it four times a turn."""
    notices = RateLimitNotices()
    shown = [notices.notice(_info()) for _ in range(4)]
    assert [n is not None for n in shown] == [True, False, False, False]


def test_changes_are_announced_and_allowed_starts_over() -> None:
    notices = RateLimitNotices()
    assert notices.notice(_info(used=0.86)) is not None
    assert notices.notice(_info(used=0.87)) is not None  # Utilization moved
    assert notices.notice(_info("allowed", None)) is None
    assert notices.notice(_info(used=0.87)) is not None  # Close again after a reset
