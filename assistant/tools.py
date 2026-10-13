"""Tools the model can call. Each one is a method decorated with @tool, which also builds its JSON schema."""
import html
import json
import logging
import platform
import re
import subprocess
import threading
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from typing import Callable

from .memory import Memory

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) JarvisVoiceAssistant/1.0"
WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "rain showers",
    82: "violent rain showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}


def tool(description: str, **params: tuple[str, str] | tuple[str, str, bool]):
    """Declares a tool. params: name=(json_type, description[, required=True])."""
    def decorate(fn):
        properties, required = {}, []
        for name, spec in params.items():
            json_type, desc, *rest = spec
            properties[name] = {"type": json_type, "description": desc}
            if not rest or rest[0]:
                required.append(name)
        fn.schema = {
            "type": "function",
            "function": {
                "name": fn.__name__,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }
        return fn
    return decorate


def fetch(url: str, timeout: float = 10.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read(2_000_000).decode(charset, errors="replace")


def fetch_json(url: str) -> dict:
    return json.loads(fetch(url))


def html_to_text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav)\b.*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", page)
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if len(line) > 1)


class ToolBox:
    def __init__(self, memory: Memory, on_timer: Callable[[str], None]) -> None:
        self.memory = memory
        self._on_timer = on_timer
        self._timers: list[threading.Timer] = []
        self._tools = {name: getattr(self, name) for name, attr in vars(type(self)).items()
                       if hasattr(attr, "schema")}

    @property
    def schemas(self) -> list[dict]:
        return [fn.schema for fn in self._tools.values()]

    def run(self, name: str, arguments: str) -> str:
        fn = self._tools.get(name)
        if fn is None:
            return f"Unknown tool: {name}"
        try:
            args = json.loads(arguments or "{}")
            result = fn(**args)
        except Exception as error:
            log.exception("tool %s failed", name)
            return f"Error: {error}"
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

    # ---------- Information ----------

    @tool("Current local date, time and weekday.")
    def get_datetime(self):
        now = datetime.now().astimezone()
        return now.strftime("%A, %d %B %Y, %H:%M (%Z, UTC%z)")

    @tool("Current weather and the next 3 days' forecast for a place.",
          location=("string", "City or place name, e.g. 'Lisbon' or 'Paris, France'"))
    def get_weather(self, location):
        query = urllib.parse.urlencode({"name": location.split(",")[0], "count": 1})
        places = fetch_json(f"https://geocoding-api.open-meteo.com/v1/search?{query}").get("results")
        if not places:
            return f"Couldn't find a place called {location}."
        place = places[0]
        params = urllib.parse.urlencode({
            "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto", "forecast_days": 4,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        })
        data = fetch_json(f"https://api.open-meteo.com/v1/forecast?{params}")
        current, daily = data["current"], data["daily"]
        return {
            "place": f"{place['name']}, {place.get('country', '')}".strip(", "),
            "now": {
                "temperature_c": current["temperature_2m"],
                "feels_like_c": current["apparent_temperature"],
                "humidity_percent": current["relative_humidity_2m"],
                "wind_kmh": current["wind_speed_10m"],
                "conditions": WEATHER_CODES.get(current["weather_code"], "unknown"),
            },
            "forecast": [
                {"date": day, "min_c": low, "max_c": high, "rain_chance_percent": rain,
                 "conditions": WEATHER_CODES.get(code, "unknown")}
                for day, low, high, rain, code in zip(
                    daily["time"], daily["temperature_2m_min"], daily["temperature_2m_max"],
                    daily["precipitation_probability_max"], daily["weather_code"])
            ],
        }

    @tool("Search the web. Use it for recent events, facts you're unsure about, prices, people, etc.",
          query=("string", "Search query"))
    def web_search(self, query):
        from ddgs import DDGS

        results = DDGS().text(query, max_results=6)
        return [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")} for r in results]

    @tool("Latest news headlines about a topic.", topic=("string", "Topic, e.g. 'technology' or 'Portugal'"))
    def get_news(self, topic):
        from ddgs import DDGS

        results = DDGS().news(topic, max_results=6)
        return [{"title": r.get("title"), "source": r.get("source"), "date": r.get("date"),
                 "summary": r.get("body"), "url": r.get("url")} for r in results]

    @tool("Read the text content of a web page, e.g. a search result, to answer in detail.",
          url=("string", "Full URL starting with http"))
    def read_webpage(self, url):
        text = html_to_text(fetch(url))
        return text[:6000] + ("\n[…truncated]" if len(text) > 6000 else "")

    @tool("Short encyclopedia summary of a topic from Wikipedia.", topic=("string", "Topic or article title"))
    def wikipedia(self, topic):
        title = urllib.parse.quote(topic.strip().replace(" ", "_"))
        try:
            data = fetch_json(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}")
        except Exception:
            query = urllib.parse.urlencode({"action": "opensearch", "search": topic, "limit": 1, "format": "json"})
            hits = fetch_json(f"https://en.wikipedia.org/w/api.php?{query}")[1]
            if not hits:
                return f"No Wikipedia article found for {topic}."
            data = fetch_json("https://en.wikipedia.org/api/rest_v1/page/summary/"
                              + urllib.parse.quote(hits[0].replace(" ", "_")))
        return {"title": data.get("title"), "summary": data.get("extract")}

    # ---------- Computer ----------

    @tool("Open a website in the default browser.", url=("string", "URL or domain, e.g. github.com"))
    def open_website(self, url):
        if not re.match(r"^https?://", url):
            url = "https://" + url
        webbrowser.open(url)
        return f"Opened {url}"

    @tool("Find a video on YouTube and start playing it in the browser.",
          query=("string", "What to play, e.g. 'lofi hip hop' or a song name"))
    def play_on_youtube(self, query):
        page = fetch("https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query}))
        match = re.search(r'"videoId":"([\w-]{11})"', page)
        url = (f"https://www.youtube.com/watch?v={match.group(1)}" if match
               else "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query}))
        webbrowser.open(url)
        return f"Playing on YouTube: {url}"

    @tool("Open an application installed on this computer.", name=("string", "App name, e.g. 'Spotify' or 'Calculator'"))
    def open_app(self, name):
        system = platform.system()
        if system == "Darwin":
            result = subprocess.run(["open", "-a", name], capture_output=True, text=True)
        elif system == "Windows":
            result = subprocess.run(["cmd", "/c", "start", "", name], capture_output=True, text=True)
        else:
            result = subprocess.run(["xdg-open", name], capture_output=True, text=True)
        return f"Opened {name}" if result.returncode == 0 else f"Couldn't open {name}: {result.stderr.strip()}"

    @tool("Set the computer's output volume (macOS only).", percent=("integer", "Volume from 0 to 100"))
    def set_volume(self, percent):
        if platform.system() != "Darwin":
            return "Changing the volume is only supported on macOS."
        percent = max(0, min(100, int(percent)))
        subprocess.run(["osascript", "-e", f"set volume output volume {percent}"], check=True)
        return f"Volume set to {percent}%"

    @tool("Battery, CPU and memory usage of this computer.")
    def system_status(self):
        import psutil

        battery = psutil.sensors_battery()
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.5),
            "memory_percent": psutil.virtual_memory().percent,
            "battery_percent": round(battery.percent) if battery else None,
            "charging": battery.power_plugged if battery else None,
            "os": f"{platform.system()} {platform.release()}",
        }

    @tool("Start a timer that alerts the user when it ends.",
          seconds=("integer", "Duration in seconds"), label=("string", "What the timer is for", False))
    def set_timer(self, seconds, label="Timer"):
        timer = threading.Timer(int(seconds), self._on_timer, args=(label,))
        timer.daemon = True
        timer.start()
        self._timers.append(timer)
        minutes, secs = divmod(int(seconds), 60)
        return f"Timer '{label}' set for {minutes} min {secs} s."

    # ---------- Memory ----------

    @tool("Save a lasting fact about the user (name, preferences, people, plans) to remember in future chats.",
          fact=("string", "The fact, written in third person, e.g. 'The user's name is Miguel'"))
    def remember(self, fact):
        self.memory.remember(fact)
        return "Saved to memory."

    @tool("Forget saved facts that contain some text.", text=("string", "Text to look for in saved facts"))
    def forget(self, text):
        removed = self.memory.forget(text)
        return f"Forgot: {removed}" if removed else "Nothing matching was saved."

    @tool("Write down a note or to-do for the user.", text=("string", "The note"))
    def add_note(self, text):
        self.memory.add_note(text)
        return "Note saved."

    @tool("Read back the user's saved notes.")
    def list_notes(self):
        return self.memory.notes or "There are no notes."

    @tool("Delete all of the user's notes.")
    def clear_notes(self):
        return f"Deleted {self.memory.clear_notes()} notes."
