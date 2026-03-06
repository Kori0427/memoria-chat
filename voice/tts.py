"""Text-to-speech — Edge TTS (free) or API (Memoria proxy) backend."""

from __future__ import annotations

import asyncio

import numpy as np


class BaseTTS:
    """Common interface for TTS backends.

    Subclasses implement ``_load_model`` and ``_synthesize_sync``.
    """

    def _load_model(self) -> None:
        raise NotImplementedError

    def _synthesize_sync(
        self, text: str, voice: str, speed: float, lang: str,
    ) -> tuple[np.ndarray, int]:
        """Run TTS. Returns ``(float32_audio, sample_rate)``."""
        raise NotImplementedError

    def _ensure_model(self) -> None:
        if getattr(self, "_model", None) is not None:
            return
        self._load_model()

    def warm(self) -> None:
        """Pre-load the model so the first synthesis is fast."""
        self._ensure_model()

    async def synthesize(
        self, text: str, voice: str = "af_heart", speed: float = 1.0, lang: str = "cmn",
    ) -> tuple[np.ndarray, int]:
        """Synthesize text to audio. Returns ``(float32_array, sample_rate)``."""
        return await asyncio.to_thread(
            self._synthesize_sync, text, voice, speed, lang,
        )


class EdgeTTS(BaseTTS):
    """Free TTS via Microsoft Edge Read Aloud service (requires internet)."""

    _LOG_PREFIX = "[TTS-edge]"

    def __init__(self) -> None:
        self._model = True  # no local model to load

    def _load_model(self) -> None:
        pass

    def warm(self) -> None:
        pass

    # English voice counterpart for bilingual support
    _EN_VOICES = {
        "zh-CN-YunxiNeural": "en-US-GuyNeural",
        "zh-CN-XiaoxiaoNeural": "en-US-JennyNeural",
        "zh-CN-YunyangNeural": "en-US-GuyNeural",
        "zh-CN-XiaoyiNeural": "en-US-JennyNeural",
        "zh-CN-YunjianNeural": "en-US-GuyNeural",
    }

    async def synthesize(self, text, voice="zh-CN-YunxiNeural", speed=1.0, lang="cmn"):
        """Call edge-tts, decode MP3 to numpy array."""
        import edge_tts
        import io

        # Strip emoji and other non-speech characters
        text = _strip_emoji(text).strip()
        if not text:
            return np.zeros(0, dtype=np.float32), 24000

        # Auto-switch to English voice if text is mostly non-CJK
        actual_voice = voice
        if _detect_lang(text) == "en-us" and voice in self._EN_VOICES:
            actual_voice = self._EN_VOICES[voice]

        # edge-tts rate format: "+0%", "+20%", "-10%"
        rate_pct = round((speed - 1.0) * 100)
        rate_str = f"{rate_pct:+d}%"

        comm = edge_tts.Communicate(text, actual_voice, rate=rate_str)
        mp3_buf = b""
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                mp3_buf += chunk["data"]

        if not mp3_buf:
            return np.zeros(0, dtype=np.float32), 24000

        # Decode MP3 → numpy via soundfile (supports MP3)
        import soundfile as sf
        audio_np, sr = sf.read(io.BytesIO(mp3_buf), dtype="float32")
        # Ensure mono
        if audio_np.ndim > 1:
            audio_np = audio_np[:, 0]
        return audio_np, sr

    def _synthesize_sync(self, text, voice, speed, lang):
        raise NotImplementedError("EdgeTTS uses async synthesize()")


class APITTS(BaseTTS):
    """TTS via Memoria /api/voice/tts proxy (OpenAI TTS API)."""

    def __init__(self, client) -> None:
        self._client = client
        self._model = True  # sentinel to skip _ensure_model

    def _load_model(self) -> None:
        pass  # no local model

    async def synthesize(self, text, voice="alloy", speed=1.0, lang="z"):
        """Call Memoria TTS API, decode WAV, return numpy array."""
        import audio_io

        wav_bytes = await self._client.text_to_speech(text, voice=voice, speed=speed)
        audio_np, sr = audio_io.decode_wav_bytes(wav_bytes)
        return audio_np, sr

    def _synthesize_sync(self, text, voice, speed, lang):
        raise NotImplementedError("APITTS uses async synthesize()")

    def warm(self) -> None:
        pass  # nothing to warm


import re

_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero width joiner
    "\U00002600-\U000026FF"  # misc symbols
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U00002300-\U000023FF"  # misc technical
    "]+",
    flags=re.UNICODE,
)


def _strip_emoji(text: str) -> str:
    """Remove emoji characters from text."""
    return _EMOJI_RE.sub("", text)


def _detect_lang(text: str) -> str:
    """Simple heuristic: if >30% CJK characters, return Chinese lang code."""
    if not text:
        return "en-us"
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return "cmn" if cjk / len(text) > 0.3 else "en-us"


def make_tts(provider: str, client=None) -> BaseTTS:
    """Create a TTS backend.

    Args:
        provider: "edge" for Microsoft Edge TTS (free),
                  "api" for Memoria proxy (OpenAI TTS).
        client: MemoriaClient instance (required for "api" provider).
    """
    if provider == "edge":
        return EdgeTTS()
    if provider == "api":
        if client is None:
            raise ValueError("APITTS requires a MemoriaClient instance")
        return APITTS(client)
    raise ValueError(f"Unknown tts_provider: {provider!r}")
