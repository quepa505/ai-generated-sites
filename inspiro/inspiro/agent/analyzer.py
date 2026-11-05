"""Analiza pojedynczych zakładek: streszczenie, tematy, technologie, pomysły
oraz dopasowanie do projektów użytkownika (jedno zapytanie na zakładkę)."""

from __future__ import annotations

import logging
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from typing import Callable, Literal

from pydantic import BaseModel, Field

from . import prompts
from .llm import LLM, LLMError, cached_system, describe_error, is_config_error

log = logging.getLogger(__name__)

Kind = Literal["tutorial", "narzędzie", "biblioteka", "pomysł", "inspiracja", "news",
               "dyskusja", "zasób", "showcase", "humor", "inne"]


class ProjectMatch(BaseModel):
    project_id: int
    relevance: int = Field(description="1-5")
    how_to_use: str


class ItemAnalysis(BaseModel):
    summary: str
    kind: Kind
    topics: list[str]
    technologies: list[str]
    key_insights: list[str]
    idea_seeds: list[str]
    usefulness: int = Field(description="1-5")
    project_matches: list[ProjectMatch]


def _clamp(v: int) -> int:
    try:
        return max(1, min(5, int(v)))
    except (TypeError, ValueError):
        return 3


class Analyzer:
    MATCH_THRESHOLD = 3  # od jakiej trafności tworzyć sugestię "użyj w projekcie"

    def __init__(self, db, llm: LLM, settings):
        self.db, self.llm, self.s = db, llm, settings

    def _system(self) -> list[dict]:
        projects = self.db.list_projects(include_inactive=False)
        memory = self.db.list_memory()
        return cached_system(
            prompts.ANALYSIS_SYSTEM,
            prompts.render_projects(projects) + "\n\n" + prompts.render_memory(memory),
        )

    def analyze_item(self, item: dict, system: list[dict] | None = None) -> dict:
        result, _resp = self.llm.parse(
            model=self.s.analysis_model,
            system=system or self._system(),
            user=prompts.render_item_full(item),
            output_format=ItemAnalysis,
            effort=self.s.analysis_effort,
        )
        data = result.model_dump()
        data["usefulness"] = _clamp(data["usefulness"])
        self.db.save_analysis(item["id"], data, self.s.analysis_model)
        created = 0
        for m in result.project_matches:
            rel = _clamp(m.relevance)
            project = self.db.get_project(m.project_id)
            if rel < self.MATCH_THRESHOLD or not project:
                continue
            if self.db.has_project_suggestion(item["id"], project["id"]):
                continue
            short = (item.get("title") or item.get("url") or f"#{item['id']}")[:90]
            self.db.add_suggestion(
                kind="project_use", title=f"{project['name']}: {short}", body=m.how_to_use,
                project_id=project["id"], item_ids=[item["id"]], score=rel, origin="analysis",
            )
            created += 1
        data["suggestions_created"] = created
        return data

    def analyze_pending(self, limit: int = 200, retry_errors: bool = False,
                        progress: Callable[[int, int, str], None] | None = None) -> dict:
        items = self.db.pending_items(limit=limit, include_errors=retry_errors)
        total = len(items)
        if not total:
            return {"analyzed": 0, "errors": 0, "suggestions": 0}
        system = self._system()  # identyczny prefiks dla całej partii -> cache
        done = errors = suggestions = 0

        def work(it):
            return self.analyze_item(it, system)

        fatal: Exception | None = None
        with ThreadPoolExecutor(max_workers=max(1, self.s.analysis_concurrency)) as pool:
            futures = {pool.submit(work, it): it for it in items}
            for fut in as_completed(futures):
                it = futures[fut]
                try:
                    res = fut.result()
                    suggestions += res.get("suggestions_created", 0)
                    done += 1
                    msg = f"✓ #{it['id']} {it.get('title', '')[:60]}"
                except CancelledError:
                    continue  # anulowane po błędzie konfiguracji — zostają "do analizy"
                except Exception as e:  # noqa: BLE001 — błąd jednej zakładki nie zatrzymuje partii
                    if is_config_error(e):
                        # Brak klucza / uprawnień: zakładki zostają "do analizy", partia stop.
                        fatal = fatal or e
                        for f in futures:
                            f.cancel()
                        continue
                    errors += 1
                    self.db.mark_item_error(it["id"], describe_error(e))
                    msg = f"✗ #{it['id']}: {describe_error(e)}"
                    log.warning("Analiza #%s nieudana: %s", it["id"], e)
                if progress:
                    progress(done + errors, total, msg)
        if fatal:
            raise LLMError(describe_error(fatal))
        return {"analyzed": done, "errors": errors, "suggestions": suggestions}
