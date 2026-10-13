"""Starts the Jarvis desktop app."""
import logging
import sys

import webview

from assistant.config import ROOT, load_config
from assistant.controller import Controller


class Bridge:
    """Methods the web UI can call through window.pywebview.api."""

    def __init__(self, controller: Controller) -> None:
        self._controller = controller  # underscore: pywebview doesn't expose it to JavaScript

    def info(self):
        return self._controller.info()

    def listen(self):
        self._controller.listen()

    def stop(self):
        self._controller.stop()

    def send_text(self, text):
        self._controller.send_text(text)

    def set_wake_word(self, enabled):
        self._controller.set_wake_word(bool(enabled))

    def set_muted(self, muted):
        self._controller.set_muted(bool(muted))

    def clear(self):
        self._controller.clear()


def main() -> None:
    logging.basicConfig(level=logging.INFO if "--verbose" in sys.argv else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    controller = Controller(cfg)
    window = webview.create_window(
        cfg.name,
        url=str(ROOT / "ui" / "index.html"),
        js_api=Bridge(controller),
        width=1120,
        height=740,
        min_size=(760, 560),
        background_color="#05060a",
    )
    controller.attach(window)
    window.events.loaded += controller.start
    webview.start(debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
