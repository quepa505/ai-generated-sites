"""Przykładowe dane do obejrzenia interfejsu bez kluczy API.

Wszystkie wpisy są fikcyjne (autorzy "demo_*") — usuń bazę data/inspiro.db, by zacząć od zera.
"""

from __future__ import annotations

from .connectors.base import NormalizedItem

PROJECTS = [
    {"name": "Roguelike w Godocie", "status": "active",
     "description": "Mała gra roguelike z proceduralnymi lochami, robiona po godzinach. Jest prototyp ruchu i walki.",
     "stack": "Godot 4, GDScript, Aseprite", "goals": "Grywalne demo na itch.io w 3 miesiące"},
    {"name": "Asystent notatek z RAG", "status": "active",
     "description": "Aplikacja webowa, która indeksuje moje notatki Markdown i odpowiada na pytania.",
     "stack": "Python, FastAPI, SQLite, Claude API", "goals": "Używać codziennie; potem udostępnić jako open source"},
]

MEMORY = [
    ("umiejętności", "Dobrze zna Pythona; Godota uczy się od pół roku. Słabiej z grafiką 2D."),
    ("czas", "Ma ok. 6–8 godzin tygodniowo na projekty poboczne, głównie w weekendy."),
]

ITEMS = [
    dict(source="reddit", external_id="t3_demo01", context="r/godot", author="demo_dev",
         url="https://www.reddit.com/r/godot/comments/demo01/",
         title="Wave Function Collapse w Godot 4 — generator lochów w 200 liniach",
         text="Podzieliłem się implementacją WFC do generowania lochów na kafelkach. Kluczowe: reguły sąsiedztwa z obrazka-wzorca, backtracking przy sprzeczności, seed do powtarzalności.",
         analysis=dict(summary="Implementacja algorytmu Wave Function Collapse do proceduralnego generowania lochów w Godot 4, z backtrackingiem i powtarzalnym seedem.",
                       kind="tutorial", topics=["gamedev", "generowanie proceduralne", "algorytmy"],
                       technologies=["Godot 4", "GDScript"], usefulness=5,
                       key_insights=["Reguły sąsiedztwa można wyciągnąć automatycznie z małego obrazka-wzorca", "Backtracking rozwiązuje sprzeczności bez restartu całej mapy"],
                       idea_seeds=["Generator lochów do roguelike'a", "Odcinek YouTube: WFC wyjaśnione na kafelkach"])),
    dict(source="x", external_id="demo_x_01", context="Demo Creator", author="@demo_creator",
         url="https://x.com/demo_creator/status/1", title="Contextual retrieval: dodaj do każdego chunka 1–2 zdania kontekstu",
         text="Prosty trik do RAG: przed embeddingiem dopisz do każdego fragmentu krótki opis, skąd pochodzi i czego dotyczy cały dokument. U nas -35% nietrafionych wyszukiwań. Łączcie z BM25.",
         analysis=dict(summary="Wątek o technice contextual retrieval w RAG: dopisywanie kontekstu dokumentu do fragmentów przed indeksowaniem, w połączeniu z BM25.",
                       kind="inspiracja", topics=["rag", "wyszukiwanie", "llm"], technologies=["BM25", "embeddings"], usefulness=5,
                       key_insights=["Kontekst dopisany do chunków zmniejsza liczbę chybionych wyszukiwań", "Hybryda BM25 + embeddings działa lepiej niż każde z osobna"],
                       idea_seeds=["Dodać contextual retrieval do asystenta notatek"])),
    dict(source="reddit", external_id="t3_demo02", context="r/gamedev", author="demo_indie",
         url="https://www.reddit.com/r/gamedev/comments/demo02/",
         title="Jak zrobiłem 'game feel' w 2 dni: screen shake, hit stop, particles",
         text="Lista 12 drobnych efektów, które zmieniły odbiór walki: hit stop 60 ms, delikatny screen shake, błysk sprite'a, knockback, dźwięk z losowym pitchem...",
         analysis=dict(summary="Praktyczna lista efektów 'game feel' (hit stop, screen shake, knockback), które poprawiają odczucie walki w grach akcji.",
                       kind="tutorial", topics=["gamedev", "game feel", "projektowanie gier"], technologies=["Godot 4"], usefulness=4,
                       key_insights=["Hit stop ~60 ms daje wyraźne poczucie siły ciosu", "Losowy pitch dźwięku zapobiega monotonii"],
                       idea_seeds=["Checklist game feel dla prototypu walki", "Film: przed/po dodaniu juice"])),
    dict(source="x", external_id="demo_x_02", context="Demo Tuber", author="@demo_tuber",
         url="https://x.com/demo_tuber/status/2", title="Devlog, który ludzie oglądają: pokaż porażkę w pierwszych 30 sekundach",
         text="Po 40 devlogach: najlepiej działają odcinki, które zaczynają się od problemu ('moja gra była nudna'), a kończą konkretną zmianą. Długość 8–12 min.",
         analysis=dict(summary="Wnioski twórcy devlogów: odcinki zaczynające się od problemu i kończące konkretną zmianą mają najlepszą retencję.",
                       kind="inspiracja", topics=["youtube", "devlog", "storytelling"], technologies=[], usefulness=4,
                       key_insights=["Hak: problem w pierwszych 30 sekundach", "Optymalna długość 8–12 minut"],
                       idea_seeds=["Seria devlogów z budowy roguelike'a"])),
    dict(source="reddit", external_id="t3_demo03", context="r/LocalLLaMA", author="demo_ml",
         url="https://www.reddit.com/r/LocalLLaMA/comments/demo03/",
         title="sqlite-vec + FTS5: wyszukiwanie hybrydowe bez osobnej bazy wektorowej",
         text="Pokazuję, jak trzymać embeddingi w SQLite (rozszerzenie sqlite-vec) obok FTS5 i łączyć wyniki przez Reciprocal Rank Fusion. Jeden plik bazy, zero serwerów.",
         analysis=dict(summary="Poradnik łączenia sqlite-vec i FTS5 w jednym pliku SQLite z fuzją wyników RRF — wyszukiwanie hybrydowe bez zewnętrznej bazy wektorowej.",
                       kind="tutorial", topics=["rag", "bazy danych", "wyszukiwanie"], technologies=["SQLite", "sqlite-vec", "FTS5"], usefulness=5,
                       key_insights=["RRF łączy rankingi bez kalibracji wyników", "Cały indeks mieści się w jednym pliku"],
                       idea_seeds=["Wyszukiwanie hybrydowe w asystencie notatek"])),
    dict(source="x", external_id="demo_x_03", context="Demo Pixel", author="@demo_pixel",
         url="https://x.com/demo_pixel/status/3", title="Paleta 16 kolorów + dithering = spójna grafika nawet bez talentu",
         text="Moja zasada dla programistów robiących grafikę: wybierz gotową paletę (np. 16 kolorów), rysuj w 16x16, cieniuj ditheringiem. Spójność > szczegóły.",
         analysis=dict(summary="Porada dla programistów: ograniczona paleta, małe sprite'y i dithering dają spójną oprawę graficzną bez zaawansowanych umiejętności.",
                       kind="inspiracja", topics=["pixel art", "gamedev", "grafika"], technologies=["Aseprite"], usefulness=4,
                       key_insights=["Ograniczenie palety wymusza spójność", "16x16 to dobry rozmiar na start"],
                       idea_seeds=["Ustalić paletę dla roguelike'a"])),
    dict(source="reddit", external_id="t3_demo04", context="r/SideProject", author="demo_maker",
         url="https://www.reddit.com/r/SideProject/comments/demo04/",
         title="Mój weekendowy projekt: bot, który zamienia zapisane linki w cotygodniowy newsletter",
         text="Zbieram linki z różnych miejsc, LLM grupuje je tematycznie i pisze krótkie podsumowanie. Wysyłam sobie maila w niedzielę. 30 osób poprosiło o dostęp.",
         analysis=dict(summary="Side project: automatyczny cotygodniowy newsletter z zapisanych linków, grupowanych i streszczanych przez LLM.",
                       kind="showcase", topics=["side project", "llm", "automatyzacja"], technologies=["LLM"], usefulness=3,
                       key_insights=["Osobiste narzędzie szybko znalazło innych chętnych"],
                       idea_seeds=["Cotygodniowy digest zakładek"])),
    dict(source="x", external_id="demo_x_04", context="Demo Dev", author="@demo_dev",
         url="https://x.com/demo_dev/status/4", title="Hot take: najlepszy pierwszy projekt AI to narzędzie dla samego siebie",
         text="Nie rób kolejnego chatbota. Zautomatyzuj coś, co sam robisz co tydzień. Masz od razu użytkownika (siebie) i feedback.", analysis=None),
    dict(source="reddit", external_id="t3_demo05", context="r/proceduralgeneration", author="demo_proc",
         url="https://www.reddit.com/r/proceduralgeneration/comments/demo05/",
         title="Generowanie nazw miejsc łańcuchami Markowa z polskich nazw miejscowości", text="", analysis=None),
]

