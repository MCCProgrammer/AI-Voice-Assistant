import os
from dataclasses import dataclass
from pathlib import Path

import certifi
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
# Python from python.org on macOS ships without root certificates; use certifi's bundle for every request.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())


def _flag(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    api_key: str
    model: str
    name: str
    voice: str
    language: str
    wake_word: bool
    wake_threshold: float
    stt: str
    stt_model: str
    data_dir: Path


def load_config() -> Config:
    data_dir = Path(os.getenv("DATA_DIR", Path.home() / ".jarvis-assistant")).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return Config(
        api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
        model=os.getenv("MODEL", "anthropic/claude-haiku-5.5").strip(),
        name=os.getenv("ASSISTANT_NAME", "Jarvis").strip() or "Jarvis",
        voice=os.getenv("VOICE", "en-GB-RyanNeural").strip(),
        language=os.getenv("LANGUAGE", "en-US").strip(),
        wake_word=_flag(os.getenv("WAKE_WORD"), True),
        wake_threshold=float(os.getenv("WAKE_THRESHOLD", "0.5")),
        stt=os.getenv("STT", "google").strip().lower(),
        stt_model=os.getenv("STT_MODEL", "google/gemini-3.8-flash").strip(),
        data_dir=data_dir,
    )
