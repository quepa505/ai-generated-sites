"""Reddit: zapisane posty i komentarze.

Dwie drogi:
1. Oficjalne API (aplikacja typu "script", OAuth password grant) — wymaga zatwierdzonego
   dostępu do Reddit Data API (od końca 2025 nowe aplikacje przechodzą akceptację).
2. Import pliku: JSON z bookmarkletu (`/user/<ty>/saved.json` pobrane w zalogowanej
   przeglądarce) albo CSV z eksportu danych Reddit (saved_posts.csv / saved_comments.csv).
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any, Callable, Iterable

import httpx2 as httpx

from .base import ConnectorError, NormalizedItem, ts_to_iso

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"
WEB_BASE = "https://www.reddit.com"


# --------------------------------------------------------------------------- parsowanie
def _thing_to_item(thing: dict) -> NormalizedItem | None:
    kind = thing.get("kind")
    d = thing.get("data") or {}
    name = d.get("name") or (f"{kind}_{d['id']}" if d.get("id") and kind else None)
    if not name:
        return None
    permalink = d.get("permalink") or ""
    link = WEB_BASE + permalink if permalink.startswith("/") else permalink
    sub = d.get("subreddit_name_prefixed") or (f"r/{d['subreddit']}" if d.get("subreddit") else "")
    media = []
    if kind == "t3":
        url = d.get("url_overridden_by_dest") or d.get("url") or ""
        if url and url != link and not url.startswith(WEB_BASE + "/r/"):
            media.append(url)
        text = d.get("selftext") or ""
        if url and not d.get("is_self") and url not in text:
            text = (text + "\n\nLink: " + url).strip()
        return NormalizedItem(
            source="reddit", external_id=name, url=link, title=d.get("title") or "",
            text=text, author=d.get("author") or "", context=sub,
            created_at=ts_to_iso(d.get("created_utc")), media_urls=media, raw=thing,
        )
    if kind == "t1":
        link_title = d.get("link_title") or ""
        return NormalizedItem(
            source="reddit", external_id=name, url=link,
            title=f"Komentarz w: {link_title}" if link_title else "Zapisany komentarz",
            text=d.get("body") or "", author=d.get("author") or "",
            context=f"{sub} · komentarz".strip(" ·"), created_at=ts_to_iso(d.get("created_utc")),
            raw=thing,
        )
    return None


def _children(payload: Any) -> Iterable[dict]:
    if isinstance(payload, list):
        for page in payload:
            yield from _children(page)
    elif isinstance(payload, dict):
        if payload.get("kind") in ("t1", "t3"):
            yield payload
        elif isinstance(payload.get("data"), dict) and "children" in payload["data"]:
            yield from payload["data"]["children"]
        elif isinstance(payload.get("items"), list):
            yield from _children(payload["items"])


def parse_listing(payload: Any) -> list[NormalizedItem]:
    """Listing Reddit (lub lista Listingów / pojedynczych rzeczy) -> zakładki."""
    out = []
    for thing in _children(payload):
        item = _thing_to_item(thing)
        if item:
            out.append(item)
    return out


def looks_like_reddit_json(payload: Any) -> bool:
    return any(True for _ in _children(payload))


_SLUG = re.compile(r"/r/([^/]+)/comments/([a-z0-9]+)/([^/?#]*)", re.I)


def parse_export_csv(text: str, kind_hint: str = "t3") -> list[NormalizedItem]:
    """CSV z oficjalnego eksportu danych Reddit: kolumny `id`, `permalink`.

    Eksport nie zawiera treści — tytuł odtwarzamy ze sluga w adresie,
    a agent oznaczy takie wpisy jako "do uzupełnienia".
    """
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        rid = (row.get("id") or "").strip()
        permalink = (row.get("permalink") or "").strip()
        if not rid or not permalink:
            continue
        if permalink.startswith("/"):
            permalink = WEB_BASE + permalink
        m = _SLUG.search(permalink)
        title = m.group(3).replace("_", " ").strip().capitalize() if m else ""
        sub = f"r/{m.group(1)}" if m else ""
        # .../comments/<post>/<slug>/<comment_id>/ -> komentarz
        is_comment = kind_hint == "t1" or bool(m and permalink[m.end():].strip("/"))
        prefix = "t1" if is_comment else "t3"
        if is_comment and title:
            title = f"Komentarz w: {title}"
        out.append(NormalizedItem(
            source="reddit", external_id=f"{prefix}_{rid}", url=permalink,
            title=title or permalink, text="", context=sub, raw=row,
        ))
    return out


# --------------------------------------------------------------------------- API
class RedditClient:
    def __init__(self, settings, http: httpx.Client | None = None):
        self.s = settings
        self.http = http or httpx.Client(timeout=30)
        self._token: str | None = None

    def _headers(self) -> dict:
        return {"User-Agent": self.s.reddit_user_agent}

    def authenticate(self) -> str:
        if not self.s.reddit_configured:
            raise ConnectorError(
                "Brak konfiguracji Reddit API (REDDIT_CLIENT_ID/SECRET/USERNAME/PASSWORD). "
                "Możesz zamiast tego zaimportować plik z bookmarkletu.")
        resp = self.http.post(
            TOKEN_URL, headers=self._headers(),
            auth=(self.s.reddit_client_id, self.s.reddit_client_secret),
            data={"grant_type": "password", "username": self.s.reddit_username,
                  "password": self.s.reddit_password, "scope": "history read identity"},
        )
        if resp.status_code != 200:
            raise ConnectorError(f"Reddit OAuth: HTTP {resp.status_code}: {resp.text[:200]}")
        token = resp.json().get("access_token")
        if not token:
            raise ConnectorError(f"Reddit OAuth: brak tokenu ({resp.text[:200]})")
        self._token = token
        return token

    def fetch_saved(self, is_known: Callable[[str], bool] | None = None,
                    max_pages: int = 20) -> list[NormalizedItem]:
        """Pobiera zapisane rzeczy (najnowsze najpierw). Zatrzymuje się, gdy strona
        zawiera wyłącznie znane już elementy — dzięki temu kolejne synchronizacje są tanie."""
        token = self._token or self.authenticate()
        headers = {**self._headers(), "Authorization": f"bearer {token}"}
        out: list[NormalizedItem] = []
        after = None
        for _ in range(max_pages):
            params = {"limit": 100, "raw_json": 1}
            if after:
                params["after"] = after
            resp = self.http.get(f"{API_BASE}/user/{self.s.reddit_username}/saved",
                                 headers=headers, params=params)
            if resp.status_code == 429:
                raise ConnectorError("Reddit: przekroczony limit zapytań, spróbuj później.")
            if resp.status_code != 200:
                raise ConnectorError(f"Reddit API: HTTP {resp.status_code}: {resp.text[:200]}")
            payload = resp.json()
            items = parse_listing(payload)
            fresh = [i for i in items if not (is_known and is_known(i.external_id))]
            out.extend(fresh)
            after = (payload.get("data") or {}).get("after")
            if not after or (items and not fresh):
                break
        return out


BOOKMARKLET = (
    "javascript:(async()=>{try{const me=await(await fetch('/api/me.json',{credentials:'include'}))"
    ".json();const u=me&&me.data&&me.data.name;if(!u){alert('Zaloguj się na Reddit');return}"
    "let after='',all=[],n=0;do{const r=await fetch(`/user/${u}/saved.json?limit=100&raw_json=1"
    "${after?'&after='+after:''}`,{credentials:'include'});if(!r.ok){alert('Reddit: HTTP '+r.status);"
    "break}const j=await r.json();all.push(...j.data.children);after=j.data.after;n++}"
    "while(after&&n<50);const b=new Blob([JSON.stringify({kind:'Listing',data:{children:all}})],"
    "{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(b);"
    "a.download='reddit-saved.json';a.click();alert('Pobrano zapisanych: '+all.length)}"
    "catch(e){alert('Błąd: '+e)}})();"
)
