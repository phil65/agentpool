"""OpenCode V2 HTTP API models (generated from opencode v2.4.1, do not edit)."""

from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, RootModel


class AgentNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["AgentNotFoundError"], Field(alias="_tag")] = "AgentNotFoundError"
    agent_id: Annotated[str, Field(alias="agentID")]
    message: str


class ArtifactCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    title: str
    files: dict[str, str]
    entry: str | None = None
    provider: str | None = None
    session_id: Annotated[str | None, Field(alias="sessionID", pattern="^ses")] = None


class ArtifactNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["ArtifactNotFoundError"], Field(alias="_tag")] = (
        "ArtifactNotFoundError"
    )
    artifact_id: Annotated[str, Field(alias="artifactID")]
    message: str


class ArtifactProvider(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    name: str
    capabilities: list[Literal["static", "server-code", "sqlite", "durable-objects"]]
    default: bool


class ArtifactTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float
    updated: float


class ArtifactUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    title: str | None = None
    files: dict[str, str]
    entry: str | None = None
    session_id: Annotated[str | None, Field(alias="sessionID", pattern="^ses")] = None


class AutomationActionCode(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["code"] = "code"
    code: str
    args: Any | None = None


class AutomationActionShell(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["shell"] = "shell"
    command: Annotated[str, Field(min_length=1)]


class AutomationCodeActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["code"] = "code"
    code: Annotated[str, Field(min_length=1)]
    '\n    Code Mode JavaScript run on each trigger with no model (32 KB output cap). Call tools statically as tools.<ns>.<tool>(...) or tools["ns-name"].tool(...); each is authorized once at creation to run unattended. Receives input = { state, now, runs, args } (also globals): state starts null, now is epoch ms, runs counts earlier runs. Event triggers also receive input.event = { id, timestamp, data }; data follows the event type\'s payloadSchema. Return nothing, or { state?, done?, send? }. JSON state is kept for the next run; done: true stops. lastOutput shows the delivery target and any logs. Return at most one send per run. { send: { prompt } } steers the creating session; { send: { prompt, to: { subagent } } } starts a permitted child that does not report back. No session IDs. Omit send to stay quiet. A rejected send commits neither state nor done. Example code string: if (runs < 2) return; return { done: true, send: { prompt: "Three checks elapsed. Review the build." } }\n    '
    args: Any | None = None


class AutomationInfoTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float
    updated: float


class AutomationNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["AutomationNotFoundError"], Field(alias="_tag")] = (
        "AutomationNotFoundError"
    )
    automation_id: Annotated[str, Field(alias="automationID")]
    message: str


class AutomationShellActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["shell"] = "shell"
    command: Annotated[str, Field(min_length=1)]
    "\n    The fixed shell command to run on each trigger without a model.\n    "


class AutomationTargetVariant0(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session: Annotated[str, Field(pattern="^ses")]
    subagent: Any | None = None


class AutomationTargetVariant1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    subagent: str
    session: Any | None = None


class AutomationTriggerAt(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["at"] = "at"
    at: str
    '\n    An instant with an offset, a wall-clock time in tz such as "2026-10-27T09:00", or "in 10 minutes".\n    '
    tz: str | None = None


class AutomationTriggerCron(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["cron"] = "cron"
    cron: str
    tz: str | None = None


class AutomationTriggerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["event"] = "event"
    server: Annotated[str, Field(min_length=1)]
    event: Annotated[str, Field(min_length=1)]
    args: dict[str, Any] | None = None


class AutomationTriggerEvery(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["every"] = "every"
    every: str
    '\n    A duration such as "90 seconds", "5 minutes" or "2 hours".\n    '
    tz: str | None = None


class BlobInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^blob_[0-9a-f]{12}[0-9A-Za-z]{14}$")]
    "\n    A stored blob, such as a generated video, which outlives the job that made it\n    "
    mime: str
    bytes: float


class BlobNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["BlobNotFoundError"], Field(alias="_tag")] = "BlobNotFoundError"
    blob_id: Annotated[str, Field(alias="blobID")]
    message: str


class CommandExecutionErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["CommandExecutionError"], Field(alias="_tag")] = (
        "CommandExecutionError"
    )
    command: str
    message: str


class CommandInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str
    description: str | None = None


class CommandNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["CommandNotFoundError"], Field(alias="_tag")] = (
        "CommandNotFoundError"
    )
    command: str
    message: str


class ConfigAgentEncodedModelVariant1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    provider_id: Annotated[str, Field(alias="providerID", pattern="^[^/#]+$")]
    model: Annotated[str, Field(pattern="^[^#]+$")]
    variant: Annotated[str | None, Field(pattern="^[^#]+$")] = None


class ConfigAgentEncodedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    headers: dict[str, str] | None = None
    body: dict[str, Any] | None = None


class ConfigCommandEncodedModelVariant1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    provider_id: Annotated[str, Field(alias="providerID", pattern="^[^/#]+$")]
    model: Annotated[str, Field(pattern="^[^#]+$")]
    variant: Annotated[str | None, Field(pattern="^[^#]+$")] = None


class ConfigDirectoryEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["directory"] = "directory"
    path: str


class ConfigFormatterEntryEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    disabled: bool | None = None
    command: list[str] | None = None
    environment: dict[str, str] | None = None
    extensions: list[str] | None = None


class ConfigInfoEncodedCompactionKeep(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    tokens: Annotated[int | None, Field(ge=0, le=9007199254740991)] = None


class ConfigInfoEncodedEnterprise(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    url: str | None = None


class ConfigInfoEncodedExperimentalImageGenerateVariant1ModelVariant1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    provider_id: Annotated[str, Field(alias="providerID", pattern="^[^/#]+$")]
    model: Annotated[str, Field(pattern="^[^#]+$")]
    variant: Annotated[str | None, Field(pattern="^[^#]+$")] = None


class ConfigInfoEncodedExperimentalPoliciesItem(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    action: Literal[
        "provider.use",
        "tool.use",
        "integration.connect",
        "integration.use",
        "mcp.use",
        "skill.use",
        "plugin.use",
        "websearch.use",
    ]
    resource: str
    effect: Literal["allow", "deny"]


class ConfigInfoEncodedExperimentalVideoGenerateVariant1ModelVariant1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    provider_id: Annotated[str, Field(alias="providerID", pattern="^[^/#]+$")]
    model: Annotated[str, Field(pattern="^[^#]+$")]
    variant: Annotated[str | None, Field(pattern="^[^#]+$")] = None


class ConfigInfoEncodedLspVariant1ValueVariant0(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    disabled: Literal[True] = True


class ConfigInfoEncodedMcpTimeout(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    startup: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None
    catalog: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None
    execution: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None


class ConfigInfoEncodedMediaImage(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    auto_resize: bool | None = None
    max_width: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None
    max_height: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None
    max_base64_bytes: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None


class ConfigInfoEncodedModelVariant1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    provider_id: Annotated[str, Field(alias="providerID", pattern="^[^/#]+$")]
    model: Annotated[str, Field(pattern="^[^#]+$")]
    variant: Annotated[str | None, Field(pattern="^[^#]+$")] = None


class ConfigInfoEncodedPoliciesItem(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    action: Literal[
        "provider.use",
        "tool.use",
        "integration.connect",
        "integration.use",
        "mcp.use",
        "skill.use",
        "plugin.use",
        "websearch.use",
    ]
    resource: str
    effect: Literal["allow", "deny"]


class ConfigInfoEncodedToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    max_lines: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None
    max_bytes: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None


class ConfigInfoEncodedWatcher(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    ignore: list[str] | None = None


class ConfigLSPServerEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    command: list[str]
    extensions: list[str] | None = None
    disabled: bool | None = None
    env: dict[str, str] | None = None
    initialization: dict[str, Any] | None = None


class ConfigModelCostEncodedCache(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    read: float | None = None
    write: float | None = None


class ConfigModelCostEncodedTier(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["context"] = "context"
    size: Annotated[int, Field(ge=-9007199254740991, le=9007199254740991)]


class ConfigModelEncodedLimit(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    context: Annotated[int | None, Field(ge=-9007199254740991, le=9007199254740991)] = None
    input: Annotated[int | None, Field(ge=-9007199254740991, le=9007199254740991)] = None
    output: Annotated[int | None, Field(ge=-9007199254740991, le=9007199254740991)] = None


class ConfigModelReasoningVariant0(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    supported: Literal[False] = False


class ConfigModelReasoningVariant1Budget(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    min: Annotated[int | None, Field(ge=-9007199254740991, le=9007199254740991)] = None
    max: Annotated[int | None, Field(ge=-9007199254740991, le=9007199254740991)] = None


class ConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    shell: str | None = None


class ConfigPluginEntryEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    package: str
    options: dict[str, Any] | None = None


class ConfigReferenceGitEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    repository: str
    branch: str | None = None
    description: str | None = None
    hidden: bool | None = None


class ConfigReferenceLocalEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    path: str
    description: str | None = None
    hidden: bool | None = None


class ConfigShellOption(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    path: str
    name: str
    acceptable: bool


class ConfigWarmingEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    prompt: str | None = None
    interval: Literal["auto"] | str | None = None
    duration: str | None = None


class ConfigWebSearchInfoEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    provider: Literal["random"] | str


class ConfigWorktree(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    directory: str


class ConflictErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["ConflictError"], Field(alias="_tag")] = "ConflictError"
    message: str
    resource: str | None = None


class ConnectionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["needs_auth"]
    message: str
    url: str | None = None


class CredentialExternal(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["external"] = "external"
    method_id: Annotated[str, Field(alias="methodID")]
    metadata: dict[str, Any] | None = None


class CredentialKey(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["key"] = "key"
    key: str
    metadata: dict[str, Any] | None = None


class CredentialOAuth(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["oauth"] = "oauth"
    method_id: Annotated[str, Field(alias="methodID")]
    refresh: str
    access: str
    expires: Annotated[int, Field(ge=0, le=9007199254740991)]
    metadata: dict[str, Any] | None = None


class CredentialSwitchedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    integration_id: Annotated[str, Field(alias="integrationID")]
    credential_id: Annotated[str | None, Field(alias="credentialID")] = None


class CredentialUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    label: str


class CredentialUpdatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    integration_id: Annotated[str, Field(alias="integrationID")]
    credential_id: Annotated[str, Field(alias="credentialID")]


class DebugHeapDumpResponseMemory(BaseModel):
    """
    The server process's memory in bytes, read just before the snapshot (Node's process.memoryUsage()). rss covers native memory the snapshot cannot show.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    rss: float | Literal["Infinity", "-Infinity", "NaN"]
    heap_total: Annotated[float | Literal["Infinity", "-Infinity", "NaN"], Field(alias="heapTotal")]
    heap_used: Annotated[float | Literal["Infinity", "-Infinity", "NaN"], Field(alias="heapUsed")]
    external: float | Literal["Infinity", "-Infinity", "NaN"]
    array_buffers: Annotated[
        float | Literal["Infinity", "-Infinity", "NaN"], Field(alias="arrayBuffers")
    ]


class EventLogSynced(BaseModel):
    """
    Marker emitted once when a log read reaches its captured watermark. The reader holds every event committed at or below seq.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["log.synced"] = "log.synced"
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int | None, Field(ge=0, le=9007199254740991)] = None


class ExperimentalFsUnwatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    path: str
    "\n    A file path relative to the requested location.\n    "


class ExperimentalFsWatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    path: str
    "\n    A file path relative to the requested location.\n    "


class ExperimentalIntegrationWellknownAddBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    url: str


class ExperimentalMcpEventData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    server: str
    name: str
    event_id: Annotated[str, Field(alias="eventId")]
    timestamp: str
    data: Any
    cursor: str | None = None
    truncated: bool | None = None


class ExperimentalSessionInstructionsEntryPutBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    value: Any


class ExperimentalSessionSkillBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    resume: bool | None = None


class FileDiffInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    file: str
    patch: str
    additions: Annotated[int, Field(ge=0, le=9007199254740991)]
    deletions: Annotated[int, Field(ge=0, le=9007199254740991)]
    status: Literal["added", "deleted", "modified"]
    more: Literal[True] | None = None


class FileNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["FileNotFoundError"], Field(alias="_tag")] = "FileNotFoundError"
    path: str
    message: str


class FileSystemEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    path: str
    type: Literal["file", "directory"]


class FileSystemWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    path: str


class FilesystemChangedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    file: str
    event: Literal["add", "change", "unlink"]


class ForbiddenErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["ForbiddenError"], Field(alias="_tag")] = "ForbiddenError"
    message: str


class FormAlreadySettledErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["FormAlreadySettledError"], Field(alias="_tag")] = (
        "FormAlreadySettledError"
    )
    id: str
    message: str


class FormCancelledData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^frm_")]
    session_id: Annotated[str, Field(alias="sessionID")]


class FormExternalField(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: str
    type: Literal["external"] = "external"
    url: str
    title: str | None = None
    description: str | None = None


class FormInvalidAnswerErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["FormInvalidAnswerError"], Field(alias="_tag")] = (
        "FormInvalidAnswerError"
    )
    id: str
    message: str


class FormNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["FormNotFoundError"], Field(alias="_tag")] = "FormNotFoundError"
    id: str
    message: str


class FormOption(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    value: str
    label: str
    description: str | None = None


class FormStateVariant0(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["pending"] = "pending"


class FormStateVariant2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["cancelled"] = "cancelled"
    message: str | None = None


class FormWhen(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: str
    op: Literal["eq", "neq"]
    value: str | float | Literal["Infinity", "-Infinity", "NaN"] | bool


class FormWhen1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: str
    op: Literal["eq", "neq"]
    value: str | float | Literal["NaN", "Infinity", "-Infinity"] | bool


class GenerateDecisionFilePart(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["file"] = "file"
    data: Annotated[
        str, Field(pattern="^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$")
    ]
    mime: str
    name: str | None = None


class GenerateDecisionJsonPart(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["json"] = "json"
    value: Any


class GenerateDecisionTextPart(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["text"] = "text"
    text: str


class GenerateErrorAnswer(BaseModel):
    """
    This one question has no answer; the other answers still came back
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["error"] = "error"
    name: str
    reason: str
    "\n    Why the question has no answer, for example refusal\n    "
    message: str | None = None


class GenerateFile(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: Annotated[
        str, Field(pattern="^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$")
    ]
    mime: str
    name: str | None = None


class GenerateImageFile(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: Annotated[
        str, Field(pattern="^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$")
    ]
    mime: str
    name: str | None = None


class GenerateImageOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["image"] = "image"
    data: Annotated[
        str, Field(pattern="^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$")
    ]
    mime: str
    name: str | None = None


class GeneratePredicateAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["predicate"] = "predicate"
    name: str
    probability: Annotated[float, Field(ge=0.0, le=1.0)]


class GeneratePredicateQuestionCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    true: str | None = None
    false: str | None = None


class GenerateScoreAnswerProbabilitiesItem(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    value: Annotated[int, Field(ge=-9007199254740991, le=9007199254740991)]
    label: str
    probability: Annotated[float, Field(ge=0.0, le=1.0)]


class GenerateScoreQuestionLevelsItem(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    label: str
    description: str | None = None


class GenerateTextResponseData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    text: str


class GenerateUsage(BaseModel):
    """
    Token counts, when the provider reports them
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    input: float | None = None
    output: float | None = None


class GenerateVideoCompleted(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^video_[0-9a-f]{12}[0-9A-Za-z]{14}$")]
    status: Literal["completed"] = "completed"
    blobs: list[BlobInfo]
    "\n    The finished videos, usually one. Read each with GET /api/experimental/blob/:id\n    "


class GenerateVideoError(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^video_[0-9a-f]{12}[0-9A-Za-z]{14}$")]
    status: Literal["error"] = "error"
    error: str
    reason: Literal["failed", "cancelled", "expired", "unknown"]
    "\n    failed: the provider failed the job. cancelled: the provider cancelled it. expired: the provider expired it. unknown: the provider gave no result in time\n    "


class GenerateVideoRunning(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^video_[0-9a-f]{12}[0-9A-Za-z]{14}$")]
    status: Literal["running"] = "running"


class InstallationUpdateAvailableData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    version: str


class InstallationUpdatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    version: str


class InstructionEntryInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: Annotated[str, Field(pattern="^[a-z0-9][a-z0-9._-]*$")]
    "\n    Instruction entry key (lowercase alphanumerics plus . _ -)\n    "
    value: Any
    "\n    JSON value attached to the session's instructions\n    "


class InstructionEntrySnapshotItem(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: Annotated[str, Field(pattern="^[a-z0-9][a-z0-9._-]*$")]
    "\n    Instruction entry key (lowercase alphanumerics plus . _ -)\n    "
    value: Any
    "\n    JSON value attached to the session's instructions\n    "
    removed: bool


class InstructionEntryValueTooLargeErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["InstructionEntryValueTooLargeError"], Field(alias="_tag")] = (
        "InstructionEntryValueTooLargeError"
    )
    actual_bytes: Annotated[
        int, Field(alias="actualBytes", ge=-9007199254740991, le=9007199254740991)
    ]
    max_bytes: Annotated[int, Field(alias="maxBytes", ge=-9007199254740991, le=9007199254740991)]
    message: str


class IntegrationAttemptEncodedTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float | Literal["Infinity", "-Infinity", "NaN"]
    expires: float | Literal["Infinity", "-Infinity", "NaN"]


class IntegrationAttemptNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["IntegrationAttemptNotFoundError"], Field(alias="_tag")] = (
        "IntegrationAttemptNotFoundError"
    )
    integration_id: Annotated[str, Field(alias="integrationID")]
    attempt_id: Annotated[str, Field(alias="attemptID")]
    message: str


class IntegrationAttemptStatusVariant0Time(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float | Literal["Infinity", "-Infinity", "NaN"]
    expires: float | Literal["Infinity", "-Infinity", "NaN"]


class IntegrationAttemptStatusVariant1Time(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float | Literal["Infinity", "-Infinity", "NaN"]
    expires: float | Literal["Infinity", "-Infinity", "NaN"]


class IntegrationAttemptStatusVariant2Time(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float | Literal["Infinity", "-Infinity", "NaN"]
    expires: float | Literal["Infinity", "-Infinity", "NaN"]


class IntegrationAttemptStatusVariant3Time(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float | Literal["Infinity", "-Infinity", "NaN"]
    expires: float | Literal["Infinity", "-Infinity", "NaN"]


class IntegrationCommandAttemptStatusVariant0Time(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float | Literal["Infinity", "-Infinity", "NaN"]
    expires: float | Literal["Infinity", "-Infinity", "NaN"]


class IntegrationCommandAttemptStatusVariant1Time(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float | Literal["Infinity", "-Infinity", "NaN"]
    expires: float | Literal["Infinity", "-Infinity", "NaN"]


class IntegrationCommandAttemptStatusVariant2Time(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float | Literal["Infinity", "-Infinity", "NaN"]
    expires: float | Literal["Infinity", "-Infinity", "NaN"]


class IntegrationCommandAttemptStatusVariant3Time(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float | Literal["Infinity", "-Infinity", "NaN"]
    expires: float | Literal["Infinity", "-Infinity", "NaN"]


class IntegrationCommandAttemptTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float | Literal["Infinity", "-Infinity", "NaN"]
    expires: float | Literal["Infinity", "-Infinity", "NaN"]


class IntegrationCommandConnectBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    method_id: Annotated[str, Field(alias="methodID")]
    label: str | None = None


class IntegrationCommandMethod(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    type: Literal["command"] = "command"
    label: str
    command: list[str]


class IntegrationEnvMethod(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["env"] = "env"
    names: list[str]


class IntegrationMethodNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["IntegrationMethodNotFoundError"], Field(alias="_tag")] = (
        "IntegrationMethodNotFoundError"
    )
    integration_id: Annotated[str, Field(alias="integrationID")]
    method_id: Annotated[str, Field(alias="methodID")]
    message: str


class IntegrationNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["IntegrationNotFoundError"], Field(alias="_tag")] = (
        "IntegrationNotFoundError"
    )
    integration_id: Annotated[str, Field(alias="integrationID")]
    message: str


class IntegrationOauthCompleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    code: str | None = None


class InvalidCursorErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["InvalidCursorError"], Field(alias="_tag")] = "InvalidCursorError"
    message: str


class InvalidRequestErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["InvalidRequestError"], Field(alias="_tag")] = (
        "InvalidRequestError"
    )
    message: str
    kind: str | None = None
    field: str | None = None


class JobTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    started: float
    "\n    Start time in milliseconds since the Unix epoch\n    "
    completed: float | None = None


class JobVideo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    kind: Literal["video"] = "video"
    id: str
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    status: Literal["running", "completed", "error", "cancelled"]
    title: str | None = None
    time: JobTime
    path: str


class LocationPublicInfoProject(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    directory: str
    canonical: str


class LocationPublicRef(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    directory: str


class LocationRef(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    directory: str
    workspace_id: Annotated[str | None, Field(alias="workspaceID", pattern="^wrk")] = None


class LocationShutdown(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["location.shutdown"] = "location.shutdown"
    location: LocationRef | None = None
    data: Any


class MachineInfoConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    kind: Literal["websocket", "iroh"]
    path: Literal["direct", "relayed"] | None = None
    rtt: float | Literal["Infinity", "-Infinity", "NaN"] | None = None


class MachineInviteReachItemIroh(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["iroh"] = "iroh"


class MachineNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["MachineNotFoundError"], Field(alias="_tag")] = (
        "MachineNotFoundError"
    )
    machine: str
    message: str


class MachineReach(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["url"] = "url"
    url: str


class McpLocalConfigEncodedTimeout(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    startup: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None
    catalog: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None
    execution: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None


class McpOAuthConfigEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    client_id: str | None = None
    client_secret: str | None = None
    scope: str | None = None
    callback_port: Annotated[int | None, Field(ge=1, le=65535)] = None
    redirect_uri: str | None = None
    auth_server_metadata_url: str | None = None


class McpRemoteConfigEncodedTimeout(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    startup: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None
    catalog: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None
    execution: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None


class McpResource(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    server: str
    name: str
    uri: str
    description: str | None = None
    mime_type: Annotated[str | None, Field(alias="mimeType")] = None


class McpResourceTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    server: str
    name: str
    uri_template: Annotated[str, Field(alias="uriTemplate")]
    description: str | None = None
    mime_type: Annotated[str | None, Field(alias="mimeType")] = None


class McpResourcesChangedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    server: str


class McpServerNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["McpServerNotFoundError"], Field(alias="_tag")] = (
        "McpServerNotFoundError"
    )
    server: str
    message: str


class McpStatusChangedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    server: str


class McpStatusConnected(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["connected"] = "connected"


class McpStatusDisabled(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["disabled"] = "disabled"


class McpStatusFailed(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["failed"] = "failed"
    error: str


class McpStatusNeedsAuth(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["needs_auth"] = "needs_auth"
    error: str


class McpStatusPending(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["pending"] = "pending"


class MessageNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["MessageNotFoundError"], Field(alias="_tag")] = (
        "MessageNotFoundError"
    )
    session_id: Annotated[str, Field(alias="sessionID")]
    message_id: Annotated[str, Field(alias="messageID")]
    message: str


class ModelCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    tools: bool
    freeform: list[Literal["text", "regex", "lark"]] | None = None
    input: list[str]
    output: list[str]
    temperature: bool | None = None


class ModelCostCache(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    read: float
    write: float


class ModelCostTier(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["context"] = "context"
    size: Annotated[int, Field(ge=-9007199254740991, le=9007199254740991)]


class ModelInfoLimit(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    context: Annotated[int, Field(ge=-9007199254740991, le=9007199254740991)]
    input: Annotated[int | None, Field(ge=-9007199254740991, le=9007199254740991)] = None
    output: Annotated[int, Field(ge=-9007199254740991, le=9007199254740991)]


class ModelInfoTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    released: float


class ModelNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["ModelNotFoundError"], Field(alias="_tag")] = "ModelNotFoundError"
    provider_id: Annotated[str, Field(alias="providerID")]
    model_id: Annotated[str, Field(alias="modelID")]
    message: str


class ModelRef(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    provider_id: Annotated[str, Field(alias="providerID")]
    variant: str | None = None


class ModelSpeed(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    base: str
    id: str
    faster: bool


class ModelUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["model.updated"] = "model.updated"
    location: LocationRef | None = None
    data: Any


class ModelsDevRefreshed(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["models-dev.refreshed"] = "models-dev.refreshed"
    location: LocationRef | None = None
    data: Any


class PairingCode(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    code: str
    expires_in: Annotated[int, Field(ge=-9007199254740991, le=9007199254740991)]


class PairingSession(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    token: str


class PermissionNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["PermissionNotFoundError"], Field(alias="_tag")] = (
        "PermissionNotFoundError"
    )
    request_id: Annotated[str, Field(alias="requestID")]
    message: str


class PermissionRepliedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    request_id: Annotated[str, Field(alias="requestID", pattern="^per")]
    reply: Literal["once", "always", "reject"]


class PermissionRule(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    action: str
    resource: str
    effect: Literal["allow", "deny", "ask"]


class PermissionSavedInfoTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float
    updated: float


class PermissionSavedNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["PermissionSavedNotFoundError"], Field(alias="_tag")] = (
        "PermissionSavedNotFoundError"
    )
    id: str
    message: str


class PermissionSavedUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    action: str | None = None
    resource: str | None = None


class PermissionSourceTool(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["tool"] = "tool"
    message_id: Annotated[str, Field(alias="messageID")]
    id: str


class PersistentPtyCreateInputSize(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    cols: Annotated[int, Field(gt=0, le=9007199254740991)]
    rows: Annotated[int, Field(gt=0, le=9007199254740991)]


class PersistentPtyHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    directory: str
    instance_id: Annotated[str, Field(alias="instanceID")]
    ticket: str
    expires_at: Annotated[float | Literal["Infinity", "-Infinity", "NaN"], Field(alias="expiresAt")]


class PersistentPtyInfoOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    head: Annotated[int, Field(ge=0, le=9007199254740991)]
    tail: Annotated[int, Field(ge=0, le=9007199254740991)]


class PersistentPtyInfoSize(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    cols: Annotated[int, Field(gt=0, le=9007199254740991)]
    rows: Annotated[int, Field(gt=0, le=9007199254740991)]


class PersistentPtyReadResultScreenCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    x: Annotated[int, Field(ge=0, le=9007199254740991)]
    y: Annotated[int, Field(ge=0, le=9007199254740991)]


class PersistentPtyRemovedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    pty_id: Annotated[str, Field(alias="ptyID", pattern="^pty")]


class PersistentPtySnapshotCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    x: Annotated[int, Field(ge=0, le=9007199254740991)]
    y: Annotated[int, Field(ge=0, le=9007199254740991)]


class PersistentPtyUpdateInputSize(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    cols: Annotated[int, Field(gt=0, le=9007199254740991)]
    rows: Annotated[int, Field(gt=0, le=9007199254740991)]


class PluginCheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    target: str | None = None


class PluginFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    server: Literal[True] | None = None
    tui: Literal[True] | None = None
    rpc: Literal[True] | None = None
    gui: Literal[True] | None = None


class PluginGuiBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    code: str
    revision: str
    options: dict[str, Any]


class PluginGuiResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: PluginGuiBundle


class PluginNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["PluginNotFoundError"], Field(alias="_tag")] = (
        "PluginNotFoundError"
    )
    plugin_id: Annotated[str, Field(alias="pluginID")]
    message: str


class PluginSourceBuiltin(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["builtin"] = "builtin"


class PluginSourceLocal(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["local"] = "local"
    path: str


class PluginSourcePackage(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["package"] = "package"
    target: str
    version: str | None = None
    outdated: Literal[True] | None = None
    updating: Literal[True] | None = None


class PluginSourceSdk(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["sdk"] = "sdk"


class PluginStateVariant0(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["active"] = "active"


class PluginStateVariant1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["failed"] = "failed"
    error: str
    ref: str | None = None


class PluginUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    targets: list[str]


class PluginUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["plugin.updated"] = "plugin.updated"
    location: LocationRef | None = None
    data: Any


class ProjectCommands(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    start: str | None = None
    "\n    Startup script to run when creating a new workspace (worktree)\n    "


class ProjectIcon(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    url: str | None = None
    override: str | None = None
    color: str | None = None


class ProjectNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["ProjectNotFoundError"], Field(alias="_tag")] = (
        "ProjectNotFoundError"
    )
    project_id: Annotated[str, Field(alias="projectID")]
    message: str


class ProjectTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: Annotated[int, Field(ge=0, le=9007199254740991)]
    updated: Annotated[int, Field(ge=0, le=9007199254740991)]
    active: Annotated[int, Field(ge=0, le=9007199254740991)]
    archived: Annotated[int | None, Field(ge=0, le=9007199254740991)] = None


class ProjectUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    canonical: str | None = None
    name: str | None = None
    icon: ProjectIcon | None = None
    commands: ProjectCommands | None = None


class ProjectUpdatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    canonical: str
    vcs: Annotated[str | None, Field(pattern="^[a-z][a-z0-9._-]*$")] = None
    name: str | None = None
    icon: ProjectIcon | None = None
    commands: ProjectCommands | None = None
    time: ProjectTime
    sandboxes: list[str]


class PromptFileSourceInline(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["inline"] = "inline"


class PromptFileSourceUri(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["uri"] = "uri"
    uri: str


class PromptMention(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    start: float
    end: float
    text: str


class PromptSkillAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    name: str
    text: str | None = None
    mention: PromptMention | None = None


class ProviderCacheWarmingVariant1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    interval: Literal["auto"] | str | None = None
    duration: str | None = None


class ProviderCompactionNative(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["native"] = "native"


class ProviderCompactionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["summary"] = "summary"


class ProviderCompatibility(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    reasoning_field: Annotated[
        Literal["reasoning", "reasoning_content", "reasoning_text"] | str | None,
        Field(alias="reasoningField"),
    ] = None
    require_reasoning: Annotated[bool | None, Field(alias="requireReasoning")] = None
    max_tokens_field: Annotated[
        Literal["max_completion_tokens", "max_tokens"] | None, Field(alias="maxTokensField")
    ] = None
    require_finish_reason: Annotated[bool | None, Field(alias="requireFinishReason")] = None
    require_assistant_after_tool: Annotated[
        bool | None, Field(alias="requireAssistantAfterTool")
    ] = None
    supports_prompt_cache_key: Annotated[bool | None, Field(alias="supportsPromptCacheKey")] = None
    stream_usage: Annotated[bool | None, Field(alias="streamUsage")] = None
    cache_format: Annotated[Literal["anthropic", "alibaba"] | None, Field(alias="cacheFormat")] = (
        None
    )
    media_input: Annotated[Literal["assets", "state", "root"] | None, Field(alias="mediaInput")] = (
        None
    )


class ProviderNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["ProviderNotFoundError"], Field(alias="_tag")] = (
        "ProviderNotFoundError"
    )
    provider_id: Annotated[str, Field(alias="providerID")]
    message: str


class ProviderRequestErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["ProviderRequestError"], Field(alias="_tag")] = (
        "ProviderRequestError"
    )
    provider_id: Annotated[str, Field(alias="providerID")]
    reason: Literal[
        "authentication",
        "rate_limit",
        "content_filter",
        "invalid_request",
        "overloaded",
        "provider",
    ]
    message: str
    status: int | None = None
    retry_after_ms: Annotated[int | None, Field(alias="retryAfterMs")] = None


class ProviderTls(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    cert: str
    "\n    PEM client certificate, usually `{file:/path/to/client.pem}`\n    "
    key: str
    "\n    PEM private key, usually `{file:/path/to/client.key}`\n    "
    passphrase: str | None = None


class ProviderUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["provider.updated"] = "provider.updated"
    location: LocationRef | None = None
    data: Any


class Pty(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^pty")]
    title: str
    command: str
    args: list[str]
    cwd: str
    status: Literal["running", "exited"]
    pid: Annotated[int, Field(ge=0, le=9007199254740991)]
    exit_code: Annotated[int | None, Field(alias="exitCode", ge=0, le=9007199254740991)] = None


class PtyCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    command: str | None = None
    args: list[str] | None = None
    cwd: str | None = None
    title: str | None = None
    env: dict[str, str] | None = None


class PtyCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: Pty


class PtyCreatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    info: Pty


class PtyDeletedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^pty")]


class PtyExitedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^pty")]
    exit_code: Annotated[int, Field(alias="exitCode", ge=0, le=9007199254740991)]


class PtyFeatureUnsupportedErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["PtyFeatureUnsupportedError"], Field(alias="_tag")] = (
        "PtyFeatureUnsupportedError"
    )
    feature: str
    message: str


class PtyGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: Pty


class PtyListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[Pty]


class PtyNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["PtyNotFoundError"], Field(alias="_tag")] = "PtyNotFoundError"
    pty_id: Annotated[str, Field(alias="ptyID")]
    message: str


class PtyTicketConnectToken(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    ticket: str
    expires_in: Annotated[int, Field(gt=0, le=9007199254740991)]


class PtyUpdateBodySize(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    rows: Annotated[int, Field(gt=0, le=9007199254740991)]
    cols: Annotated[int, Field(gt=0, le=9007199254740991)]


class PtyUpdateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: Pty


class PtyUpdatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    info: Pty


class ReferenceGitSource(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["git"] = "git"
    repository: str
    branch: str | None = None


class ReferenceLocalSource(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["local"] = "local"
    path: str


class ReferenceUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["reference.updated"] = "reference.updated"
    location: LocationRef | None = None
    data: Any


class RpcErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["RpcError"], Field(alias="_tag")] = "RpcError"
    type: str
    message: str
    data: Any = None


class RpcInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    input: Any | None = None


class RpcInternalErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["RpcInternalError"], Field(alias="_tag")] = "RpcInternalError"
    type: Literal["rpc.internal", "rpc.invalid_output"]
    message: str
    data: Any = None


class RpcOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    output: Any | None = None


class ServerInfoCapabilitiesVariant0(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    persistent_pty: Annotated[bool | None, Field(alias="persistentPty")] = None


class ServerInfoPaths(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    tmp: str


class ServiceUnavailableErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["ServiceUnavailableError"], Field(alias="_tag")] = (
        "ServiceUnavailableError"
    )
    message: str
    service: str | None = None


class SessionActive(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["running"] = "running"


class SessionActiveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: dict[str, SessionActive]


class SessionAgentSelectedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    agent: str
    previous: str | None = None


class SessionAgentSelectedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionArchivedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]


class SessionArchivedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionBusyErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["SessionBusyError"], Field(alias="_tag")] = "SessionBusyError"
    session_id: Annotated[str, Field(alias="sessionID")]
    message: str


class SessionCompactBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str | None = None
    delivery: Literal["steer", "queue"] | None = None


class SessionCompactionDeltaData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    text: str


class SessionCompactionEndedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionCompactionFailedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionCompactionStartedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    reason: Literal["auto", "manual"]
    input_id: Annotated[str | None, Field(alias="inputID", pattern="^msg_")] = None


class SessionCompactionStartedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionCreatedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionDeletedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]


class SessionDeletedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[2] = 2


class SessionDiffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: list[FileDiffInfo]


class SessionEnvironmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    variables: dict[str, str]


class SessionExecutionFailedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionExecutionInterruptedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    reason: Literal["user", "shutdown", "superseded", "inactivity"]


class SessionExecutionInterruptedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionExecutionStartedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]


class SessionExecutionStartedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionExecutionSucceededData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]


class SessionExecutionSucceededDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionForkBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    before: str | None = None


class SessionForkBoundaryBefore(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["before"] = "before"
    message_id: Annotated[str, Field(alias="messageID", pattern="^msg_")]


class SessionForkBoundaryThrough(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["through"] = "through"
    message_id: Annotated[str, Field(alias="messageID", pattern="^msg_")]


class SessionForkedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[2] = 2


class SessionGenerateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    prompt: str


class SessionIdleData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]


class SessionInboxCancelledData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    inbox_id: Annotated[str, Field(alias="inboxID", pattern="^msg_")]


class SessionInboxCancelledDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionInboxCompactionTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float


class SessionInboxDeliveredData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    inbox_id: Annotated[str, Field(alias="inboxID", pattern="^msg_")]


class SessionInboxDeliveredDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionInboxDeliveryChangedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    inbox_id: Annotated[str, Field(alias="inboxID", pattern="^msg_")]
    delivery: Literal["steer", "queue"]


class SessionInboxDeliveryChangedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionInboxEnqueuedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionInboxItemCompaction(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["compaction"] = "compaction"
    payload: Any
    delivery: Literal["steer", "queue"]


class SessionInboxMovePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    project_id: Annotated[str, Field(alias="projectID")]
    subpath: str | None = None
    location: LocationPublicRef


class SessionInboxMovePayload1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationRef
    project_id: Annotated[str, Field(alias="projectID")]
    subpath: str | None = None


class SessionInboxMoveTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float


class SessionInboxSyntheticPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    text: str
    description: str | None = None
    metadata: dict[str, Any] | None = None


class SessionInboxSyntheticTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float


class SessionInboxUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    delivery: Literal["steer", "queue"]


class SessionInboxUserTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float


class SessionInfoTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float
    updated: float
    busy: float | None = None
    idle: float | None = None
    viewed: float | None = None
    archived: float | None = None


class SessionInstructionsUpdatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    delta: dict[str, str | Literal["removed"]]
    text: str | None = None


class SessionInstructionsUpdatedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[2] = 2


class SessionInterruptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    interrupted: bool
    "\n    Whether an active execution owned by this OpenCode process was interrupted.\n    "


class SessionMessageAgentSelectedTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float


class SessionMessageAssistantReasoningTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float
    completed: float | None = None


class SessionMessageAssistantSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    start: str | None = None
    end: str | None = None
    files: list[str] | None = None


class SessionMessageAssistantTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float
    first_token: Annotated[float | None, Field(alias="firstToken")] = None
    streamed: float | None = None
    completed: float | None = None


class SessionMessageAssistantToolTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float
    ran: float | None = None
    completed: float | None = None


class SessionMessageCompactionCompletedTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float


class SessionMessageCompactionFailedTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float


class SessionMessageCompactionRunningTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float


class SessionMessageContentUpdatedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionMessageIdleTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float


class SessionMessageLocationSwitchedPreviousVariant0(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    project_id: Annotated[str | None, Field(alias="projectID")] = None
    subpath: str | None = None


class SessionMessageLocationSwitchedTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float


class SessionMessageModelSelectedTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float


class SessionMessageShellOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    output: str
    cursor: Annotated[int, Field(ge=0, le=9007199254740991)]
    size: Annotated[int, Field(ge=0, le=9007199254740991)]
    truncated: bool


class SessionMessageShellTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float
    completed: float | None = None


class SessionMessageSkillTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float


class SessionMessageSyntheticTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float


class SessionMessageSystemTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float


class SessionMessageToolStateRunning(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["running"] = "running"
    input: dict[str, Any]
    metadata: dict[str, Any]


class SessionMessageToolStateStreaming(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["streaming"] = "streaming"
    input: str


class SessionMessageUserTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    created: float


class SessionMessagesResponseCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    previous: str | None = None
    next: str | None = None


class SessionMetadataUpdatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    metadata: dict[str, Any]


class SessionMetadataUpdatedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionModelSelectedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    model: ModelRef
    previous: ModelRef | None = None


class SessionModelSelectedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionMoveBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    directory: str
    delivery: Literal["steer", "queue"] | None = None


class SessionMovedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    location: LocationRef
    project_id: Annotated[str, Field(alias="projectID")]
    subpath: str | None = None


class SessionMovedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["SessionNotFoundError"], Field(alias="_tag")] = (
        "SessionNotFoundError"
    )
    session_id: Annotated[str, Field(alias="sessionID")]
    message: str


class SessionPermissionCreateResponseData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^per")]
    effect: Literal["allow", "deny", "ask"]


class SessionPermissionReplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    decision: Literal["once", "always", "reject"]
    message: str | None = None


class SessionPermissionsData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    permissions: list[PermissionRule]


class SessionPermissionsDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionReasoningDeltaData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    ordinal: Annotated[int, Field(ge=0, le=9007199254740991)]
    delta: str


class SessionReasoningEndedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    ordinal: Annotated[int, Field(ge=0, le=9007199254740991)]
    text: str
    native: dict[str, Any] | None = None


class SessionReasoningEndedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionReasoningStartedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    ordinal: Annotated[int, Field(ge=0, le=9007199254740991)]
    native: dict[str, Any] | None = None


class SessionReasoningStartedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionRenamedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    title: str


class SessionRenamedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionRetryScheduledDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionRevert(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    message_id: Annotated[str, Field(alias="messageID", pattern="^msg_")]
    part_id: Annotated[str | None, Field(alias="partID")] = None
    snapshot: str | None = None
    files: list[FileDiffInfo] | None = None


class SessionRevertClearedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]


class SessionRevertClearedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionRevertCommittedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    to: Annotated[str, Field(pattern="^msg_")]


class SessionRevertCommittedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionRevertStageBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    message_id: Annotated[str, Field(alias="messageID", pattern="^msg_")]
    files: bool | None = None


class SessionRevertStageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: SessionRevert


class SessionRevertStagedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    revert: SessionRevert


class SessionRevertStagedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionShellBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str | None = None
    command: str


class SessionShellEndedDataOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    output: str
    cursor: Annotated[int, Field(ge=0, le=9007199254740991)]
    size: Annotated[int, Field(ge=0, le=9007199254740991)]
    truncated: bool


class SessionShellEndedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionShellStartedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionSkillActivatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    id: str
    name: str
    text: str


class SessionSkillActivatedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionStatsActivity(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    date: str
    steps: Annotated[int, Field(ge=0, le=9007199254740991)]


class SessionStatsInfoRange(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: Annotated[float, Field(alias="from")]
    to: float


class SessionStatsToolTotals(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    calls: Annotated[int, Field(ge=0, le=9007199254740991)]
    succeeded: Annotated[int, Field(ge=0, le=9007199254740991)]
    failed: Annotated[int, Field(ge=0, le=9007199254740991)]
    unfinished: Annotated[int, Field(ge=0, le=9007199254740991)]


class SessionStatsToolUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str
    calls: Annotated[int, Field(ge=0, le=9007199254740991)]
    succeeded: Annotated[int, Field(ge=0, le=9007199254740991)]
    failed: Annotated[int, Field(ge=0, le=9007199254740991)]
    unfinished: Annotated[int, Field(ge=0, le=9007199254740991)]
    duration_p50: Annotated[float | None, Field(alias="durationP50")] = None


class SessionStatsToolsVariant0(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    mode: Literal["none"] = "none"


class SessionStatsToolsVariant1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    mode: Literal["summary"] = "summary"
    totals: SessionStatsToolTotals


class SessionStatsToolsVariant2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    mode: Literal["detail"] = "detail"
    totals: SessionStatsToolTotals
    usage: list[SessionStatsToolUsage]


class SessionStatusBusy(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["busy"] = "busy"


class SessionStatusIdle(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["idle"] = "idle"


class SessionStatusRetryAction(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    reason: str
    provider: str
    title: str
    message: str
    label: str
    link: str | None = None


class SessionStepEndedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionStepFailedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionStepFirstTokenData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]


class SessionStepFirstTokenDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionStepStartedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    agent: str
    model: ModelRef
    snapshot: str | None = None
    started: Annotated[int, Field(ge=0, le=9007199254740991)]


class SessionStepStartedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionStepStreamedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]


class SessionStepStreamedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionStructuredErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    body: str


class SessionSwitchAgentBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    agent: str


class SessionSwitchModelBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    model: ModelRef


class SessionSyntheticBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str | None = None
    text: str
    description: str | None = None
    metadata: dict[str, Any] | None = None
    delivery: Literal["steer", "queue"] | None = None
    resume: bool | None = None


class SessionSyntheticData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    text: str
    description: str | None = None
    metadata: dict[str, Any] | None = None


class SessionSyntheticDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionTextDeltaData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    ordinal: Annotated[int, Field(ge=0, le=9007199254740991)]
    delta: str


class SessionTextEndedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    ordinal: Annotated[int, Field(ge=0, le=9007199254740991)]
    text: str
    native: dict[str, Any] | None = None


class SessionTextEndedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionTextStartedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    ordinal: Annotated[int, Field(ge=0, le=9007199254740991)]


class SessionTextStartedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionToolCalledData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    id: str
    input: dict[str, Any]
    executed: bool
    native: dict[str, Any] | None = None


class SessionToolCalledDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionToolFailedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[2] = 2


class SessionToolInputDeltaData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    id: str
    delta: str


class SessionToolInputEndedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    id: str
    text: str


class SessionToolInputEndedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionToolInputStartedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    id: str
    name: str


class SessionToolInputStartedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionToolProgressData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    id: str
    metadata: dict[str, Any]


class SessionToolSuccessDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[2] = 2


class SessionUnarchivedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]


class SessionUnarchivedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    title: str | None = None
    metadata: dict[str, Any] | None = None
    permissions: list[PermissionRule] | None = None
    archived: bool | None = None
    "\n    Archive the session with true or restore it with false. Repeating the current state is a no-op.\n    "


class SessionUsageRecordedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionViewBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    idle: float


class SessionViewedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    idle: float


class SessionViewedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class SessionsResponseCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    previous: str | None = None
    next: str | None = None


class ShellCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    command: str
    cwd: str | None = None
    timeout: Annotated[int | None, Field(ge=0, le=9007199254740991)] = None
    metadata: dict[str, Any] | None = None


class ShellDeletedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^sh_")]


class ShellExitedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^sh_")]
    exit: float | None = None
    status: Literal["running", "exited", "timeout", "killed"]


class ShellInfoTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    started: float
    "\n    Start time in milliseconds since the Unix epoch\n    "
    completed: float | None = None


class ShellNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["ShellNotFoundError"], Field(alias="_tag")] = "ShellNotFoundError"
    id: str
    message: str


class ShellOutputResponseData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    output: str
    cursor: Annotated[int, Field(ge=0, le=9007199254740991)]
    size: Annotated[int, Field(ge=0, le=9007199254740991)]
    truncated: bool


class SkillInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    name: str
    description: str | None = None
    autoinvoke: bool | None = None
    path: str
    content: str


class SkillListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[SkillInfo]


class SkillNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["SkillNotFoundError"], Field(alias="_tag")] = "SkillNotFoundError"
    skill: str
    message: str


class SkillUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["skill.updated"] = "skill.updated"
    location: LocationRef | None = None
    data: Any


class TokenUsageInfoCache(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    read: float
    write: float


class ToolFileContent(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["file"] = "file"
    uri: str
    mime: str
    name: str | None = None


class ToolTextContent(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["text"] = "text"
    text: str


class TuiCommandExecuteData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    command: (
        Literal[
            "session.list",
            "session.new",
            "session.interrupt",
            "session.background",
            "session.compact",
            "session.page.up",
            "session.page.down",
            "session.line.up",
            "session.line.down",
            "session.half.page.up",
            "session.half.page.down",
            "session.first",
            "session.last",
            "prompt.clear",
            "prompt.submit",
            "agent.cycle",
        ]
        | str
    )


class TuiPromptAppendData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    text: str


class SessionId(BaseModel):
    pass
    model_config = ConfigDict(populate_by_name=True)


class TuiSessionSelectData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[SessionId, Field(alias="sessionID")]


class TuiToastShowData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    title: str | None = None
    message: str
    variant: Literal["info", "success", "warning", "error"]
    duration: int | None = None


class UnauthorizedErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["UnauthorizedError"], Field(alias="_tag")] = "UnauthorizedError"
    message: str


class UnknownErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["UnknownError"], Field(alias="_tag")] = "UnknownError"
    message: str
    ref: str | None = None


class UnsupportedModelErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["UnsupportedModelError"], Field(alias="_tag")] = (
        "UnsupportedModelError"
    )
    provider_id: Annotated[str, Field(alias="providerID")]
    model_id: Annotated[str, Field(alias="modelID")]
    capability: str
    message: str


class UsageLimitsBudgetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    provider_id: Annotated[str, Field(alias="providerID")]
    model_id: Annotated[str, Field(alias="modelID")]
    name: str
    spent: float | None = None


class UsageLimitsInfoWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    name: str | None = None
    usage_url: Annotated[str, Field(alias="usageURL")]


class UsageLimitsMeter(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    percent: Annotated[float, Field(ge=0.0, le=100.0)]
    limited: bool
    resets_at: Annotated[float, Field(alias="resetsAt")]
    "\n    Milliseconds since the Unix epoch\n    "


class V2EventRpc(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Annotated[str, Field(pattern="^rpc\\.[\\s\\S]*?$")]
    location: LocationPublicRef
    data: dict[str, Any]


class V2EventServerConnected(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    metadata: dict[str, Any] | None = None
    location: LocationPublicRef | None = None
    type: Literal["server.connected"] = "server.connected"
    data: Any


class VcsBase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str
    ref: str
    source: Literal["reflog", "default"]


class VcsBaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: VcsBase | None = None


class VcsBranch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    current: str | None = None
    default: str | None = None


class VcsBranchListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[str]


class VcsBranchUpdatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    branch: str | None = None


class VcsDiffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[FileDiffInfo]


class VcsFileStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    file: str
    additions: Annotated[int, Field(ge=0, le=9007199254740991)]
    deletions: Annotated[int, Field(ge=0, le=9007199254740991)]
    status: Literal["added", "deleted", "modified"]


class VcsInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    provider: str | None = None
    branch: VcsBranch


class VcsInitNotSupportedErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["VcsInitNotSupportedError"], Field(alias="_tag")] = (
        "VcsInitNotSupportedError"
    )
    provider_id: Annotated[str, Field(alias="providerID")]
    message: str


class VcsStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[VcsFileStatus]


class VideoNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["VideoNotFoundError"], Field(alias="_tag")] = "VideoNotFoundError"
    video_id: Annotated[str, Field(alias="videoID")]
    message: str


class VoiceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    model: str
    name: str
    engine: str
    provider: str
    transports: list[Literal["webrtc", "websocket"]]
    available: bool


class VoiceModelsResponseData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    models: list[VoiceModel]
    default: str | None = None


class VoiceWebRTCTransport(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["webrtc"] = "webrtc"
    sdp: str


class VoiceWebRTCTransportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["webrtc"] = "webrtc"
    sdp: str
    dcid: float | Literal["Infinity", "-Infinity", "NaN"] | None = None


class VoiceWebSocketTransport(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["websocket"] = "websocket"
    url: str
    token: str
    expires_at: Annotated[float | Literal["Infinity", "-Infinity", "NaN"], Field(alias="expiresAt")]


class VoiceWebSocketTransportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["websocket"] = "websocket"


class WebSearchProvider(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    name: str


class WebSearchResultTime(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    published: float | None = None


class WebsearchProvidersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[WebSearchProvider]


class WebsearchQueryBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    query: str
    provider_id: Annotated[str | None, Field(alias="providerID")] = None


class WebsearchUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["websearch.updated"] = "websearch.updated"
    location: LocationRef | None = None
    data: Any


class WorktreeCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    project_id: Annotated[str, Field(alias="projectID")]
    from_: Annotated[str | None, Field(alias="from")] = None
    branch: str | None = None
    directory: str | None = None
    name: str | None = None


class WorktreeDirectory(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    directory: str
    strategy: str | None = None


class WorktreeErrorEncodedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    message: str
    force_required: Annotated[bool | None, Field(alias="forceRequired")] = None


class WorktreeInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    directory: str


class WorktreeList(RootModel[list[WorktreeDirectory]]):
    model_config = ConfigDict(populate_by_name=True)
    root: list[WorktreeDirectory]


class WorktreeRefreshBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    project_id: Annotated[str, Field(alias="projectID")]


class WorktreeRemoveInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    project_id: Annotated[str, Field(alias="projectID")]
    directory: str
    force: bool


class WorktreeResolvedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    project_id: Annotated[str, Field(alias="projectID")]
    directory: str
    previous: str
    adopted: list[str] | None = None


class WorktreeResolvedDurable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    aggregate_id: Annotated[str, Field(alias="aggregateID")]
    seq: Annotated[int, Field(ge=0, le=9007199254740991)]
    version: Literal[1] = 1


class WorktreeUpdatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    project_id: Annotated[str, Field(alias="projectID")]


class AgentUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["agent.updated"] = "agent.updated"
    location: LocationRef | None = None
    data: Any


class ArtifactInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^art_")]
    provider: str
    session_id: Annotated[str | None, Field(alias="sessionID", pattern="^ses")] = None
    title: str
    entry: str
    url: str
    time: ArtifactTime


class ArtifactUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["artifact.updated"] = "artifact.updated"
    location: LocationRef | None = None
    data: Any


class CommandListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[CommandInfo]


class CommandUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["command.updated"] = "command.updated"
    location: LocationRef | None = None
    data: Any


class ConfigAgentEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    model: str | ConfigAgentEncodedModelVariant1 | None = None
    request: ConfigAgentEncodedRequest | None = None
    system: str | None = None
    description: str | None = None
    mode: Literal["subagent", "primary", "all"] | None = None
    hidden: bool | None = None
    color: Annotated[str | None, Field(pattern="^#[0-9a-fA-F]{6}$")] = None
    steps: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None
    disabled: bool | None = None
    permissions: list[PermissionRule] | None = None


class ConfigCommandEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    template: str
    description: str | None = None
    agent: str | None = None
    model: str | ConfigCommandEncodedModelVariant1 | None = None
    subagent: bool | None = None
    subtask: bool | None = None
    "\n    Deprecated alias for subagent.\n    "


class ConfigInfoEncodedCompaction(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    auto: bool | None = None
    background: bool | None = None
    keep: ConfigInfoEncodedCompactionKeep | None = None
    buffer: str | int | None = None


class ConfigInfoEncodedExperimentalImageGenerateVariant1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    model: str | ConfigInfoEncodedExperimentalImageGenerateVariant1ModelVariant1 | None = None


class ConfigInfoEncodedExperimentalVideoGenerateVariant1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    model: str | ConfigInfoEncodedExperimentalVideoGenerateVariant1ModelVariant1 | None = None


class ConfigInfoEncodedMedia(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    image: ConfigInfoEncodedMediaImage | None = None


class ConfigModelCostEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    tier: ConfigModelCostEncodedTier | None = None
    input: float
    output: float
    cache: ConfigModelCostEncodedCache | None = None


class ConfigModelReasoningVariant1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    supported: Literal[True] = True
    toggle: bool | None = None
    effort: list[str] | None = None
    budget: ConfigModelReasoningVariant1Budget | None = None


class ConfigUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["config.updated"] = "config.updated"
    location: LocationRef | None = None
    data: Any


class ConnectionCredentialInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["credential"] = "credential"
    id: str
    label: str
    method: Literal["key", "oauth", "external"]
    status: ConnectionStatus | None = None


class ConnectionEnvInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["env"] = "env"
    name: str
    status: ConnectionStatus | None = None


class CredentialCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str | None = None
    integration_id: Annotated[str, Field(alias="integrationID")]
    label: str | None = None
    value: Annotated[
        CredentialOAuth | CredentialKey | CredentialExternal, Field(discriminator="type")
    ]
    activate: bool | None = None


class CredentialEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    integration_id: Annotated[str, Field(alias="integrationID")]
    label: str
    active: bool
    value: Annotated[
        CredentialOAuth | CredentialKey | CredentialExternal, Field(discriminator="type")
    ]


class CredentialListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: list[CredentialEntry]


class CredentialSwitched(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["credential.switched"] = "credential.switched"
    location: LocationRef | None = None
    data: CredentialSwitchedData


class CredentialUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["credential.updated"] = "credential.updated"
    location: LocationRef | None = None
    data: CredentialUpdatedData


class DebugHeapDumpResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    path: str
    pid: Annotated[int, Field(ge=-9007199254740991, le=9007199254740991)]
    memory: DebugHeapDumpResponseMemory


class ExperimentalArtifactsCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: ArtifactInfo


class ExperimentalArtifactsListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[ArtifactInfo]


class ExperimentalArtifactsProvidersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[ArtifactProvider]


class ExperimentalArtifactsUpdateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: ArtifactInfo


class ExperimentalFsWriteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: FileSystemWrite


class ExperimentalGenerateTextBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    prompt: str
    model: ModelRef | None = None


class ExperimentalGenerateTextLegacyBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    prompt: str
    model: ModelRef | None = None


class ExperimentalMcpClientTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: PtyTicketConnectToken


class ExperimentalMcpEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["experimental.mcp.event"] = "experimental.mcp.event"
    location: LocationRef | None = None
    data: ExperimentalMcpEventData


class ExperimentalPersistentPtyConnectTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: PtyTicketConnectToken


class ExperimentalPersistentPtyHandoffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    handoff: PersistentPtyHandoff | None = None


class ExperimentalSessionInstructionsEntryListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: list[InstructionEntryInfo]


class FilesystemChanged(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["filesystem.changed"] = "filesystem.changed"
    location: LocationRef | None = None
    data: FilesystemChangedData


class FormBooleanField(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: str
    title: str | None = None
    description: str | None = None
    required: bool | None = None
    hidden: bool | None = None
    when: list[FormWhen] | None = None
    type: Literal["boolean"] = "boolean"
    default: bool | None = None


class FormBooleanField1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: str
    title: str | None = None
    description: str | None = None
    required: bool | None = None
    hidden: bool | None = None
    when: list[FormWhen1] | None = None
    type: Literal["boolean"] = "boolean"
    default: bool | None = None


class FormCancelled(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["form.cancelled"] = "form.cancelled"
    location: LocationRef | None = None
    data: FormCancelledData


class FormIntegerField(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: str
    title: str | None = None
    description: str | None = None
    required: bool | None = None
    hidden: bool | None = None
    when: list[FormWhen] | None = None
    type: Literal["integer"] = "integer"
    minimum: float | Literal["Infinity", "-Infinity", "NaN"] | None = None
    maximum: float | Literal["Infinity", "-Infinity", "NaN"] | None = None
    default: float | Literal["Infinity", "-Infinity", "NaN"] | None = None


class FormIntegerField1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: str
    title: str | None = None
    description: str | None = None
    required: bool | None = None
    hidden: bool | None = None
    when: list[FormWhen1] | None = None
    type: Literal["integer"] = "integer"
    minimum: float | Literal["NaN", "Infinity", "-Infinity"] | None = None
    maximum: float | Literal["NaN", "Infinity", "-Infinity"] | None = None
    default: float | Literal["NaN", "Infinity", "-Infinity"] | None = None


class FormMultiselectField(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: str
    title: str | None = None
    description: str | None = None
    required: bool | None = None
    hidden: bool | None = None
    when: list[FormWhen] | None = None
    type: Literal["multiselect"] = "multiselect"
    options: list[FormOption]
    min_items: Annotated[int | None, Field(alias="minItems", ge=0, le=9007199254740991)] = None
    max_items: Annotated[int | None, Field(alias="maxItems", ge=0, le=9007199254740991)] = None
    custom: bool | None = None
    default: list[str] | None = None


class FormMultiselectField1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: str
    title: str | None = None
    description: str | None = None
    required: bool | None = None
    hidden: bool | None = None
    when: list[FormWhen1] | None = None
    type: Literal["multiselect"] = "multiselect"
    options: list[FormOption]
    min_items: Annotated[int | None, Field(alias="minItems", ge=0, le=9007199254740991)] = None
    max_items: Annotated[int | None, Field(alias="maxItems", ge=0, le=9007199254740991)] = None
    custom: bool | None = None
    default: list[str] | None = None


class FormNumberField(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: str
    title: str | None = None
    description: str | None = None
    required: bool | None = None
    hidden: bool | None = None
    when: list[FormWhen] | None = None
    type: Literal["number"] = "number"
    minimum: float | Literal["Infinity", "-Infinity", "NaN"] | None = None
    maximum: float | Literal["Infinity", "-Infinity", "NaN"] | None = None
    default: float | Literal["Infinity", "-Infinity", "NaN"] | None = None


class FormNumberField1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: str
    title: str | None = None
    description: str | None = None
    required: bool | None = None
    hidden: bool | None = None
    when: list[FormWhen1] | None = None
    type: Literal["number"] = "number"
    minimum: float | Literal["NaN", "Infinity", "-Infinity"] | None = None
    maximum: float | Literal["NaN", "Infinity", "-Infinity"] | None = None
    default: float | Literal["NaN", "Infinity", "-Infinity"] | None = None


class FormRepliedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^frm_")]
    session_id: Annotated[str, Field(alias="sessionID")]
    answer: dict[str, str | float | Literal["NaN", "Infinity", "-Infinity"] | bool | list[str]]


class FormReply(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    answer: dict[str, str | float | Literal["Infinity", "-Infinity", "NaN"] | bool | list[str]]


class FormStateVariant1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["answered"] = "answered"
    answer: dict[str, str | float | Literal["Infinity", "-Infinity", "NaN"] | bool | list[str]]


class FormStringField(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: str
    title: str | None = None
    description: str | None = None
    required: bool | None = None
    hidden: bool | None = None
    when: list[FormWhen] | None = None
    type: Literal["string"] = "string"
    format: Literal["email", "uri", "date", "date-time"] | None = None
    min_length: Annotated[int | None, Field(alias="minLength", ge=0, le=9007199254740991)] = None
    max_length: Annotated[int | None, Field(alias="maxLength", ge=0, le=9007199254740991)] = None
    pattern: str | None = None
    placeholder: str | None = None
    default: str | None = None
    options: list[FormOption] | None = None
    custom: bool | None = None


class FormStringField1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: str
    title: str | None = None
    description: str | None = None
    required: bool | None = None
    hidden: bool | None = None
    when: list[FormWhen1] | None = None
    type: Literal["string"] = "string"
    format: Literal["email", "uri", "date", "date-time"] | None = None
    min_length: Annotated[int | None, Field(alias="minLength", ge=0, le=9007199254740991)] = None
    max_length: Annotated[int | None, Field(alias="maxLength", ge=0, le=9007199254740991)] = None
    pattern: str | None = None
    placeholder: str | None = None
    default: str | None = None
    options: list[FormOption] | None = None
    custom: bool | None = None


class FsFindResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[FileSystemEntry]


class FsListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[FileSystemEntry]


class GenerateChoiceAnswerProbabilitiesItem(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    value: str | bool
    '\n    Choice values are typed: true and "true" are different values\n    '
    probability: Annotated[float, Field(ge=0.0, le=1.0)]


class GenerateChoiceQuestionChoicesItem(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    value: str | bool
    '\n    Choice values are typed: true and "true" are different values\n    '
    description: str | None = None


class GenerateImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    model: ModelRef
    prompt: str
    images: list[GenerateFile] | None = None
    n: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None
    aspect: Annotated[
        str | None,
        Field(
            pattern="^[+-]?(?:\\d+(?:\\.\\d+)?|\\.\\d+)(?:[Ee][+-]?\\d+)?:[+-]?(?:\\d+(?:\\.\\d+)?|\\.\\d+)(?:[Ee][+-]?\\d+)?$"
        ),
    ] = None
    "\n    Aspect ratio as width:height, for example 16:9\n    "
    resolution: Literal["1K", "2K", "4K"] | str | None = None
    "\n    Size tier such as 1K, 2K or 4K; 1K is about as many pixels as 1024x1024 at the requested aspect\n    "


class GenerateImageResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    images: list[GenerateImageOutput]
    cost: float | None = None
    tokens: GenerateUsage | None = None
    native: dict[str, Any] | None = None


class GeneratePredicateQuestion(BaseModel):
    """
    The probability that a statement is true
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["predicate"] = "predicate"
    name: str
    "\n    Unique within the request; echoed on the matching answer\n    "
    instructions: str
    criteria: GeneratePredicateQuestionCriteria | None = None


class GenerateScoreAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["score"] = "score"
    name: str
    score: float
    "\n    Probability-weighted level index, so it can fall between levels\n    "
    probabilities: list[GenerateScoreAnswerProbabilitiesItem]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]


class GenerateScoreQuestion(BaseModel):
    """
    Place the input on ordered levels, lowest first
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["score"] = "score"
    name: str
    "\n    Unique within the request; echoed on the matching answer\n    "
    instructions: str
    levels: Annotated[list[GenerateScoreQuestionLevelsItem], Field(min_length=1)]


class GenerateTextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: GenerateTextResponseData


class GenerateVideoCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: GenerateVideoRunning


class GenerateVideoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    model: ModelRef
    prompt: str
    start: GenerateImageFile | None = None
    end: GenerateImageFile | None = None
    references: list[GenerateFile] | None = None
    duration: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None
    aspect: str | Literal["auto"] | None = None
    "\n    Aspect ratio as width:height, or auto to follow the start image\n    "
    resolution: Literal["480p", "720p", "1080p", "4k"] | str | None = None
    sound: bool | None = None


class InstallationUpdateAvailable(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["installation.update-available"] = "installation.update-available"
    location: LocationRef | None = None
    data: InstallationUpdateAvailableData


class InstallationUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["installation.updated"] = "installation.updated"
    location: LocationRef | None = None
    data: InstallationUpdatedData


class IntegrationAttemptEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    attempt_id: Annotated[str, Field(alias="attemptID")]
    url: str
    instructions: str
    mode: Literal["auto", "code"]
    paste: Literal[True] | None = None
    time: IntegrationAttemptEncodedTime


class IntegrationAttemptStatusVariant0(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["pending"] = "pending"
    time: IntegrationAttemptStatusVariant0Time


class IntegrationAttemptStatusVariant1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["complete"] = "complete"
    time: IntegrationAttemptStatusVariant1Time


class IntegrationAttemptStatusVariant2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["failed"] = "failed"
    message: str
    time: IntegrationAttemptStatusVariant2Time


class IntegrationAttemptStatusVariant3(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["expired"] = "expired"
    time: IntegrationAttemptStatusVariant3Time


class IntegrationCommandAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    attempt_id: Annotated[str, Field(alias="attemptID")]
    time: IntegrationCommandAttemptTime


class IntegrationCommandAttemptStatusVariant0(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["pending"] = "pending"
    message: str | None = None
    time: IntegrationCommandAttemptStatusVariant0Time


class IntegrationCommandAttemptStatusVariant1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["complete"] = "complete"
    time: IntegrationCommandAttemptStatusVariant1Time


class IntegrationCommandAttemptStatusVariant2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["failed"] = "failed"
    message: str
    time: IntegrationCommandAttemptStatusVariant2Time


class IntegrationCommandAttemptStatusVariant3(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["expired"] = "expired"
    time: IntegrationCommandAttemptStatusVariant3Time


class IntegrationCommandConnectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: IntegrationCommandAttempt


class IntegrationConnectExternalBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    method_id: Annotated[str, Field(alias="methodID")]
    answer: (
        dict[str, str | float | Literal["Infinity", "-Infinity", "NaN"] | bool | list[str]] | None
    ) = None
    label: str | None = None


class IntegrationConnectKeyBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: str
    answer: (
        dict[str, str | float | Literal["Infinity", "-Infinity", "NaN"] | bool | list[str]] | None
    ) = None
    label: str | None = None


class IntegrationOauthConnectBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    method_id: Annotated[str, Field(alias="methodID")]
    answer: (
        dict[str, str | float | Literal["Infinity", "-Infinity", "NaN"] | bool | list[str]] | None
    ) = None
    label: str | None = None


class IntegrationOauthConnectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: IntegrationAttemptEncoded


class IntegrationUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["integration.updated"] = "integration.updated"
    location: LocationRef | None = None
    data: Any


class JobCodeMode(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    kind: Literal["codemode"] = "codemode"
    id: str
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    status: Literal["running", "completed", "error", "cancelled"]
    title: str | None = None
    time: JobTime


class JobImage(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    kind: Literal["image"] = "image"
    id: str
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    status: Literal["running", "completed", "error", "cancelled"]
    title: str | None = None
    time: JobTime
    path: str
    model: str


class JobShell(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    kind: Literal["shell"] = "shell"
    id: str
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    status: Literal["running", "completed", "error", "cancelled"]
    title: str | None = None
    time: JobTime
    shell_id: Annotated[str, Field(alias="shellID")]
    command: str


class JobSubagent(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    kind: Literal["subagent"] = "subagent"
    id: str
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    status: Literal["running", "completed", "error", "cancelled"]
    title: str | None = None
    time: JobTime
    child_session_id: Annotated[str, Field(alias="childSessionID", pattern="^ses")]
    agent: str
    description: str


class LocationNotFoundErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["LocationNotFoundError"], Field(alias="_tag")] = (
        "LocationNotFoundError"
    )
    location: LocationPublicRef
    message: str


class LocationPublicInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    directory: str
    project: LocationPublicInfoProject


class MachineAddPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str
    reach: list[MachineReach]
    replace: bool | None = None


class MachineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    online: bool
    paired: bool
    os: str | None = None
    arch: str | None = None
    connection: MachineInfoConnection | None = None


class MachineInvite(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str
    join: str
    expires: Annotated[int, Field(ge=-9007199254740991, le=9007199254740991)]
    reach: list[Annotated[MachineReach | MachineInviteReachItemIroh, Field(discriminator="type")]]


class McpLocalConfigEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["local"] = "local"
    command: list[str]
    cwd: str | None = None
    environment: dict[str, str] | None = None
    disabled: bool | None = None
    codemode: bool | None = None
    timeout: McpLocalConfigEncodedTimeout | None = None
    protocol: Literal["legacy", "auto", "2026-07-28"] | None = None
    '\n    MCP protocol negotiation. "auto" (default) probes for the 2026-07-28 revision and falls back to legacy when the server does not support it. "legacy" opens with the initialize handshake and speaks protocol revisions up to 2025-11-25. "2026-07-28" requires that revision and fails otherwise.\n    '
    pool: bool | None = None


class McpRemoteConfigEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["remote"] = "remote"
    url: str
    headers: dict[str, str] | None = None
    oauth: McpOAuthConfigEncoded | Literal[False] | None = None
    disabled: bool | None = None
    codemode: bool | None = None
    timeout: McpRemoteConfigEncodedTimeout | None = None
    protocol: Literal["legacy", "auto", "2026-07-28"] | None = None
    '\n    MCP protocol negotiation. "auto" (default) probes for the 2026-07-28 revision and falls back to legacy when the server does not support it. "legacy" opens with the initialize handshake and speaks protocol revisions up to 2025-11-25. "2026-07-28" requires that revision and fails otherwise.\n    '
    pool: bool | None = None


class McpResourceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    resources: list[McpResource]
    templates: list[McpResourceTemplate]


class McpResourceCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: McpResourceCatalog


class McpResourcesChanged(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["mcp.resources.changed"] = "mcp.resources.changed"
    location: LocationRef | None = None
    data: McpResourcesChangedData


class McpServer(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str
    status: (
        McpStatusConnected
        | McpStatusPending
        | McpStatusDisabled
        | McpStatusFailed
        | McpStatusNeedsAuth
    )
    title: str | None = None
    integration_id: Annotated[str | None, Field(alias="integrationID")] = None


class McpStatusChanged(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["mcp.status.changed"] = "mcp.status.changed"
    location: LocationRef | None = None
    data: McpStatusChangedData


class ModelCompatibility(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    reasoning_field: Annotated[
        Literal["reasoning", "reasoning_content", "reasoning_text"] | str | None,
        Field(alias="reasoningField"),
    ] = None
    require_reasoning: Annotated[bool | None, Field(alias="requireReasoning")] = None
    max_tokens_field: Annotated[
        Literal["max_completion_tokens", "max_tokens"] | None, Field(alias="maxTokensField")
    ] = None
    require_finish_reason: Annotated[bool | None, Field(alias="requireFinishReason")] = None
    require_assistant_after_tool: Annotated[
        bool | None, Field(alias="requireAssistantAfterTool")
    ] = None
    supports_prompt_cache_key: Annotated[bool | None, Field(alias="supportsPromptCacheKey")] = None
    stream_usage: Annotated[bool | None, Field(alias="streamUsage")] = None
    cache_format: Annotated[Literal["anthropic", "alibaba"] | None, Field(alias="cacheFormat")] = (
        None
    )
    supports_thinking_block_binding: Annotated[
        bool | None, Field(alias="supportsThinkingBlockBinding")
    ] = None
    supports_effort_updates: Annotated[bool | None, Field(alias="supportsEffortUpdates")] = None
    media_input: Annotated[Literal["assets", "state", "root"] | None, Field(alias="mediaInput")] = (
        None
    )


class ModelCost(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    tier: ModelCostTier | None = None
    input: float
    output: float
    cache: ModelCostCache


class ModelInfoExperimental(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    speed: ModelSpeed | None = None


class PermissionReplied(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["permission.replied"] = "permission.replied"
    location: LocationRef | None = None
    data: PermissionRepliedData


class PermissionSavedInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    project_id: Annotated[str, Field(alias="projectID")]
    action: str
    resource: str
    time: PermissionSavedInfoTime


class PermissionSavedListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: list[PermissionSavedInfo]


class PermissionSavedUpdateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: PermissionSavedInfo


class PersistentPtyCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    command: str | None = None
    args: list[str]
    cwd: str | None = None
    title: str
    env: dict[str, str]
    size: PersistentPtyCreateInputSize | None = None


class PersistentPtyInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^pty")]
    title: str
    command: str
    args: list[str]
    cwd: str
    status: Literal["running", "exited"]
    pid: Annotated[int, Field(ge=0, le=9007199254740991)]
    exit_code: Annotated[int | None, Field(alias="exitCode", ge=0, le=9007199254740991)] = None
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    user_title: Annotated[str | None, Field(alias="userTitle")] = None
    foreground_process: Annotated[str | None, Field(alias="foregroundProcess")] = None
    size: PersistentPtyInfoSize
    output: PersistentPtyInfoOutput


class PersistentPtyReadResultScreen(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    text: str
    cols: Annotated[int, Field(gt=0, le=9007199254740991)]
    rows: Annotated[int, Field(gt=0, le=9007199254740991)]
    cursor: PersistentPtyReadResultScreenCursor


class PersistentPtyRemoved(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["persistent-pty.removed"] = "persistent-pty.removed"
    location: LocationRef | None = None
    data: PersistentPtyRemovedData


class PersistentPtySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    info: PersistentPtyInfo
    text: str
    checkpoint: str
    cursor: PersistentPtySnapshotCursor


class PersistentPtyUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    attachment_id: Annotated[str | None, Field(alias="attachmentID")] = None
    size: PersistentPtyUpdateInputSize | None = None
    title: str | None = None


class PersistentPtyUpdatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    terminal: PersistentPtyInfo


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    canonical: str
    vcs: Annotated[str | None, Field(pattern="^[a-z][a-z0-9._-]*$")] = None
    name: str | None = None
    icon: ProjectIcon | None = None
    commands: ProjectCommands | None = None
    time: ProjectTime
    sandboxes: list[str]


class ProjectUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["project.updated"] = "project.updated"
    location: LocationRef | None = None
    data: ProjectUpdatedData


class PromptAgentAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str
    mention: PromptMention | None = None


class PromptInputFileAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    uri: str
    source: Annotated[
        PromptFileSourceInline | PromptFileSourceUri | None, Field(discriminator="type")
    ] = None
    name: str | None = None
    description: str | None = None
    mention: PromptMention | None = None


class PromptInputSkillAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    mention: PromptMention | None = None


class PtyConnectTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: PtyTicketConnectToken


class PtyCreated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["pty.created"] = "pty.created"
    location: LocationRef | None = None
    data: PtyCreatedData


class PtyDeleted(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["pty.deleted"] = "pty.deleted"
    location: LocationRef | None = None
    data: PtyDeletedData


class PtyExited(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["pty.exited"] = "pty.exited"
    location: LocationRef | None = None
    data: PtyExitedData


class PtyUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    title: str | None = None
    size: PtyUpdateBodySize | None = None


class PtyUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["pty.updated"] = "pty.updated"
    location: LocationRef | None = None
    data: PtyUpdatedData


class ReferenceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str
    path: str
    description: str | None = None
    hidden: bool | None = None
    source: Annotated[ReferenceLocalSource | ReferenceGitSource, Field(discriminator="type")]


class ReferenceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[ReferenceInfo]


class ServerInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    version: str
    pid: Annotated[int, Field(ge=0, le=9007199254740991)]
    hostname: str | None = None
    urls: list[str]
    paths: ServerInfoPaths
    capabilities: ServerInfoCapabilitiesVariant0 | None = None


class SessionAgentSelected(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.agent.selected"] = "session.agent.selected"
    durable: SessionAgentSelectedDurable
    location: LocationRef | None = None
    data: SessionAgentSelectedData


class SessionArchived(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.archived"] = "session.archived"
    durable: SessionArchivedDurable
    location: LocationRef | None = None
    data: SessionArchivedData


class SessionCommandBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    name: str
    text: str
    files: list[PromptInputFileAttachment] | None = None
    agents: list[PromptAgentAttachment] | None = None
    skills: list[PromptInputSkillAttachment] | None = None
    delivery: Literal["steer", "queue"] | None = None


class SessionCompactionDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.compaction.delta"] = "session.compaction.delta"
    location: LocationRef | None = None
    data: SessionCompactionDeltaData


class SessionCompactionStarted(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.compaction.started"] = "session.compaction.started"
    durable: SessionCompactionStartedDurable
    location: LocationRef | None = None
    data: SessionCompactionStartedData


class SessionCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str | None = None
    parent_id: Annotated[str | None, Field(alias="parentID")] = None
    "\n    Set parentID to create a subagent session at the parent's location; set agent to choose its agent (otherwise it uses the default) and start it with session.prompt. After each completed round, the child sends a `<background-result kind=\"subagent\">` report to the parent's inbox and wakes the parent unless it has a staged revert.\n    "
    title: str | None = None
    agent: str | None = None
    model: ModelRef | None = None
    location: LocationPublicRef | None = None
    metadata: dict[str, Any] | None = None
    permissions: list[PermissionRule] | None = None


class SessionCreatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    project_id: Annotated[str, Field(alias="projectID")]
    location: LocationRef
    subpath: str | None = None
    parent_id: Annotated[str | None, Field(alias="parentID", pattern="^ses")] = None
    slug: str
    title: str | None = None
    agent: str | None = None
    model: ModelRef | None = None
    metadata: dict[str, Any] | None = None
    permissions: list[PermissionRule] | None = None
    version: str


class SessionDeleted(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.deleted"] = "session.deleted"
    durable: SessionDeletedDurable
    location: LocationRef | None = None
    data: SessionDeletedData


class SessionExecutionInterrupted(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.execution.interrupted"] = "session.execution.interrupted"
    durable: SessionExecutionInterruptedDurable
    location: LocationRef | None = None
    data: SessionExecutionInterruptedData


class SessionExecutionStarted(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.execution.started"] = "session.execution.started"
    durable: SessionExecutionStartedDurable
    location: LocationRef | None = None
    data: SessionExecutionStartedData


class SessionExecutionSucceeded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.execution.succeeded"] = "session.execution.succeeded"
    durable: SessionExecutionSucceededDurable
    location: LocationRef | None = None
    data: SessionExecutionSucceededData


class SessionForkedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    parent_id: Annotated[str, Field(alias="parentID", pattern="^ses")]
    boundary: Annotated[
        SessionForkBoundaryBefore | SessionForkBoundaryThrough, Field(discriminator="type")
    ]
    instructions: dict[str, str] | None = None
    instruction_entries: Annotated[
        list[InstructionEntrySnapshotItem] | None, Field(alias="instructionEntries")
    ] = None


class SessionIdle(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.idle"] = "session.idle"
    location: LocationRef | None = None
    data: SessionIdleData


class SessionInboxCancelled(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.inbox.cancelled"] = "session.inbox.cancelled"
    durable: SessionInboxCancelledDurable
    location: LocationRef | None = None
    data: SessionInboxCancelledData


class SessionInboxCompaction(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^msg_")]
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    time: SessionInboxCompactionTime
    type: Literal["compaction"] = "compaction"
    payload: Any
    delivery: Literal["steer", "queue"]


class SessionInboxDelivered(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.inbox.delivered"] = "session.inbox.delivered"
    durable: SessionInboxDeliveredDurable
    location: LocationRef | None = None
    data: SessionInboxDeliveredData


class SessionInboxDeliveryChanged(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.inbox.delivery.changed"] = "session.inbox.delivery.changed"
    durable: SessionInboxDeliveryChangedDurable
    location: LocationRef | None = None
    data: SessionInboxDeliveryChangedData


class SessionInboxItemMove(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["move"] = "move"
    payload: SessionInboxMovePayload1
    delivery: Literal["steer", "queue"]


class SessionInboxItemSynthetic(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["synthetic"] = "synthetic"
    payload: SessionInboxSyntheticPayload
    delivery: Literal["steer", "queue"]


class SessionInboxMove(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^msg_")]
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    time: SessionInboxMoveTime
    type: Literal["move"] = "move"
    delivery: Literal["steer", "queue"]
    payload: SessionInboxMovePayload


class SessionInboxSynthetic(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^msg_")]
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    time: SessionInboxSyntheticTime
    type: Literal["synthetic"] = "synthetic"
    payload: SessionInboxSyntheticPayload
    delivery: Literal["steer", "queue"]


class SessionInfoFork(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    boundary: Annotated[
        SessionForkBoundaryBefore | SessionForkBoundaryThrough, Field(discriminator="type")
    ]


class SessionInstructionsUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.instructions.updated"] = "session.instructions.updated"
    durable: SessionInstructionsUpdatedDurable
    location: LocationRef | None = None
    data: SessionInstructionsUpdatedData


class SessionMessageAgentSelected(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^msg_")]
    metadata: dict[str, Any] | None = None
    time: SessionMessageAgentSelectedTime
    type: Literal["agent-switched"] = "agent-switched"
    agent: str
    previous: str | None = None


class SessionMessageAssistantReasoning(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["reasoning"] = "reasoning"
    text: str
    native: dict[str, Any] | None = None
    time: SessionMessageAssistantReasoningTime | None = None


class SessionMessageAssistantText(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["text"] = "text"
    text: str
    native: dict[str, Any] | None = None


class SessionMessageCompactionRunning(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["compaction"] = "compaction"
    id: Annotated[str, Field(pattern="^msg_")]
    metadata: dict[str, Any] | None = None
    time: SessionMessageCompactionRunningTime
    status: Literal["running"] = "running"
    reason: Literal["auto", "manual"]
    summary: str


class SessionMessageLocationSwitched(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^msg_")]
    metadata: dict[str, Any] | None = None
    time: SessionMessageLocationSwitchedTime
    type: Literal["location-switched"] = "location-switched"
    project_id: Annotated[str | None, Field(alias="projectID")] = None
    subpath: str | None = None
    location: LocationPublicRef
    previous: SessionMessageLocationSwitchedPreviousVariant0 | None = None


class SessionMessageModelSelected(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^msg_")]
    metadata: dict[str, Any] | None = None
    time: SessionMessageModelSelectedTime
    type: Literal["model-switched"] = "model-switched"
    model: ModelRef
    previous: ModelRef | None = None


class SessionMessageShell(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^msg_")]
    metadata: dict[str, Any] | None = None
    time: SessionMessageShellTime
    type: Literal["shell"] = "shell"
    shell_id: Annotated[str, Field(alias="shellID", pattern="^sh_")]
    command: str
    status: Literal["running", "exited", "timeout", "killed"]
    exit: float | Literal["Infinity", "-Infinity", "NaN"] | None = None
    output: SessionMessageShellOutput | None = None


class SessionMessageSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^msg_")]
    metadata: dict[str, Any] | None = None
    time: SessionMessageSkillTime
    type: Literal["skill"] = "skill"
    skill: str
    name: str
    text: str


class SessionMessageSynthetic(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^msg_")]
    metadata: dict[str, Any] | None = None
    time: SessionMessageSyntheticTime
    text: str
    description: str | None = None
    type: Literal["synthetic"] = "synthetic"


class SessionMessageSystem(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^msg_")]
    metadata: dict[str, Any] | None = None
    time: SessionMessageSystemTime
    type: Literal["system"] = "system"
    text: str
    description: str | None = None


class SessionMetadataUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.metadata.updated"] = "session.metadata.updated"
    durable: SessionMetadataUpdatedDurable
    location: LocationRef | None = None
    data: SessionMetadataUpdatedData


class SessionModelSelected(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.model.selected"] = "session.model.selected"
    durable: SessionModelSelectedDurable
    location: LocationRef | None = None
    data: SessionModelSelectedData


class SessionMoved(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.moved"] = "session.moved"
    durable: SessionMovedDurable
    location: LocationRef | None = None
    data: SessionMovedData


class SessionPermissionCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str | None = None
    action: str
    resources: list[str]
    save: list[str] | None = None
    metadata: dict[str, Any] | None = None
    source: PermissionSourceTool | None = None
    agent: str | None = None


class SessionPermissionCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: SessionPermissionCreateResponseData


class SessionPermissions(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.permissions"] = "session.permissions"
    durable: SessionPermissionsDurable
    location: LocationRef | None = None
    data: SessionPermissionsData


class SessionPromptBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str | None = None
    text: str
    files: list[PromptInputFileAttachment] | None = None
    agents: list[PromptAgentAttachment] | None = None
    skills: list[PromptInputSkillAttachment] | None = None
    metadata: dict[str, Any] | None = None
    delivery: Literal["steer", "queue"] | None = None
    resume: bool | None = None


class SessionReasoningDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.reasoning.delta"] = "session.reasoning.delta"
    location: LocationRef | None = None
    data: SessionReasoningDeltaData


class SessionReasoningEnded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.reasoning.ended"] = "session.reasoning.ended"
    durable: SessionReasoningEndedDurable
    location: LocationRef | None = None
    data: SessionReasoningEndedData


class SessionReasoningStarted(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.reasoning.started"] = "session.reasoning.started"
    durable: SessionReasoningStartedDurable
    location: LocationRef | None = None
    data: SessionReasoningStartedData


class SessionRenamed(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.renamed"] = "session.renamed"
    durable: SessionRenamedDurable
    location: LocationRef | None = None
    data: SessionRenamedData


class SessionRevertCleared(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.revert.cleared"] = "session.revert.cleared"
    durable: SessionRevertClearedDurable
    location: LocationRef | None = None
    data: SessionRevertClearedData


class SessionRevertCommitted(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.revert.committed"] = "session.revert.committed"
    durable: SessionRevertCommittedDurable
    location: LocationRef | None = None
    data: SessionRevertCommittedData


class SessionRevertStaged(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.revert.staged"] = "session.revert.staged"
    durable: SessionRevertStagedDurable
    location: LocationRef | None = None
    data: SessionRevertStagedData


class SessionSkillActivated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.skill.activated"] = "session.skill.activated"
    durable: SessionSkillActivatedDurable
    location: LocationRef | None = None
    data: SessionSkillActivatedData


class SessionStatusRetry(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["retry"] = "retry"
    attempt: Annotated[int, Field(ge=0, le=9007199254740991)]
    message: str
    action: SessionStatusRetryAction | None = None
    next: Annotated[int, Field(ge=0, le=9007199254740991)]


class SessionStepFirstToken(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.step.first-token"] = "session.step.first-token"
    durable: SessionStepFirstTokenDurable
    location: LocationRef | None = None
    data: SessionStepFirstTokenData


class SessionStepStarted(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.step.started"] = "session.step.started"
    durable: SessionStepStartedDurable
    location: LocationRef | None = None
    data: SessionStepStartedData


class SessionStepStreamed(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.step.streamed"] = "session.step.streamed"
    durable: SessionStepStreamedDurable
    location: LocationRef | None = None
    data: SessionStepStreamedData


class SessionStructuredError(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: str
    message: str
    status: Annotated[int | None, Field(ge=100, le=599)] = None
    response: SessionStructuredErrorResponse | None = None
    retry_after_ms: Annotated[
        int | None, Field(alias="retryAfterMs", ge=0, le=9007199254740991)
    ] = None


class SessionSynthetic(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.synthetic"] = "session.synthetic"
    durable: SessionSyntheticDurable
    location: LocationRef | None = None
    data: SessionSyntheticData


class SessionSyntheticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: SessionInboxSynthetic


class SessionTextDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.text.delta"] = "session.text.delta"
    location: LocationRef | None = None
    data: SessionTextDeltaData


class SessionTextEnded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.text.ended"] = "session.text.ended"
    durable: SessionTextEndedDurable
    location: LocationRef | None = None
    data: SessionTextEndedData


class SessionTextStarted(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.text.started"] = "session.text.started"
    durable: SessionTextStartedDurable
    location: LocationRef | None = None
    data: SessionTextStartedData


class SessionToolCalled(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.tool.called"] = "session.tool.called"
    durable: SessionToolCalledDurable
    location: LocationRef | None = None
    data: SessionToolCalledData


class SessionToolInputDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.tool.input.delta"] = "session.tool.input.delta"
    location: LocationRef | None = None
    data: SessionToolInputDeltaData


class SessionToolInputEnded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.tool.input.ended"] = "session.tool.input.ended"
    durable: SessionToolInputEndedDurable
    location: LocationRef | None = None
    data: SessionToolInputEndedData


class SessionToolInputStarted(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.tool.input.started"] = "session.tool.input.started"
    durable: SessionToolInputStartedDurable
    location: LocationRef | None = None
    data: SessionToolInputStartedData


class SessionToolProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.tool.progress"] = "session.tool.progress"
    location: LocationRef | None = None
    data: SessionToolProgressData


class SessionUnarchived(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.unarchived"] = "session.unarchived"
    durable: SessionUnarchivedDurable
    location: LocationRef | None = None
    data: SessionUnarchivedData


class SessionViewed(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.viewed"] = "session.viewed"
    durable: SessionViewedDurable
    location: LocationRef | None = None
    data: SessionViewedData


class ShellDeleted(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["shell.deleted"] = "shell.deleted"
    location: LocationRef | None = None
    data: ShellDeletedData


class ShellExited(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["shell.exited"] = "shell.exited"
    location: LocationRef | None = None
    data: ShellExitedData


class ShellInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^sh_")]
    status: Literal["running", "exited", "timeout", "killed"]
    command: str
    cwd: str
    shell: str
    file: str
    pid: Annotated[int | None, Field(ge=0, le=9007199254740991)] = None
    exit: float | None = None
    signal: str | None = None
    metadata: dict[str, Any]
    time: ShellInfoTime


class ShellListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[ShellInfo]


class ShellOutputResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: ShellOutputResponseData


class TokenUsageInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    input: float
    output: float
    reasoning: float
    cache: TokenUsageInfoCache


class TuiCommandExecute(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["tui.command.execute"] = "tui.command.execute"
    location: LocationRef | None = None
    data: TuiCommandExecuteData


class TuiPromptAppend(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["tui.prompt.append"] = "tui.prompt.append"
    location: LocationRef | None = None
    data: TuiPromptAppendData


class TuiSessionSelect(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["tui.session.select"] = "tui.session.select"
    location: LocationRef | None = None
    data: TuiSessionSelectData


class TuiToastShow(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["tui.toast.show"] = "tui.toast.show"
    location: LocationRef | None = None
    data: TuiToastShowData


class UsageLimitsBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    limit: float
    spent: float
    scope: Literal["each", "combined"]
    per: Literal["set", "model"]
    coverage: Literal["all", "models"]
    models: list[UsageLimitsBudgetModel]
    vendors: list[str]
    input_price_at_least: Annotated[float | None, Field(alias="inputPriceAtLeast")] = None
    picked: bool
    shared: bool
    resets_at: Annotated[float, Field(alias="resetsAt")]
    "\n    Milliseconds since the Unix epoch\n    "


class UsageLimitsGo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    rolling: UsageLimitsMeter
    weekly: UsageLimitsMeter
    monthly: UsageLimitsMeter


class UsageLimitsInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    workspace: UsageLimitsInfoWorkspace
    go: UsageLimitsGo | None = None
    budgets: list[UsageLimitsBudget]


class VcsBranchUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["vcs.branch.updated"] = "vcs.branch.updated"
    location: LocationRef | None = None
    data: VcsBranchUpdatedData


class VcsGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: VcsInfo


class VoiceModelsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: VoiceModelsResponseData


class WebSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    url: str
    title: str | None = None
    content: str | None = None
    time: WebSearchResultTime


class WorktreeErrorEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_tag: Annotated[Literal["WorktreeError"], Field(alias="_tag")] = "WorktreeError"
    name: Literal["WorktreeError"] = "WorktreeError"
    data: WorktreeErrorEncodedData


class WorktreeResolved(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["worktree.resolved"] = "worktree.resolved"
    durable: WorktreeResolvedDurable
    location: LocationRef | None = None
    data: WorktreeResolvedData


class WorktreeUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["worktree.updated"] = "worktree.updated"
    location: LocationRef | None = None
    data: WorktreeUpdatedData


class ArtifactContent(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    info: ArtifactInfo
    files: dict[str, str]


class AutomationActionPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["prompt"] = "prompt"
    prompt: str
    to: AutomationTargetVariant0 | AutomationTargetVariant1


class AutomationPromptActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["prompt"] = "prompt"
    prompt: Annotated[str, Field(min_length=1)]
    "\n    The message the target Session receives, with enough context to act on it without this conversation.\n    "
    to: AutomationTargetVariant0 | AutomationTargetVariant1 | None = None


class ConfigInfoEncodedExperimental(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    portable_shell_scanner: bool | None = None
    codemode_vm: bool | None = None
    all_flags: bool | None = None
    todo: bool | None = None
    media_blob: bool | None = None
    artifacts: bool | None = None
    console_watches: bool | None = None
    recommended_api: bool | None = None
    model_speeds: bool | None = None
    full_codemode: bool | None = None
    machines: bool | None = None
    home_memory: bool | None = None
    project_overlay: bool | None = None
    opencode_api: bool | None = None
    sqlite_fts: bool | None = None
    video_generate: bool | ConfigInfoEncodedExperimentalVideoGenerateVariant1 | None = None
    part_rows: bool | None = None
    image_generate: bool | ConfigInfoEncodedExperimentalImageGenerateVariant1 | None = None
    subagent_depth: Annotated[int | None, Field(ge=0, le=9007199254740991)] = None
    location_idle_timeout: str | Literal[False] | None = None
    policies: list[ConfigInfoEncodedExperimentalPoliciesItem] | None = None


class ConfigInfoEncodedMcp(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    timeout: ConfigInfoEncodedMcpTimeout | None = None
    servers: dict[str, McpLocalConfigEncoded | McpRemoteConfigEncoded] | None = None


class ConfigModelVariantSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    compaction: Annotated[
        ProviderCompactionSummary | ProviderCompactionNative | None, Field(discriminator="type")
    ] = None
    encoding: Literal["zstd", "gzip", "none"] | None = None


class CredentialCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: CredentialEntry


class ExperimentalArtifactsGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: ArtifactContent


class ExperimentalMachineAddResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: MachineInvite


class ExperimentalMachineListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: list[MachineInfo]


class ExperimentalMcpAddBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    config: Annotated[McpLocalConfigEncoded | McpRemoteConfigEncoded, Field(discriminator="type")]


class ExperimentalPersistentPtyCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: PersistentPtyInfo


class ExperimentalPersistentPtyGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: PersistentPtyInfo


class ExperimentalPersistentPtyListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: list[PersistentPtyInfo]


class ExperimentalPersistentPtySnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: PersistentPtySnapshot


class ExperimentalPersistentPtyUpdateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: PersistentPtyInfo


class ExperimentalUsageLimitsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: UsageLimitsInfo | None = None


class ExperimentalVoiceSessionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    model: str
    session: dict[str, Any] | None = None
    transport: Annotated[
        VoiceWebRTCTransportInput | VoiceWebSocketTransportInput, Field(discriminator="type")
    ]


class FormInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^frm_")]
    session_id: Annotated[str, Field(alias="sessionID")]
    title: str
    metadata: dict[str, Any] | None = None
    fields: Annotated[
        list[
            Annotated[
                FormStringField
                | FormNumberField
                | FormIntegerField
                | FormBooleanField
                | FormMultiselectField
                | FormExternalField,
                Field(discriminator="type"),
            ]
        ],
        Field(min_length=1),
    ]


class FormInfo1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^frm_")]
    session_id: Annotated[str, Field(alias="sessionID")]
    title: str
    metadata: dict[str, Any] | None = None
    fields: Annotated[
        list[
            Annotated[
                FormStringField1
                | FormNumberField1
                | FormIntegerField1
                | FormBooleanField1
                | FormMultiselectField1
                | FormExternalField,
                Field(discriminator="type"),
            ]
        ],
        Field(min_length=1),
    ]


class FormListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[FormInfo]


class FormReplied(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["form.replied"] = "form.replied"
    location: LocationRef | None = None
    data: FormRepliedData


class GenerateChoiceAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["choice"] = "choice"
    name: str
    choice: str | bool
    '\n    Choice values are typed: true and "true" are different values\n    '
    probabilities: list[GenerateChoiceAnswerProbabilitiesItem]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]


class GenerateChoiceQuestion(BaseModel):
    """
    Pick one value from a fixed set
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["choice"] = "choice"
    name: str
    "\n    Unique within the request; echoed on the matching answer\n    "
    instructions: str
    choices: Annotated[list[GenerateChoiceQuestionChoicesItem], Field(min_length=1)]


class GenerateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    model: ModelRef
    input: list[
        Annotated[
            GenerateDecisionTextPart | GenerateDecisionJsonPart | GenerateDecisionFilePart,
            Field(discriminator="type"),
        ]
    ]
    "\n    The evidence every question is asked about, in order\n    "
    questions: Annotated[
        list[
            Annotated[
                GeneratePredicateQuestion | GenerateChoiceQuestion | GenerateScoreQuestion,
                Field(discriminator="type"),
            ]
        ],
        Field(min_length=1),
    ]
    "\n    Question names must be unique; duplicates fail with InvalidRequestError\n    "


class GenerateDecisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    answers: list[
        Annotated[
            GeneratePredicateAnswer
            | GenerateChoiceAnswer
            | GenerateScoreAnswer
            | GenerateErrorAnswer,
            Field(discriminator="type"),
        ]
    ]
    "\n    One answer per question, in question order\n    "
    cost: float | None = None
    tokens: GenerateUsage | None = None
    native: dict[str, Any] | None = None


class GenerateDecisionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: GenerateDecisionResult


class GenerateImageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: GenerateImageResult


class GenerateVideoGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: GenerateVideoRunning | GenerateVideoCompleted | GenerateVideoError


class IntegrationCommandStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: (
        IntegrationCommandAttemptStatusVariant0
        | IntegrationCommandAttemptStatusVariant1
        | IntegrationCommandAttemptStatusVariant2
        | IntegrationCommandAttemptStatusVariant3
    )


class IntegrationExternalMethod(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    type: Literal["external"] = "external"
    label: str
    form: Annotated[
        list[
            Annotated[
                FormStringField
                | FormNumberField
                | FormIntegerField
                | FormBooleanField
                | FormMultiselectField
                | FormExternalField,
                Field(discriminator="type"),
            ]
        ]
        | None,
        Field(min_length=1),
    ] = None


class IntegrationKeyMethod(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["key"] = "key"
    label: str | None = None
    form: Annotated[
        list[
            Annotated[
                FormStringField
                | FormNumberField
                | FormIntegerField
                | FormBooleanField
                | FormMultiselectField
                | FormExternalField,
                Field(discriminator="type"),
            ]
        ]
        | None,
        Field(min_length=1),
    ] = None


class IntegrationOAuthMethod(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    type: Literal["oauth"] = "oauth"
    label: str
    form: Annotated[
        list[
            Annotated[
                FormStringField
                | FormNumberField
                | FormIntegerField
                | FormBooleanField
                | FormMultiselectField
                | FormExternalField,
                Field(discriminator="type"),
            ]
        ]
        | None,
        Field(min_length=1),
    ] = None


class IntegrationOauthStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: (
        IntegrationAttemptStatusVariant0
        | IntegrationAttemptStatusVariant1
        | IntegrationAttemptStatusVariant2
        | IntegrationAttemptStatusVariant3
    )


class McpListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[McpServer]


class ModelVariantSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    compaction: Annotated[
        ProviderCompactionSummary | ProviderCompactionNative | None, Field(discriminator="type")
    ] = None
    encoding: Literal["zstd", "gzip", "none"] | None = None


class PermissionAskedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^per")]
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    action: str
    resources: list[str]
    save: list[str] | None = None
    metadata: dict[str, Any] | None = None
    source: PermissionSourceTool | None = None
    message: str | None = None


class PermissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^per")]
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    action: str
    resources: list[str]
    save: list[str] | None = None
    metadata: dict[str, Any] | None = None
    source: PermissionSourceTool | None = None
    message: str | None = None


class PermissionRequestListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[PermissionRequest]


class PersistentPtyAddedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    terminal: PersistentPtyInfo


class PersistentPtyReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    pty_id: Annotated[str, Field(alias="ptyID", pattern="^pty")]
    title: str
    cwd: str
    foreground_process: Annotated[str | None, Field(alias="foregroundProcess")] = None
    screen: PersistentPtyReadResultScreen


class PersistentPtyUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["persistent-pty.updated"] = "persistent-pty.updated"
    location: LocationRef | None = None
    data: PersistentPtyUpdatedData


class PluginInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str | None = None
    source: Annotated[
        PluginSourceBuiltin | PluginSourcePackage | PluginSourceLocal | PluginSourceSdk,
        Field(discriminator="type"),
    ]
    features: PluginFeatures
    state: PluginStateVariant0 | PluginStateVariant1


class PluginListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[PluginInfo]


class PromptFileAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: Annotated[
        str, Field(pattern="^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$")
    ]
    mime: str
    source: Annotated[PromptFileSourceInline | PromptFileSourceUri, Field(discriminator="type")]
    name: str | None = None
    description: str | None = None
    mention: PromptMention | None = None


class ProviderCache(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    ttl: Annotated[
        str | None,
        Field(pattern="^[+-]?(?:\\d+(?:\\.\\d+)?|\\.\\d+)(?:[Ee][+-]?\\d+)?(?:ms|s|m|h|d)$"),
    ] = None
    warming: bool | ProviderCacheWarmingVariant1 | None = None


class ProviderSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    timeout: float | Literal[False] | None = None
    header_timeout: Annotated[float | Literal[False] | None, Field(alias="headerTimeout")] = None
    chunk_timeout: Annotated[float | Literal[False] | None, Field(alias="chunkTimeout")] = None
    compaction: Annotated[
        ProviderCompactionSummary | ProviderCompactionNative | None, Field(discriminator="type")
    ] = None
    cache: ProviderCache | None = None
    transport: Literal["http", "websocket"] | None = None
    encoding: Literal["zstd", "gzip", "none"] | None = None
    tls: ProviderTls | None = None


class SessionCompactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: SessionInboxCompaction


class SessionCompactionEndedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    reason: Literal["auto", "manual"]
    model: ModelRef | None = None
    native: dict[str, Any] | None = None
    text: str
    cost: float | None = None
    tokens: TokenUsageInfo | None = None


class SessionCompactionFailedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    reason: Literal["auto", "manual"]
    error: SessionStructuredError
    input_id: Annotated[str | None, Field(alias="inputID", pattern="^msg_")] = None
    cost: float | None = None
    tokens: TokenUsageInfo | None = None


class SessionCreated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.created"] = "session.created"
    durable: SessionCreatedDurable
    location: LocationRef | None = None
    data: SessionCreatedData


class SessionExecutionFailedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    error: SessionStructuredError


class SessionForked(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.forked"] = "session.forked"
    durable: SessionForkedDurable
    location: LocationRef | None = None
    data: SessionForkedData


class SessionFormCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: FormInfo


class SessionFormListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: list[FormInfo]


class SessionGenerateResponseData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    text: str
    tokens: TokenUsageInfo
    cost: float


class SessionInboxUserPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    text: str
    files: list[PromptFileAttachment] | None = None
    agents: list[PromptAgentAttachment] | None = None
    skills: list[PromptSkillAttachment] | None = None
    metadata: dict[str, Any] | None = None


class SessionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^ses")]
    parent_id: Annotated[str | None, Field(alias="parentID", pattern="^ses")] = None
    fork: SessionInfoFork | None = None
    project_id: Annotated[str, Field(alias="projectID")]
    agent: str | None = None
    model: ModelRef | None = None
    cost: float
    tokens: TokenUsageInfo
    outcome: Literal["succeeded", "failed", "interrupted"] | None = None
    time: SessionInfoTime
    title: str | None = None
    subpath: str | None = None
    metadata: dict[str, Any] | None = None
    permissions: list[PermissionRule] | None = None
    revert: SessionRevert | None = None
    location: LocationPublicRef


class SessionMessageAssistantRetry(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    attempt: Annotated[int, Field(gt=0, le=9007199254740991)]
    at: float
    error: SessionStructuredError


class SessionMessageCompactionCompleted(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["compaction"] = "compaction"
    id: Annotated[str, Field(pattern="^msg_")]
    metadata: dict[str, Any] | None = None
    time: SessionMessageCompactionCompletedTime
    status: Literal["completed"] = "completed"
    reason: Literal["auto", "manual"]
    model: ModelRef | None = None
    native: dict[str, Any] | None = None
    summary: str
    cost: float | None = None
    tokens: TokenUsageInfo | None = None


class SessionMessageCompactionFailed(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["compaction"] = "compaction"
    id: Annotated[str, Field(pattern="^msg_")]
    metadata: dict[str, Any] | None = None
    time: SessionMessageCompactionFailedTime
    status: Literal["failed"] = "failed"
    reason: Literal["auto", "manual"]
    error: SessionStructuredError
    cost: float | None = None
    tokens: TokenUsageInfo | None = None


class SessionMessageIdle(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^msg_")]
    metadata: dict[str, Any] | None = None
    time: SessionMessageIdleTime
    type: Literal["idle"] = "idle"
    outcome: Literal["succeeded", "failed", "interrupted"]
    error: SessionStructuredError | None = None


class SessionMessageToolStateCompleted(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["completed"] = "completed"
    input: dict[str, Any]
    content: Annotated[
        list[Annotated[ToolTextContent | ToolFileContent, Field(discriminator="type")]],
        Field(min_length=1),
    ]
    metadata: dict[str, Any] | None = None


class SessionMessageToolStateError(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    status: Literal["error"] = "error"
    input: dict[str, Any]
    error: SessionStructuredError
    content: Annotated[
        list[Annotated[ToolTextContent | ToolFileContent, Field(discriminator="type")]] | None,
        Field(min_length=1),
    ] = None
    metadata: dict[str, Any] | None = None


class SessionMessageUser(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^msg_")]
    metadata: dict[str, Any] | None = None
    time: SessionMessageUserTime
    text: str
    files: list[PromptFileAttachment] | None = None
    agents: list[PromptAgentAttachment] | None = None
    skills: list[PromptSkillAttachment] | None = None
    type: Literal["user"] = "user"


class SessionPermissionGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: PermissionRequest


class SessionPermissionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: list[PermissionRequest]


class SessionRetryScheduledData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    attempt: Annotated[int, Field(gt=0, le=9007199254740991)]
    at: Annotated[int, Field(ge=0, le=9007199254740991)]
    error: SessionStructuredError


class SessionShellEndedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    shell: ShellInfo
    output: SessionShellEndedDataOutput


class SessionShellStartedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    shell: ShellInfo


class SessionStatsModelUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    model: ModelRef
    steps: Annotated[int, Field(ge=0, le=9007199254740991)]
    tokens: TokenUsageInfo
    cost: float


class SessionStatusUpdatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    status: Annotated[
        SessionStatusIdle | SessionStatusRetry | SessionStatusBusy, Field(discriminator="type")
    ]


class SessionStepEndedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    finish: Literal["stop", "length", "tool-calls", "content-filter", "error", "unknown"]
    raw_finish: Annotated[str | None, Field(alias="rawFinish")] = None
    native: dict[str, Any] | None = None
    cost: float
    tokens: TokenUsageInfo
    snapshot: str | None = None
    files: list[str] | None = None


class SessionStepFailedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    error: SessionStructuredError
    finish: Literal["content-filter"] | None = None
    raw_finish: Annotated[str | None, Field(alias="rawFinish")] = None
    native: dict[str, Any] | None = None
    cost: float | None = None
    tokens: TokenUsageInfo | None = None
    snapshot: str | None = None
    files: list[str] | None = None


class SessionToolFailedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    id: str
    error: SessionStructuredError
    content: Annotated[
        list[Annotated[ToolTextContent | ToolFileContent, Field(discriminator="type")]] | None,
        Field(min_length=1),
    ] = None
    metadata: dict[str, Any] | None = None
    executed: bool
    native: dict[str, Any] | None = None


class SessionToolSuccessData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    assistant_message_id: Annotated[str, Field(alias="assistantMessageID", pattern="^msg_")]
    id: str
    content: Annotated[
        list[Annotated[ToolTextContent | ToolFileContent, Field(discriminator="type")]],
        Field(min_length=1),
    ]
    metadata: dict[str, Any] | None = None
    executed: bool
    native: dict[str, Any] | None = None


class SessionUsageRecordedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    source: Literal["title", "compaction", "generate"]
    cost: float
    tokens: TokenUsageInfo


class SessionUsageUpdatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    cost: float
    tokens: TokenUsageInfo


class SessionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: list[SessionInfo]
    cursor: SessionsResponseCursor


class ShellCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: ShellInfo


class ShellCreatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    info: ShellInfo


class ShellGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: ShellInfo


class VoiceSessionResponseData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    transport: Annotated[
        VoiceWebRTCTransport | VoiceWebSocketTransport, Field(discriminator="type")
    ]


class WebSearchResponseEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    provider_id: Annotated[str, Field(alias="providerID")]
    results: list[WebSearchResult]


class WebsearchQueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: WebSearchResponseEncoded


class AutomationCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str | None, Field(alias="sessionID", pattern="^ses")] = None
    directory: str | None = None
    "\n    Where it runs when there is no Session. Defaults to the server user's home folder.\n    "
    name: Annotated[str, Field(min_length=1)]
    "\n    A short label shown in lists.\n    "
    trigger: Annotated[
        AutomationTriggerAt
        | AutomationTriggerEvery
        | AutomationTriggerCron
        | AutomationTriggerEvent,
        Field(discriminator="type"),
    ]
    '\n    When it runs: { type: "at", at: "in 20 minutes" | "2026-10-27T09:00" }, { type: "every", every: "1 hour" } or { type: "cron", cron: "0 9 * * 1-5" }, each with an optional tz. Or { type: "event", server, event, args? } for a connected MCP server advertising push delivery.\n    '
    action: Annotated[
        AutomationPromptActionInput | AutomationCodeActionInput | AutomationShellActionInput,
        Field(discriminator="type"),
    ]
    '\n    What it does: { type: "prompt", prompt, to? } sends a prompt; { type: "code", code, args? } runs Code Mode; { type: "shell", command } runs a fixed shell command.\n    '
    expires: str | None = None


class AutomationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^aut_")]
    name: str
    project_id: Annotated[str, Field(alias="projectID")]
    session_id: Annotated[str | None, Field(alias="sessionID", pattern="^ses")] = None
    directory: str
    trigger: Annotated[
        AutomationTriggerAt
        | AutomationTriggerEvery
        | AutomationTriggerCron
        | AutomationTriggerEvent,
        Field(discriminator="type"),
    ]
    tz: str
    action: Annotated[
        AutomationActionPrompt | AutomationActionCode | AutomationActionShell,
        Field(discriminator="type"),
    ]
    expires: float | None = None
    status: Literal["active", "paused", "done"]
    next_due_at: Annotated[float | None, Field(alias="nextDueAt")] = None
    run_count: Annotated[int, Field(alias="runCount", ge=0, le=9007199254740991)]
    last_run_at: Annotated[float | None, Field(alias="lastRunAt")] = None
    last_outcome: Annotated[
        Literal["fired", "late", "skipped", "disabled", "failed", "expired"] | None,
        Field(alias="lastOutcome"),
    ] = None
    last_output: Annotated[str | None, Field(alias="lastOutput")] = None
    state: Any | None = None
    time: AutomationInfoTime


class ConfigModelCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    tools: bool | None = None
    freeform: list[Literal["text", "regex", "lark"]] | None = None
    input: list[str] | None = None
    output: list[str] | None = None
    temperature: bool | None = None
    reasoning: ConfigModelReasoningVariant0 | ConfigModelReasoningVariant1 | None = None


class ConfigModelEncodedVariantsItem(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    settings: ConfigModelVariantSettings | None = None
    headers: dict[str, str] | None = None
    body: dict[str, Any] | None = None


class ConfigModelSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    compaction: Annotated[
        ProviderCompactionSummary | ProviderCompactionNative | None, Field(discriminator="type")
    ] = None
    cache: ProviderCache | None = None
    encoding: Literal["zstd", "gzip", "none"] | None = None


class ConfigProviderSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    timeout: float | Literal[False] | None = None
    header_timeout: Annotated[float | Literal[False] | None, Field(alias="headerTimeout")] = None
    chunk_timeout: Annotated[float | Literal[False] | None, Field(alias="chunkTimeout")] = None
    compaction: Annotated[
        ProviderCompactionSummary | ProviderCompactionNative | None, Field(discriminator="type")
    ] = None
    cache: ProviderCache | None = None
    transport: Literal["http", "websocket"] | None = None
    encoding: Literal["zstd", "gzip", "none"] | None = None
    tls: ProviderTls | None = None


class ExperimentalAutomationCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: AutomationInfo


class ExperimentalAutomationGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: AutomationInfo


class ExperimentalAutomationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: list[AutomationInfo]


class ExperimentalAutomationPauseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: AutomationInfo


class ExperimentalAutomationResumeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: AutomationInfo


class ExperimentalJobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: list[JobShell | JobSubagent | JobImage | JobVideo | JobCodeMode]


class ExperimentalJobUpdatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    info: JobShell | JobSubagent | JobImage | JobVideo | JobCodeMode


class ExperimentalPersistentPtyReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: PersistentPtyReadResult | None = None


class ExperimentalSessionImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    info: SessionInfo
    messages: list
    location: LocationPublicRef | None = None


class ExperimentalSessionImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: SessionInfo


class FormCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str | None = None
    title: str
    metadata: dict[str, Any] | None = None
    fields: Annotated[
        list[
            Annotated[
                FormStringField
                | FormNumberField
                | FormIntegerField
                | FormBooleanField
                | FormMultiselectField
                | FormExternalField,
                Field(discriminator="type"),
            ]
        ],
        Field(min_length=1),
    ]


class FormCreatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    form: FormInfo1


class FormDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^frm_")]
    session_id: Annotated[str, Field(alias="sessionID")]
    title: str
    metadata: dict[str, Any] | None = None
    fields: Annotated[
        list[
            Annotated[
                FormStringField
                | FormNumberField
                | FormIntegerField
                | FormBooleanField
                | FormMultiselectField
                | FormExternalField,
                Field(discriminator="type"),
            ]
        ],
        Field(min_length=1),
    ]
    state: FormStateVariant0 | FormStateVariant1 | FormStateVariant2


class ModelSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    compaction: Annotated[
        ProviderCompactionSummary | ProviderCompactionNative | None, Field(discriminator="type")
    ] = None
    cache: ProviderCache | None = None
    encoding: Literal["zstd", "gzip", "none"] | None = None


class ModelVariant(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    settings: ModelVariantSettings | None = None
    headers: dict[str, str] | None = None
    body: dict[str, Any] | None = None


class PermissionAsked(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["permission.asked"] = "permission.asked"
    location: LocationRef | None = None
    data: PermissionAskedData


class PersistentPtyAdded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["persistent-pty.added"] = "persistent-pty.added"
    location: LocationRef | None = None
    data: PersistentPtyAddedData


class PluginCheckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[PluginInfo]


class ProviderInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    canonical: str | None = None
    integration_id: Annotated[str | None, Field(alias="integrationID")] = None
    name: str
    activation: Literal["auto", "enabled", "disabled"]
    package: str
    compatibility: ProviderCompatibility | None = None
    settings: ProviderSettings | None = None
    headers: dict[str, str] | None = None
    body: dict[str, Any] | None = None


class ProviderListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[ProviderInfo]


class ProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    settings: ProviderSettings
    headers: dict[str, str]
    body: dict[str, Any]


class SessionCompactionEnded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.compaction.ended"] = "session.compaction.ended"
    durable: SessionCompactionEndedDurable
    location: LocationRef | None = None
    data: SessionCompactionEndedData


class SessionCompactionFailed(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.compaction.failed"] = "session.compaction.failed"
    durable: SessionCompactionFailedDurable
    location: LocationRef | None = None
    data: SessionCompactionFailedData


class SessionCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: SessionInfo


class SessionExecutionFailed(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.execution.failed"] = "session.execution.failed"
    durable: SessionExecutionFailedDurable
    location: LocationRef | None = None
    data: SessionExecutionFailedData


class SessionForkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: SessionInfo


class SessionFormGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: FormDetail


class SessionGenerateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: SessionGenerateResponseData


class SessionGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: SessionInfo


class SessionInboxItemUser(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["user"] = "user"
    payload: SessionInboxUserPayload
    delivery: Literal["steer", "queue"]


class SessionInboxUser(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^msg_")]
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    time: SessionInboxUserTime
    type: Literal["user"] = "user"
    payload: SessionInboxUserPayload
    delivery: Literal["steer", "queue"]


class SessionMessageAssistantTool(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["tool"] = "tool"
    id: str
    name: str
    executed: bool | None = None
    native: dict[str, Any] | None = None
    state: (
        SessionMessageToolStateStreaming
        | SessionMessageToolStateRunning
        | SessionMessageToolStateCompleted
        | SessionMessageToolStateError
    )
    time: SessionMessageAssistantToolTime


class SessionPromptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: SessionInboxUser


class SessionRetryScheduled(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.retry.scheduled"] = "session.retry.scheduled"
    durable: SessionRetryScheduledDurable
    location: LocationRef | None = None
    data: SessionRetryScheduledData


class SessionShellEnded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.shell.ended"] = "session.shell.ended"
    durable: SessionShellEndedDurable
    location: LocationRef | None = None
    data: SessionShellEndedData


class SessionShellStarted(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.shell.started"] = "session.shell.started"
    durable: SessionShellStartedDurable
    location: LocationRef | None = None
    data: SessionShellStartedData


class SessionStatsInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    range: SessionStatsInfoRange
    sessions: Annotated[int, Field(ge=0, le=9007199254740991)]
    subagents: Annotated[int, Field(ge=0, le=9007199254740991)]
    prompts: Annotated[int, Field(ge=0, le=9007199254740991)]
    steps: Annotated[int, Field(ge=0, le=9007199254740991)]
    tokens: TokenUsageInfo
    cost: float
    tools: SessionStatsToolsVariant0 | SessionStatsToolsVariant1 | SessionStatsToolsVariant2
    active_days: Annotated[int, Field(alias="activeDays", ge=0, le=9007199254740991)]
    streak: Annotated[int, Field(ge=0, le=9007199254740991)]
    activity: list[SessionStatsActivity]
    models: list[SessionStatsModelUsage]


class SessionStatusUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.status"] = "session.status"
    location: LocationRef | None = None
    data: SessionStatusUpdatedData


class SessionStepEnded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.step.ended"] = "session.step.ended"
    durable: SessionStepEndedDurable
    location: LocationRef | None = None
    data: SessionStepEndedData


class SessionStepFailed(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.step.failed"] = "session.step.failed"
    durable: SessionStepFailedDurable
    location: LocationRef | None = None
    data: SessionStepFailedData


class SessionToolFailed(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.tool.failed"] = "session.tool.failed"
    durable: SessionToolFailedDurable
    location: LocationRef | None = None
    data: SessionToolFailedData


class SessionToolSuccess(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.tool.success"] = "session.tool.success"
    durable: SessionToolSuccessDurable
    location: LocationRef | None = None
    data: SessionToolSuccessData


class SessionUsageRecorded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.usage.recorded"] = "session.usage.recorded"
    durable: SessionUsageRecordedDurable
    location: LocationRef | None = None
    data: SessionUsageRecordedData


class SessionUsageUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.usage.updated"] = "session.usage.updated"
    location: LocationRef | None = None
    data: SessionUsageUpdatedData


class ShellCreated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["shell.created"] = "shell.created"
    location: LocationRef | None = None
    data: ShellCreatedData


class VoiceSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: VoiceSessionResponseData


class AgentInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    name: str
    model: ModelRef | None = None
    request: ProviderRequest
    system: str | None = None
    description: str | None = None
    mode: Literal["subagent", "primary", "all"]
    hidden: bool
    color: str | None = None
    steps: Annotated[int | None, Field(gt=0, le=9007199254740991)] = None
    permissions: list[PermissionRule]


class AgentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[AgentInfo]


class ConfigModelEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    model_id: Annotated[str | None, Field(alias="modelID")] = None
    type: Literal["chat", "decision", "image", "video"] | str | None = None
    family: str | None = None
    name: str | None = None
    release_date: Annotated[
        str | None, Field(alias="releaseDate", pattern="^\\d{4}-\\d{2}(-\\d{2})?$")
    ] = None
    "\n    Release date as YYYY-MM-DD or YYYY-MM\n    "
    compatibility: ModelCompatibility | None = None
    package: str | None = None
    settings: ConfigModelSettings | None = None
    headers: dict[str, str] | None = None
    body: dict[str, Any] | None = None
    capabilities: ConfigModelCapabilities | None = None
    variants: list[ConfigModelEncodedVariantsItem] | None = None
    cost: ConfigModelCostEncoded | list[ConfigModelCostEncoded] | None = None
    disabled: bool | None = None
    limit: ConfigModelEncodedLimit | None = None


class ConfigProviderEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    canonical: str | None = None
    name: str | None = None
    env: list[str] | None = None
    package: str | None = None
    compatibility: ProviderCompatibility | None = None
    settings: ConfigProviderSettings | None = None
    headers: dict[str, str] | None = None
    body: dict[str, Any] | None = None
    models: dict[str, ConfigModelEncoded] | None = None


class ExperimentalJobUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["experimental.job.updated"] = "experimental.job.updated"
    location: LocationRef | None = None
    data: ExperimentalJobUpdatedData


class ExperimentalSessionStatsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: SessionStatsInfo


class FormCreated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["form.created"] = "form.created"
    location: LocationRef | None = None
    data: FormCreatedData


class IntegrationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    name: str
    metadata: dict[str, Any] | None = None
    methods: list[
        Annotated[
            IntegrationOAuthMethod
            | IntegrationCommandMethod
            | IntegrationKeyMethod
            | IntegrationExternalMethod
            | IntegrationEnvMethod,
            Field(discriminator="type"),
        ]
    ]
    connections: list[
        Annotated[ConnectionCredentialInfo | ConnectionEnvInfo, Field(discriminator="type")]
    ]


class IntegrationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[IntegrationInfo]


class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str
    model_id: Annotated[str, Field(alias="modelID")]
    provider_id: Annotated[str, Field(alias="providerID")]
    canonical: str | None = None
    type: Literal["chat", "decision", "image", "video"] | str | None = None
    family: str | None = None
    name: str
    compatibility: ModelCompatibility | None = None
    package: str | None = None
    settings: ModelSettings | None = None
    headers: dict[str, str] | None = None
    body: dict[str, Any] | None = None
    capabilities: ModelCapabilities
    variants: list[ModelVariant]
    experimental: ModelInfoExperimental | None = None
    time: ModelInfoTime
    cost: list[ModelCost]
    status: Literal["alpha", "beta", "deprecated", "active"]
    enabled: bool
    limit: ModelInfoLimit


class ModelListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: list[ModelInfo]


class ProviderGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: ProviderInfo


class SessionInboxListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: list[
        Annotated[
            SessionInboxUser | SessionInboxSynthetic | SessionInboxCompaction | SessionInboxMove,
            Field(discriminator="type"),
        ]
    ]


class SessionMessageAssistant(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^msg_")]
    metadata: dict[str, Any] | None = None
    time: SessionMessageAssistantTime
    type: Literal["assistant"] = "assistant"
    agent: str
    model: ModelRef
    content: list[
        Annotated[
            SessionMessageAssistantText
            | SessionMessageAssistantReasoning
            | SessionMessageAssistantTool,
            Field(discriminator="type"),
        ]
    ]
    snapshot: SessionMessageAssistantSnapshot | None = None
    finish: Literal["stop", "length", "tool-calls", "content-filter", "error", "unknown"] | None = (
        None
    )
    raw_finish: Annotated[str | None, Field(alias="rawFinish")] = None
    native: dict[str, Any] | None = None
    cost: float | None = None
    tokens: TokenUsageInfo | None = None
    error: SessionStructuredError | None = None
    retry: SessionMessageAssistantRetry | None = None


class SessionMessageContentUpdatedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    message_id: Annotated[str, Field(alias="messageID", pattern="^msg_")]
    content: list[
        Annotated[
            SessionMessageAssistantText
            | SessionMessageAssistantReasoning
            | SessionMessageAssistantTool,
            Field(discriminator="type"),
        ]
    ]


class SessionMessagesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: list[
        SessionMessageAgentSelected
        | SessionMessageModelSelected
        | SessionMessageLocationSwitched
        | SessionMessageUser
        | SessionMessageSynthetic
        | SessionMessageSystem
        | SessionMessageSkill
        | SessionMessageShell
        | SessionMessageAssistant
        | SessionMessageIdle
        | SessionMessageCompactionRunning
        | SessionMessageCompactionCompleted
        | SessionMessageCompactionFailed
    ]
    cursor: SessionMessagesResponseCursor


class SessionTransferData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    info: SessionInfo
    messages: list[
        SessionMessageAgentSelected
        | SessionMessageModelSelected
        | SessionMessageLocationSwitched
        | SessionMessageUser
        | SessionMessageSynthetic
        | SessionMessageSystem
        | SessionMessageSkill
        | SessionMessageShell
        | SessionMessageAssistant
        | SessionMessageIdle
        | SessionMessageCompactionRunning
        | SessionMessageCompactionCompleted
        | SessionMessageCompactionFailed
    ]


class AgentGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: AgentInfo


class ConfigInfoEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_schema: Annotated[str | None, Field(alias="$schema")] = None
    shell: str | None = None
    model: str | ConfigInfoEncodedModelVariant1 | None = None
    default_agent: str | None = None
    update: Literal["disable", "notify", "auto"] | None = None
    share: Literal["manual", "auto", "disabled"] | None = None
    enterprise: ConfigInfoEncodedEnterprise | None = None
    username: str | None = None
    timezone: str | None = None
    automations: bool | None = None
    permissions: list[PermissionRule] | None = None
    policies: list[ConfigInfoEncodedPoliciesItem] | None = None
    agents: dict[str, ConfigAgentEncoded] | None = None
    snapshots: bool | None = None
    watcher: ConfigInfoEncodedWatcher | None = None
    formatter: bool | dict[str, ConfigFormatterEntryEncoded] | None = None
    lsp: (
        bool | dict[str, ConfigInfoEncodedLspVariant1ValueVariant0 | ConfigLSPServerEncoded] | None
    ) = None
    media: ConfigInfoEncodedMedia | None = None
    tool_output: ConfigInfoEncodedToolOutput | None = None
    mcp: ConfigInfoEncodedMcp | None = None
    compaction: ConfigInfoEncodedCompaction | None = None
    skills: list[str] | None = None
    commands: dict[str, ConfigCommandEncoded] | None = None
    instructions: list[str] | None = None
    references: dict[str, str | ConfigReferenceGitEncoded | ConfigReferenceLocalEncoded] | None = (
        None
    )
    websearch: Literal[False] | ConfigWebSearchInfoEncoded | None = None
    plugins: list[str | ConfigPluginEntryEncoded] | None = None
    worktree: ConfigWorktree | None = None
    warming: bool | ConfigWarmingEncoded | None = None
    providers: dict[str, ConfigProviderEncoded] | None = None
    experimental: ConfigInfoEncodedExperimental | None = None


class ExperimentalSessionExportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: SessionTransferData


class IntegrationGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: IntegrationInfo


class ModelDefaultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    location: LocationPublicRef
    data: ModelInfo | None = None


class SessionContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: list[
        SessionMessageAgentSelected
        | SessionMessageModelSelected
        | SessionMessageLocationSwitched
        | SessionMessageUser
        | SessionMessageSynthetic
        | SessionMessageSystem
        | SessionMessageSkill
        | SessionMessageShell
        | SessionMessageAssistant
        | SessionMessageIdle
        | SessionMessageCompactionRunning
        | SessionMessageCompactionCompleted
        | SessionMessageCompactionFailed
    ]


class SessionInboxEnqueuedData(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    session_id: Annotated[str, Field(alias="sessionID", pattern="^ses")]
    inbox_id: Annotated[str, Field(alias="inboxID", pattern="^msg_")]
    item: Annotated[
        SessionInboxItemUser
        | SessionInboxItemSynthetic
        | SessionInboxItemCompaction
        | SessionInboxItemMove,
        Field(discriminator="type"),
    ]


class SessionMessageContentUpdated(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.message.content.updated"] = "session.message.content.updated"
    durable: SessionMessageContentUpdatedDurable
    location: LocationRef | None = None
    data: SessionMessageContentUpdatedData


class SessionMessageGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    data: (
        SessionMessageAgentSelected
        | SessionMessageModelSelected
        | SessionMessageLocationSwitched
        | SessionMessageUser
        | SessionMessageSynthetic
        | SessionMessageSystem
        | SessionMessageSkill
        | SessionMessageShell
        | SessionMessageAssistant
        | SessionMessageIdle
        | SessionMessageCompactionRunning
        | SessionMessageCompactionCompleted
        | SessionMessageCompactionFailed
    )


class ConfigDocumentEncoded(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    type: Literal["document"] = "document"
    path: str | None = None
    info: ConfigInfoEncoded


class ConfigEntry(RootModel[ConfigDocumentEncoded | ConfigDirectoryEncoded]):
    model_config = ConfigDict(populate_by_name=True)
    root: Annotated[ConfigDocumentEncoded | ConfigDirectoryEncoded, Field(discriminator="type")]


class SessionInboxEnqueued(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: Annotated[str, Field(pattern="^evt_")]
    created: float
    metadata: dict[str, Any] | None = None
    type: Literal["session.inbox.enqueued"] = "session.inbox.enqueued"
    durable: SessionInboxEnqueuedDurable
    location: LocationRef | None = None
    data: SessionInboxEnqueuedData


class SessionLogItem(
    RootModel[
        SessionCreated
        | SessionAgentSelected
        | SessionModelSelected
        | SessionMoved
        | SessionRenamed
        | SessionMetadataUpdated
        | SessionPermissions
        | SessionViewed
        | SessionArchived
        | SessionUnarchived
        | SessionDeleted
        | SessionForked
        | SessionInboxDelivered
        | SessionInboxEnqueued
        | SessionInboxCancelled
        | SessionInboxDeliveryChanged
        | SessionExecutionStarted
        | SessionExecutionSucceeded
        | SessionExecutionFailed
        | SessionExecutionInterrupted
        | SessionInstructionsUpdated
        | SessionSynthetic
        | SessionSkillActivated
        | SessionShellStarted
        | SessionShellEnded
        | SessionStepStarted
        | SessionStepFirstToken
        | SessionStepStreamed
        | SessionStepEnded
        | SessionStepFailed
        | SessionTextStarted
        | SessionTextEnded
        | SessionReasoningStarted
        | SessionReasoningEnded
        | SessionToolInputStarted
        | SessionToolInputEnded
        | SessionToolCalled
        | SessionToolSuccess
        | SessionToolFailed
        | SessionRetryScheduled
        | SessionCompactionStarted
        | SessionCompactionEnded
        | SessionCompactionFailed
        | SessionRevertStaged
        | SessionRevertCleared
        | SessionRevertCommitted
        | SessionUsageRecorded
        | SessionMessageContentUpdated
        | EventLogSynced
    ]
):
    model_config = ConfigDict(populate_by_name=True)
    root: Annotated[
        SessionCreated
        | SessionAgentSelected
        | SessionModelSelected
        | SessionMoved
        | SessionRenamed
        | SessionMetadataUpdated
        | SessionPermissions
        | SessionViewed
        | SessionArchived
        | SessionUnarchived
        | SessionDeleted
        | SessionForked
        | SessionInboxDelivered
        | SessionInboxEnqueued
        | SessionInboxCancelled
        | SessionInboxDeliveryChanged
        | SessionExecutionStarted
        | SessionExecutionSucceeded
        | SessionExecutionFailed
        | SessionExecutionInterrupted
        | SessionInstructionsUpdated
        | SessionSynthetic
        | SessionSkillActivated
        | SessionShellStarted
        | SessionShellEnded
        | SessionStepStarted
        | SessionStepFirstToken
        | SessionStepStreamed
        | SessionStepEnded
        | SessionStepFailed
        | SessionTextStarted
        | SessionTextEnded
        | SessionReasoningStarted
        | SessionReasoningEnded
        | SessionToolInputStarted
        | SessionToolInputEnded
        | SessionToolCalled
        | SessionToolSuccess
        | SessionToolFailed
        | SessionRetryScheduled
        | SessionCompactionStarted
        | SessionCompactionEnded
        | SessionCompactionFailed
        | SessionRevertStaged
        | SessionRevertCleared
        | SessionRevertCommitted
        | SessionUsageRecorded
        | SessionMessageContentUpdated
        | EventLogSynced,
        Field(discriminator="type"),
    ]


class V2Event(
    RootModel[
        LocationShutdown
        | ModelsDevRefreshed
        | CredentialUpdated
        | CredentialSwitched
        | IntegrationUpdated
        | ProviderUpdated
        | ModelUpdated
        | AgentUpdated
        | SessionCreated
        | SessionAgentSelected
        | SessionModelSelected
        | SessionMoved
        | SessionRenamed
        | SessionMetadataUpdated
        | SessionPermissions
        | SessionViewed
        | SessionArchived
        | SessionUnarchived
        | SessionUsageUpdated
        | SessionDeleted
        | SessionForked
        | SessionInboxDelivered
        | SessionInboxEnqueued
        | SessionInboxCancelled
        | SessionInboxDeliveryChanged
        | SessionExecutionStarted
        | SessionExecutionSucceeded
        | SessionExecutionFailed
        | SessionExecutionInterrupted
        | SessionInstructionsUpdated
        | SessionSynthetic
        | SessionSkillActivated
        | SessionShellStarted
        | SessionShellEnded
        | SessionStepStarted
        | SessionStepFirstToken
        | SessionStepStreamed
        | SessionStepEnded
        | SessionStepFailed
        | SessionTextStarted
        | SessionTextDelta
        | SessionTextEnded
        | SessionReasoningStarted
        | SessionReasoningDelta
        | SessionReasoningEnded
        | SessionToolInputStarted
        | SessionToolInputDelta
        | SessionToolInputEnded
        | SessionToolCalled
        | SessionToolProgress
        | SessionToolSuccess
        | SessionToolFailed
        | SessionRetryScheduled
        | SessionCompactionStarted
        | SessionCompactionDelta
        | SessionCompactionEnded
        | SessionCompactionFailed
        | SessionRevertStaged
        | SessionRevertCleared
        | SessionRevertCommitted
        | FilesystemChanged
        | ReferenceUpdated
        | PermissionAsked
        | PermissionReplied
        | PluginUpdated
        | ProjectUpdated
        | WorktreeUpdated
        | WorktreeResolved
        | CommandUpdated
        | ConfigUpdated
        | SkillUpdated
        | PtyCreated
        | PtyUpdated
        | PtyExited
        | PtyDeleted
        | PersistentPtyAdded
        | PersistentPtyRemoved
        | PersistentPtyUpdated
        | ShellCreated
        | ShellExited
        | ShellDeleted
        | ExperimentalJobUpdated
        | FormCreated
        | FormReplied
        | FormCancelled
        | WebsearchUpdated
        | ArtifactUpdated
        | SessionStatusUpdated
        | SessionIdle
        | TuiPromptAppend
        | TuiCommandExecute
        | TuiToastShow
        | TuiSessionSelect
        | InstallationUpdated
        | InstallationUpdateAvailable
        | VcsBranchUpdated
        | McpStatusChanged
        | McpResourcesChanged
        | ExperimentalMcpEvent
        | V2EventRpc
        | V2EventServerConnected
    ]
):
    model_config = ConfigDict(populate_by_name=True)
    root: (
        LocationShutdown
        | ModelsDevRefreshed
        | CredentialUpdated
        | CredentialSwitched
        | IntegrationUpdated
        | ProviderUpdated
        | ModelUpdated
        | AgentUpdated
        | SessionCreated
        | SessionAgentSelected
        | SessionModelSelected
        | SessionMoved
        | SessionRenamed
        | SessionMetadataUpdated
        | SessionPermissions
        | SessionViewed
        | SessionArchived
        | SessionUnarchived
        | SessionUsageUpdated
        | SessionDeleted
        | SessionForked
        | SessionInboxDelivered
        | SessionInboxEnqueued
        | SessionInboxCancelled
        | SessionInboxDeliveryChanged
        | SessionExecutionStarted
        | SessionExecutionSucceeded
        | SessionExecutionFailed
        | SessionExecutionInterrupted
        | SessionInstructionsUpdated
        | SessionSynthetic
        | SessionSkillActivated
        | SessionShellStarted
        | SessionShellEnded
        | SessionStepStarted
        | SessionStepFirstToken
        | SessionStepStreamed
        | SessionStepEnded
        | SessionStepFailed
        | SessionTextStarted
        | SessionTextDelta
        | SessionTextEnded
        | SessionReasoningStarted
        | SessionReasoningDelta
        | SessionReasoningEnded
        | SessionToolInputStarted
        | SessionToolInputDelta
        | SessionToolInputEnded
        | SessionToolCalled
        | SessionToolProgress
        | SessionToolSuccess
        | SessionToolFailed
        | SessionRetryScheduled
        | SessionCompactionStarted
        | SessionCompactionDelta
        | SessionCompactionEnded
        | SessionCompactionFailed
        | SessionRevertStaged
        | SessionRevertCleared
        | SessionRevertCommitted
        | FilesystemChanged
        | ReferenceUpdated
        | PermissionAsked
        | PermissionReplied
        | PluginUpdated
        | ProjectUpdated
        | WorktreeUpdated
        | WorktreeResolved
        | CommandUpdated
        | ConfigUpdated
        | SkillUpdated
        | PtyCreated
        | PtyUpdated
        | PtyExited
        | PtyDeleted
        | PersistentPtyAdded
        | PersistentPtyRemoved
        | PersistentPtyUpdated
        | ShellCreated
        | ShellExited
        | ShellDeleted
        | ExperimentalJobUpdated
        | FormCreated
        | FormReplied
        | FormCancelled
        | WebsearchUpdated
        | ArtifactUpdated
        | SessionStatusUpdated
        | SessionIdle
        | TuiPromptAppend
        | TuiCommandExecute
        | TuiToastShow
        | TuiSessionSelect
        | InstallationUpdated
        | InstallationUpdateAvailable
        | VcsBranchUpdated
        | McpStatusChanged
        | McpResourcesChanged
        | ExperimentalMcpEvent
        | V2EventRpc
        | V2EventServerConnected
    )


class SessionEventDurable(
    RootModel[
        SessionCreated
        | SessionAgentSelected
        | SessionModelSelected
        | SessionMoved
        | SessionRenamed
        | SessionMetadataUpdated
        | SessionPermissions
        | SessionViewed
        | SessionArchived
        | SessionUnarchived
        | SessionDeleted
        | SessionForked
        | SessionInboxDelivered
        | SessionInboxEnqueued
        | SessionInboxCancelled
        | SessionInboxDeliveryChanged
        | SessionExecutionStarted
        | SessionExecutionSucceeded
        | SessionExecutionFailed
        | SessionExecutionInterrupted
        | SessionInstructionsUpdated
        | SessionSynthetic
        | SessionSkillActivated
        | SessionShellStarted
        | SessionShellEnded
        | SessionStepStarted
        | SessionStepFirstToken
        | SessionStepStreamed
        | SessionStepEnded
        | SessionStepFailed
        | SessionTextStarted
        | SessionTextEnded
        | SessionReasoningStarted
        | SessionReasoningEnded
        | SessionToolInputStarted
        | SessionToolInputEnded
        | SessionToolCalled
        | SessionToolSuccess
        | SessionToolFailed
        | SessionRetryScheduled
        | SessionCompactionStarted
        | SessionCompactionEnded
        | SessionCompactionFailed
        | SessionRevertStaged
        | SessionRevertCleared
        | SessionRevertCommitted
        | SessionUsageRecorded
        | SessionMessageContentUpdated
    ]
):
    model_config = ConfigDict(populate_by_name=True)
    root: Annotated[
        SessionCreated
        | SessionAgentSelected
        | SessionModelSelected
        | SessionMoved
        | SessionRenamed
        | SessionMetadataUpdated
        | SessionPermissions
        | SessionViewed
        | SessionArchived
        | SessionUnarchived
        | SessionDeleted
        | SessionForked
        | SessionInboxDelivered
        | SessionInboxEnqueued
        | SessionInboxCancelled
        | SessionInboxDeliveryChanged
        | SessionExecutionStarted
        | SessionExecutionSucceeded
        | SessionExecutionFailed
        | SessionExecutionInterrupted
        | SessionInstructionsUpdated
        | SessionSynthetic
        | SessionSkillActivated
        | SessionShellStarted
        | SessionShellEnded
        | SessionStepStarted
        | SessionStepFirstToken
        | SessionStepStreamed
        | SessionStepEnded
        | SessionStepFailed
        | SessionTextStarted
        | SessionTextEnded
        | SessionReasoningStarted
        | SessionReasoningEnded
        | SessionToolInputStarted
        | SessionToolInputEnded
        | SessionToolCalled
        | SessionToolSuccess
        | SessionToolFailed
        | SessionRetryScheduled
        | SessionCompactionStarted
        | SessionCompactionEnded
        | SessionCompactionFailed
        | SessionRevertStaged
        | SessionRevertCleared
        | SessionRevertCommitted
        | SessionUsageRecorded
        | SessionMessageContentUpdated,
        Field(discriminator="type"),
    ]


type AutomationAction = Annotated[
    AutomationActionPrompt | AutomationActionCode | AutomationActionShell,
    Field(discriminator="type"),
]
type AutomationActionInput = Annotated[
    AutomationPromptActionInput | AutomationCodeActionInput | AutomationShellActionInput,
    Field(discriminator="type"),
]
type AutomationTarget = AutomationTargetVariant0 | AutomationTargetVariant1
type AutomationTrigger = Annotated[
    AutomationTriggerAt | AutomationTriggerEvery | AutomationTriggerCron | AutomationTriggerEvent,
    Field(discriminator="type"),
]
type AutomationTrigger1 = Annotated[
    AutomationTriggerAt | AutomationTriggerEvery | AutomationTriggerCron | AutomationTriggerEvent,
    Field(discriminator="type"),
]
type ConfigModelReasoning = ConfigModelReasoningVariant0 | ConfigModelReasoningVariant1
type ConnectionInfo = Annotated[
    ConnectionCredentialInfo | ConnectionEnvInfo, Field(discriminator="type")
]
type CredentialValue = Annotated[
    CredentialOAuth | CredentialKey | CredentialExternal, Field(discriminator="type")
]
type FormField = Annotated[
    FormStringField
    | FormNumberField
    | FormIntegerField
    | FormBooleanField
    | FormMultiselectField
    | FormExternalField,
    Field(discriminator="type"),
]
type FormField1 = Annotated[
    FormStringField1
    | FormNumberField1
    | FormIntegerField1
    | FormBooleanField1
    | FormMultiselectField1
    | FormExternalField,
    Field(discriminator="type"),
]
type FormState = FormStateVariant0 | FormStateVariant1 | FormStateVariant2
type GenerateChoiceValue = str | bool
type GenerateDecisionAnswer = Annotated[
    GeneratePredicateAnswer | GenerateChoiceAnswer | GenerateScoreAnswer | GenerateErrorAnswer,
    Field(discriminator="type"),
]
type GenerateDecisionPart = Annotated[
    GenerateDecisionTextPart | GenerateDecisionJsonPart | GenerateDecisionFilePart,
    Field(discriminator="type"),
]
type GenerateDecisionQuestion = Annotated[
    GeneratePredicateQuestion | GenerateChoiceQuestion | GenerateScoreQuestion,
    Field(discriminator="type"),
]
type GenerateImageResolution = Literal["1K", "2K", "4K"] | str
type GenerateVideoAspect = str | Literal["auto"]
type GenerateVideoJob = GenerateVideoRunning | GenerateVideoCompleted | GenerateVideoError
type GenerateVideoResolution = Literal["480p", "720p", "1080p", "4k"] | str
type IntegrationAttemptStatus = (
    IntegrationAttemptStatusVariant0
    | IntegrationAttemptStatusVariant1
    | IntegrationAttemptStatusVariant2
    | IntegrationAttemptStatusVariant3
)
type IntegrationCommandAttemptStatus = (
    IntegrationCommandAttemptStatusVariant0
    | IntegrationCommandAttemptStatusVariant1
    | IntegrationCommandAttemptStatusVariant2
    | IntegrationCommandAttemptStatusVariant3
)
type IntegrationMethod = Annotated[
    IntegrationOAuthMethod
    | IntegrationCommandMethod
    | IntegrationKeyMethod
    | IntegrationExternalMethod
    | IntegrationEnvMethod,
    Field(discriminator="type"),
]
type JobInfo = JobShell | JobSubagent | JobImage | JobVideo | JobCodeMode
type ModelReasoningField = Literal["reasoning", "reasoning_content", "reasoning_text"] | str
type ModelType = Literal["chat", "decision", "image", "video"] | str
type PermissionSource = PermissionSourceTool
type PluginSource = Annotated[
    PluginSourceBuiltin | PluginSourcePackage | PluginSourceLocal | PluginSourceSdk,
    Field(discriminator="type"),
]
type PluginState = PluginStateVariant0 | PluginStateVariant1
type PromptFileSource = Annotated[
    PromptFileSourceInline | PromptFileSourceUri, Field(discriminator="type")
]
type ProviderCacheWarming = bool | ProviderCacheWarmingVariant1
type ProviderCompaction = Annotated[
    ProviderCompactionSummary | ProviderCompactionNative, Field(discriminator="type")
]
type ReferenceSource = Annotated[
    ReferenceLocalSource | ReferenceGitSource, Field(discriminator="type")
]
type SessionForkBoundary = Annotated[
    SessionForkBoundaryBefore | SessionForkBoundaryThrough, Field(discriminator="type")
]
type SessionInboxInfo = Annotated[
    SessionInboxUser | SessionInboxSynthetic | SessionInboxCompaction | SessionInboxMove,
    Field(discriminator="type"),
]
type SessionInboxItem = Annotated[
    SessionInboxItemUser
    | SessionInboxItemSynthetic
    | SessionInboxItemCompaction
    | SessionInboxItemMove,
    Field(discriminator="type"),
]
type SessionMessageAssistantContentEncoded = Annotated[
    SessionMessageAssistantText | SessionMessageAssistantReasoning | SessionMessageAssistantTool,
    Field(discriminator="type"),
]
type SessionMessageCompaction = (
    SessionMessageCompactionRunning
    | SessionMessageCompactionCompleted
    | SessionMessageCompactionFailed
)
type SessionMessageInfo = (
    SessionMessageAgentSelected
    | SessionMessageModelSelected
    | SessionMessageLocationSwitched
    | SessionMessageUser
    | SessionMessageSynthetic
    | SessionMessageSystem
    | SessionMessageSkill
    | SessionMessageShell
    | SessionMessageAssistant
    | SessionMessageCompaction
    | SessionMessageIdle
)
type SessionStatsTools = (
    SessionStatsToolsVariant0 | SessionStatsToolsVariant1 | SessionStatsToolsVariant2
)
type SessionStatus = Annotated[
    SessionStatusIdle | SessionStatusRetry | SessionStatusBusy, Field(discriminator="type")
]
type ToolContent = Annotated[ToolTextContent | ToolFileContent, Field(discriminator="type")]
type VoiceTransport = Annotated[
    VoiceWebRTCTransport | VoiceWebSocketTransport, Field(discriminator="type")
]
type VoiceTransportInput = Annotated[
    VoiceWebRTCTransportInput | VoiceWebSocketTransportInput, Field(discriminator="type")
]
type AbsolutePath = str
type AgentColor = str
type AgentID = str
type ArtifactCapability = Literal["static", "server-code", "sqlite", "durable-objects"]
type ArtifactID = str
type ArtifactProviderIDEncoded = str
type AutomationDuration = str
type AutomationID = str
type AutomationOutcome = Literal["fired", "late", "skipped", "disabled", "failed", "expired"]
type AutomationStatus = Literal["active", "paused", "done"]
type BlobID = str
type ConfigCompactionPercent = str
type CredentialID = str
type EventID = str
type EventSeq = int
type EventVersionEncodedC3ea984aceae = Literal[2]
type EventVersionEncodedC77c5abcb68d = Literal[1]
type FormID = str
type GenerateImageAspect = str
type GenerateVideoErrorReason = Literal["failed", "cancelled", "expired", "unknown"]
type GenerateVideoID = str
type InstructionHash = str
type InstructionKey = str
type InstructionEntryKey = str
type IntegrationAttemptID = str
type IntegrationID = str
type IntegrationMethodID = str
type JobKind = Literal["shell", "subagent", "image", "video", "codemode"]
type JobStatus = Literal["running", "completed", "error", "cancelled"]
type McpProtocol = Literal["legacy", "auto", "2026-07-28"]
type MediaHash = str
type ModelCacheFormat = Literal["anthropic", "alibaba"]
type ModelMaxTokensField = Literal["max_completion_tokens", "max_tokens"]
type ModelMediaInput = Literal["assets", "state", "root"]
type MoneyUSD = float
type MoneyUSDPerMillionTokens = float
type PermissionEffect = Literal["allow", "deny", "ask"]
type PermissionID = str
type PermissionReply = Literal["once", "always", "reject"]
type PermissionSavedID = str
type PluginID = str
type ProjectID = str
type ProjectVcs = str
type PromptBase64 = str
type ProviderCacheTTL = str
type ProviderEncoding = Literal["zstd", "gzip", "none"]
type ProviderID = str
type ProviderTransport = Literal["http", "websocket"]
type PtyID = str
type RelativePath = str
type SessionID = str
type SessionInboxDelivery = Literal["steer", "queue"]
type SessionMessageID = str
type SessionLogItemEncoded = str
type SessionsCursor = str
type ShellID = str
type SkillID = str
type SkillName = str
type SnapshotID = str
type ToolFreeformFormat = Literal["text", "regex", "lark"]
type V2EventEncoded = str
type VcsMode = Literal["working", "branch", "committed"]
type VcsUntracked = Literal["files", "folders"]
type VoiceTransportType = Literal["webrtc", "websocket"]
type WebSearchID = str
type WorkspaceID = str
