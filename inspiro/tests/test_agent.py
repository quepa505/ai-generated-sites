from types import SimpleNamespace

import anthropic
import httpx2 as httpx
import pytest
from anthropic.types.beta import BetaTextBlock, BetaThinkingBlock, BetaToolUseBlock

from inspiro.agent import LLM, Analyzer, ChatAgent, IdeaGenerator, display_messages
from inspiro.agent.analyzer import ItemAnalysis, ProjectMatch
from inspiro.agent.ideas import IdeaProposal, IdeasResult
from inspiro.agent.llm import FALLBACK_BETA, LLMRefusal
from inspiro.agent.prompts import CONTEXT_MARKER
from inspiro.connectors.base import NormalizedItem


def _analysis(project_id=0, relevance=4, usefulness=7):
    return ItemAnalysis(summary="Streszczenie", kind="tutorial", topics=["gamedev"], technologies=["Godot 4"],
                        key_insights=["w"], idea_seeds=["p"], usefulness=usefulness,
                        project_matches=[ProjectMatch(project_id=project_id, relevance=relevance,
                                                      how_to_use="Zrób X")] if project_id else [])


def _seed(db, n=2):
    return [db.upsert_item(NormalizedItem(source="reddit", external_id=f"t3_{i}", title=f"Post {i}",
                                          text=f"treść {i} godot"))[0] for i in range(n)]


def test_llm_adds_server_fallback_except_haiku(settings, fake_client):
    llm = LLM(settings, fake_client)
    assert llm._extra("claude-opus-5-5") == {"betas": [FALLBACK_BETA], "fallbacks": "default"}
    assert llm._extra("claude-haiku-5-5") == {}
    settings.server_fallback = False
    assert llm._extra("claude-opus-5-5") == {}


def test_analyzer_saves_analysis_and_project_suggestions(db, settings, fake_client):
    project = db.save_project({"name": "Roguelike", "stack": "Godot"})
    ids = _seed(db, 2)
    fake_client.beta.messages.parse_results = [_analysis(project_id=project["id"])]
    res = Analyzer(db, LLM(settings, fake_client), settings).analyze_pending()
    assert res == {"analyzed": 2, "errors": 0, "suggestions": 2}
    item = db.get_item(ids[0])
    assert item["status"] == "analyzed" and item["usefulness"] == 5      # 7 przycięte do 1–5
    sugg = db.list_suggestions(kind="project_use")
    assert {s["project_id"] for s in sugg} == {project["id"]} and len(sugg) == 2
    call = fake_client.beta.messages.parse_calls[0]
    assert call["model"] == "claude-opus-5-5" and call["output_config"] == {"effort": "low"}
    assert call["output_format"] is ItemAnalysis
    assert call["system"][-1]["cache_control"] == {"type": "ephemeral"}   # stały prefiks cache'owany
    assert "Roguelike" in call["system"][-1]["text"]
    assert '<zakladka id="' in call["messages"][0]["content"]
    # ponowna analiza nie dubluje sugestii
    Analyzer(db, LLM(settings, fake_client), settings).analyze_item(db.get_item(ids[0]))
    assert len(db.list_suggestions(kind="project_use")) == 2


def test_analyzer_ignores_weak_or_unknown_matches_and_records_errors(db, settings, fake_client):
    project = db.save_project({"name": "P"})
    ids = _seed(db, 3)
    fake_client.beta.messages.parse_results = [
        _analysis(project_id=project["id"], relevance=2),      # za słabe
        _analysis(project_id=999, relevance=5),                # nieistniejący projekt
        RuntimeError("boom"),
    ]
    settings.analysis_concurrency = 1
    res = Analyzer(db, LLM(settings, fake_client), settings).analyze_pending()
    assert res["analyzed"] == 2 and res["errors"] == 1 and res["suggestions"] == 0
    assert db.get_item(ids[2])["status"] == "error"


def test_refusal_is_reported(db, settings, fake_client):
    _seed(db, 1)
    msgs = fake_client.beta.messages
    msgs.parse = lambda **kw: SimpleNamespace(stop_reason="refusal", parsed_output=None,
                                              stop_details=SimpleNamespace(category="cyber"))
    with pytest.raises(LLMRefusal):
        Analyzer(db, LLM(settings, fake_client), settings).analyze_item(db.pending_items()[0])


def test_idea_generator(db, settings, fake_client):
    ids = _seed(db, 3)
    for i in ids:
        db.save_analysis(i, {"summary": "s", "kind": "tutorial", "topics": ["gamedev"], "technologies": [],
                             "key_insights": [], "idea_seeds": [], "usefulness": 4}, "m")
    fake_client.beta.messages.parse_results = [IdeasResult(ideas=[
        IdeaProposal(title="Gra", idea_type="game", pitch="Opis", why_you="Bo tak", first_steps=["a", "b"],
                     source_item_ids=[ids[0], 12345], project_id=0, score=4)],
        interest_profile="Lubi gamedev.")]
    res = IdeaGenerator(db, LLM(settings, fake_client), settings).generate(count=1, focus="gry mobilne")
    assert res["created"] == 1
    idea = db.list_suggestions(kind="new_idea")[0]
    assert idea["item_ids"] == [ids[0]] and idea["project_id"] is None   # nieznane id odfiltrowane
    assert db.list_memory()[0]["content"] == "Lubi gamedev."
    call = fake_client.beta.messages.parse_calls[0]
    assert "gry mobilne" in call["messages"][0]["content"] and call["output_config"] == {"effort": "high"}


