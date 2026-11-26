"""Email channel implementation using IMAP polling + SMTP replies."""

import asyncio
import contextlib
from dataclasses import dataclass, field
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
import html
import imaplib
import re
import smtplib
import ssl
from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger
from agentpool_bot.channels.base import BaseChannel


if TYPE_CHECKING:
    from datetime import date

    from agentpool_bot.bus import MessageBus, OutboundMessage
    from agentpool_bot.config import EmailConfig


@dataclass(frozen=True, slots=True)
class EmailMetadata:
    """Metadata extracted from a parsed email message."""

    message_id: str = ""
    subject: str = ""
    date: str = ""
    sender_email: str = ""
    uid: str = ""

    def to_dict(self) -> dict[str, str]:
        """Convert to plain dict for passing to channel base class."""
        return {
            "message_id": self.message_id,
            "subject": self.subject,
            "date": self.date,
            "sender_email": self.sender_email,
            "uid": self.uid,
        }


@dataclass(frozen=True, slots=True)
class ParsedEmail:
    """A parsed inbound email message."""

    sender: str
    content: str
    subject: str = ""
    message_id: str = ""
    metadata: EmailMetadata = field(default_factory=EmailMetadata)


logger = get_logger(__name__)


class EmailChannel(BaseChannel):
    """Email channel.

    Inbound:
    - Poll IMAP mailbox for unread messages.
    - Convert each message into an inbound event.

    Outbound:
    - Send responses via SMTP back to the sender address.
    """

    name = "email"
    _IMAP_MONTHS = (
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    )

    def __init__(self, config: EmailConfig, bus: MessageBus) -> None:
        super().__init__(config, bus)
        self.config: EmailConfig = config
        self._last_subject_by_chat: dict[str, str] = {}
        self._last_message_id_by_chat: dict[str, str] = {}
        self._processed_uids: set[str] = set()
        self._MAX_PROCESSED_UIDS = 100000

    async def start(self) -> None:
        """Start polling IMAP for inbound emails."""
        if not self.config.consent_granted:
            logger.warning(
                "Email channel disabled: consent_granted is false. "
                "Set channels.email.consentGranted=true after explicit user permission."
            )
            return

        self._running = True
        logger.info("Starting Email channel (IMAP polling mode)...")

        poll_seconds = max(5, int(self.config.poll_interval_seconds))
        while self._running:
            try:
                inbound_items = await asyncio.to_thread(self._fetch_new_messages)
                for item in inbound_items:
                    if item.subject:
                        self._last_subject_by_chat[item.sender] = item.subject
                    if item.message_id:
                        self._last_message_id_by_chat[item.sender] = item.message_id

                    await self._handle_message(
                        sender_id=item.sender,
                        chat_id=item.sender,
                        content=item.content,
                        metadata=item.metadata.to_dict(),
                    )
            except Exception:
                logger.exception("Email polling error")

            await asyncio.sleep(poll_seconds)

    async def stop(self) -> None:
        """Stop polling loop."""
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        """Send email via SMTP."""
        if not self.config.consent_granted:
            logger.warning("Skip email send: consent_granted is false")
            return

        force_send = bool((msg.metadata or {}).get("force_send"))
        if not self.config.auto_reply_enabled and not force_send:
            logger.info("Skip automatic email reply: auto_reply_enabled is false")
            return

        if not self.config.smtp_host:
            logger.warning("Email channel SMTP host not configured")
            return

        to_addr = msg.chat_id.strip()
        if not to_addr:
            logger.warning("Email channel missing recipient address")
            return

        base_subject = self._last_subject_by_chat.get(to_addr, "agentpool reply")
        subject = self._reply_subject(base_subject)
        if isinstance(msg.metadata.get("subject"), str) and (
            override := msg.metadata["subject"].strip()
        ):
            subject = override

        email_msg = EmailMessage()
        email_msg["From"] = (
            self.config.from_address or self.config.smtp_username or self.config.imap_username
        )
        email_msg["To"] = to_addr
        email_msg["Subject"] = subject
        email_msg.set_content(msg.content or "")

        in_reply_to = self._last_message_id_by_chat.get(to_addr)
        if in_reply_to:
            email_msg["In-Reply-To"] = in_reply_to
            email_msg["References"] = in_reply_to

        try:
            await asyncio.to_thread(self._smtp_send, email_msg)
        except Exception:
            logger.exception("Error sending email", to=to_addr)
            raise

    def _smtp_send(self, msg: EmailMessage) -> None:
        timeout = 30
        if self.config.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                self.config.smtp_host,
                self.config.smtp_port,
                timeout=timeout,
            ) as smtp:
                smtp.login(self.config.smtp_username, self.config.smtp_password)
                smtp.send_message(msg)
            return

        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=timeout) as smtp:
            if self.config.smtp_use_tls:
                smtp.starttls(context=ssl.create_default_context())
            smtp.login(self.config.smtp_username, self.config.smtp_password)
            smtp.send_message(msg)

    def _fetch_new_messages(self) -> list[ParsedEmail]:
        """Poll IMAP and return parsed unread messages."""
        return self._fetch_messages(
            search_criteria=("UNSEEN",),
            mark_seen=self.config.mark_seen,
            dedupe=True,
            limit=0,
        )

    def fetch_messages_between_dates(
        self,
        start_date: date,
        end_date: date,
        limit: int = 20,
    ) -> list[ParsedEmail]:
        """Fetch messages in [start_date, end_date) by IMAP date search.

        This is used for historical summarization tasks (e.g. "yesterday").
        """
        if end_date <= start_date:
            return []
        start = self._format_imap_date(start_date)
        end = self._format_imap_date(end_date)
        return self._fetch_messages(
            search_criteria=("SINCE", start, "BEFORE", end),
            mark_seen=False,
            dedupe=False,
            limit=max(1, int(limit)),
        )

    def _fetch_messages(
        self,
        search_criteria: tuple[str, ...],
        mark_seen: bool,
        dedupe: bool,
        limit: int,
    ) -> list[ParsedEmail]:
        """Fetch messages by arbitrary IMAP search criteria."""
        messages: list[ParsedEmail] = []
        mailbox = self.config.imap_mailbox or "INBOX"

        if not self.config.imap_use_ssl:
            client = imaplib.IMAP4(self.config.imap_host, self.config.imap_port)
        else:
            client = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port)

        try:
            client.login(self.config.imap_username, self.config.imap_password)
            status, _ = client.select(mailbox)
            if status != "OK":
                return messages

            status, data = client.search(None, *search_criteria)
            if status != "OK" or not data:
                return messages

            ids = data[0].split()
            if limit > 0 and len(ids) > limit:
                ids = ids[-limit:]
            for imap_id in ids:
                status, fetched = client.fetch(imap_id, "(BODY.PEEK[] UID)")
                if status != "OK" or not fetched:
                    continue

                raw_bytes = self._extract_message_bytes(fetched)
                if raw_bytes is None:
                    continue

                uid = self._extract_uid(fetched)
                if dedupe and uid and uid in self._processed_uids:
                    continue

                parsed = BytesParser(policy=policy.default).parsebytes(raw_bytes)
                sender = parseaddr(parsed.get("From", ""))[1].strip().lower()
                if not sender:
                    continue

                subject = self._decode_header_value(parsed.get("Subject", ""))
                date_value = parsed.get("Date", "")
                message_id = parsed.get("Message-ID", "").strip()
                body = self._extract_text_body(parsed)

                if not body:
                    body = "(empty email body)"

                body = body[: self.config.max_body_chars]
                content = (
                    f"Email received.\n"
                    f"From: {sender}\n"
                    f"Subject: {subject}\n"
                    f"Date: {date_value}\n\n"
                    f"{body}"
                )
                metadata = EmailMetadata(
                    message_id=message_id,
                    subject=subject,
                    date=date_value,
                    sender_email=sender,
                    uid=uid,
                )
                messages.append(
                    ParsedEmail(
                        sender=sender,
                        subject=subject,
                        message_id=message_id,
                        content=content,
                        metadata=metadata,
                    )
                )

                if dedupe and uid:
                    self._processed_uids.add(uid)
                    if len(self._processed_uids) > self._MAX_PROCESSED_UIDS:
                        self._processed_uids.clear()

                if mark_seen:
                    client.store(imap_id, "+FLAGS", "\\Seen")
        finally:
            with contextlib.suppress(Exception):
                client.logout()

        return messages

    @classmethod
    def _format_imap_date(cls, value: date) -> str:
        """Format date for IMAP search (always English month abbreviations)."""
        month = cls._IMAP_MONTHS[value.month - 1]
        return f"{value.day:02d}-{month}-{value.year}"

    @staticmethod
    def _extract_message_bytes(fetched: list[Any]) -> bytes | None:
        for item in fetched:
            if (
                isinstance(item, tuple)
                and len(item) >= 2  # noqa: PLR2004
                and isinstance(item[1], bytes | bytearray)
            ):
                return bytes(item[1])
        return None

    @staticmethod
    def _extract_uid(fetched: list[Any]) -> str:
        for item in fetched:
            if isinstance(item, tuple) and item and isinstance(item[0], bytes | bytearray):
                head = bytes(item[0]).decode("utf-8", errors="ignore")
                if m := re.search(r"UID\s+(\d+)", head):
                    return m.group(1)
        return ""

    @staticmethod
    def _decode_header_value(value: str) -> str:
        if not value:
            return ""
        try:
            return str(make_header(decode_header(value)))
        except Exception:  # noqa: BLE001
            return value

    @classmethod
    def _extract_text_body(cls, msg: Any) -> str:
        """Best-effort extraction of readable body text."""
        if msg.is_multipart():
            plain_parts: list[str] = []
            html_parts: list[str] = []
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
                    continue
                try:
                    payload = part.get_content()
                except Exception:  # noqa: BLE001
                    payload_bytes = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    payload = payload_bytes.decode(charset, errors="replace")
                if not isinstance(payload, str):
                    continue
                match part.get_content_type():
                    case "text/plain":
                        plain_parts.append(payload)
                    case "text/html":
                        html_parts.append(payload)
            if plain_parts:
                return "\n\n".join(plain_parts).strip()
            if html_parts:
                return cls._html_to_text("\n\n".join(html_parts)).strip()
            return ""

        try:
            payload = msg.get_content()
        except Exception:  # noqa: BLE001
            payload_bytes = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            payload = payload_bytes.decode(charset, errors="replace")
        if not isinstance(payload, str):
            return ""
        if msg.get_content_type() == "text/html":
            return cls._html_to_text(payload).strip()
        return payload.strip()

    @staticmethod
    def _html_to_text(raw_html: str) -> str:
        text = re.sub(r"<\s*br\s*/?>", "\n", raw_html, flags=re.IGNORECASE)
        text = re.sub(r"<\s*/\s*p\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        return html.unescape(text)

    def _reply_subject(self, base_subject: str) -> str:
        subject = (base_subject or "").strip() or "agentpool reply"
        prefix = self.config.subject_prefix or "Re: "
        if subject.lower().startswith("re:"):
            return subject
        return f"{prefix}{subject}"
