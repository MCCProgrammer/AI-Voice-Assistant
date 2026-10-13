"""Glues microphone, wake word, speech, the model and the UI together.

States: idle → listening → transcribing → thinking → speaking → (follow-up listening) → idle
"""
import json
import logging
import platform
import queue
import re
import subprocess
import threading
import time
from datetime import datetime

from .audio import Microphone, Speaker, WakeWord, record_utterance, rms
from .brain import Brain, make_client
from .config import Config
from .memory import Memory
from .speech import SentenceSplitter, speakable, synthesize, transcribe
from .tools import ToolBox

log = logging.getLogger(__name__)

# Saying one of these ends the conversation without asking the model.
END_PHRASES = re.compile(
    r"^(ok(ay)?,? )?(stop|cancel|never ?mind|forget it|that'?s (all|it)|nothing|no,? thanks?( you)?|bye|goodbye|"
    r"thanks?( you)?,? (that'?s all|bye))[.!]*$", re.I)
# In "always on" mode these pause listening; any of them turns it (back) on.
PAUSE_PHRASES = re.compile(r"^(ok(ay)?,? )?(stop|stop listening|pause|pause listening|go to sleep|be quiet|"
                           r"never ?mind|that'?s (all|it)|bye|goodbye)[.!]*$", re.I)
RESUME_PHRASES = re.compile(r"\b(start listening|always listen(ing)?|keep listening|listen to me)\b", re.I)
MODES = ("wake", "always", "manual")  # "Hey Jarvis" · always listening · push to talk only

# Sounds that speech-to-text sometimes returns for noise; ignored while waiting for an answer.
FILLERS = {"uh", "um", "umm", "hmm", "mm", "mhm", "ah", "oh", "er", "erm", "huh", "the", "a"}


