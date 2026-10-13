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
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .memory import Memory

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) JarvisVoiceAssistant/1.0"
WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "rain showers",
    82: "violent rain showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}


@dataclass
class ToolResult:
    """data goes to the model; card (optional) is what the UI draws for the user."""
    data: Any
    card: dict | None = None


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


def domain_of(url: str) -> str:
    return urllib.parse.urlparse(url or "").netloc.removeprefix("www.")


def geocode(query: str) -> tuple[dict | None, list[str]]:
    """Finds a place. Returns it plus a few alternatives, so the UI can offer "did you mean…".

    Open-Meteo's geocoder needs the exact name; misheard speech ("costa caparica") falls back to
    OpenStreetMap's fuzzier search.
    """
    name, _, hint = query.partition(",")
    params = urllib.parse.urlencode({"name": name.strip(), "count": 5, "language": "en"})
    results = fetch_json(f"https://geocoding-api.open-meteo.com/v1/search?{params}").get("results") or []
    if hint.strip():
        hinted = [r for r in results if hint.strip().lower() in
                  f"{r.get('country', '')} {r.get('country_code', '')} {r.get('admin1', '')}".lower()]
        results = hinted or results
    places = [{"name": r["name"], "region": r.get("admin1", ""), "country": r.get("country", ""),
               "lat": r["latitude"], "lon": r["longitude"]} for r in results]
    if not places:
        params = urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": 3, "addressdetails": 1,
                                         "featureType": "settlement", "accept-language": "en"})
        for r in json.loads(fetch(f"https://nominatim.openstreetmap.org/search?{params}")):
            address = r.get("address", {})
            places.append({"name": r.get("name") or query, "region": address.get("state") or address.get("county", ""),
                           "country": address.get("country", ""), "lat": float(r["lat"]), "lon": float(r["lon"])})
    if not places:
        return None, []
    label = lambda p: ", ".join(x for x in (p["name"], p["region"], p["country"]) if x)
    return places[0], [label(p) for p in places[1:4]]


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

    def run(self, name: str, arguments: str) -> tuple[str, dict | None]:
        """Runs a tool and returns (text for the model, card for the UI or None)."""
        fn = self._tools.get(name)
        if fn is None:
            return f"Unknown tool: {name}", None
        try:
            args = json.loads(arguments or "{}")
            result = fn(**args)
        except Exception as error:
            log.exception("tool %s failed", name)
            return f"Error: {error}", None
        card = None
        if isinstance(result, ToolResult):
            result, card = result.data, result.card
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return text, card

    # ---------- Information ----------

    @tool("Current local date, time and weekday.")
    def get_datetime(self):
        now = datetime.now().astimezone()
        return now.strftime("%A, %d %B %Y, %H:%M (%Z, UTC%z)")

    @tool("Weather now plus the hourly and daily forecast for the next 7 days for a place.",
          location=("string", "Place name, optionally with region or country, e.g. 'Costa da Caparica, Portugal'"),
          date=("string", "The day the user asked about as YYYY-MM-DD (e.g. tomorrow's date). Omit for today.", False))
    def get_weather(self, location, date=None):
        place, alternatives = geocode(location)
        if not place:
            return ToolResult(f"Couldn't find a place called {location}.",
                              {"type": "notfound", "query": location})
        params = urllib.parse.urlencode({
            "latitude": place["lat"], "longitude": place["lon"], "timezone": "auto", "forecast_days": 7,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day",
            "hourly": "temperature_2m,precipitation_probability,weather_code,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     "precipitation_sum,wind_speed_10m_max,uv_index_max,sunrise,sunset",
        })
        data = fetch_json(f"https://api.open-meteo.com/v1/forecast?{params}")
        current, daily, hourly = data["current"], data["daily"], data["hourly"]
        days = [
            {"date": d, "code": code, "max": round(hi), "min": round(lo), "rain": rain or 0,
             "precip_mm": mm or 0, "wind": round(wind or 0), "uv": round(uv or 0),
             "sunrise": rise[-5:], "sunset": sset[-5:]}
            for d, code, hi, lo, rain, mm, wind, uv, rise, sset in zip(
                daily["time"], daily["weather_code"], daily["temperature_2m_max"], daily["temperature_2m_min"],
                daily["precipitation_probability_max"], daily["precipitation_sum"], daily["wind_speed_10m_max"],
                daily["uv_index_max"], daily["sunrise"], daily["sunset"])
        ]
        hours = [
            {"time": t, "temp": round(temp), "rain": rain or 0, "code": code, "day": bool(is_day)}
            for t, temp, rain, code, is_day in zip(
                hourly["time"], hourly["temperature_2m"], hourly["precipitation_probability"],
                hourly["weather_code"], hourly["is_day"])
        ]
        focus = date if date in daily["time"] else daily["time"][0]
        place_label = ", ".join(x for x in (place["name"], place["region"], place["country"]) if x)
        now = {"temp": round(current["temperature_2m"]), "feels": round(current["apparent_temperature"]),
               "humidity": current["relative_humidity_2m"], "wind": round(current["wind_speed_10m"]),
               "code": current["weather_code"], "day": bool(current["is_day"])}
        focus_day = next(d for d in days if d["date"] == focus)
        for_model = {
            "place": place_label,
            "now": {**now, "conditions": WEATHER_CODES.get(now["code"], "unknown")},
            "requested_day": {**focus_day, "conditions": WEATHER_CODES.get(focus_day["code"], "unknown"),
                              "every_3_hours": [f"{h['time'][-5:]} {h['temp']}°C rain {h['rain']}%"
                                                for h in hours if h["time"].startswith(focus)][::3]},
            "next_days": [f"{d['date']}: {WEATHER_CODES.get(d['code'], '?')}, {d['min']}-{d['max']}°C, "
                          f"rain {d['rain']}%" for d in days],
        }
        card = {"type": "weather", "query": location, "place": place, "alternatives": alternatives,
                "focus": focus, "now": now, "days": days, "hours": hours}
        return ToolResult(for_model, card)

    @tool("Search the web. Use it for recent events, facts you're unsure about, prices, people, etc.",
          query=("string", "Search query"))
    def web_search(self, query):
        from ddgs import DDGS

        results = [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")}
                   for r in DDGS().text(query, max_results=6)]
        card = {"type": "links", "title": f"Web results for “{query}”",
                "items": [{**r, "domain": domain_of(r["url"])} for r in results]}
        return ToolResult(results, card)

    @tool("Latest news headlines about a topic.", topic=("string", "Topic, e.g. 'technology' or 'Portugal'"))
    def get_news(self, topic):
        from ddgs import DDGS

        results = [{"title": r.get("title"), "source": r.get("source"), "date": r.get("date"),
                    "summary": r.get("body"), "url": r.get("url"), "image": r.get("image")}
                   for r in DDGS().news(topic, max_results=6)]
        for_model = [{k: v for k, v in r.items() if k != "image"} for r in results]
        return ToolResult(for_model, {"type": "news", "topic": topic, "items": results})

    @tool("Read the text content of a web page, e.g. a search result, to answer in detail.",
          url=("string", "Full URL starting with http"))
    def read_webpage(self, url):
        page = fetch(url)
        title = re.search(r"(?is)<title[^>]*>(.*?)</title>", page)
        text = html_to_text(page)
        card = {"type": "page", "url": url, "domain": domain_of(url),
                "title": html.unescape(title.group(1)).strip() if title else domain_of(url), "excerpt": text[:280]}
        return ToolResult(text[:6000] + ("\n[…truncated]" if len(text) > 6000 else ""), card)

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
        card = {"type": "wiki", "title": data.get("title"), "summary": data.get("extract"),
                "description": data.get("description"), "image": (data.get("thumbnail") or {}).get("source"),
                "url": (data.get("content_urls") or {}).get("desktop", {}).get("page")}
        return ToolResult({"title": data.get("title"), "summary": data.get("extract")}, card)

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
        search = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query})
        page = fetch(search)
        match = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})".{0,4000}?"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"', page, re.S)
        if not match:
            webbrowser.open(search)
            return f"Opened YouTube search results for {query}"
        video_id, title = match.group(1), json.loads(f'"{match.group(2)}"')
        url = f"https://www.youtube.com/watch?v={video_id}"
        webbrowser.open(url)
        card = {"type": "video", "title": title, "url": url, "thumb": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"}
        return ToolResult(f"Playing on YouTube: {title}", card)

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
        status = {
            "cpu_percent": psutil.cpu_percent(interval=0.5),
            "memory_percent": psutil.virtual_memory().percent,
            "battery_percent": round(battery.percent) if battery else None,
            "charging": battery.power_plugged if battery else None,
            "os": f"{platform.system()} {platform.release()}",
        }
        return ToolResult(status, {"type": "system", **status})

    @tool("Start a timer that alerts the user when it ends.",
          seconds=("integer", "Duration in seconds"), label=("string", "What the timer is for", False))
    def set_timer(self, seconds, label="Timer"):
        timer = threading.Timer(int(seconds), self._on_timer, args=(label,))
        timer.daemon = True
        timer.start()
        self._timers.append(timer)
        minutes, secs = divmod(int(seconds), 60)
        card = {"type": "timer", "label": label, "seconds": int(seconds),
                "ends_at": (datetime.now().timestamp() + int(seconds)) * 1000}
        return ToolResult(f"Timer '{label}' set for {minutes} min {secs} s.", card)

    # ---------- Memory ----------

    @tool("Save a lasting fact about the user (name, preferences, people, plans) to remember in future chats.",
          fact=("string", "The fact, written in third person, e.g. 'The user's name is Miguel'"))
    def remember(self, fact):
        self.memory.remember(fact)
        return ToolResult("Saved to memory.", {"type": "memory", "text": fact})

    @tool("Forget saved facts that contain some text.", text=("string", "Text to look for in saved facts"))
    def forget(self, text):
        removed = self.memory.forget(text)
        return f"Forgot: {removed}" if removed else "Nothing matching was saved."

    @tool("Write down a note or to-do for the user.", text=("string", "The note"))
    def add_note(self, text):
        self.memory.add_note(text)
        return ToolResult("Note saved.", {"type": "notes", "items": self.memory.notes, "highlight": len(self.memory.notes) - 1})

    @tool("Read back the user's saved notes.")
    def list_notes(self):
        notes = self.memory.notes
        return ToolResult(notes or "There are no notes.", {"type": "notes", "items": notes})

    @tool("Delete all of the user's notes.")
    def clear_notes(self):
        return f"Deleted {self.memory.clear_notes()} notes."
