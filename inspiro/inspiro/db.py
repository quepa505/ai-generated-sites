"""Warstwa danych: SQLite + FTS5 (wyszukiwanie pełnotekstowe po zakładkach)."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .connectors.base import NormalizedItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY,
    source       TEXT NOT NULL,                 -- reddit | x | manual
    external_id  TEXT NOT NULL,
    url          TEXT,
    title        TEXT,
    text         TEXT,
    author       TEXT,
    context      TEXT,                          -- np. r/gamedev, wątek
    media_urls   TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT,                          -- data publikacji oryginału
    imported_at  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'new',   -- new | analyzed | error
    error        TEXT,
    raw          TEXT,
    UNIQUE(source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);

CREATE TABLE IF NOT EXISTS analyses (
    item_id       INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    summary       TEXT,
    kind          TEXT,
    topics        TEXT NOT NULL DEFAULT '[]',
    technologies  TEXT NOT NULL DEFAULT '[]',
    key_insights  TEXT NOT NULL DEFAULT '[]',
    idea_seeds    TEXT NOT NULL DEFAULT '[]',
    usefulness    INTEGER,
    model         TEXT,
    analyzed_at   TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    stack        TEXT NOT NULL DEFAULT '',
    goals        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active',  -- active | paused | done
    created_at   TEXT,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS suggestions (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,                  -- project_use | new_idea
    idea_type    TEXT,                           -- app | game | youtube | tool | content | business | learning | other
    title        TEXT NOT NULL,
    body         TEXT NOT NULL,
    why_you      TEXT NOT NULL DEFAULT '',
    project_id   INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    item_ids     TEXT NOT NULL DEFAULT '[]',
    first_steps  TEXT NOT NULL DEFAULT '[]',
    score        INTEGER NOT NULL DEFAULT 3,
    status       TEXT NOT NULL DEFAULT 'new',    -- new | accepted | dismissed | done
    origin       TEXT,                           -- analysis | ideas | chat
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sugg_status ON suggestions(status);

CREATE TABLE IF NOT EXISTS memory (
    id          INTEGER PRIMARY KEY,
    category    TEXT NOT NULL,
    content     TEXT NOT NULL,
    origin      TEXT NOT NULL DEFAULT 'agent',   -- agent | user | auto
    created_at  TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY,
    title       TEXT,
    messages    TEXT NOT NULL DEFAULT '[]',      -- pełna, dopisywana historia Messages API
    created_at  TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS kv (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS search_idx USING fts5(
    title, body, tokenize = 'unicode61 remove_diacritics 2'
);
"""

JSON_FIELDS_ANALYSIS = ("topics", "technologies", "key_insights", "idea_seeds")
JSON_FIELDS_SUGGESTION = ("item_ids", "first_steps")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _fts_query(text: str, mode: str = "AND") -> str:
    tokens = re.findall(r"[\w#+.-]+", text.lower(), flags=re.UNICODE)
    tokens = [t.strip(".-") for t in tokens if len(t.strip(".-")) >= 2][:12]
    if not tokens:
        return ""
    quoted = ['"' + t.replace('"', "") + '"*' for t in tokens]
    return (" OR " if mode == "OR" else " ").join(quoted)


