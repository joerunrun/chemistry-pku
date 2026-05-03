from __future__ import annotations

import time
from typing import Any


class TimingRecord:
    def __init__(self) -> None:
        self._entries: dict[str, float] = {}
        self._order: list[str] = []

    def record(self, name: str, ms: float) -> None:
        self._entries[name] = round(ms, 1)
        if name not in self._order:
            self._order.append(name)

    def to_dict(self) -> dict[str, float]:
        return {name: self._entries[name] for name in self._order}


class StepTimer:
    def __init__(self, record: TimingRecord, name: str) -> None:
        self._record = record
        self._name = name
        self._started_at: float = 0.0

    def __enter__(self) -> StepTimer:
        self._started_at = time.perf_counter()
        return self

    def __exit__(self, *args: Any) -> None:
        elapsed = (time.perf_counter() - self._started_at) * 1000
        self._record.record(self._name, elapsed)
