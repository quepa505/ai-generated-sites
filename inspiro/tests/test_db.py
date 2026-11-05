from inspiro.connectors.base import NormalizedItem


def _item(eid, title="Tytuł", text="treść", source="reddit"):
    return NormalizedItem(source=source, external_id=eid, url=f"https://ex.com/{eid}", title=title, text=text)


def test_upsert_deduplicates(db):
    a, new_a = db.upsert_item(_item("t3_a"))
    b, new_b = db.upsert_item(_item("t3_a", title="inny"))
    assert new_a and not new_b and a == b
    assert db.add_items([_item("t3_a"), _item("t3_b")]) == {"added": 1, "skipped": 1}
    assert db.count_items() == 2


def test_search_handles_polish_diacritics_and_analysis_fields(db):
    i1, _ = db.upsert_item(_item("t3_1", "Generowanie lochów w Godocie", "algorytm WFC"))
    db.upsert_item(_item("t3_2", "Przepis na pierogi", "kuchnia"))
    assert [r["id"] for r in db.search("lochow")] == [i1]          # bez polskich znaków
    assert [r["id"] for r in db.search("WFC godot")] == [i1]
    db.save_analysis(i1, {"summary": "s", "kind": "tutorial", "topics": ["gamedev"],
                          "technologies": ["Godot 4"], "key_insights": [], "idea_seeds": [],
                          "usefulness": 5}, "m")
    assert [r["id"] for r in db.search("gamedev")] == [i1]          # pole z analizy trafia do indeksu
    assert db.search("pierogi gamedev")                             # fallback OR
    assert db.search("") == []
    assert db.search('"; DROP TABLE items; --') == []                # bezpieczne dla FTS


def test_analysis_stats_and_status(db):
    i1, _ = db.upsert_item(_item("t3_1"))
    db.upsert_item(_item("x1", source="x"))
    assert len(db.pending_items()) == 2
    db.save_analysis(i1, {"summary": "s", "kind": "tool", "topics": ["AI", "rag"],
                          "technologies": ["SQLite"], "key_insights": ["k"], "idea_seeds": ["i"],
                          "usefulness": 4}, "m")
    item = db.get_item(i1)
    assert item["status"] == "analyzed" and item["topics"] == ["AI", "rag"]
    st = db.stats()
    assert st["items_total"] == 2 and st["by_source"] == {"reddit": 1, "x": 1}
    assert ("ai", 1) in st["top_topics"] and ("SQLite", 1) in st["top_technologies"]
    assert [i["id"] for i in db.list_items(min_usefulness=4)] == [i1]


def test_projects_suggestions_memory(db):
    p = db.save_project({"name": "Gra", "stack": "Godot"})
    p2 = db.save_project({"description": "nowy opis"}, project_id=p["id"])
    assert p2["name"] == "Gra" and p2["description"] == "nowy opis"   # puste pola nie kasują
    item_id, _ = db.upsert_item(_item("t3_1"))
    s = db.add_suggestion(kind="project_use", title="T", body="B", project_id=p["id"],
                          item_ids=[item_id, item_id], score=9)
    assert s["item_ids"] == [item_id] and s["score"] == 5 and s["project_name"] == "Gra"
    assert db.has_project_suggestion(item_id, p["id"])
    assert db.list_suggestions(item_id=item_id)[0]["id"] == s["id"]
    assert db.set_suggestion_status(s["id"], "accepted")["status"] == "accepted"
    db.set_auto_memory("profil", "v1")
    db.set_auto_memory("profil", "v2")
    mem = db.list_memory()
    assert len(mem) == 1 and mem[0]["content"] == "v2"


def test_conversation_roundtrip(db):
    c = db.create_conversation()
    db.save_conversation(c["id"], [{"role": "user", "content": "hej"}], title="Hej")
    got = db.get_conversation(c["id"])
    assert got["title"] == "Hej" and got["messages"][0]["content"] == "hej"
