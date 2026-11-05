"""X (Twitter): zakładki (bookmarks).

Oficjalne API v2: GET /2/users/{id}/bookmarks, OAuth 2.0 Authorization Code + PKCE,
zakresy: tweet.read users.read bookmark.read offline.access.
API X działa w modelu pay-per-use; odczyt własnych zakładek to "owned read" (tani),
ale wymaga konta deweloperskiego z doładowanymi kredytami. Endpoint zwraca
maksymalnie ~800 najnowszych zakładek.

Alternatywa bez API: skrypt w przeglądarce na x.com/i/bookmarks (patrz SCRAPER_SNIPPET)
-> plik JSON -> import.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any, Callable
from urllib.parse import urlencode

import httpx2 as httpx

from .base import ConnectorError, NormalizedItem, ts_to_iso

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
API_BASE = "https://api.x.com/2"
SCOPES = "tweet.read users.read bookmark.read offline.access"
TOKEN_KEY = "x_tokens"


# --------------------------------------------------------------------------- OAuth 2.0 PKCE
def make_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    return AUTHORIZE_URL + "?" + urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "scope": SCOPES, "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def _token_request(settings, http: httpx.Client, data: dict) -> dict:
    data = {**data, "client_id": settings.x_client_id}
    auth = (settings.x_client_id, settings.x_client_secret) if settings.x_client_secret else None
    resp = http.post(TOKEN_URL, data=data, auth=auth,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    if resp.status_code != 200:
        raise ConnectorError(f"X OAuth: HTTP {resp.status_code}: {resp.text[:300]}")
    tok = resp.json()
    tok["expires_at"] = int(time.time()) + int(tok.get("expires_in", 7200)) - 60
    return tok


def exchange_code(settings, http: httpx.Client, code: str, verifier: str) -> dict:
    return _token_request(settings, http, {
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": settings.x_redirect_uri, "code_verifier": verifier,
    })


def refresh_tokens(settings, http: httpx.Client, refresh_token: str) -> dict:
    return _token_request(settings, http, {"grant_type": "refresh_token",
                                           "refresh_token": refresh_token})


# --------------------------------------------------------------------------- parsowanie
def _expand_urls(text: str, entities: dict | None) -> str:
    for u in (entities or {}).get("urls", []) or []:
        short, full = u.get("url"), u.get("expanded_url") or u.get("unwound_url")
        if short and full:
            text = text.replace(short, full)
    return text


def parse_bookmarks_response(payload: dict) -> list[NormalizedItem]:
    """Odpowiedź API v2 ({data, includes}) -> zakładki."""
    users = {u["id"]: u for u in (payload.get("includes") or {}).get("users", []) or []}
    media = {m["media_key"]: m for m in (payload.get("includes") or {}).get("media", []) or []}
    out = []
    for t in payload.get("data") or []:
        if not t.get("id"):
            continue
        note = (t.get("note_tweet") or {})
        text = note.get("text") or t.get("text") or ""
        text = _expand_urls(text, note.get("entities") or t.get("entities"))
        user = users.get(t.get("author_id") or "", {})
        handle = user.get("username") or t.get("author_username") or ""
        url = f"https://x.com/{handle or 'i'}/status/{t['id']}"
        media_urls = []
        for key in (t.get("attachments") or {}).get("media_keys", []) or []:
            m = media.get(key) or {}
            if m.get("url") or m.get("preview_image_url"):
                media_urls.append(m.get("url") or m.get("preview_image_url"))
        for u in (t.get("entities") or {}).get("urls", []) or []:
            full = u.get("expanded_url")
            if full and "x.com/" not in full and "twitter.com/" not in full:
                media_urls.append(full)
        title = text.split("\n", 1)[0][:140]
        out.append(NormalizedItem(
            source="x", external_id=str(t["id"]), url=url, title=title, text=text,
            author=f"@{handle}" if handle else (user.get("name") or ""),
            context=user.get("name") or "", created_at=ts_to_iso(t.get("created_at")),
            media_urls=media_urls, raw=t,
        ))
    return out


def parse_simple_list(rows: list[dict], source: str = "x") -> list[NormalizedItem]:
    """Prosty format: [{"id"?, "url", "text", "author"?, "created_at"?}, ...]
    (np. ze skryptu SCRAPER_SNIPPET albo z innych narzędzi eksportujących)."""
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        url = r.get("url") or r.get("link") or ""
        text = r.get("text") or r.get("full_text") or r.get("content") or ""
        ext = str(r.get("id") or r.get("id_str") or "")
        if not ext and url:
            ext = url.rstrip("/").rsplit("/", 1)[-1] if "/status/" in url else url
        if not ext:
            continue
        author = r.get("author") or r.get("username") or r.get("screen_name") or ""
        if author and not author.startswith("@") and source == "x":
            author = "@" + author
        out.append(NormalizedItem(
            source=source, external_id=ext, url=url, title=r.get("title") or text.split("\n", 1)[0][:140],
            text=text, author=author, context=r.get("context") or "",
            created_at=ts_to_iso(r.get("created_at")), media_urls=list(r.get("media") or []), raw=r,
        ))
    return out


# --------------------------------------------------------------------------- API
class XClient:
    def __init__(self, settings, db, http: httpx.Client | None = None):
        self.s = settings
        self.db = db
        self.http = http or httpx.Client(timeout=30)

    @property
    def connected(self) -> bool:
        return bool(self.db.kv_get(TOKEN_KEY))

    def _access_token(self) -> str:
        tok = self.db.kv_get(TOKEN_KEY)
        if not tok:
            raise ConnectorError("X nie jest połączony — kliknij „Połącz z X” w zakładce Źródła.")
        if tok.get("expires_at", 0) <= time.time():
            if not tok.get("refresh_token"):
                raise ConnectorError("Token X wygasł — połącz konto ponownie.")
            new = refresh_tokens(self.s, self.http, tok["refresh_token"])
            new.setdefault("refresh_token", tok["refresh_token"])
            self.db.kv_set(TOKEN_KEY, new)
            tok = new
        return tok["access_token"]

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self.http.get(API_BASE + path, params=params,
                             headers={"Authorization": f"Bearer {self._access_token()}"})
        if resp.status_code == 429:
            raise ConnectorError("X: limit zapytań — spróbuj za kilkanaście minut.")
        if resp.status_code in (401, 403):
            raise ConnectorError(f"X: brak dostępu (HTTP {resp.status_code}). Sprawdź zakresy aplikacji "
                                 f"i saldo kredytów w konsoli deweloperskiej. {resp.text[:200]}")
        if resp.status_code != 200:
            raise ConnectorError(f"X API: HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def fetch_bookmarks(self, is_known: Callable[[str], bool] | None = None,
                        max_pages: int = 10) -> list[NormalizedItem]:
        me = self._get("/users/me")["data"]
        out: list[NormalizedItem] = []
        token = None
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "max_results": 100,
                "tweet.fields": "created_at,author_id,entities,note_tweet,public_metrics,attachments",
                "expansions": "author_id,attachments.media_keys",
                "user.fields": "username,name",
                "media.fields": "url,preview_image_url,type",
            }
            if token:
                params["pagination_token"] = token
            payload = self._get(f"/users/{me['id']}/bookmarks", params)
            items = parse_bookmarks_response(payload)
            fresh = [i for i in items if not (is_known and is_known(i.external_id))]
            out.extend(fresh)
            token = (payload.get("meta") or {}).get("next_token")
            if not token or (items and not fresh):
                break
        return out


# Uruchom w konsoli przeglądarki (F12) na https://x.com/i/bookmarks — przewija stronę,
# zbiera widoczne posty i pobiera plik x-bookmarks.json do zaimportowania.
# Eksperymentalne: zależy od struktury HTML X, która może się zmienić.
SCRAPER_SNIPPET = r"""(async () => {
  const seen = new Map(); let idle = 0;
  while (idle < 6 && seen.size < 2000) {
    const before = seen.size;
    document.querySelectorAll('article[data-testid="tweet"]').forEach(a => {
      const link = [...a.querySelectorAll('a[href*="/status/"]')].find(l => l.querySelector('time'));
      if (!link) return;
      const url = link.href.split('?')[0]; const id = url.split('/status/')[1]?.split('/')[0];
      if (!id || seen.has(id)) return;
      const text = a.querySelector('[data-testid="tweetText"]')?.innerText || '';
      const author = url.split('/')[3] || '';
      const created_at = link.querySelector('time')?.getAttribute('datetime');
      const media = [...a.querySelectorAll('a[href^="http"]')].map(x => x.href)
        .filter(h => !h.includes('x.com') && !h.includes('twitter.com'));
      seen.set(id, { id, url, text, author, created_at, media });
    });
    idle = seen.size === before ? idle + 1 : 0;
    window.scrollBy(0, window.innerHeight * 2);
    await new Promise(r => setTimeout(r, 1200));
  }
  const blob = new Blob([JSON.stringify({ source: 'x', items: [...seen.values()] }, null, 1)],
                        { type: 'application/json' });
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
  a.download = 'x-bookmarks.json'; a.click();
  console.log('Zebrano zakładek:', seen.size);
})();"""
