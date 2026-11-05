# 💡 Inspiro — agent Twoich zakładek z Reddita i X

Inspiro pobiera zapisane posty i zakładki z **Reddita** i **X**, a agent oparty na Claude:

1. **analizuje** każdą zakładkę: streszczenie, tematy, technologie, wnioski, ocena przydatności;
2. **dopasowuje** ją do Twoich projektów i pisze, *jak konkretnie* jej użyć;
3. **generuje nowe pomysły**: aplikacje, gry, serie na YouTube, narzędzia. Łączy przy tym kilka Twoich zakładek i proponuje pierwsze kroki;
4. **pamięta** Twoje umiejętności, cele i preferencje, które poznaje w rozmowach;
5. **rozmawia** z Tobą o całej kolekcji. Czat ma 12 narzędzi: wyszukiwanie, projekty, pamięć i tablicę pomysłów.

Wszystko działa lokalnie: SQLite + FastAPI + prosty interfejs po polsku, bez kroku budowania.

## Szybki start

```bash
cd inspiro
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # wpisz ANTHROPIC_API_KEY

python -m inspiro demo            # opcjonalnie: przykładowe dane (fikcyjne)
python -m inspiro serve           # http://127.0.0.1:8000
```

Potem:
1. W **Projektach** opisz, nad czym pracujesz. Im lepszy opis, tym trafniejsze sugestie.
2. W **Źródłach** podłącz Reddit lub X albo zaimportuj plik.
3. Kliknij **Analizuj nowe**, a potem **Wygeneruj pomysły**.
4. W zakładce **Agent** opowiedz o sobie. Agent zapisze ważne rzeczy w pamięci.

## Źródła danych: co działa i ile kosztuje

| Źródło | Droga | Uwagi |
|---|---|---|
| Reddit | API, aplikacja typu *script* (`REDDIT_*` w `.env`) | Od listopada 2025 nowe aplikacje muszą być zatwierdzone przez Reddit („Responsible Builder Policy”). Dotyczy to też prywatnych skryptów. |
| Reddit | **Bookmarklet** (zakładka Źródła) | Działa w zalogowanej przeglądarce i pobiera `reddit-saved.json`, który potem importujesz. Nie wymaga klucza API. |
| Reddit | Eksport danych (reddit.com/settings/data-request) | `saved_posts.csv` zawiera tylko ID i linki. Agent zobaczy tytuł odczytany z adresu, ale nie treść. |
| X | API v2, OAuth 2.0 PKCE (`X_CLIENT_ID`) | Rozliczenie *pay-per-use*. Odczyt **własnych** zakładek to „owned read”, wg cennika X ok. $0.001 za wpis. Potrzebujesz konta deweloperskiego z kredytami. Endpoint zwraca ~800 najnowszych zakładek. |
| X | Skrypt do konsoli na x.com/i/bookmarks | Darmowy, ale eksperymentalny: zależy od HTML-a X. |
| Ręcznie | Formularz, plik CSV `url,title,text`, TXT z linkami | |

Synchronizacja jest przyrostowa. Pobieranie kończy się na pierwszej stronie, na której wszystkie elementy są już znane, a duplikaty są pomijane.

Callback dla X: `http://127.0.0.1:8000/auth/x/callback`. Musi się zgadzać z ustawieniem aplikacji w konsoli X.

## Jak działa agent

```
Reddit / X / pliki ──► konektory ──► SQLite (+ FTS5) ◄──► narzędzia agenta ◄──► czat
                                         │
                       analiza (1 zapytanie / zakładkę: streszczenie + dopasowanie do projektów)
                                         │
                       generator pomysłów (cała kolekcja + projekty + pamięć) ──► tablica pomysłów
```

- **Model:** domyślnie `claude-opus-5-5` (anthropic SDK 2.x, Messages API). Do analizy setek zakładek możesz ustawić tańszy `INSPIRO_ANALYSIS_MODEL=claude-haiku-5-5` lub `claude-sonnet-5-5`. Siłę rozumowania ustawiasz przez `INSPIRO_*_EFFORT`. Domyślnie analiza ma `low`, czat `medium`, pomysły `high`.
- **Structured outputs:** analiza i pomysły wracają jako zwalidowane modele Pydantic (`messages.parse`).
- **Prompt caching:** system prompt razem z projektami i pamięcią to stały prefiks z `cache_control`. Przy analizie całej partii kolejne zapytania czytają go z cache.
- **Czat:** pętla tool-use z historią tylko dopisywaną, bez przepisywania wcześniejszych tur. System prompt jest zamrożony, a kontekst startowy (statystyki, projekty, pamięć) trafia do pierwszej wiadomości rozmowy. Dzięki temu cache i bloki rozumowania modelu pozostają ważne.
- **Odmowy:** włączony `fallbacks: "default"`. Jeśli klasyfikator bezpieczeństwa odrzuci zapytanie, API samo ponowi je na modelu zastępczym. Wyłączysz to przez `INSPIRO_SERVER_FALLBACK=0`.
- **Wyszukiwanie w internecie** w czacie: `INSPIRO_WEB_SEARCH=1`. Przydaje się, gdy chcesz, żeby agent sprawdził konkurencję albo czy biblioteka jest wciąż rozwijana.
- **Bezpieczeństwo:** treść zakładek pochodzi od osób trzecich. Prompty każą traktować ją jako dane, nie polecenia. Agent ma narzędzia piszące tylko do lokalnej bazy: pamięć, projekty i pomysły.

## Automatyzacja (cron)

```bash
# codziennie o 7:15: synchronizacja -> analiza -> nowe pomysły (gdy przybyło ≥5 zakładek)
15 7 * * * cd /ścieżka/inspiro && .venv/bin/python -m inspiro auto >> data/auto.log 2>&1
```

Inne polecenia CLI: `sync [reddit|x]`, `analyze [--limit N] [--retry-errors]`, `ideas [--count N] [--focus "gra mobilna"]`, `import PLIK`.

## Uruchomienie poza localhostem

Ustaw `INSPIRO_PASSWORD`, żeby włączyć HTTP Basic auth na całej aplikacji. Następnie uruchom `python -m inspiro serve --host 0.0.0.0` za reverse proxy z HTTPS. Pamiętaj też o zmianie `X_REDIRECT_URI`.

## Struktura

```
inspiro/
  config.py          ustawienia (.env)
  db.py              SQLite: items, analyses, projects, suggestions, memory, conversations + FTS5
  connectors/        reddit.py, x.py (OAuth PKCE), files.py (import JSON/CSV/TXT)
  agent/             llm.py, analyzer.py, ideas.py, chat.py, tools.py, prompts.py
  services.py        spina wszystko + zadania w tle
  web/app.py         FastAPI (JSON API + /auth/x/*), web/static/ — interfejs
  __main__.py        CLI
tests/               pytest z atrapą Claude API i HTTP (bez sieci)
```

Testy: `pip install -r requirements-dev.txt && pytest`.

## Pomysły na rozwój

- **Batch API** przy pierwszym imporcie setek zakładek (50% taniej, asynchronicznie).
- **web_fetch** dla zakładek zawierających sam link. Agent mógłby wtedy przeczytać artykuł lub repozytorium.
- Wyszukiwanie semantyczne (embeddingi, np. sqlite-vec) obok FTS5.
- Cotygodniowy digest mailem lub na Telegramie: „co zapisałeś i co z tym zrobić”.
- Kolejne źródła: gwiazdki na GitHubie, „Obejrzyj później” z YouTube, Raindrop/Pocket.
- Wersja hostowana na **Claude Managed Agents** z harmonogramem zamiast crona.
