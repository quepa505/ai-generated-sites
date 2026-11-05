"""Generator nowych pomysłów (aplikacje, gry, YouTube...) na bazie całej kolekcji."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from . import prompts
from .llm import LLM, cached_system

IdeaType = Literal["app", "game", "youtube", "tool", "content", "business", "learning", "other"]

MAX_CONTEXT_ITEMS = 150


class IdeaProposal(BaseModel):
    title: str
    idea_type: IdeaType
    pitch: str
    why_you: str
    first_steps: list[str]
    source_item_ids: list[int]
    project_id: int = Field(description="0 = nowe przedsięwzięcie")
    score: int = Field(description="1-5")


class IdeasResult(BaseModel):
    ideas: list[IdeaProposal]
    interest_profile: str


class IdeaGenerator:
    PROFILE_CATEGORY = "profil zainteresowań (auto)"

    def __init__(self, db, llm: LLM, settings):
        self.db, self.llm, self.s = db, llm, settings

    def _items_context(self) -> tuple[str, set[int]]:
        items = self.db.list_items(status="analyzed", limit=1000)
        # Najpierw najbardziej przydatne, w ramach tej samej oceny — najnowsze (sort stabilny).
        items.sort(key=lambda i: i.get("imported_at") or "", reverse=True)
        items.sort(key=lambda i: i.get("usefulness") or 0, reverse=True)
        items = items[:MAX_CONTEXT_ITEMS]
        return "\n".join(prompts.render_item_line(i) for i in items), {i["id"] for i in items}

    def generate(self, count: int = 5, focus: str = "") -> dict:
        items_text, valid_ids = self._items_context()
        if not valid_ids:
            raise ValueError("Brak przeanalizowanych zakładek — najpierw zsynchronizuj i przeanalizuj treści.")
        existing = self.db.list_suggestions(kind="new_idea", limit=100)
        existing_text = "\n".join(f"- {s['title']} [{s['status']}]" for s in existing) or "(brak)"
        system = cached_system(
            prompts.IDEAS_SYSTEM,
            prompts.render_projects(self.db.list_projects()) + "\n\n"
            + prompts.render_memory(self.db.list_memory()),
        )
        user = (
            f"Data: {prompts.today()}\n\n"
            f"Statystyki kolekcji:\n{prompts.render_stats(self.db.stats())}\n\n"
            f"Zapisane treści (id, źródło, tytuł, streszczenie...):\n{items_text}\n\n"
            f"Pomysły już na tablicy (nie powtarzaj):\n{existing_text}\n\n"
            f"Zaproponuj {count} pomysłów."
            + (f"\nKierunek od użytkownika: {focus.strip()}" if focus.strip() else "")
        )
        result, _ = self.llm.parse(model=self.s.model, system=system, user=user,
                                   output_format=IdeasResult, effort=self.s.ideas_effort)
        created = []
        for idea in result.ideas:
            item_ids = [i for i in idea.source_item_ids if i in valid_ids]
            created.append(self.db.add_suggestion(
                kind="new_idea", idea_type=idea.idea_type, title=idea.title, body=idea.pitch,
                why_you=idea.why_you, project_id=idea.project_id or None, item_ids=item_ids,
                first_steps=idea.first_steps, score=idea.score, origin="ideas",
            ))
        if result.interest_profile.strip():
            self.db.set_auto_memory(self.PROFILE_CATEGORY, result.interest_profile.strip())
        return {"created": len(created), "ideas": created}
