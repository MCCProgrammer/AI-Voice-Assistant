"""Speech-to-text and text-to-speech."""
import asyncio
import base64
import io
import logging
import re
import wave

import miniaudio
import numpy as np
import speech_recognition as sr

from .audio import RATE, Speaker
from .config import Config

log = logging.getLogger(__name__)


# ---------- Speech to text ----------

def transcribe(pcm: bytes, cfg: Config, client=None) -> str:
    """Turns recorded PCM into text. Returns "" when nothing intelligible was said."""
    if cfg.stt == "openrouter" and client is not None:
        return _transcribe_openrouter(pcm, cfg, client)
    audio = sr.AudioData(pcm, RATE, 2)
    try:
        return sr.Recognizer().recognize_google(audio, language=cfg.language).strip()
    except sr.UnknownValueError:
        return ""


def _transcribe_openrouter(pcm: bytes, cfg: Config, client) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    response = client.chat.completions.create(
        model=cfg.stt_model,
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Transcribe this audio exactly. Reply with the transcript only, "
                                         "or with nothing if there is no speech."},
                {"type": "input_audio",
                 "input_audio": {"data": base64.b64encode(buf.getvalue()).decode(), "format": "wav"}},
            ],
        }],
    )
    return (response.choices[0].message.content or "").strip()


# ---------- Text to speech ----------

async def _synthesize(text: str, voice: str) -> bytes:
    import edge_tts

    audio = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
    return bytes(audio)


def synthesize(text: str, voice: str) -> np.ndarray | None:
    """Neural voice (Microsoft Edge TTS) as 24 kHz mono PCM, or None if it isn't reachable."""
    try:
        mp3 = asyncio.run(_synthesize(text, voice))
        decoded = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16,
                                   nchannels=1, sample_rate=Speaker.OUT_RATE)
        return np.frombuffer(decoded.samples, dtype=np.int16)
    except Exception:
        log.exception("text-to-speech failed")
        return None


_MARKDOWN = re.compile(r"[*_`#>|]+|\[(.*?)\]\(.*?\)")


def speakable(text: str) -> str:
    """Strips Markdown so the voice doesn't read symbols out loud."""
    return _MARKDOWN.sub(lambda m: m.group(1) or "", text).strip()


class SentenceSplitter:
    """Buffers streamed text and returns whole sentences, so speech can start before the reply ends.

    Very short sentences ("Sure.") are merged with the next one to avoid choppy clips.
    """

    _BOUNDARY = re.compile(r"[.!?…:;](?=\s)|\n")  # punctuation followed by a space, so "3.5" isn't split

    def __init__(self, min_length: int = 24) -> None:
        self._buffer = ""
        self._min = min_length

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        sentences, start = [], 0
        for match in self._BOUNDARY.finditer(self._buffer):
            piece = self._buffer[start:match.end()].strip()
            if len(piece) >= self._min:
                sentences.append(piece)
                start = match.end()
        self._buffer = self._buffer[start:]
        return sentences

    def flush(self) -> list[str]:
        rest, self._buffer = self._buffer.strip(), ""
        return [rest] if rest else []
