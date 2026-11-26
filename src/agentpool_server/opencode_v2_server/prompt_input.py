"""Prompt admission: V2 prompt bodies -> inbox payloads -> agent prompt content."""

import base64
import binascii
import mimetypes
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

from pydantic_ai import BinaryContent

from agentpool.log import get_logger
from opencode_v2_sdk.models import (
    PromptFileAttachment,
    PromptFileSourceInline,
    PromptFileSourceUri,
    PromptSkillAttachment,
    SessionInboxUserPayload,
)


if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem
    from pydantic_ai import UserContent

    from agentpool.skills.manager import SkillsManager
    from opencode_v2_sdk.models import (
        PromptInputFileAttachment,
        PromptInputSkillAttachment,
        SessionPromptBody,
    )


logger = get_logger(__name__)

TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIME_TYPES = frozenset({
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
    "application/toml",
    "application/javascript",
    "application/x-sh",
})


class AttachmentError(ValueError):
    """An attachment could not be read."""


def _is_text_mime(mime: str) -> bool:
    return mime.startswith(TEXT_MIME_PREFIXES) or mime in TEXT_MIME_TYPES


def _guess_mime(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def _parse_data_uri(uri: str) -> tuple[str, bytes]:
    header, _, payload = uri.partition(",")
    meta = header.removeprefix("data:")
    mime = meta.split(";")[0] or "application/octet-stream"
    try:
        data = base64.b64decode(payload) if ";base64" in meta else unquote(payload).encode()
    except binascii.Error as e:
        msg = "Invalid base64 data URI"
        raise AttachmentError(msg) from e
    return mime, data


async def admit_file(
    attachment: PromptInputFileAttachment,
    fs: AsyncFileSystem,
    base_dir: str,
) -> PromptFileAttachment:
    """Resolve an input attachment into the stored (inline data) form.

    Raises:
        AttachmentError: If the file cannot be read
    """
    uri = attachment.uri
    parsed = urlparse(uri)
    name = attachment.name
    if parsed.scheme == "data":
        mime, data = _parse_data_uri(uri)
        source: PromptFileSourceInline | PromptFileSourceUri = PromptFileSourceInline()
    elif parsed.scheme in {"", "file"}:
        path = unquote(parsed.path) if parsed.scheme == "file" else uri
        if not path.startswith("/"):
            path = f"{base_dir.rstrip('/')}/{path}"
        try:
            data = await fs._cat_file(path)
        except (OSError, FileNotFoundError) as e:
            msg = f"Cannot read attachment {path}: {e}"
            raise AttachmentError(msg) from e
        mime = _guess_mime(path)
        name = name or path.rsplit("/", 1)[-1]
        source = PromptFileSourceUri(uri=uri)
    else:
        # Remote URIs are passed through by reference.
        return PromptFileAttachment(
            data="",
            mime=_guess_mime(parsed.path),
            source=PromptFileSourceUri(uri=uri),
            name=name or parsed.path.rsplit("/", 1)[-1] or uri,
            description=attachment.description,
            mention=attachment.mention,
        )
    return PromptFileAttachment(
        data=base64.b64encode(data).decode(),
        mime=mime,
        source=source,
        name=name,
        description=attachment.description,
        mention=attachment.mention,
    )


def admit_skill(
    attachment: PromptInputSkillAttachment, skills: SkillsManager
) -> PromptSkillAttachment:
    """Resolve a skill attachment (name lookup) into the stored form."""
    name = skills.get_skill(attachment.id).name if attachment.id in skills.registry else None
    return PromptSkillAttachment(
        id=attachment.id, name=name or attachment.id, mention=attachment.mention
    )


async def admit_prompt(
    body: SessionPromptBody,
    *,
    fs: AsyncFileSystem,
    base_dir: str,
    skills: SkillsManager,
) -> SessionInboxUserPayload:
    """Turn a ``session.prompt`` request body into the durable inbox payload."""
    files = [await admit_file(f, fs, base_dir) for f in body.files or []]
    skill_items = [admit_skill(s, skills) for s in body.skills or []]
    return SessionInboxUserPayload(
        text=body.text,
        files=files or None,
        agents=body.agents or None,
        skills=skill_items or None,
        metadata=body.metadata,
    )


def _file_content(file: PromptFileAttachment) -> UserContent:
    label = file.name or "attachment"
    match file.source:
        case PromptFileSourceUri(uri=uri) if not file.data:
            return f"Attached file (by reference): {label} <{uri}>"
        case _:
            data = base64.b64decode(file.data)
    if _is_text_mime(file.mime):
        text = data.decode("utf-8", errors="replace")
        return f"Attached file {label}:\n```\n{text}\n```"
    return BinaryContent(data=data, media_type=file.mime, identifier=label)


async def payload_to_prompts(
    payload: SessionInboxUserPayload,
    *,
    skills: SkillsManager,
) -> list[UserContent]:
    """Build the prompt content handed to ``run_stream`` from an inbox payload."""
    prompts: list[Any] = [payload.text] if payload.text else []
    for skill in payload.skills or []:
        if skill.id not in skills.registry:
            logger.warning("Unknown skill attachment", skill=skill.id)
            continue
        instructions = await skills.get_skill_instructions(skill.id)
        prompts.append(f"<skill name={skill.name!r}>\n{instructions}\n</skill>")
    prompts.extend(_file_content(file) for file in payload.files or [])
    prompts.extend(
        "Use the above message and context to generate a prompt and delegate it via the "
        f"task tool with agent_or_team={agent.name!r}."
        for agent in payload.agents or []
    )
    return prompts
