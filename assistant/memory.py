"""Long-term memory (facts about the user) and notes, stored as JSON on disk."""
import json
import threading
from datetime import datetime
from pathlib import Path


class Memory:
    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "memory.json"
        self._lock = threading.Lock()
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}
        self._data.setdefault("facts", [])
        self._data.setdefault("notes", [])

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")

    @property
    def facts(self) -> list[str]:
        return list(self._data["facts"])

    @property
    def notes(self) -> list[dict]:
        return list(self._data["notes"])

    def remember(self, fact: str) -> None:
        with self._lock:
            if fact not in self._data["facts"]:
                self._data["facts"].append(fact)
                self._save()

    def forget(self, text: str) -> list[str]:
        """Removes every fact containing `text` (case-insensitive) and returns them."""
        with self._lock:
            needle = text.lower()
            removed = [f for f in self._data["facts"] if needle in f.lower()]
            self._data["facts"] = [f for f in self._data["facts"] if f not in removed]
            self._save()
            return removed

    def add_note(self, text: str) -> None:
        with self._lock:
            self._data["notes"].append({"text": text, "created": datetime.now().isoformat(timespec="minutes")})
            self._save()

    def clear_notes(self) -> int:
        with self._lock:
            count = len(self._data["notes"])
            self._data["notes"] = []
            self._save()
            return count