def asks_question(reply: str) -> bool:
    """Whether the assistant ended its reply with a question, so the user is expected to answer."""
    return reply.strip().rstrip("\"'”’)] ").endswith("?")

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
        self.memory.on_change = lambda: self._emit("data", **self._data())
        self.client = make_client(cfg)
        self.tools = ToolBox(self.memory, on_timer=self._timer_done)
        self.brain = Brain(cfg, self.client, self.tools, self.memory)
        self.mic = Microphone()
        self.speaker = Speaker(on_level=lambda level: self._emit("level", value=level, source="speaker"))
        self.wake: WakeWord | None = None
        self.mode = self.memory.setting("mode") or ("wake" if cfg.wake_word else "manual")
        self.mic_ready = False
        self.muted = False
        self.state = "idle"
        self._cancel = threading.Event()
        self._busy = threading.Lock()
        self._last_level = 0.0
        self._started = False
        self._ambient_cancel = threading.Event()  # interrupts the always-on recording
        self._turn_ended = 0.0
        self._awaiting_answer = False  # the last reply asked the user something
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

    def _set_state(self, state: str, **extra) -> None:
        self.state = state
        self._emit("state", state=state, **extra)

    def info(self) -> dict:
        return {
            "name": self.cfg.name,
            "model": self.cfg.model,
            "hasKey": bool(self.cfg.api_key),
            "mode": self.mode,
            "wakeReady": self.wake is not None,
            "muted": self.muted,
            "state": self.state,
            "listenSeconds": self.cfg.listen_seconds,
            **self._data(),
        }

    def _data(self) -> dict:
        return {"reminders": self.memory.reminders, "shortcuts": self.memory.shortcuts}

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
        self.mic_ready = True
        self.mic.subscribe(self._on_frame)
        threading.Thread(target=self._load_wake_word, daemon=True).start()
        threading.Thread(target=self._ambient_loop, name="always-on", daemon=True).start()
        self._set_state(self._rest_state())
        threading.Thread(target=self._reminder_loop, name="reminders", daemon=True).start()

    def _load_wake_word(self) -> None:
        try:
            self.wake = WakeWord(self.cfg.wake_threshold)
            self._emit("info", **self.info())
        except Exception as error:
            log.exception("wake word unavailable")
            self._emit("toast", message=f"Wake word unavailable ({error}). Use the mic button or Space.")

    def _on_frame(self, frame) -> None:
        if self.state == "idle" and self.mode == "wake" and self.wake and self.wake.heard(frame):
            self.listen()
        if self.state in ("idle", "listening", "ambient"):
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

    def set_mode(self, mode: str, announce: bool = False) -> None:
        """Switches between "wake" (Hey Jarvis), "always" (always listening) and "manual" (push to talk)."""
        if mode not in MODES:
            return
        self.mode = mode
        self.memory.set_setting("mode", mode)
        self._ambient_cancel.set()  # restart or stop the always-on recording
        if self.wake:
            self.wake.reset()
        if self.state in ("idle", "ambient"):
            self._set_state(self._rest_state())
        self._emit("info", **self.info())
        if announce:
            self._emit("toast", message={
                "always": "Always listening — just talk. Say “stop” to pause.",
                "wake": f"Paused. Say “Hey {self.cfg.name}, start listening” or click Always on.",
                "manual": "Push to talk: press Space or click the orb.",
            }[mode])

    def _rest_state(self) -> str:
        return "ambient" if self.mode == "always" and self.mic_ready else "idle"

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if muted:
            self.speaker.stop()

    def clear(self) -> None:
        self.brain.reset()

    # ---------- Conversation loop ----------

    def _conversation(self, voice: bool, text: str | None = None) -> None:
        """One exchange. When talking, the mic stays open only if the assistant asked a question:
        then it waits for the answer until the user speaks, says "never mind"/"stop", or stays quiet."""
        if not self._busy.acquire(blocking=False):
            return
        self._cancel.clear()
        self._ambient_cancel.set()  # the always-on loop must not record this turn (or our own voice)
        follow_up = False
        misses = 0
        try:
            while not self._cancel.is_set():
                if voice:
                    text = self._hear(follow_up)
                    if text is None:  # nobody spoke
                        break
                    if not text or text.strip().lower().strip(".!?") in FILLERS:
                        # Noise or unintelligible: when waiting for an answer, keep listening quietly.
                        misses += 1
                        if misses > (3 if follow_up else 1):
                            break
                        continue
                    misses = 0
                self._emit("user", text=text)
                if voice and RESUME_PHRASES.search(text):
                    self.set_mode("always", announce=True)
                    break
                if voice and END_PHRASES.match(text.strip()):
                    self._emit("toast", message="Okay — I'm here if you need me 👋")
                    break
                reply = self._respond(text)
                if not voice or self._cancel.is_set() or not asks_question(reply):
                    break  # an action or a plain answer: done, the mic closes
                follow_up = True
        except Exception as error:
            log.exception("conversation failed")
            self._emit("error", message=self._describe(error))
        finally:
            self.speaker.stop()
            self._turn_ended = time.monotonic()
            self._set_state(self._rest_state())
            if self.wake:
                self.wake.reset()
            self._busy.release()

    # ---------- Always-on listening ----------

    def _ambient_loop(self) -> None:
        """In "always" mode: every sentence the user says is answered, until they say "stop"."""
        while True:
            if not self.speaker.idle.is_set():
                self._turn_ended = time.monotonic()  # e.g. a reminder is being read out
            if self.mode != "always" or self._busy.locked() or time.monotonic() - self._turn_ended < 0.7:
                time.sleep(0.2)  # paused, busy with a turn, or letting our own voice die out
                continue
            self._ambient_cancel.clear()
            if self.state not in ("ambient", "listening"):
                self._set_state("ambient")
            pcm = record_utterance(
                self.mic, self._ambient_cancel, start_timeout=60.0, silence_after=self.cfg.pause_seconds,
                max_seconds=45.0, min_speech=0.5, sensitivity=3.5,
                on_voice=lambda speaking: self._set_state("listening" if speaking else "ambient"))
            if not pcm or self.mode != "always" or not self._busy.acquire(blocking=False):
                if self.state == "listening" and not self._busy.locked():
                    self._set_state(self._rest_state())
                continue
            try:
                self._cancel.clear()
                self._set_state("transcribing")
                text = transcribe(pcm, self.cfg, self.client).strip()
                if not text or text.lower().strip(".!?") in FILLERS:
                    continue
                if PAUSE_PHRASES.match(text):
                    self._emit("user", text=text)
                    self.set_mode("wake" if self.wake else "manual", announce=True)
                    continue
                if len(text.split()) < 2 and not self._awaiting_answer:
                    continue  # single words are usually noise, unless they answer a question ("yes", "Lisbon")
                self._emit("user", text=text)
                self._awaiting_answer = asks_question(self._respond(text))
            except Exception as error:
                log.exception("always-on turn failed")
                self._emit("error", message=self._describe(error))
            finally:
                self.speaker.stop()
                self._turn_ended = time.monotonic()
                self._set_state(self._rest_state())
                self._busy.release()

    def _hear(self, follow_up: bool) -> str | None:
        """Returns what was said, "" if it couldn't be understood, or None if nobody spoke."""
        if follow_up:
            time.sleep(0.5)  # let the tail of our own voice die out before listening
        self._set_state("listening", followUp=follow_up)
        pcm = record_utterance(
            self.mic, self._cancel,
            start_timeout=self.cfg.listen_seconds * (2 if follow_up else 1),
            silence_after=self.cfg.pause_seconds,
            max_seconds=45.0,
            min_speech=0.6 if follow_up else 0.4,
            sensitivity=4.0 if follow_up else 3.0,
        )
        if not pcm:
            return None
        self._set_state("transcribing")
        text = transcribe(pcm, self.cfg, self.client)
        if not text and not follow_up:
            self._emit("toast", message="Sorry, I didn't catch that — say it again.")
        return text

    def _respond(self, text: str) -> str:
        """Asks the model, shows and speaks the reply as it streams; returns the full reply."""
        if not self.cfg.api_key:
            self._emit("error", message="Add your OPENROUTER_API_KEY to the .env file and restart.")
            return ""
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
            reply = self.brain.reply(text, self._cancel, on_text, on_tool,
                             on_card=lambda name, card: self._emit("card", tool=name, card=card))
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
        return reply

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

    # ---------- Reminders and timers ----------

    def _reminder_loop(self) -> None:
        while True:
            for reminder in self.memory.due_reminders(datetime.now()):
                self.memory.complete_reminder(reminder["id"])
                self._alert(f"Reminder: {reminder['text']}", {"type": "reminder", **reminder, "alert": True})
            time.sleep(10)

    def delete_reminder(self, reminder_id: str) -> None:
        self.memory.cancel_reminders(reminder_id)

    def _timer_done(self, label: str) -> None:
        self._alert(f"Your {label.lower()} timer is done.", {"type": "memory", "text": f"⏰ {label} — time's up!"})

    def _alert(self, spoken: str, card: dict) -> None:
        """Shows, notifies and says something the user asked to be reminded of."""
        self._emit("card", tool="alert", card=card)
        self._emit("toast", message=f"🔔 {spoken}")
        if platform.system() == "Darwin":
            script = f'display notification {json.dumps(spoken)} with title {json.dumps(self.cfg.name)} sound name "Glass"'
            subprocess.run(["osascript", "-e", script], capture_output=True)
        if not self.muted and not self._busy.locked():
            pcm = synthesize(spoken, self.cfg.voice)
            if pcm is not None:
                self._ambient_cancel.set()  # don't let always-on listening hear the alert
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
