from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .safety import redact_structured


class TraceRecorder:
    def __init__(
        self,
        trace_dir: Path,
        run_id: str,
        url_template: str | None = None,
    ) -> None:
        self.trace_dir = trace_dir
        self.run_id = run_id
        self.url_template = url_template
        self.events: list[dict[str, Any]] = []
        self.data: dict[str, Any] = {"schema_version": 1, "run_id": run_id}

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def event(self, category: str, payload: dict[str, Any]) -> None:
        self.events.append({"category": category, **payload})

    def write(self) -> Path:
        self.data["events"] = self.events
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        path = self.trace_dir / f"{self.run_id}.json"
        temporary = path.with_suffix(".json.tmp")
        sanitized = redact_structured(self.data)
        temporary.write_text(
            json.dumps(sanitized, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
        return path

    def url(self, path: Path) -> str:
        if self.url_template:
            return self.url_template.format(run_id=self.run_id, path=str(path))
        return str(path)