def test_idea_generator_needs_analyzed_items(db, settings, fake_client):
    with pytest.raises(ValueError):
        IdeaGenerator(db, LLM(settings, fake_client), settings).generate()


def _resp(blocks, stop):
    return SimpleNamespace(content=blocks, stop_reason=stop, stop_details=None)


def test_chat_tool_loop_and_append_only_history(db, settings, fake_client):
    item_id = _seed(db, 1)[0]
    msgs = fake_client.beta.messages
    msgs.create_results = [
        _resp([BetaThinkingBlock(type="thinking", thinking="", signature="sig1"),
               BetaToolUseBlock(type="tool_use", id="tu1", name="search_saved", input={"query": "godot", "limit": 5}),
               BetaToolUseBlock(type="tool_use", id="tu2", name="remember",
                                input={"category": "umiejętności", "content": "Zna Godota"})], "tool_use"),
        _resp([BetaTextBlock(type="text", text=f"Znalazłem [#{item_id}] Post 0.")], "end_turn"),
        _resp([BetaTextBlock(type="text", text="Druga odpowiedź")], "end_turn"),
    ]
    agent = ChatAgent(db, LLM(settings, fake_client), settings)
    conv = db.create_conversation()
    out = agent.send(conv["id"], "Co mam o Godocie? Znam Godota.")
    assert out["reply"] == f"Znalazłem [#{item_id}] Post 0."
    assert [e["tool"] for e in out["events"]] == ["search_saved", "remember"]
    assert db.list_memory()[0]["content"] == "Zna Godota"

    first, second = msgs.create_calls[0], msgs.create_calls[1]
    assert first["messages"][0]["content"][0]["text"].startswith(CONTEXT_MARKER)
    assert first["tools"][0]["strict"] is True and first["output_config"] == {"effort": "medium"}
    results = second["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["tu1", "tu2"]          # wszystkie wyniki w jednej wiadomości
    assert '"Post 0"' in results[0]["content"]

    agent.send(conv["id"], "A coś jeszcze?")
    third = msgs.create_calls[2]
    # historia jest dopisywana: wcześniejsze wiadomości identyczne bajt w bajt, system zamrożony
    assert third["messages"][:4] == second["messages"][:3] + [third["messages"][3]]
    assert third["system"] == first["system"] and third["tools"] == first["tools"]
    assert third["messages"][1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig1"}

    shown = display_messages(db.get_conversation(conv["id"])["messages"])
    assert [m["role"] for m in shown] == ["user", "assistant", "user", "assistant"]
    assert shown[0]["text"] == "Co mam o Godocie? Znam Godota."          # kontekst startowy ukryty
    assert [t["tool"] for t in shown[1]["tools"]] == ["search_saved", "remember"]
    assert db.get_conversation(conv["id"])["title"].startswith("Co mam o Godocie")


def test_chat_strips_thinking_after_signature_error(db, settings, fake_client):
    conv = db.create_conversation()
    db.save_conversation(conv["id"], [
        {"role": "user", "content": "hej"},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "", "signature": "zła"},
                                          {"type": "text", "text": "cześć"}]},
    ])
    bad = anthropic.BadRequestError(
        "messages.1.content.0: Invalid `signature` in `thinking` block.",
        response=httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")),
        body=None)
    fake_client.beta.messages.create_results = [bad, _resp([BetaTextBlock(type="text", text="ok")], "end_turn")]
    out = ChatAgent(db, LLM(settings, fake_client), settings).send(conv["id"], "i co?")
    assert out["reply"] == "ok"
    retry = fake_client.beta.messages.create_calls[1]["messages"]
    assert retry[1]["content"] == [{"type": "text", "text": "cześć"}]


def test_tool_errors_are_returned_not_raised(db):
    from inspiro.agent.tools import ToolExecutor
    ex = ToolExecutor(db)
    out, err = ex.run("get_saved_item", {"item_id": 999})
    assert not err and "Brak zakładki" in out
    out, err = ex.run("save_project", {"project_id": 0, "name": "", "description": "", "stack": "",
                                       "goals": "", "status": "active"})
    assert err and "nazwę" in out
    out, err = ex.run("nope", {})
    assert err


def test_missing_api_key_stops_batch_and_keeps_items_pending(db, settings, fake_client):
    from inspiro.agent.llm import LLMError
    _seed(db, 3)
    fake_client.beta.messages.parse_results = [TypeError(
        "Could not resolve authentication method. Expected one of api_key, auth_token, or credentials")]
    settings.analysis_concurrency = 1
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        Analyzer(db, LLM(settings, fake_client), settings).analyze_pending()
    assert db.count_items(status="new") == 3 and db.count_items(status="error") == 0