SUGGESTIONS = [
    dict(kind="project_use", project="Roguelike w Godocie", items=["t3_demo01"], score=5,
         title="Roguelike w Godocie: generator lochów WFC",
         body="Zastąp ręcznie robione pokoje generatorem WFC: przygotuj obrazek-wzorzec 8x8 z kafelków, wyciągnij reguły sąsiedztwa i dodaj seed do powtarzalnych poziomów."),
    dict(kind="project_use", project="Asystent notatek z RAG", items=["demo_x_01", "t3_demo03"], score=5,
         title="Asystent notatek z RAG: contextual retrieval + hybryda w SQLite",
         body="Przed indeksowaniem dopisz do fragmentów notatek 1–2 zdania kontekstu, a wyszukiwanie zrób hybrydowo: FTS5 + sqlite-vec połączone przez RRF — bez osobnej bazy wektorowej."),
    dict(kind="new_idea", idea_type="youtube", items=["t3_demo01", "t3_demo02", "demo_x_02"], score=5,
         title="Seria devlogów „Roguelike od zera po godzinach”",
         body="Kanał/seria 8–12 minutowych odcinków dokumentujących budowę roguelike'a. Każdy odcinek: problem → eksperyment → efekt przed/po (np. game feel, generator WFC).",
         why_you="Zapisujesz zarówno techniczne poradniki gamedev, jak i wskazówki o devlogach — a roguelike i tak powstaje, więc materiał nagrywa się przy okazji.",
         steps=["Nagraj stan obecny prototypu jako punkt startowy", "Odcinek 1: „Moja walka była nudna” — 12 efektów game feel", "Ustal stały format miniatur i tytułów"]),
    dict(kind="new_idea", idea_type="game", items=["t3_demo01", "demo_x_03"], score=4,
         title="Mikro-gra: kartograf lochów",
         body="Gra-zagadka, w której gracz układa kafelki zgodnie z regułami WFC, a gra generuje resztę mapy. Pixel art w 16 kolorach.",
         why_you="Łączy Twoje zainteresowanie generowaniem proceduralnym z prostą oprawą graficzną, którą zrobisz sam.",
         steps=["Prototyp siatki 8x8 z regułami sąsiedztwa", "Paleta 16 kolorów i 6 kafelków", "Wydanie na itch.io jako jam game"]),
    dict(kind="new_idea", idea_type="app", items=["t3_demo04", "demo_x_04"], score=4,
         title="Niedzielny digest Twoich zakładek",
         body="Cotygodniowy mail/strona z pogrupowanymi zakładkami z tygodnia, krótkim podsumowaniem i jedną propozycją działania.",
         why_you="Masz już Inspiro i bazę zakładek — to naturalne rozszerzenie, a podobny projekt szybko zdobył chętnych.",
         steps=["Zapytanie: zakładki z 7 dni pogrupowane po tematach", "Szablon maila", "Cron w niedzielę o 9:00"]),
]


def load_demo(db) -> dict:
    if db.count_items() and db.has_item("reddit", "t3_demo01"):
        return {"status": "demo już wczytane"}
    projects = {p["name"]: db.save_project(p)["id"] for p in PROJECTS}
    for cat, content in MEMORY:
        db.add_memory(cat, content, origin="user")
    ids = {}
    for spec in ITEMS:
        spec = dict(spec)
        analysis = spec.pop("analysis")
        item_id, _ = db.upsert_item(NormalizedItem(**spec))
        ids[spec["external_id"]] = item_id
        if analysis:
            db.save_analysis(item_id, analysis, "demo")
    for s in SUGGESTIONS:
        db.add_suggestion(kind=s["kind"], idea_type=s.get("idea_type"), title=s["title"], body=s["body"],
                          why_you=s.get("why_you", ""), project_id=projects.get(s.get("project")),
                          item_ids=[ids[e] for e in s["items"]], first_steps=s.get("steps", []),
                          score=s["score"], origin="demo")
    return {"status": "ok", "items": len(ITEMS), "projects": len(PROJECTS), "suggestions": len(SUGGESTIONS)}
