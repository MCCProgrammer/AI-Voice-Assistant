"""Microphone input, utterance recording, wake word detection and speech playback."""
import logging
import queue
import threading
import time
from collections import deque
from typing import Callable

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

RATE = 16000
BLOCK = 1280  # 80 ms — the frame size openWakeWord expects


def rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


class Microphone:
    """One always-open input stream that fans frames out to subscribers.

    Also tracks the background noise level so speech detection adapts to the room.
    """

    def __init__(self) -> None:
        self._listeners: list[Callable[[np.ndarray], None]] = []
        self._lock = threading.Lock()
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._stream: sd.InputStream | None = None
        self.noise_floor = 200.0

    def start(self) -> None:
        self._stream = sd.InputStream(
            samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK, callback=self._callback
        )
        self._stream.start()
        threading.Thread(target=self._dispatch, name="mic-dispatch", daemon=True).start()

    def subscribe(self, fn: Callable[[np.ndarray], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._listeners:
                    self._listeners.remove(fn)

        return unsubscribe

    def _callback(self, indata, _frames, _time, status) -> None:
        if status:
            log.debug("input status: %s", status)
        try:
            self._queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            log.warning("microphone frame dropped (the app is too busy)")

    def _dispatch(self) -> None:
        while True:
            frame = self._queue.get()
            level = rms(frame)
            # Follow quiet frames quickly and loud ones very slowly, so speech doesn't raise the floor.
            if level < self.noise_floor * 2:
                self.noise_floor = 0.95 * self.noise_floor + 0.05 * level
            else:
                self.noise_floor *= 1.002
            self.noise_floor = max(self.noise_floor, 50.0)
            with self._lock:
                listeners = list(self._listeners)
            for fn in listeners:
                try:
                    fn(frame)
                except Exception:
                    log.exception("microphone listener failed")


def record_utterance(
    mic: Microphone,
    cancel: threading.Event,
    *,
    start_timeout: float = 6.0,
    max_seconds: float = 15.0,
    silence_after: float = 0.9,
) -> bytes | None:
    """Records one spoken phrase and returns 16 kHz mono PCM, or None if nobody spoke."""
    frames: queue.Queue[np.ndarray] = queue.Queue()
    unsubscribe = mic.subscribe(frames.put)
    preroll: deque[np.ndarray] = deque(maxlen=4)  # keep ~300 ms before speech starts
    recorded: list[np.ndarray] = []
    frame_seconds = BLOCK / RATE
    waited = silent = spoken = 0.0
    try:
        while not cancel.is_set():
            try:
                frame = frames.get(timeout=0.5)
            except queue.Empty:
                continue
            loud = rms(frame) > max(mic.noise_floor * 3.0, 350.0)
            if not recorded:
                preroll.append(frame)
                waited += frame_seconds
                if loud:
                    recorded.extend(preroll)
                elif waited >= start_timeout:
                    return None
                continue
            recorded.append(frame)
            spoken += frame_seconds
            silent = 0.0 if loud else silent + frame_seconds
            if silent >= silence_after or spoken >= max_seconds:
                break
    finally:
        unsubscribe()
    if cancel.is_set() or spoken < 0.3:
        return None
    return np.concatenate(recorded).tobytes()


class WakeWord:
    """Detects "Hey Jarvis" with openWakeWord (runs locally, nothing is sent anywhere)."""

    def __init__(self, threshold: float) -> None:
        from openwakeword.model import Model
        from openwakeword.utils import download_models

        download_models(model_names=["hey_jarvis"])  # no-op once the models are cached
        self._model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        self._threshold = threshold
        self._cooldown_until = 0.0
        self._last_log = 0.0

    def heard(self, frame: np.ndarray) -> bool:
        score = max(self._model.predict(frame).values())
        if score > 0.1 and time.monotonic() > self._last_log + 0.5:
            self._last_log = time.monotonic()  # run with --verbose to tune WAKE_THRESHOLD
            log.info("wake word score %.2f (threshold %.2f)", score, self._threshold)
        if time.monotonic() < self._cooldown_until:
            return False
        if score >= self._threshold:
            self.reset()
            return True
        return False

    def reset(self, cooldown: float = 1.5) -> None:
        self._model.reset()
        self._cooldown_until = time.monotonic() + cooldown


class Speaker:
    """Plays queued PCM clips one after another and reports the output level for the orb."""

    OUT_RATE = 24000  # edge-tts voices are 24 kHz

    def __init__(self, on_level: Callable[[float], None]) -> None:
        self._on_level = on_level
        self._clips: queue.Queue[np.ndarray | None] = queue.Queue()
        self._stop = threading.Event()
        self.idle = threading.Event()
        self.idle.set()
        threading.Thread(target=self._run, name="speaker", daemon=True).start()

    def play(self, pcm: np.ndarray) -> None:
        self.idle.clear()
        self._stop.clear()
        self._clips.put(pcm)

    def finish(self) -> None:
        """Marks the end of the current reply: `idle` is set once everything queued has played."""
        self._clips.put(None)

    def stop(self) -> None:
        self._stop.set()
        while not self._clips.empty():
            try:
                self._clips.get_nowait()
            except queue.Empty:
                break
        self.idle.set()

    def _run(self) -> None:
        block = self.OUT_RATE // 25  # 40 ms
        while True:
            clip = self._clips.get()
            if clip is None:
                self.idle.set()
                self._on_level(0.0)
                continue
            if self._stop.is_set():
                continue
            try:
                with sd.OutputStream(samplerate=self.OUT_RATE, channels=1, dtype="int16") as out:
                    for start in range(0, len(clip), block):
                        if self._stop.is_set():
                            break
                        chunk = clip[start:start + block]
                        out.write(chunk.reshape(-1, 1))
                        self._on_level(min(1.0, rms(chunk) / 6000))
            except Exception:
                log.exception("audio playback failed")
            self._on_level(0.0)
