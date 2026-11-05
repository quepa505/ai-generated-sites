"""Wspólny, znormalizowany format zapisanej treści — niezależny od źródła."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class NormalizedItem:
    source: str                  # reddit | x | manual
    external_id: str             # stabilne ID w źródle (deduplikacja)
    url: str = ""
    title: str = ""
    text: str = ""
    author: str = ""
    context: str = ""            # np. "r/gamedev" albo "odpowiedź w wątku"
    created_at: str | None = None
    media_urls: list[str] = field(default_factory=list)
    raw: Any = None


class ConnectorError(RuntimeError):
    """Błąd po stronie źródła (brak konfiguracji, odmowa API, limit)."""


def ts_to_iso(value: Any) -> str | None:
    """Unix timestamp (Reddit) lub ISO string (X) -> ISO 8601 UTC."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(microsecond=0).isoformat()
    return str(value)
