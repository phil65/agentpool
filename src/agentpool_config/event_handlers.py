"""Event handler configuration models for AgentPool."""

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import ConfigDict, Field
from pydantic.types import SecretStr
from schemez import Schema


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.common_types import IndividualEventHandler

StdOutStyle = Literal["simple", "detailed"]

OpenAITTSModel = Literal["tts-1", "tts-1-hd"]
OpenAITTSVoice = Literal["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
SyncMode = Literal["sync_sentence", "sync_run", "async_queue", "async_cancel"]
EdgeTTSVoice = Literal[
    "af-ZA-AdriNeural",
    "af-ZA-WillemNeural",
    "sq-AL-AnilaNeural",
    "sq-AL-IlirNeural",
    "am-ET-AmehaNeural",
    "am-ET-MekdesNeural",
    "ar-DZ-AminaNeural",
    "ar-DZ-IsmaelNeural",
    "ar-BH-AliNeural",
    "ar-BH-LailaNeural",
    "ar-EG-SalmaNeural",
    "ar-EG-ShakirNeural",
    "ar-IQ-BasselNeural",
    "ar-IQ-RanaNeural",
    "ar-JO-SanaNeural",
    "ar-JO-TaimNeural",
    "ar-KW-FahedNeural",
    "ar-KW-NouraNeural",
    "ar-LB-LaylaNeural",
    "ar-LB-RamiNeural",
    "ar-LY-ImanNeural",
    "ar-LY-OmarNeural",
    "ar-MA-JamalNeural",
    "ar-MA-MounaNeural",
    "ar-OM-AbdullahNeural",
    "ar-OM-AyshaNeural",
    "ar-QA-AmalNeural",
    "ar-QA-MoazNeural",
    "ar-SA-HamedNeural",
    "ar-SA-ZariyahNeural",
    "ar-SY-AmanyNeural",
    "ar-SY-LaithNeural",
    "ar-TN-HediNeural",
    "ar-TN-ReemNeural",
    "ar-AE-FatimaNeural",
    "ar-AE-HamdanNeural",
    "ar-YE-MaryamNeural",
    "ar-YE-SalehNeural",
    "az-AZ-BabekNeural",
    "az-AZ-BanuNeural",
    "bn-BD-NabanitaNeural",
    "bn-BD-PradeepNeural",
    "bn-IN-BashkarNeural",
    "bn-IN-TanishaaNeural",
    "bs-BA-GoranNeural",
    "bs-BA-VesnaNeural",
    "bg-BG-BorislavNeural",
    "bg-BG-KalinaNeural",
    "my-MM-NilarNeural",
    "my-MM-ThihaNeural",
    "ca-ES-EnricNeural",
    "ca-ES-JoanaNeural",
    "zh-HK-HiuGaaiNeural",
    "zh-HK-HiuMaanNeural",
    "zh-HK-WanLungNeural",
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-XiaoyiNeural",
    "zh-CN-YunjianNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-YunxiaNeural",
    "zh-CN-YunyangNeural",
    "zh-CN-liaoning-XiaobeiNeural",
    "zh-TW-HsiaoChenNeural",
    "zh-TW-YunJheNeural",
    "zh-TW-HsiaoYuNeural",
    "zh-CN-shaanxi-XiaoniNeural",
    "hr-HR-GabrijelaNeural",
    "hr-HR-SreckoNeural",
    "cs-CZ-AntoninNeural",
    "cs-CZ-VlastaNeural",
    "da-DK-ChristelNeural",
    "da-DK-JeppeNeural",
    "nl-BE-ArnaudNeural",
    "nl-BE-DenaNeural",
    "nl-NL-ColetteNeural",
    "nl-NL-FennaNeural",
    "nl-NL-MaartenNeural",
    "en-AU-NatashaNeural",
    "en-AU-WilliamNeural",
    "en-CA-ClaraNeural",
    "en-CA-LiamNeural",
    "en-HK-SamNeural",
    "en-HK-YanNeural",
    "en-IN-NeerjaNeural",
    "en-IN-PrabhatNeural",
    "en-IE-ConnorNeural",
    "en-IE-EmilyNeural",
    "en-KE-AsiliaNeural",
    "en-KE-ChilembaNeural",
    "en-NZ-MitchellNeural",
    "en-NZ-MollyNeural",
    "en-NG-AbeoNeural",
    "en-NG-EzinneNeural",
    "en-PH-JamesNeural",
    "en-PH-RosaNeural",
    "en-SG-LunaNeural",
    "en-SG-WayneNeural",
    "en-ZA-LeahNeural",
    "en-ZA-LukeNeural",
    "en-TZ-ElimuNeural",
    "en-TZ-ImaniNeural",
    "en-GB-LibbyNeural",
    "en-GB-MaisieNeural",
    "en-GB-RyanNeural",
    "en-GB-SoniaNeural",
    "en-GB-ThomasNeural",
    "en-US-AriaNeural",
    "en-US-AnaNeural",
    "en-US-ChristopherNeural",
    "en-US-EricNeural",
    "en-US-GuyNeural",
    "en-US-JennyNeural",
    "en-US-MichelleNeural",
    "en-US-RogerNeural",
    "en-US-SteffanNeural",
    "et-EE-AnuNeural",
    "et-EE-KertNeural",
    "fil-PH-AngeloNeural",
    "fil-PH-BlessicaNeural",
    "fi-FI-HarriNeural",
    "fi-FI-NooraNeural",
    "fr-BE-CharlineNeural",
    "fr-BE-GerardNeural",
    "fr-CA-AntoineNeural",
    "fr-CA-JeanNeural",
    "fr-CA-SylvieNeural",
    "fr-FR-DeniseNeural",
    "fr-FR-EloiseNeural",
    "fr-FR-HenriNeural",
    "fr-CH-ArianeNeural",
    "fr-CH-FabriceNeural",
    "gl-ES-RoiNeural",
    "gl-ES-SabelaNeural",
    "ka-GE-EkaNeural",
    "ka-GE-GiorgiNeural",
    "de-AT-IngridNeural",
    "de-AT-JonasNeural",
    "de-DE-AmalaNeural",
    "de-DE-ConradNeural",
    "de-DE-KatjaNeural",
    "de-DE-KillianNeural",
    "de-CH-JanNeural",
    "de-CH-LeniNeural",
    "el-GR-AthinaNeural",
    "el-GR-NestorasNeural",
    "gu-IN-DhwaniNeural",
    "gu-IN-NiranjanNeural",
    "he-IL-AvriNeural",
    "he-IL-HilaNeural",
    "hi-IN-MadhurNeural",
    "hi-IN-SwaraNeural",
    "hu-HU-NoemiNeural",
    "hu-HU-TamasNeural",
    "is-IS-GudrunNeural",
    "is-IS-GunnarNeural",
    "id-ID-ArdiNeural",
    "id-ID-GadisNeural",
    "ga-IE-ColmNeural",
    "ga-IE-OrlaNeural",
    "it-IT-DiegoNeural",
    "it-IT-ElsaNeural",
    "it-IT-IsabellaNeural",
    "ja-JP-KeitaNeural",
    "ja-JP-NanamiNeural",
    "jv-ID-DimasNeural",
    "jv-ID-SitiNeural",
    "kn-IN-GaganNeural",
    "kn-IN-SapnaNeural",
    "kk-KZ-AigulNeural",
    "kk-KZ-DauletNeural",
    "km-KH-PisethNeural",
    "km-KH-SreymomNeural",
    "ko-KR-InJoonNeural",
    "ko-KR-SunHiNeural",
    "lo-LA-ChanthavongNeural",
    "lo-LA-KeomanyNeural",
    "lv-LV-EveritaNeural",
    "lv-LV-NilsNeural",
    "lt-LT-LeonasNeural",
    "lt-LT-OnaNeural",
    "mk-MK-AleksandarNeural",
    "mk-MK-MarijaNeural",
    "ms-MY-OsmanNeural",
    "ms-MY-YasminNeural",
    "ml-IN-MidhunNeural",
    "ml-IN-SobhanaNeural",
    "mt-MT-GraceNeural",
    "mt-MT-JosephNeural",
    "mr-IN-AarohiNeural",
    "mr-IN-ManoharNeural",
    "mn-MN-BataaNeural",
    "mn-MN-YesuiNeural",
    "ne-NP-HemkalaNeural",
    "ne-NP-SagarNeural",
    "nb-NO-FinnNeural",
    "nb-NO-PernilleNeural",
    "ps-AF-GulNawazNeural",
    "ps-AF-LatifaNeural",
    "fa-IR-DilaraNeural",
    "fa-IR-FaridNeural",
    "pl-PL-MarekNeural",
    "pl-PL-ZofiaNeural",
    "pt-BR-AntonioNeural",
    "pt-BR-FranciscaNeural",
    "pt-PT-DuarteNeural",
    "pt-PT-RaquelNeural",
    "ro-RO-AlinaNeural",
    "ro-RO-EmilNeural",
    "ru-RU-DmitryNeural",
    "ru-RU-SvetlanaNeural",
    "sr-RS-NicholasNeural",
    "sr-RS-SophieNeural",
    "si-LK-SameeraNeural",
    "si-LK-ThiliniNeural",
    "sk-SK-LukasNeural",
    "sk-SK-ViktoriaNeural",
    "sl-SI-PetraNeural",
    "sl-SI-RokNeural",
    "so-SO-MuuseNeural",
    "so-SO-UbaxNeural",
    "es-AR-ElenaNeural",
    "es-AR-TomasNeural",
    "es-BO-MarceloNeural",
    "es-BO-SofiaNeural",
    "es-CL-CatalinaNeural",
    "es-CL-LorenzoNeural",
    "es-CO-GonzaloNeural",
    "es-CO-SalomeNeural",
    "es-CR-JuanNeural",
    "es-CR-MariaNeural",
    "es-CU-BelkysNeural",
    "es-CU-ManuelNeural",
    "es-DO-EmilioNeural",
    "es-DO-RamonaNeural",
    "es-EC-AndreaNeural",
    "es-EC-LuisNeural",
    "es-SV-LorenaNeural",
    "es-SV-RodrigoNeural",
    "es-GQ-JavierNeural",
    "es-GQ-TeresaNeural",
    "es-GT-AndresNeural",
    "es-GT-MartaNeural",
    "es-HN-CarlosNeural",
    "es-HN-KarlaNeural",
    "es-MX-DaliaNeural",
    "es-MX-JorgeNeural",
    "es-MX-LorenzoEsCLNeural",
    "es-NI-FedericoNeural",
    "es-NI-YolandaNeural",
    "es-PA-MargaritaNeural",
    "es-PA-RobertoNeural",
    "es-PY-MarioNeural",
    "es-PY-TaniaNeural",
    "es-PE-AlexNeural",
    "es-PE-CamilaNeural",
    "es-PR-KarinaNeural",
    "es-PR-VictorNeural",
    "es-ES-AlvaroNeural",
    "es-ES-ElviraNeural",
    "es-ES-ManuelEsCUNeural",
    "es-US-AlonsoNeural",
    "es-US-PalomaNeural",
    "es-UY-MateoNeural",
    "es-UY-ValentinaNeural",
    "es-VE-PaolaNeural",
    "es-VE-SebastianNeural",
    "su-ID-JajangNeural",
    "su-ID-TutiNeural",
    "sw-KE-RafikiNeural",
    "sw-KE-ZuriNeural",
    "sw-TZ-DaudiNeural",
    "sw-TZ-RehemaNeural",
    "sv-SE-MattiasNeural",
    "sv-SE-SofieNeural",
    "ta-IN-PallaviNeural",
    "ta-IN-ValluvarNeural",
    "ta-MY-KaniNeural",
    "ta-MY-SuryaNeural",
    "ta-SG-AnbuNeural",
    "ta-SG-VenbaNeural",
    "ta-LK-KumarNeural",
    "ta-LK-SaranyaNeural",
    "te-IN-MohanNeural",
    "te-IN-ShrutiNeural",
    "th-TH-NiwatNeural",
    "th-TH-PremwadeeNeural",
    "tr-TR-AhmetNeural",
    "tr-TR-EmelNeural",
    "uk-UA-OstapNeural",
    "uk-UA-PolinaNeural",
    "ur-IN-GulNeural",
    "ur-IN-SalmanNeural",
    "ur-PK-AsadNeural",
    "ur-PK-UzmaNeural",
    "uz-UZ-MadinaNeural",
    "uz-UZ-SardorNeural",
    "vi-VN-HoaiMyNeural",
    "vi-VN-NamMinhNeural",
    "cy-GB-AledNeural",
    "cy-GB-NiaNeural",
    "zu-ZA-ThandoNeural",
    "zu-ZA-ThembaNeural",
]


class BaseEventHandlerConfig(Schema):
    """Base configuration for event handlers."""

    type: str = Field(init=False)
    """Event handler type discriminator."""

    enabled: bool = Field(default=True)
    """Whether this handler is enabled."""

    def get_handler(self) -> IndividualEventHandler:
        """Create and return the configured event handler.

        Returns:
            Configured event handler callable.

        Raises:
            NotImplementedError: Must be implemented by subclasses.
        """
        raise NotImplementedError


class StdoutEventHandlerConfig(BaseEventHandlerConfig):
    """Configuration for built-in event handlers (simple, detailed)."""

    model_config = ConfigDict(title="Stdout Event Handler")

    type: Literal["builtin"] = Field("builtin", init=False)
    """Builtin event handler."""

    handler: StdOutStyle = Field(default="simple", examples=["simple", "detailed"])
    """Which builtin handler to use.

    - simple: Basic text and tool notifications
    - detailed: Comprehensive execution visibility
    """

    def get_handler(self) -> IndividualEventHandler:
        """Get the builtin event handler."""
        from agentpool.agents.events import detailed_print_handler, simple_print_handler

        handlers = {"simple": simple_print_handler, "detailed": detailed_print_handler}
        return handlers[self.handler]


class CallbackEventHandlerConfig(BaseEventHandlerConfig):
    """Configuration for custom callback event handlers via import path."""

    model_config = ConfigDict(title="Callback Event Handler")

    type: Literal["callback"] = Field("callback", init=False)
    """Callback event handler."""

    import_path: str = Field(
        examples=[
            "mymodule:my_handler",
            "mypackage.handlers:custom_event_handler",
        ],
    )
    """Import path to the handler function (module:function format)."""

    def get_handler(self) -> IndividualEventHandler:
        """Import and return the callback handler."""
        from agentpool.utils.importing import import_callable

        return import_callable(self.import_path)


class FileStreamEventHandlerConfig(BaseEventHandlerConfig):
    """Configuration for streaming agent output to a file."""

    model_config = ConfigDict(title="File Stream Event Handler")

    type: Literal["file"] = Field("file", init=False)
    """File stream event handler."""

    path: str = Field(
        examples=["~/agent_output.txt", "/tmp/agent.log", "./output.md"],
    )
    """Path to the output file. Supports ~ expansion."""

    mode: Literal["w", "a"] = Field(
        default="a",
        examples=["w", "a"],
    )
    """File open mode.

    - w: Overwrite file on each run
    - a: Append to existing file
    """

    include_tools: bool = Field(default=False)
    """Whether to include tool call and result information."""

    include_thinking: bool = Field(default=False)
    """Whether to include thinking/reasoning content."""

    def get_handler(self) -> IndividualEventHandler:
        """Create and return the file stream handler."""
        from agentpool.agents.events.builtin_handlers import create_file_stream_handler

        return create_file_stream_handler(
            path=self.path,
            mode=self.mode,
            include_tools=self.include_tools,
            include_thinking=self.include_thinking,
        )


class TTSEventHandlerConfig(BaseEventHandlerConfig):
    """Configuration for Text-to-Speech event handler with OpenAI streaming."""

    model_config = ConfigDict(title="Text-to-Speech Event Handler")

    type: Literal["tts-openai"] = Field("tts-openai", init=False)
    """OpenAI TTS event handler."""

    api_key: SecretStr | None = Field(default=None, examples=["sk-..."], title="OpenAI API Key")
    """OpenAI API key. If not provided, uses OPENAI_API_KEY env var."""

    model: OpenAITTSModel = Field(
        default="tts-1", examples=["tts-1", "tts-1-hd"], title="TTS Model"
    )
    """TTS model to use.

    - tts-1: Fast, optimized for real-time streaming
    - tts-1-hd: Higher quality, slightly higher latency
    """

    voice: OpenAITTSVoice = Field(
        default="alloy",
        examples=["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
        title="Voice type",
    )
    """Voice to use for synthesis."""

    speed: float = Field(
        default=1.0,
        ge=0.25,
        le=4.0,
        examples=[0.5, 1.0, 1.5, 2.0],
        title="Speed of speech",
    )
    """Speed of speech (0.25 to 4.0, default 1.0)."""

    chunk_size: int = Field(default=1024, ge=256, examples=[512, 1024, 2048], title="Chunk Size")
    """Size of audio chunks to process (in bytes)."""

    sample_rate: int = Field(default=24000, examples=[16000, 24000, 44100], title="Sample Rate")
    """Audio sample rate in Hz (for PCM format)."""

    min_text_length: int = Field(
        default=20,
        ge=5,
        examples=[10, 20, 50],
        title="Minimum Text Length",
    )
    """Minimum text length before synthesizing (in characters)."""

    mode: SyncMode = Field(
        default="sync_run",
        examples=["sync_sentence", "sync_run", "async_queue", "async_cancel"],
        title="Synchronization Mode",
    )
    """How TTS synthesis synchronizes with the event stream.

    - sync_sentence: Wait for each sentence's audio before continuing (slowest, most synchronized)
    - sync_run: Stream fast, wait for all audio at run end (default, recommended)
    - async_queue: Stream fast, audio plays in background, multiple runs queue up
    - async_cancel: Stream fast, audio plays in background, new run cancels previous audio
    """

    def get_handler(self) -> IndividualEventHandler:
        """Get the TTS event handler."""
        from agentpool.agents.events.tts_handlers import OpenAITTSEventHandler

        key = self.api_key.get_secret_value() if self.api_key else None
        return OpenAITTSEventHandler(
            api_key=key,
            model=self.model,
            voice=self.voice,
            speed=self.speed,
            chunk_size=self.chunk_size,
            sample_rate=self.sample_rate,
            mode=self.mode,
            min_text_length=self.min_text_length,
        )


class EdgeTTSEventHandlerConfig(BaseEventHandlerConfig):
    """Configuration for Edge TTS event handler (free, no API key required).

    Uses Microsoft Edge's TTS service via edge-tts library.
    Supports many voices and languages without requiring an API key.
    """

    model_config = ConfigDict(title="Edge TTS Event Handler")

    type: Literal["tts-edge"] = Field("tts-edge", init=False)
    """Edge TTS event handler."""

    voice: EdgeTTSVoice = Field(
        default="en-US-AriaNeural",
        examples=[
            "en-US-AriaNeural",
            "en-US-GuyNeural",
            "en-GB-SoniaNeural",
            "de-DE-KatjaNeural",
            "fr-FR-DeniseNeural",
        ],
        title="Voice name",
    )
    """Voice to use for synthesis.

    Use `edge-tts --list-voices` to see all available voices.
    Format: {locale}-{Name}Neural (e.g., en-US-AriaNeural)
    """

    speed: float = Field(
        default=1.0,
        ge=0.25,
        le=4.0,
        examples=[0.5, 1.0, 1.5, 2.0],
        title="Speed of speech",
    )
    """Speed of speech (0.25 to 4.0, default 1.0)."""

    volume: float = Field(
        default=1.0,
        ge=0.0,
        le=2.0,
        examples=[0.5, 1.0, 1.5, 2.0],
        title="Volume",
    )
    """Volume level (0.0 to 2.0, default 1.0 = normal)."""

    pitch: float = Field(
        default=0.0,
        ge=-100.0,
        le=100.0,
        examples=[-50.0, 0.0, 25.0, 50.0],
        title="Pitch",
    )
    """Pitch adjustment in Hz (default 0.0 = no change)."""

    sample_rate: int = Field(default=24000, examples=[16000, 24000, 44100], title="Sample Rate")
    """Audio sample rate in Hz for playback."""

    min_text_length: int = Field(
        default=20,
        ge=5,
        examples=[10, 20, 50],
        title="Minimum Text Length",
    )
    """Minimum text length before synthesizing (in characters)."""

    mode: SyncMode = Field(
        default="sync_run",
        examples=["sync_sentence", "sync_run", "async_queue", "async_cancel"],
        title="Synchronization Mode",
    )
    """How TTS synthesis synchronizes with the event stream.

    - sync_sentence: Wait for each sentence's audio before continuing (slowest, most synchronized)
    - sync_run: Stream fast, wait for all audio at run end (default, recommended)
    - async_queue: Stream fast, audio plays in background, multiple runs queue up
    - async_cancel: Stream fast, audio plays in background, new run cancels previous audio
    """

    def get_handler(self) -> IndividualEventHandler:
        """Get the Edge TTS event handler."""
        from agentpool.agents.events.tts_handlers import EdgeTTSEventHandler

        # Convert to Edge TTS string formats
        # speed: 1.0 -> "+0%", 1.5 -> "+50%", 0.5 -> "-50%"
        # volume: 1.0 -> "+0%", 1.5 -> "+50%", 0.5 -> "-50%"
        # pitch: 0.0 -> "+0Hz", 50.0 -> "+50Hz", -25.0 -> "-25Hz"
        rate = f"{round((self.speed - 1.0) * 100):+d}%"
        volume = f"{round((self.volume - 1.0) * 100):+d}%"
        pitch = f"{round(self.pitch):+d}Hz"

        return EdgeTTSEventHandler(
            voice=self.voice,
            rate=rate,
            volume=volume,
            pitch=pitch,
            sample_rate=self.sample_rate,
            mode=self.mode,
            min_text_length=self.min_text_length,
        )


EventHandlerConfig = Annotated[
    StdoutEventHandlerConfig
    | CallbackEventHandlerConfig
    | FileStreamEventHandlerConfig
    | TTSEventHandlerConfig
    | EdgeTTSEventHandlerConfig,
    Field(discriminator="type"),
]


def resolve_handler_configs(
    configs: Sequence[EventHandlerConfig] | None,
) -> list[IndividualEventHandler]:
    """Resolve event handler configs to actual handler callables.

    Args:
        configs: List of event handler configurations.

    Returns:
        List of resolved event handler callables.
    """
    if not configs:
        return []
    return [cfg.get_handler() for cfg in configs]
