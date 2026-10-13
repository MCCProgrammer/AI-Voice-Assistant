"""Conversation with the model through OpenRouter (OpenAI-compatible API), with streaming and tool calls."""
import json
import logging
import platform
import threading
from datetime import datetime
from typing import Callable

from openai import OpenAI

from .config import Config
from .memory import Memory
from .tools import ToolBox

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
MAX_HISTORY = 40  # messages kept between turns


def make_client(cfg: Config) -> OpenAI:
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=cfg.api_key,
        default_headers={
            # Optional OpenRouter attribution headers
            "HTTP-Referer": "https://github.com/MCCProgrammer/voice-assistance",
            "X-Title": f"{cfg.name} Voice Assistant",
        },
    )


class Brain:
    def __init__(self, cfg: Config, client: OpenAI, tools: ToolBox, memory: Memory) -> None:
        self.cfg = cfg
        self.client = client
        self.tools = tools
        self.memory = memory
        self.history: list[dict] = []

    def system_prompt(self) -> str:
        facts = "\n".join(f"- {fact}" for fact in self.memory.facts) or "- (nothing yet)"
        now = datetime.now().astimezone().strftime("%A, %d %B %Y, %H:%M %Z")
        return f"""You are {self.cfg.name}, a friendly and witty voice assistant running on the user's {platform.system()} computer.

Your replies are spoken out loud, so:
- Answer in one to three short sentences unless the user asks for detail.
- Write plain conversational text: no Markdown, lists, emoji, URLs or code.
- Say numbers, dates and units the way a person would say them.
- Reply in the language the user speaks.

Use your tools whenever they help: look things up instead of guessing, check the weather, open apps or websites,
set timers, take notes. Before a slow tool (web search, reading a page) you may say a few words like "Let me check".
When the user shares something worth keeping (their name, preferences, important people or plans), save it with
the remember tool without asking.

Current date and time: {now}.
What you remember about the user:
{facts}"""

    def reset(self) -> None:
        self.history.clear()

    def reply(
        self,
        user_text: str,
        cancel: threading.Event,
        on_text: Callable[[str], None],
        on_tool: Callable[[str, dict], None],
    ) -> str:
        """Runs one user turn, streaming text through on_text and calling tools as needed."""
        self.history.append({"role": "user", "content": user_text})
        full_text = ""
        for _ in range(MAX_TOOL_ROUNDS):
            text, calls = self._stream_once(cancel, on_text)
            full_text += text
            message: dict = {"role": "assistant", "content": text or None}
            if calls:
                message["tool_calls"] = [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                    for c in calls
                ]
            self.history.append(message)
            if not calls or cancel.is_set():
                break
            for call in calls:
                try:
                    args = json.loads(call["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                on_tool(call["name"], args)
                result = self.tools.run(call["name"], call["arguments"])
                log.info("tool %s(%s) -> %s", call["name"], call["arguments"], result[:200])
                self.history.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            if full_text and not full_text.endswith((" ", "\n")):
                full_text += " "
                on_text(" ")
        self._trim()
        return full_text.strip()

    def _stream_once(self, cancel: threading.Event, on_text: Callable[[str], None]) -> tuple[str, list[dict]]:
        stream = self.client.chat.completions.create(
            model=self.cfg.model,
            messages=[{"role": "system", "content": self.system_prompt()}, *self.history],
            tools=self.tools.schemas,
            max_tokens=800,
            stream=True,
        )
        text, calls = "", {}
        try:
            for chunk in stream:
                if cancel.is_set():
                    break
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    text += delta.content
                    on_text(delta.content)
                for tc in delta.tool_calls or []:
                    call = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        call["id"] = tc.id
                    if tc.function and tc.function.name:
                        call["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        call["arguments"] += tc.function.arguments
        finally:
            stream.close()
        return text, [calls[i] for i in sorted(calls)]

    def _trim(self) -> None:
        """Keeps the last messages, cutting only at a user message so tool calls stay paired."""
        if len(self.history) <= MAX_HISTORY:
            return
        cut = len(self.history) - MAX_HISTORY
        while cut < len(self.history) and self.history[cut]["role"] != "user":
            cut += 1
        self.history = self.history[cut:]
