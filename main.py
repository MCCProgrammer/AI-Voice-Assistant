"""Starts the Jarvis desktop app."""
import logging
import sys

import webview

from assistant.config import ROOT, Config, load_config
from assistant.controller import Controller

UI = str(ROOT / "ui" / "index.html")
MINI_SIZE = (300, 380)


class Bridge:
    """Methods the web UI can call through window.pywebview.api (one instance per window)."""

    def __init__(self, app: "App", mode: str) -> None:
        self._app = app  # underscore: pywebview doesn't expose it to JavaScript
        self._mode = mode

    def mode(self):
        return self._mode

    def info(self):
        return self._app.controller.info()

    def listen(self):
        self._app.controller.listen()

    def stop(self):
        self._app.controller.stop()

    def send_text(self, text):
        self._app.controller.send_text(text)

    def set_wake_word(self, enabled):
        self._app.controller.set_wake_word(bool(enabled))

    def set_muted(self, muted):
        self._app.controller.set_muted(bool(muted))

    def clear(self):
        self._app.controller.clear()

    def mini(self):
        self._app.enter_mini()

    def expand(self):
        self._app.exit_mini()

    def quit(self):
        self._app.quit()


class App:
    """The main window plus a floating "mini" window with just the orb and live captions."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.controller = Controller(cfg)
        self.mini_window = None
        self.main_window = webview.create_window(
            cfg.name,
            url=UI,
            js_api=Bridge(self, "full"),
            width=1120,
            height=740,
            min_size=(760, 560),
            background_color="#05060a",
        )
        self.controller.attach(self.main_window)
        self.main_window.events.loaded += self.controller.start
        self.main_window.events.minimized += self.enter_mini  # minimizing turns into the floating orb
        self.main_window.events.closed += self.quit

    def enter_mini(self) -> None:
        if self.mini_window is None:
            screen = webview.screens[0]
            width, height = MINI_SIZE
            self.mini_window = webview.create_window(
                self.cfg.name,
                url=UI,
                js_api=Bridge(self, "mini"),
                width=width,
                height=height,
                x=screen.width - width - 24,
                y=72,
                frameless=True,
                transparent=True,
                easy_drag=True,
                on_top=True,
                resizable=False,
            )
            self.controller.attach(self.mini_window)
        else:
            self.mini_window.show()
        self.main_window.hide()

    def exit_mini(self) -> None:
        self.main_window.show()
        self.main_window.restore()
        if self.mini_window is not None:
            self.mini_window.hide()

    def quit(self) -> None:
        for window in list(webview.windows):
            window.destroy()


def main() -> None:
    logging.basicConfig(level=logging.INFO if "--verbose" in sys.argv else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    App(load_config())
    webview.start(debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
