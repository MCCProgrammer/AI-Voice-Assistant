"""Long-term memory: facts about the user, notes, reminders and shortcuts, stored as JSON on disk."""
import json
import threading
import uuid
from typing import Callable
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
        self._data.setdefault("reminders", [])
        self._data.setdefault("shortcuts", {})
        self.on_change: Callable[[], None] | None = None  # e.g. refresh the reminders list in the UI

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        if self.on_change:
            self.on_change()

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

    # ---------- Reminders ----------

    @property
    def reminders(self) -> list[dict]:
        """Pending reminders, soonest first."""
        return sorted((r for r in self._data["reminders"] if not r.get("done")), key=lambda r: r["due"])

    def add_reminder(self, text: str, due: datetime) -> dict:
        reminder = {"id": uuid.uuid4().hex[:8], "text": text, "due": due.isoformat(timespec="minutes"),
                    "created": datetime.now().isoformat(timespec="minutes")}
        with self._lock:
            self._data["reminders"].append(reminder)
            self._save()
        return reminder

    def due_reminders(self, now: datetime) -> list[dict]:
        return [r for r in self.reminders if datetime.fromisoformat(r["due"]) <= now]

    def complete_reminder(self, reminder_id: str) -> None:
        with self._lock:
            for r in self._data["reminders"]:
                if r["id"] == reminder_id:
                    r["done"] = True
            # Keep the file small: drop finished reminders beyond the last 50.
            done = [r for r in self._data["reminders"] if r.get("done")]
            if len(done) > 50:
                drop = {r["id"] for r in done[:-50]}
                self._data["reminders"] = [r for r in self._data["reminders"] if r["id"] not in drop]
            self._save()

    def cancel_reminders(self, match: str) -> list[dict]:
        """Removes pending reminders whose id or text matches; returns them."""
        with self._lock:
            needle = match.lower()
            removed = [r for r in self.reminders if r["id"] == match or needle in r["text"].lower()]
            ids = {r["id"] for r in removed}
            self._data["reminders"] = [r for r in self._data["reminders"] if r["id"] not in ids]
            self._save()
            return removed

    # ---------- Shortcuts ----------

    @property
    def shortcuts(self) -> dict[str, str]:
        return dict(self._data["shortcuts"])

    def add_shortcut(self, name: str, target: str) -> None:
        with self._lock:
            self._data["shortcuts"][name.strip().lower()] = target.strip()
            self._save()

    def remove_shortcut(self, name: str) -> bool:
        with self._lock:
            found = self._data["shortcuts"].pop(name.strip().lower(), None) is not None
            self._save()
            return found
