"""Narzędzia agenta czatu: dostęp do zakładek, projektów, pamięci i tablicy pomysłów."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import prompts

IDEA_TYPES = ["app", "game", "youtube", "tool", "content", "business", "learning", "other"]


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


S = {"type": "string"}
I = {"type": "integer"}

TOOL_SPECS: list[dict] = [
    _tool("search_saved",
          "Pełnotekstowe wyszukiwanie w zapisanych zakładkach (tytuł, treść, streszczenie, tematy, "
          "technologie). Zwraca listę skrótów. Dla lepszych wyników próbuj kilku słów kluczowych.",
          {"query": S, "limit": I}),
    _tool("get_saved_item",
          "Pełna treść i analiza jednej zakładki wraz z powiązanymi sugestiami.",
          {"item_id": I}),
    _tool("list_recent_saved",
          "Ostatnio zaimportowane zakładki (z ostatnich N dni), opcjonalnie z jednego źródła.",
          {"days": I, "source": {"type": "string", "enum": ["all", "reddit", "x", "manual"]}, "limit": I}),
    _tool("get_overview",
          "Statystyki kolekcji: liczby zakładek, dominujące tematy i technologie, liczba pomysłów.",
          {}),
    _tool("list_projects", "Lista projektów użytkownika (id, nazwa, opis, stack, cele, status).", {}),
    _tool("save_project",
          "Tworzy projekt (project_id=0) albo aktualizuje istniejący. Puste pola przy aktualizacji "
          "zostają bez zmian.",
          {"project_id": I, "name": S, "description": S, "stack": S, "goals": S,
           "status": {"type": "string", "enum": ["active", "paused", "done"]}}),
    _tool("list_memory", "Wszystkie wpisy długoterminowej pamięci o użytkowniku.", {}),
    _tool("remember",
          "Zapisuje trwały fakt o użytkowniku (umiejętności, cele, preferencje, ograniczenia).",
          {"category": S, "content": S}),
    _tool("update_memory", "Poprawia treść wpisu pamięci.", {"memory_id": I, "content": S}),
    _tool("forget", "Usuwa nieaktualny wpis pamięci.", {"memory_id": I}),
    _tool("save_suggestion",
          "Zapisuje pomysł na tablicy. kind=project_use (użycie treści w istniejącym projekcie, "
          "podaj project_id) albo new_idea (nowe przedsięwzięcie; project_id=0 jeśli niezwiązane). "
          "item_ids: id zakładek, na których bazuje pomysł.",
          {"kind": {"type": "string", "enum": ["project_use", "new_idea"]},
           "idea_type": {"type": "string", "enum": IDEA_TYPES},
           "title": S, "body": S, "why_you": S, "project_id": I,
           "item_ids": {"type": "array", "items": I},
           "first_steps": {"type": "array", "items": S},
           "score": I}),
    _tool("list_suggestions", "Pomysły z tablicy według statusu.",
          {"status": {"type": "string", "enum": ["new", "accepted", "dismissed", "done", "all"]}}),
]


def _brief(item: dict) -> dict:
    return {k: item.get(k) for k in ("id", "source", "title", "url", "author", "context", "summary",
                                     "kind", "topics", "technologies", "usefulness", "status")
            if item.get(k) not in (None, "", [])}


class ToolExecutor:
    def __init__(self, db):
        self.db = db
        self.handlers: dict[str, Callable[[dict], Any]] = {
            "search_saved": self.search_saved,
            "get_saved_item": self.get_saved_item,
            "list_recent_saved": self.list_recent_saved,
            "get_overview": lambda _: self.db.stats(),
            "list_projects": lambda _: self.db.list_projects(),
            "save_project": self.save_project,
            "list_memory": lambda _: self.db.list_memory(),
            "remember": lambda a: self.db.add_memory(a["category"], a["content"], origin="agent"),
            "update_memory": lambda a: self.db.update_memory(int(a["memory_id"]), a["content"])
            or {"error": "nie ma takiego wpisu"},
            "forget": lambda a: {"deleted": self.db.delete_memory(int(a["memory_id"]))},
            "save_suggestion": self.save_suggestion,
            "list_suggestions": self.list_suggestions,
        }

    def run(self, name: str, args: dict) -> tuple[str, bool]:
        """Zwraca (treść wyniku jako JSON, czy_błąd)."""
        handler = self.handlers.get(name)
        if not handler:
            return json.dumps({"error": f"Nieznane narzędzie {name}"}), True
        if not isinstance(args, dict):
            return json.dumps({"error": "Argumenty muszą być obiektem JSON"}), True
        try:
            result = handler(args)
        except (KeyError, ValueError, TypeError) as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False), True
        return json.dumps(result, ensure_ascii=False, default=str), False

    # ------------------------------------------------------------------ handlery
    def search_saved(self, a: dict) -> dict:
        limit = max(1, min(30, int(a.get("limit") or 10)))
        hits = self.db.search(a["query"], limit=limit)
        return {"query": a["query"], "count": len(hits), "results": [_brief(h) for h in hits]}

    def get_saved_item(self, a: dict) -> dict:
        item = self.db.get_item(int(a["item_id"]))
        if not item:
            return {"error": f"Brak zakładki #{a['item_id']}"}
        text = item.get("text") or ""
        if len(text) > 20_000:
            item["text"] = text[:20_000] + "\n[...obcięto do 20 000 znaków]"
        item["suggestions"] = [{"id": s["id"], "title": s["title"], "status": s["status"]}
                               for s in item.get("suggestions", [])]
        item.pop("error", None)
        return item

    def list_recent_saved(self, a: dict) -> dict:
        days = max(1, min(365, int(a.get("days") or 7)))
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        source = a.get("source") if a.get("source") not in (None, "all") else None
        items = self.db.list_items(source=source, since=since,
                                   limit=max(1, min(50, int(a.get("limit") or 20))))
        return {"days": days, "count": len(items), "results": [_brief(i) for i in items]}

    def save_project(self, a: dict) -> dict:
        pid = int(a.get("project_id") or 0)
        return self.db.save_project(a, project_id=pid or None)

    def save_suggestion(self, a: dict) -> dict:
        return self.db.add_suggestion(
            kind=a["kind"], idea_type=a.get("idea_type"), title=a["title"], body=a["body"],
            why_you=a.get("why_you", ""), project_id=int(a.get("project_id") or 0) or None,
            item_ids=[int(i) for i in a.get("item_ids") or []],
            first_steps=a.get("first_steps") or [], score=int(a.get("score") or 3), origin="chat")

    def list_suggestions(self, a: dict) -> dict:
        status = a.get("status")
        rows = self.db.list_suggestions(status=None if status in (None, "all") else status, limit=60)
        return {"count": len(rows), "results": [
            {k: r.get(k) for k in ("id", "kind", "idea_type", "title", "body", "project_name",
                                   "item_ids", "score", "status")} for r in rows]}


def context_snapshot(db) -> str:
    """Kontekst doklejany do PIERWSZEJ wiadomości rozmowy (historia pozostaje append-only)."""
    return "\n\n".join([
        f"{prompts.CONTEXT_MARKER} Dzisiaj jest {prompts.today()}.",
        prompts.render_stats(db.stats()),
        prompts.render_projects(db.list_projects()),
        prompts.render_memory(db.list_memory()),
    ])
