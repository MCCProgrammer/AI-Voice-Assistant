"""Glues microphone, wake word, speech, the model and the UI together.

States: idle → listening → transcribing → thinking → speaking → (follow-up listening) → idle
"""
import json
import logging
import queue
import threading
import time

from .audio import Microphone, Speaker, WakeWord, record_utterance, rms
from .brain import Brain, make_client
from .config import Config
from .memory import Memory
from .speech import SentenceSplitter, speakable, synthesize, transcribe
from .tools import ToolBox

log = logging.getLogger(__name__)

TOOL_LABELS = {
    "get_datetime": "Checking the time", "get_weather": "Checking the weather", "web_search": "Searching the web",
    "get_news": "Reading the news", "read_webpage": "Reading a page", "wikipedia": "Looking it up on Wikipedia",
    "open_website": "Opening a website", "play_on_youtube": "Playing on YouTube", "open_app": "Opening an app",
    "set_volume": "Changing the volume", "system_status": "Checking the system", "set_timer": "Setting a timer",
    "remember": "Remembering", "forget": "Forgetting", "add_note": "Taking a note", "list_notes": "Reading notes",
    "clear_notes": "Clearing notes",
}


class Controller:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.windows: list = []
        self.memory = Memory(cfg.data_dir)
        self.client = make_client(cfg)
        self.tools = ToolBox(self.memory, on_timer=self._timer_done)
        self.brain = Brain(cfg, self.client, self.tools, self.memory)
        self.mic = Microphone()
        self.speaker = Speaker(on_level=lambda level: self._emit("level", value=level, source="speaker"))
        self.wake: WakeWord | None = None
        self.wake_enabled = cfg.wake_word
        self.muted = False
        self.state = "idle"
        self._cancel = threading.Event()
        self._busy = threading.Lock()
        self._last_level = 0.0
        self._started = False
        # UI updates go through their own thread: evaluate_js waits for the page, and doing that on
        # the microphone thread made it drop audio frames (and miss the wake word).
        self._events: queue.Queue[dict] = queue.Queue()
        self._pending_level: dict | None = None
        threading.Thread(target=self._ui_loop, name="ui-events", daemon=True).start()

    # ---------- UI bridge ----------

    def attach(self, window) -> None:
        if window not in self.windows:
            self.windows.append(window)

    def _emit(self, kind: str, **data) -> None:
        if kind == "level":
            self._pending_level = data  # only the latest level matters
        else:
            self._events.put({"type": kind, **data})

    def _ui_loop(self) -> None:
        last_level = 0.0
        while True:
            try:
                self._send(self._events.get(timeout=0.03))
            except queue.Empty:
                pass
            if self._pending_level is not None and time.monotonic() - last_level >= 0.04:
                level, self._pending_level = self._pending_level, None
                last_level = time.monotonic()
                self._send({"type": "level", **level})

    def _send(self, event: dict) -> None:
        script = f"window.ui && window.ui.handle({json.dumps(event)})"
        for window in list(self.windows):
            try:
                window.evaluate_js(script)
            except Exception:
                log.debug("UI not ready for %s", event["type"])

    def _set_state(self, state: str) -> None:
        self.state = state
        self._emit("state", state=state)

    def info(self) -> dict:
        return {
            "name": self.cfg.name,
            "model": self.cfg.model,
            "hasKey": bool(self.cfg.api_key),
            "wakeWord": self.wake_enabled,
            "wakeReady": self.wake is not None,
            "muted": self.muted,
            "state": self.state,
        }

    # ---------- Startup ----------

    def start(self) -> None:
        if self._started:  # the "loaded" event fires again if the page reloads
            return
        self._started = True
        try:
            self.mic.start()
        except Exception as error:
            log.exception("microphone unavailable")
            self._emit("error", message=f"Microphone unavailable: {error}. You can still type.")
            return
        self.mic.subscribe(self._on_frame)
        threading.Thread(target=self._load_wake_word, daemon=True).start()

    def _load_wake_word(self) -> None:
        try:
            self.wake = WakeWord(self.cfg.wake_threshold)
            self._emit("info", **self.info())
        except Exception as error:
            log.exception("wake word unavailable")
            self._emit("toast", message=f"Wake word unavailable ({error}). Use the mic button or Space.")

    def _on_frame(self, frame) -> None:
        if self.state == "idle" and self.wake_enabled and self.wake and self.wake.heard(frame):
            self.listen()
        if self.state in ("idle", "listening"):
            level = min(1.0, rms(frame) / 4000)
            # Throttle UI updates a little and skip near-silence repeats.
            if abs(level - self._last_level) > 0.01 or level > 0.02:
                self._last_level = level
                self._emit("level", value=level, source="mic")

    # ---------- Actions from the UI ----------

    def listen(self) -> None:
        """Starts a voice conversation (wake word, mic button or Space)."""
        if self._busy.locked():
            return
        threading.Thread(target=self._conversation, kwargs={"voice": True}, daemon=True).start()

    def send_text(self, text: str) -> None:
        text = text.strip()
        if not text or self._busy.locked():
            return
        threading.Thread(target=self._conversation, kwargs={"voice": False, "text": text}, daemon=True).start()

    def stop(self) -> None:
        self._cancel.set()
        self.speaker.stop()

    def set_wake_word(self, enabled: bool) -> None:
        self.wake_enabled = enabled
        if self.wake:
            self.wake.reset()

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if muted:
            self.speaker.stop()

    def clear(self) -> None:
        self.brain.reset()

    # ---------- Conversation loop ----------

    def _conversation(self, voice: bool, text: str | None = None) -> None:
        if not self._busy.acquire(blocking=False):
            return
        self._cancel.clear()
        follow_up = False
        try:
            while not self._cancel.is_set():
                if voice:
                    text = self._hear(follow_up)
                    if not text:
                        break
                self._emit("user", text=text)
                self._respond(text)
                if not voice or self._cancel.is_set():
                    break
                follow_up = True  # keep listening for a reply without the wake word
        except Exception as error:
            log.exception("conversation failed")
            self._emit("error", message=self._describe(error))
        finally:
            self.speaker.stop()
            self._set_state("idle")
            if self.wake:
                self.wake.reset()
            self._busy.release()

    def _hear(self, follow_up: bool) -> str | None:
        self._set_state("listening")
        pcm = record_utterance(self.mic, self._cancel, start_timeout=5.0 if follow_up else 7.0)
        if not pcm:
            return None
        self._set_state("transcribing")
        text = transcribe(pcm, self.cfg, self.client)
        if not text:
            self._emit("toast", message="Sorry, I didn't catch that.")
        return text or None

    def _respond(self, text: str) -> None:
        if not self.cfg.api_key:
            self._emit("error", message="Add your OPENROUTER_API_KEY to the .env file and restart.")
            return
        self._set_state("thinking")
        self._emit("assistant_start")
        sentences: queue.Queue[str | None] = queue.Queue()
        voice_thread = threading.Thread(target=self._voice_worker, args=(sentences,), daemon=True)
        voice_thread.start()
        splitter = SentenceSplitter()

        def on_text(delta: str) -> None:
            self._emit("assistant_delta", text=delta)
            for sentence in splitter.feed(delta):
                sentences.put(sentence)

        def on_tool(name: str, args: dict) -> None:
            for sentence in splitter.flush():  # speak what was said before the tool runs
                sentences.put(sentence)
            self._emit("tool", name=name, label=TOOL_LABELS.get(name, name), args=args)

        try:
            self.brain.reply(text, self._cancel, on_text, on_tool)
        finally:
            for sentence in splitter.flush():
                sentences.put(sentence)
            sentences.put(None)
            self._emit("assistant_end")
        voice_thread.join()
        # Wait until the last clip has played (or the user pressed stop).
        while not self.speaker.idle.wait(0.1):
            if self._cancel.is_set():
                break

    def _voice_worker(self, sentences: "queue.Queue[str | None]") -> None:
        spoke = False
        while (sentence := sentences.get()) is not None:
            if self.muted or self._cancel.is_set():
                continue
            pcm = synthesize(speakable(sentence), self.cfg.voice)
            if pcm is None or self._cancel.is_set():
                continue
            if not spoke:
                spoke = True
                self._set_state("speaking")
            self.speaker.play(pcm)
        self.speaker.finish()

    def _timer_done(self, label: str) -> None:
        self._emit("toast", message=f"⏰ {label} — time's up!")
        if not self.muted and not self._busy.locked():
            pcm = synthesize(f"Your {label.lower()} timer is done.", self.cfg.voice)
            if pcm is not None:
                self.speaker.play(pcm)
                self.speaker.finish()

    @staticmethod
    def _describe(error: Exception) -> str:
        message = str(error)
        if "401" in message or "No auth" in message:
            return "OpenRouter rejected the API key. Check OPENROUTER_API_KEY in .env."
        if "402" in message:
            return "Your OpenRouter account is out of credits."
        if "429" in message:
            return "Rate limited by OpenRouter — wait a moment and try again."
        return f"Something went wrong: {message[:200]}"
