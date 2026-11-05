"""Spina konfigurację, bazę, konektory i agenta; uruchamia zadania w tle."""

from __future__ import annotations

import logging
import threading
import traceback
import uuid
from collections import deque
from typing import Any, Callable

from . import connectors
from .agent import LLM, Analyzer, ChatAgent, IdeaGenerator
from .agent.llm import describe_error
from .config import Settings
from .connectors.x import XClient
from .db import Database, now_iso

log = logging.getLogger(__name__)


class Job:
    def __init__(self, kind: str, params: dict):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.params = params
        self.status = "running"
        self.started_at = now_iso()
        self.finished_at: str | None = None
        self.done = 0
        self.total = 0
        self.log: deque[str] = deque(maxlen=60)
        self.result: Any = None
        self.error: str | None = None

    def progress(self, done: int, total: int, msg: str = "") -> None:
        self.done, self.total = done, total
        if msg:
            self.log.append(msg)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "params": self.params, "status": self.status,
                "started_at": self.started_at, "finished_at": self.finished_at,
                "done": self.done, "total": self.total, "log": list(self.log),
                "result": self.result, "error": self.error}


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, kind: str, fn: Callable[[Job], Any], params: dict | None = None,
              background: bool = True) -> Job:
        with self._lock:
            for j in self._jobs.values():
                if j.kind == kind and j.status == "running":
                    return j  # nie dublujemy tego samego zadania
            job = Job(kind, params or {})
            self._jobs[job.id] = job

        def run():
            try:
                job.result = fn(job)
                job.status = "done"
            except Exception as e:  # noqa: BLE001 — błąd raportujemy w UI
                job.status = "error"
                job.error = describe_error(e)
                log.error("Zadanie %s nieudane:\n%s", kind, traceback.format_exc())
            finally:
                job.finished_at = now_iso()

        if background:
            threading.Thread(target=run, name=f"job-{kind}", daemon=True).start()
        else:
            run()
        return job

    def list(self) -> list[dict]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)
        return [j.to_dict() for j in jobs[:20]]

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)


class Services:
    def __init__(self, settings: Settings, db: Database | None = None, llm: LLM | None = None,
                 http=None):
        self.settings = settings
        self.db = db or Database(settings.db_path)
        self.llm = llm or LLM(settings)
        self.http = http
        self.analyzer = Analyzer(self.db, self.llm, settings)
        self.ideas = IdeaGenerator(self.db, self.llm, settings)
        self.chat = ChatAgent(self.db, self.llm, settings)
        self.jobs = JobManager()

    # ------------------------------------------------------------------ status źródeł
    def sources_status(self) -> dict:
        return {
            "reddit": {"configured": self.settings.reddit_configured,
                       "username": self.settings.reddit_username or None,
                       "last_sync": self.db.kv_get("last_sync_reddit")},
            "x": {"configured": self.settings.x_configured,
                  "connected": XClient(self.settings, self.db, self.http).connected,
                  "last_sync": self.db.kv_get("last_sync_x")},
            "claude": {"model": self.settings.model, "analysis_model": self.settings.analysis_model},
        }

    # ------------------------------------------------------------------ operacje
    def sync(self, source: str = "all", job: Job | None = None) -> dict:
        targets = []
        if source in ("all", "reddit") and (source == "reddit" or self.settings.reddit_configured):
            targets.append(("reddit", connectors.sync_reddit))
        if source in ("all", "x") and (source == "x" or XClient(self.settings, self.db, self.http).connected):
            targets.append(("x", connectors.sync_x))
        if not targets:
            raise connectors.ConnectorError(
                "Żadne źródło nie jest skonfigurowane. Ustaw Reddit API w .env, połącz X "
                "albo zaimportuj plik.")
        results = {}
        for i, (name, fn) in enumerate(targets):
            try:
                res = fn(self.settings, self.db, self.http)
                self.db.kv_set(f"last_sync_{name}", now_iso())
                results[name] = res
                msg = f"{name}: nowych {res['added']}, znanych {res['skipped']}"
            except connectors.ConnectorError as e:
                results[name] = {"error": str(e)}
                msg = f"{name}: {e}"
            if job:
                job.progress(i + 1, len(targets), msg)
        return results

    def analyze(self, limit: int = 200, retry_errors: bool = False, job: Job | None = None) -> dict:
        return self.analyzer.analyze_pending(limit=limit, retry_errors=retry_errors,
                                             progress=job.progress if job else None)

    def generate_ideas(self, count: int = 5, focus: str = "", job: Job | None = None) -> dict:
        if job:
            job.progress(0, 1, "Agent przegląda kolekcję i szuka pomysłów…")
        res = self.ideas.generate(count=count, focus=focus)
        if job:
            job.progress(1, 1, f"Nowych pomysłów: {res['created']}")
        return {"created": res["created"], "titles": [i["title"] for i in res["ideas"]]}

    def auto(self, ideas_threshold: int = 5, job: Job | None = None) -> dict:
        """Pełny cykl (np. z crona): synchronizacja -> analiza -> pomysły, gdy dużo nowości."""
        out: dict[str, Any] = {}
        try:
            out["sync"] = self.sync("all", job)
        except connectors.ConnectorError as e:
            out["sync"] = {"error": str(e)}
        out["analyze"] = self.analyze(job=job)
        if out["analyze"]["analyzed"] >= ideas_threshold:
            out["ideas"] = self.generate_ideas(job=job)
        return out

    def start_job(self, kind: str, params: dict | None = None, background: bool = True) -> Job:
        params = params or {}
        runners: dict[str, Callable[[Job], Any]] = {
            "sync": lambda j: self.sync(params.get("source", "all"), j),
            "analyze": lambda j: self.analyze(int(params.get("limit", 200)),
                                              bool(params.get("retry_errors")), j),
            "ideas": lambda j: self.generate_ideas(int(params.get("count", 5)),
                                                   params.get("focus", ""), j),
            "auto": lambda j: self.auto(job=j),
        }
        if kind not in runners:
            raise ValueError(f"Nieznany typ zadania: {kind}")
        return self.jobs.start(kind, runners[kind], params, background=background)