class Database:
    """Cienka warstwa nad SQLite. Jedno połączenie na wątek (FastAPI + wątki analizy)."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._shared: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        with self._lock:
            self.conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ infra
    @property
    def conn(self) -> sqlite3.Connection:
        if self.path == ":memory:":
            # Baza w pamięci musi być współdzielona między wątkami (testy).
            if self._shared is None:
                self._shared = self._connect()
            return self._shared
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def _one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        with self._lock:
            row = self.conn.execute(sql, tuple(params)).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------ kv
    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self._one("SELECT value FROM kv WHERE key = ?", (key,))
        return _loads(row["value"], default) if row else default

    def kv_set(self, key: str, value: Any) -> None:
        self._exec("INSERT INTO kv(key, value) VALUES (?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                   (key, json.dumps(value, ensure_ascii=False)))

    def kv_delete(self, key: str) -> None:
        self._exec("DELETE FROM kv WHERE key = ?", (key,))

    # ------------------------------------------------------------------ items
    def has_item(self, source: str, external_id: str) -> bool:
        return self._one("SELECT 1 FROM items WHERE source = ? AND external_id = ?",
                         (source, external_id)) is not None

    def upsert_item(self, item: NormalizedItem) -> tuple[int, bool]:
        """Dodaje zakładkę. Zwraca (id, czy_nowa). Istniejące nie są nadpisywane."""
        existing = self._one("SELECT id FROM items WHERE source = ? AND external_id = ?",
                             (item.source, item.external_id))
        if existing:
            return existing["id"], False
        cur = self._exec(
            "INSERT INTO items(source, external_id, url, title, text, author, context, media_urls,"
            " created_at, imported_at, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (item.source, item.external_id, item.url, item.title, item.text, item.author,
             item.context, json.dumps(item.media_urls), item.created_at, now_iso(),
             json.dumps(item.raw, ensure_ascii=False) if item.raw is not None else None),
        )
        item_id = cur.lastrowid
        self._reindex(item_id)
        return item_id, True

    def add_items(self, items: Iterable[NormalizedItem]) -> dict:
        added = skipped = 0
        for it in items:
            _, new = self.upsert_item(it)
            added += int(new)
            skipped += int(not new)
        return {"added": added, "skipped": skipped}

    def delete_item(self, item_id: int) -> None:
        self._exec("DELETE FROM search_idx WHERE rowid = ?", (item_id,))
        self._exec("DELETE FROM items WHERE id = ?", (item_id,))

    def _item_row(self, row: dict) -> dict:
        row["media_urls"] = _loads(row.get("media_urls"), [])
        row.pop("raw", None)
        for f in JSON_FIELDS_ANALYSIS:
            if f in row:
                row[f] = _loads(row[f], [])
        return row

    _ITEM_SELECT = (
        "SELECT i.*, a.summary, a.kind, a.topics, a.technologies, a.key_insights, a.idea_seeds,"
        " a.usefulness, a.analyzed_at FROM items i LEFT JOIN analyses a ON a.item_id = i.id"
    )

    def get_item(self, item_id: int) -> dict | None:
        row = self._one(self._ITEM_SELECT + " WHERE i.id = ?", (item_id,))
        if not row:
            return None
        item = self._item_row(row)
        item["suggestions"] = self.list_suggestions(item_id=item_id)
        return item

    def list_items(self, *, status: str | None = None, source: str | None = None,
                   query: str | None = None, min_usefulness: int | None = None,
                   since: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
        where, params = [], []
        if status:
            where.append("i.status = ?"); params.append(status)
        if source:
            where.append("i.source = ?"); params.append(source)
        if min_usefulness:
            where.append("a.usefulness >= ?"); params.append(min_usefulness)
        if since:
            where.append("i.imported_at >= ?"); params.append(since)
        if query:
            ids = [r["id"] for r in self.search(query, limit=500)]
            if not ids:
                return []
            where.append(f"i.id IN ({','.join('?' * len(ids))})"); params.extend(ids)
        sql = self._ITEM_SELECT
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY i.imported_at DESC, i.id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        return [self._item_row(r) for r in self._all(sql, params)]

    def count_items(self, status: str | None = None) -> int:
        if status:
            return self._one("SELECT COUNT(*) c FROM items WHERE status = ?", (status,))["c"]
        return self._one("SELECT COUNT(*) c FROM items")["c"]

    def pending_items(self, limit: int = 100, include_errors: bool = False) -> list[dict]:
        statuses = ("new", "error") if include_errors else ("new",)
        rows = self._all(
            f"SELECT * FROM items WHERE status IN ({','.join('?' * len(statuses))})"
            " ORDER BY id LIMIT ?", (*statuses, limit))
        return [self._item_row(r) for r in rows]

    def save_analysis(self, item_id: int, analysis: dict, model: str) -> None:
        self._exec(
            "INSERT INTO analyses(item_id, summary, kind, topics, technologies, key_insights,"
            " idea_seeds, usefulness, model, analyzed_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(item_id) DO UPDATE SET summary=excluded.summary, kind=excluded.kind,"
            " topics=excluded.topics, technologies=excluded.technologies,"
            " key_insights=excluded.key_insights, idea_seeds=excluded.idea_seeds,"
            " usefulness=excluded.usefulness, model=excluded.model, analyzed_at=excluded.analyzed_at",
            (item_id, analysis.get("summary"), analysis.get("kind"),
             *(json.dumps(analysis.get(f, []), ensure_ascii=False) for f in JSON_FIELDS_ANALYSIS),
             analysis.get("usefulness"), model, now_iso()),
        )
        self._exec("UPDATE items SET status = 'analyzed', error = NULL WHERE id = ?", (item_id,))
        self._reindex(item_id)

    def mark_item_error(self, item_id: int, error: str) -> None:
        self._exec("UPDATE items SET status = 'error', error = ? WHERE id = ?", (error[:500], item_id))

    def reset_item(self, item_id: int) -> None:
        self._exec("UPDATE items SET status = 'new', error = NULL WHERE id = ?", (item_id,))

    def _reindex(self, item_id: int) -> None:
        row = self._one(self._ITEM_SELECT + " WHERE i.id = ?", (item_id,))
        if not row:
            return
        r = self._item_row(row)
        body = " \n".join(filter(None, [
            r.get("text"), r.get("summary"), r.get("context"), r.get("author"), r.get("url"),
            " ".join(r.get("topics") or []), " ".join(r.get("technologies") or []),
            " ".join(r.get("key_insights") or []), " ".join(r.get("idea_seeds") or []),
        ]))
        with self._lock:
            self.conn.execute("DELETE FROM search_idx WHERE rowid = ?", (item_id,))
            self.conn.execute("INSERT INTO search_idx(rowid, title, body) VALUES (?, ?, ?)",
                              (item_id, r.get("title") or "", body))
            self.conn.commit()

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Wyszukiwanie BM25. Najpierw wszystkie słowa (AND), potem dowolne (OR)."""
        for mode in ("AND", "OR"):
            fts = _fts_query(query, mode)
            if not fts:
                return []
            try:
                rows = self._all(
                    "SELECT rowid AS id, bm25(search_idx, 5.0, 1.0) AS rank FROM search_idx"
                    " WHERE search_idx MATCH ? ORDER BY rank LIMIT ?", (fts, limit))
            except sqlite3.OperationalError:
                rows = []
            if rows:
                out = []
                for r in rows:
                    item = self._one(self._ITEM_SELECT + " WHERE i.id = ?", (r["id"],))
                    if item:
                        out.append(self._item_row(item))
                return out
        return []

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict:
        by_source = {r["source"]: r["c"] for r in
                     self._all("SELECT source, COUNT(*) c FROM items GROUP BY source")}
        by_status = {r["status"]: r["c"] for r in
                     self._all("SELECT status, COUNT(*) c FROM items GROUP BY status")}
        topics, techs, kinds = Counter(), Counter(), Counter()
        for r in self._all("SELECT topics, technologies, kind FROM analyses"):
            topics.update(t.strip().lower() for t in _loads(r["topics"], []) if t.strip())
            techs.update(t.strip() for t in _loads(r["technologies"], []) if t.strip())
            if r["kind"]:
                kinds[r["kind"]] += 1
        sugg = {r["status"]: r["c"] for r in
                self._all("SELECT status, COUNT(*) c FROM suggestions GROUP BY status")}
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        return {
            "items_total": sum(by_source.values()),
            "by_source": by_source,
            "by_status": by_status,
            "imported_last_7_days": self._one(
                "SELECT COUNT(*) c FROM items WHERE imported_at >= ?", (week_ago,))["c"],
            "top_topics": topics.most_common(15),
            "top_technologies": techs.most_common(15),
            "kinds": kinds.most_common(),
            "projects": self._one("SELECT COUNT(*) c FROM projects")["c"],
            "suggestions": sugg,
            "memory_entries": self._one("SELECT COUNT(*) c FROM memory")["c"],
        }

    # ------------------------------------------------------------------ projects
    def list_projects(self, include_inactive: bool = True) -> list[dict]:
        sql = "SELECT * FROM projects"
        if not include_inactive:
            sql += " WHERE status = 'active'"
        return self._all(sql + " ORDER BY status = 'active' DESC, updated_at DESC, id DESC")

    def get_project(self, project_id: int) -> dict | None:
        return self._one("SELECT * FROM projects WHERE id = ?", (project_id,))

    def save_project(self, data: dict, project_id: int | None = None) -> dict:
        fields = {k: (data.get(k) or "").strip() for k in ("name", "description", "stack", "goals")}
        status = data.get("status") or "active"
        if project_id:
            current = self.get_project(project_id)
            if not current:
                raise KeyError(f"Projekt {project_id} nie istnieje")
            merged = {k: (v if v else current[k]) for k, v in fields.items()}
            self._exec("UPDATE projects SET name=?, description=?, stack=?, goals=?, status=?,"
                       " updated_at=? WHERE id=?",
                       (merged["name"], merged["description"], merged["stack"], merged["goals"],
                        data.get("status") or current["status"], now_iso(), project_id))
            return self.get_project(project_id)
        if not fields["name"]:
            raise ValueError("Projekt musi mieć nazwę")
        ts = now_iso()
        cur = self._exec("INSERT INTO projects(name, description, stack, goals, status, created_at,"
                         " updated_at) VALUES (?,?,?,?,?,?,?)",
                         (fields["name"], fields["description"], fields["stack"], fields["goals"],
                          status, ts, ts))
        return self.get_project(cur.lastrowid)

    def delete_project(self, project_id: int) -> None:
        self._exec("DELETE FROM projects WHERE id = ?", (project_id,))

    # ------------------------------------------------------------------ suggestions
    def _sugg_row(self, row: dict) -> dict:
        for f in JSON_FIELDS_SUGGESTION:
            row[f] = _loads(row.get(f), [])
        return row

    def add_suggestion(self, *, kind: str, title: str, body: str, idea_type: str | None = None,
                       why_you: str = "", project_id: int | None = None,
                       item_ids: list[int] | None = None, first_steps: list[str] | None = None,
                       score: int = 3, origin: str = "agent") -> dict:
        if project_id and not self.get_project(project_id):
            project_id = None
        cur = self._exec(
            "INSERT INTO suggestions(kind, idea_type, title, body, why_you, project_id, item_ids,"
            " first_steps, score, origin, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (kind, idea_type, title.strip(), body.strip(), why_you.strip(), project_id or None,
             json.dumps(sorted(set(item_ids or []))), json.dumps(first_steps or [], ensure_ascii=False),
             max(1, min(5, int(score or 3))), origin, now_iso()))
        return self.get_suggestion(cur.lastrowid)

    def has_project_suggestion(self, item_id: int, project_id: int) -> bool:
        rows = self._all("SELECT item_ids FROM suggestions WHERE kind = 'project_use'"
                         " AND project_id = ?", (project_id,))
        return any(item_id in _loads(r["item_ids"], []) for r in rows)

    def get_suggestion(self, sid: int) -> dict | None:
        row = self._one("SELECT s.*, p.name AS project_name FROM suggestions s"
                        " LEFT JOIN projects p ON p.id = s.project_id WHERE s.id = ?", (sid,))
        return self._sugg_row(row) if row else None

    def list_suggestions(self, *, status: str | None = None, kind: str | None = None,
                         project_id: int | None = None, item_id: int | None = None,
                         limit: int = 200) -> list[dict]:
        where, params = [], []
        if status:
            where.append("s.status = ?"); params.append(status)
        if kind:
            where.append("s.kind = ?"); params.append(kind)
        if project_id:
            where.append("s.project_id = ?"); params.append(project_id)
        sql = ("SELECT s.*, p.name AS project_name FROM suggestions s"
               " LEFT JOIN projects p ON p.id = s.project_id")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY s.status = 'new' DESC, s.score DESC, s.id DESC LIMIT ?"
        rows = [self._sugg_row(r) for r in self._all(sql, (*params, limit))]
        if item_id is not None:
            rows = [r for r in rows if item_id in r["item_ids"]]
        return rows

    def set_suggestion_status(self, sid: int, status: str) -> dict | None:
        if status not in {"new", "accepted", "dismissed", "done"}:
            raise ValueError("Nieprawidłowy status")
        self._exec("UPDATE suggestions SET status = ? WHERE id = ?", (status, sid))
        return self.get_suggestion(sid)

    def delete_suggestion(self, sid: int) -> None:
        self._exec("DELETE FROM suggestions WHERE id = ?", (sid,))

    # ------------------------------------------------------------------ memory
    def list_memory(self) -> list[dict]:
        return self._all("SELECT * FROM memory ORDER BY category, id")

    def add_memory(self, category: str, content: str, origin: str = "agent") -> dict:
        ts = now_iso()
        cur = self._exec("INSERT INTO memory(category, content, origin, created_at, updated_at)"
                         " VALUES (?,?,?,?,?)", (category.strip() or "ogólne", content.strip(),
                                                 origin, ts, ts))
        return self._one("SELECT * FROM memory WHERE id = ?", (cur.lastrowid,))

    def update_memory(self, mid: int, content: str) -> dict | None:
        self._exec("UPDATE memory SET content = ?, updated_at = ? WHERE id = ?",
                   (content.strip(), now_iso(), mid))
        return self._one("SELECT * FROM memory WHERE id = ?", (mid,))

    def set_auto_memory(self, category: str, content: str) -> None:
        """Jeden automatycznie utrzymywany wpis na kategorię (np. profil zainteresowań)."""
        row = self._one("SELECT id FROM memory WHERE category = ? AND origin = 'auto'", (category,))
        if row:
            self.update_memory(row["id"], content)
        else:
            self.add_memory(category, content, origin="auto")

    def delete_memory(self, mid: int) -> bool:
        return self._exec("DELETE FROM memory WHERE id = ?", (mid,)).rowcount > 0

    # ------------------------------------------------------------------ conversations
    def create_conversation(self, title: str = "Nowa rozmowa") -> dict:
        ts = now_iso()
        cur = self._exec("INSERT INTO conversations(title, messages, created_at, updated_at)"
                         " VALUES (?, '[]', ?, ?)", (title, ts, ts))
        return self.get_conversation(cur.lastrowid)

    def get_conversation(self, cid: int) -> dict | None:
        row = self._one("SELECT * FROM conversations WHERE id = ?", (cid,))
        if row:
            row["messages"] = _loads(row["messages"], [])
        return row

    def list_conversations(self, limit: int = 30) -> list[dict]:
        return self._all("SELECT id, title, created_at, updated_at FROM conversations"
                         " ORDER BY updated_at DESC LIMIT ?", (limit,))

    def save_conversation(self, cid: int, messages: list, title: str | None = None) -> None:
        if title:
            self._exec("UPDATE conversations SET messages = ?, title = ?, updated_at = ? WHERE id = ?",
                       (json.dumps(messages, ensure_ascii=False), title, now_iso(), cid))
        else:
            self._exec("UPDATE conversations SET messages = ?, updated_at = ? WHERE id = ?",
                       (json.dumps(messages, ensure_ascii=False), now_iso(), cid))

    def delete_conversation(self, cid: int) -> None:
        self._exec("DELETE FROM conversations WHERE id = ?", (cid,))
