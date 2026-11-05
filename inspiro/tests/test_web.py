import base64
import json
import time
from types import SimpleNamespace

import pytest
from anthropic.types.beta import BetaTextBlock
from fastapi.testclient import TestClient

from inspiro.agent import LLM
from inspiro.agent.analyzer import ItemAnalysis
from inspiro.demo import load_demo
from inspiro.services import Services
from inspiro.web.app import create_app


@pytest.fixture
def svc(settings, db, fake_client):
    return Services(settings, db=db, llm=LLM(settings, fake_client))


@pytest.fixture
def client(svc):
    return TestClient(create_app(svc))


def test_ui_and_demo_data(client, svc):
    assert "Inspiro" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    load_demo(svc.db)
    assert load_demo(svc.db) == {"status": "demo już wczytane"}
    st = client.get("/api/stats").json()
    assert st["items_total"] == 9 and st["by_status"]["new"] == 2 and st["projects"] == 2
    assert st["sources"]["reddit"]["configured"] is False
    hits = client.get("/api/items", params={"q": "sqlite"}).json()
    assert hits and "sqlite" in hits[0]["title"].lower()
    first = client.get(f"/api/items/{hits[0]['id']}").json()
    assert first["suggestions"]                      # powiązana propozycja do projektu
    assert len(client.get("/api/suggestions", params={"status": "new", "kind": "new_idea"}).json()) == 3


def test_import_manual_and_projects(client):
    payload = {"filename": "x.json", "content": json.dumps(
        {"source": "x", "items": [{"id": "1", "url": "https://x.com/a/status/1", "text": "hej"}]})}
    assert client.post("/api/import", json=payload).json() == {"parsed": 1, "added": 1, "skipped": 0}
    assert client.post("/api/import", json={"filename": "a.txt", "content": "nic"}).status_code == 400
    assert client.post("/api/items", json={"url": "https://example.com/z"}).json()["created"] is True
    assert client.post("/api/items", json={}).status_code == 400
    p = client.post("/api/projects", json={"name": "Gra", "stack": "Godot"}).json()
    assert client.put(f"/api/projects/{p['id']}", json={"goals": "demo"}).json()["goals"] == "demo"
    assert client.post("/api/projects", json={"name": ""}).status_code == 400
    assert client.delete(f"/api/projects/{p['id']}").json() == {"ok": True}


def test_suggestions_memory_and_chat(client, svc, fake_client):
    s = svc.db.add_suggestion(kind="new_idea", title="T", body="B", idea_type="game")
    assert client.patch(f"/api/suggestions/{s['id']}", json={"status": "accepted"}).json()["status"] == "accepted"
    assert client.patch(f"/api/suggestions/{s['id']}", json={"status": "zły"}).status_code == 400
    m = client.post("/api/memory", json={"category": "cele", "content": "Wydać grę"}).json()
    assert client.get("/api/memory").json()[0]["origin"] == "user"
    client.delete(f"/api/memory/{m['id']}")

    fake_client.beta.messages.create_results = [
        SimpleNamespace(content=[BetaTextBlock(type="text", text="**Cześć!**")], stop_reason="end_turn", stop_details=None)]
    conv = client.post("/api/conversations").json()
    out = client.post(f"/api/conversations/{conv['id']}/messages", json={"text": "Hej"}).json()
    assert out["reply"] == "**Cześć!**"
    shown = client.get(f"/api/conversations/{conv['id']}").json()
    assert [m["role"] for m in shown["messages"]] == ["user", "assistant"]
    assert client.post("/api/conversations/999/messages", json={"text": "x"}).status_code == 404


def test_background_analyze_job(client, svc, fake_client):
    load_demo(svc.db)
    fake_client.beta.messages.parse_results = [ItemAnalysis(
        summary="s", kind="inne", topics=["t"], technologies=[], key_insights=[], idea_seeds=[],
        usefulness=3, project_matches=[])]
    job = client.post("/api/jobs", json={"kind": "analyze"}).json()
    for _ in range(100):
        state = next(j for j in client.get("/api/jobs").json() if j["id"] == job["id"])
        if state["status"] != "running":
            break
        time.sleep(0.05)
    assert state["status"] == "done" and state["result"]["analyzed"] == 2
    assert client.post("/api/jobs", json={"kind": "nope"}).status_code == 400
    # synchronizacja bez skonfigurowanych źródeł kończy się czytelnym błędem
    job = svc.start_job("sync", background=False)
    assert job.status == "error" and "Żadne źródło" in job.error


def test_basic_auth(settings, db, fake_client):
    settings.password = "tajne"
    c = TestClient(create_app(Services(settings, db=db, llm=LLM(settings, fake_client))))
    assert c.get("/api/stats").status_code == 401
    ok = "Basic " + base64.b64encode(b"u:tajne").decode()
    assert c.get("/api/stats", headers={"Authorization": ok}).status_code == 200


def test_x_oauth_flow(settings, db, fake_client, monkeypatch):
    settings.x_client_id = "cid"
    svc = Services(settings, db=db, llm=LLM(settings, fake_client))
    c = TestClient(create_app(svc), follow_redirects=False)
    r = c.get("/auth/x/login")
    assert r.status_code == 307 and r.headers["location"].startswith("https://x.com/i/oauth2/authorize")
    state = db.kv_get("x_oauth_pending")["state"]
    assert c.get("/auth/x/callback", params={"code": "c", "state": "zły"}).status_code == 400
    from inspiro.connectors import x as x_mod
    monkeypatch.setattr(x_mod, "exchange_code", lambda s, http, code, v: {"access_token": "a", "expires_at": 9e9})
    r = c.get("/auth/x/callback", params={"code": "c", "state": state})
    assert r.status_code == 307 and db.kv_get(x_mod.TOKEN_KEY)["access_token"] == "a"
    assert c.get("/api/sources").json()["x"]["connected"] is True
