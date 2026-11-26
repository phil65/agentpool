"""Configuration models for agentpool_bot channels."""

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


if TYPE_CHECKING:
    from agentpool_bot.bus import MessageBus
    from agentpool_bot.channels.discord import DiscordChannel
    from agentpool_bot.channels.email import EmailChannel
    from agentpool_bot.channels.slack import SlackChannel
    from agentpool_bot.channels.telegram import TelegramChannel
    from agentpool_bot.channels.whatsapp import WhatsAppChannel


class BaseChannelConfig(BaseModel):
    """Base model that accepts both camelCase and snake_case keys."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    enabled: bool = False


class WhatsAppConfig(BaseChannelConfig):
    """WhatsApp channel configuration."""

    type: Literal["whatsapp"] = Field("whatsapp", init=False)
    """WhatsApp channel."""
    bridge_url: str = "ws://localhost:3001"
    bridge_token: str = ""
    allow_from: list[str] = Field(default_factory=list)

    def get_provider(self, bus: MessageBus) -> WhatsAppChannel:
        from agentpool_bot.channels.whatsapp import WhatsAppChannel

        return WhatsAppChannel(self, bus)


class TelegramConfig(BaseChannelConfig):
    """Telegram channel configuration."""

    type: Literal["telegram"] = Field("telegram", init=False)
    """Telegram channel."""
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    proxy: str | None = None

    def get_provider(self, bus: MessageBus) -> TelegramChannel:
        from agentpool_bot.channels.telegram import TelegramChannel

        return TelegramChannel(self, bus)


class DiscordConfig(BaseChannelConfig):
    """Discord channel configuration."""

    type: Literal["discord"] = Field("discord", init=False)
    """Discord channel."""
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377  # GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT

    def get_provider(self, bus: MessageBus) -> DiscordChannel:
        from agentpool_bot.channels.discord import DiscordChannel

        return DiscordChannel(self, bus)


class EmailConfig(BaseChannelConfig):
    """Email channel configuration (IMAP inbound + SMTP outbound)."""

    type: Literal["email"] = Field("email", init=False)
    """Email channel."""
    consent_granted: bool = False

    # IMAP (receive)
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_use_ssl: bool = True

    # SMTP (send)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    from_address: str = ""

    @model_validator(mode="after")
    def _check_credentials_when_enabled(self) -> EmailConfig:
        if not self.enabled:
            return self
        fields = {
            "imap_host": self.imap_host,
            "imap_username": self.imap_username,
            "imap_password": self.imap_password,
            "smtp_host": self.smtp_host,
            "smtp_username": self.smtp_username,
            "smtp_password": self.smtp_password,
        }
        missing = [name for name, value in fields.items() if not value]
        if missing:
            msg = f"Email channel enabled but missing required fields: {', '.join(missing)}"
            raise ValueError(msg)
        return self

    # Behavior
    auto_reply_enabled: bool = True
    poll_interval_seconds: int = 30
    mark_seen: bool = True
    max_body_chars: int = 12000
    subject_prefix: str = "Re: "
    allow_from: list[str] = Field(default_factory=list)

    def get_provider(self, bus: MessageBus) -> EmailChannel:
        from agentpool_bot.channels.email import EmailChannel

        return EmailChannel(self, bus)


class SlackDMConfig(BaseChannelConfig):
    """Slack DM policy configuration."""

    enabled: bool = True
    policy: Literal["open", "allowlist"] = "open"
    allow_from: list[str] = Field(default_factory=list)


class SlackConfig(BaseChannelConfig):
    """Slack channel configuration."""

    type: Literal["slack"] = Field("slack", init=False)
    """Slack channel."""
    mode: str = "socket"
    webhook_path: str = "/slack/events"
    bot_token: str = ""
    app_token: str = ""
    user_token_read_only: bool = True
    reply_in_thread: bool = True
    react_emoji: str = "eyes"
    group_policy: Literal["open", "mention", "allowlist"] = "mention"
    group_allow_from: list[str] = Field(default_factory=list)
    dm: SlackDMConfig = Field(default_factory=SlackDMConfig)

    def get_provider(self, bus: MessageBus) -> SlackChannel:
        from agentpool_bot.channels.slack import SlackChannel

        return SlackChannel(self, bus)


ChannelConfig = WhatsAppConfig | TelegramConfig | DiscordConfig | EmailConfig | SlackConfig


class ChannelsConfig(BaseChannelConfig):
    """Configuration for all chat channels."""

    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
