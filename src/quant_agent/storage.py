from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Event, utc_now


class EventStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def append(self, event: Event) -> None:
        path = self.directory / f"{event.event_id}.json"
        path.write_text(json.dumps(event.to_dict(), indent=2, sort_keys=True), encoding="utf-8")

    def list_events(self, event_type: str | None = None) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.directory.glob("*.json")):
            event = json.loads(path.read_text(encoding="utf-8"))
            if event_type is None or event["type"] == event_type:
                result.append(event)
        return result

    def newest(self) -> dict[str, Any] | None:
        events = self.list_events()
        return max(events, key=lambda event: event["timestamp"], default=None)


def event_id(prefix: str) -> str:
    return f"{prefix}-{utc_now().strftime('%Y%m%dT%H%M%S%fZ')}"
