"""FastAPI: JSON API + statyczny interfejs (SPA)."""

from __future__ import annotations

import base64
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from ..agent import LLMError, display_messages
from ..agent.llm import describe_error
from ..connectors import ConnectorError
from ..connectors import files as file_import
from ..connectors import reddit as reddit_mod
from ..connectors import x as x_mod
from ..services import Services

STATIC = Path(__file__).parent / "static"


class BasicAuth(BaseHTTPMiddleware):
    def __init__(self, app, password: str):
        super().__init__(app)
        self.password = password

    async def dispatch(self, request: Request, call_next):
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                _, pwd = base64.b64decode(header[6:]).decode().split(":", 1)
                if secrets.compare_digest(pwd.encode(), self.password.encode()):
                    return await call_next(request)
            except (ValueError, UnicodeDecodeError):
                pass
        return HTMLResponse("Wymagane logowanie", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="inspiro"'})


class ProjectIn(BaseModel):
    name: str = ""
    description: str = ""
    stack: str = ""
    goals: str = ""
    status: str = "active"


class ManualItemIn(BaseModel):
    url: str = ""
    title: str = ""
    text: str = ""
    note: str = ""


class ImportIn(BaseModel):
    filename: str
    content: str


class MemoryIn(BaseModel):
    category: str = "ogólne"
    content: str


class MessageIn(BaseModel):
    text: str


class JobIn(BaseModel):
    kind: str
    params: dict[str, Any] = {}


def create_app(services: Services) -> FastAPI:
    s, db = services.settings, services.db
    app = FastAPI(title="Inspiro", docs_url="/api/docs", redoc_url=None)
    if s.password:
        app.add_middleware(BasicAuth, password=s.password)

    @app.exception_handler(ConnectorError)
    async def _connector_err(_req, exc: ConnectorError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(LLMError)
    async def _llm_err(_req, exc: LLMError):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    # ------------------------------------------------------------------ UI
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # ------------------------------------------------------------------ przegląd
    @app.get("/api/stats")
    def stats():
        return {**db.stats(), "sources": services.sources_status()}

    # ------------------------------------------------------------------ zakładki
    @app.get("/api/items")
    def list_items(status: str | None = None, source: str | None = None, q: str | None = None,
                   min_usefulness: int | None = None, limit: int = 50, offset: int = 0):
        return db.list_items(status=status or None, source=source or None, query=q or None,
                             min_usefulness=min_usefulness, limit=min(limit, 200), offset=offset)

    @app.get("/api/items/{item_id}")
    def get_item(item_id: int):
        item = db.get_item(item_id)
        if not item:
            raise HTTPException(404, "Nie ma takiej zakładki")
        return item

    @app.post("/api/items")
    def add_item(body: ManualItemIn):
        item = file_import.manual_item(body.url, body.title, body.text, body.note)
        item_id, new = db.upsert_item(item)
        return {"id": item_id, "created": new}

    @app.delete("/api/items/{item_id}")
    def delete_item(item_id: int):
        db.delete_item(item_id)
        return {"ok": True}

    @app.post("/api/items/{item_id}/analyze")
    def analyze_item(item_id: int):
        item = db.get_item(item_id)
        if not item:
            raise HTTPException(404, "Nie ma takiej zakładki")
        try:
            services.analyzer.analyze_item(item)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Analiza nieudana: {describe_error(e)}") from e
        return db.get_item(item_id)

    @app.post("/api/import")
    def import_file(body: ImportIn):
        items = file_import.parse_file(body.filename, body.content)
        if not items:
            raise HTTPException(400, "W pliku nie znaleziono żadnych zakładek.")
        return {"parsed": len(items), **db.add_items(items)}

    # ------------------------------------------------------------------ projekty
    @app.get("/api/projects")
    def projects():
        return db.list_projects()

    @app.post("/api/projects")
    def create_project(body: ProjectIn):
        try:
            return db.save_project(body.model_dump())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.put("/api/projects/{pid}")
    def update_project(pid: int, body: ProjectIn):
        try:
            return db.save_project(body.model_dump(), project_id=pid)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e

    @app.delete("/api/projects/{pid}")
    def delete_project(pid: int):
        db.delete_project(pid)
        return {"ok": True}

    # ------------------------------------------------------------------ pomysły
    @app.get("/api/suggestions")
    def suggestions(status: str | None = None, kind: str | None = None,
                    project_id: int | None = None):
        return db.list_suggestions(status=status or None, kind=kind or None, project_id=project_id)

    @app.patch("/api/suggestions/{sid}")
    def update_suggestion(sid: int, body: dict = Body(...)):
        try:
            row = db.set_suggestion_status(sid, body.get("status", ""))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if not row:
            raise HTTPException(404, "Nie ma takiego pomysłu")
        return row

    @app.delete("/api/suggestions/{sid}")
    def delete_suggestion(sid: int):
        db.delete_suggestion(sid)
        return {"ok": True}

    # ------------------------------------------------------------------ pamięć
    @app.get("/api/memory")
    def memory():
        return db.list_memory()

    @app.post("/api/memory")
    def add_memory(body: MemoryIn):
        if not body.content.strip():
            raise HTTPException(400, "Pusta treść")
        return db.add_memory(body.category, body.content, origin="user")

    @app.put("/api/memory/{mid}")
    def edit_memory(mid: int, body: MemoryIn):
        return db.update_memory(mid, body.content)

    @app.delete("/api/memory/{mid}")
    def delete_memory(mid: int):
        db.delete_memory(mid)
        return {"ok": True}

    # ------------------------------------------------------------------ czat
    @app.get("/api/conversations")
    def conversations():
        return db.list_conversations()

    @app.post("/api/conversations")
    def new_conversation():
        conv = db.create_conversation()
        return {"id": conv["id"], "title": conv["title"]}

    @app.get("/api/conversations/{cid}")
    def get_conversation(cid: int):
        conv = db.get_conversation(cid)
        if not conv:
            raise HTTPException(404, "Nie ma takiej rozmowy")
        return {"id": conv["id"], "title": conv["title"],
                "messages": display_messages(conv["messages"])}

    @app.delete("/api/conversations/{cid}")
    def delete_conversation(cid: int):
        db.delete_conversation(cid)
        return {"ok": True}

    @app.post("/api/conversations/{cid}/messages")
    def send_message(cid: int, body: MessageIn):
        if not body.text.strip():
            raise HTTPException(400, "Pusta wiadomość")
        try:
            return services.chat.send(cid, body.text.strip())
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except Exception as e:  # noqa: BLE001 — np. brak klucza API
            raise HTTPException(502, describe_error(e)) from e

    # ------------------------------------------------------------------ zadania
    @app.post("/api/jobs")
    def start_job(body: JobIn):
        try:
            return services.start_job(body.kind, body.params).to_dict()
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/jobs")
    def jobs():
        return services.jobs.list()

    # ------------------------------------------------------------------ źródła
    @app.get("/api/sources")
    def sources():
        return {**services.sources_status(), "reddit_bookmarklet": reddit_mod.BOOKMARKLET,
                "x_scraper": x_mod.SCRAPER_SNIPPET, "x_redirect_uri": s.x_redirect_uri}

    @app.get("/auth/x/login", include_in_schema=False)
    def x_login():
        if not s.x_configured:
            raise HTTPException(400, "Ustaw X_CLIENT_ID w pliku .env")
        verifier, challenge = x_mod.make_pkce()
        state = secrets.token_urlsafe(16)
        db.kv_set("x_oauth_pending", {"state": state, "verifier": verifier})
        return RedirectResponse(x_mod.authorize_url(s.x_client_id, s.x_redirect_uri, state, challenge))

    @app.get("/auth/x/callback", include_in_schema=False)
    def x_callback(code: str = "", state: str = "", error: str = ""):
        pending = db.kv_get("x_oauth_pending") or {}
        if error:
            return RedirectResponse(f"/#zrodla?x_error={quote(error)}")
        expected = pending.get("state", "")
        if not code or not expected or not secrets.compare_digest(state.encode(), expected.encode()):
            raise HTTPException(400, "Nieprawidłowy stan OAuth — spróbuj połączyć ponownie.")
        import httpx2
        http = services.http or httpx2.Client(timeout=30)
        tokens = x_mod.exchange_code(s, http, code, pending["verifier"])
        db.kv_set(x_mod.TOKEN_KEY, tokens)
        db.kv_delete("x_oauth_pending")
        return RedirectResponse("/#zrodla?x_connected=1")

    @app.post("/api/sources/x/disconnect")
    def x_disconnect():
        db.kv_delete(x_mod.TOKEN_KEY)
        return {"ok": True}

    return app
