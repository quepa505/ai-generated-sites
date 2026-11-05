"""Import zakładek z plików (JSON / CSV / TXT) — rozpoznaje format automatycznie."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re

from . import reddit, x
from .base import ConnectorError, NormalizedItem

URL_RE = re.compile(r"https?://\S+")


def manual_item(url: str = "", title: str = "", text: str = "", note: str = "") -> NormalizedItem:
    url = (url or "").strip()
    text = "\n\n".join(p for p in [(text or "").strip(), (f"Notatka: {note.strip()}" if note else "")] if p)
    if not url and not text:
        raise ConnectorError("Podaj adres URL albo treść.")
    source = "manual"
    ext = url or hashlib.sha1(text.encode()).hexdigest()[:16]
    if "reddit.com/" in url:
        source = "reddit"
        m = re.search(r"/comments/([a-z0-9]+)", url, re.I)
        ext = f"t3_{m.group(1)}" if m else url
    elif re.search(r"(x|twitter)\.com/[^/]+/status/(\d+)", url):
        source = "x"
        ext = re.search(r"/status/(\d+)", url).group(1)
    return NormalizedItem(source=source, external_id=ext, url=url,
                          title=(title or text.split("\n", 1)[0] or url)[:200], text=text)


def parse_file(filename: str, content: str) -> list[NormalizedItem]:
    name = (filename or "").lower()
    stripped = content.lstrip()
    if name.endswith(".json") or stripped.startswith(("{", "[")):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as e:
            raise ConnectorError(f"Niepoprawny JSON: {e}") from e
        return parse_json(payload, filename)
    if name.endswith(".csv"):
        return parse_csv(content, filename)
    # TXT / MD: jeden link na linię
    items = []
    for line in content.splitlines():
        m = URL_RE.search(line)
        if m:
            rest = line.replace(m.group(0), "").strip(" -–:\t")
            items.append(manual_item(url=m.group(0).rstrip(").,"), title=rest))
    return items


def parse_json(payload, filename: str = "") -> list[NormalizedItem]:
    if reddit.looks_like_reddit_json(payload):
        return reddit.parse_listing(payload)
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return x.parse_bookmarks_response(payload)  # surowa odpowiedź X API v2
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        src = payload.get("source") or ("x" if "x" in filename.lower() else "manual")
        return _simple(payload["items"], src)
    if isinstance(payload, list):
        src = "x" if any(isinstance(r, dict) and "/status/" in str(r.get("url", "")) for r in payload) \
            else "manual"
        return _simple(payload, src)
    raise ConnectorError("Nie rozpoznano formatu JSON (oczekiwano Listingu Reddit, odpowiedzi X API "
                         "albo listy obiektów {url, text}).")


def _simple(rows: list, source: str) -> list[NormalizedItem]:
    if source == "x":
        return x.parse_simple_list(rows, "x")
    out = []
    for r in rows:
        if isinstance(r, str):
            out.append(manual_item(url=r))
        elif isinstance(r, dict):
            out.append(manual_item(url=r.get("url", ""), title=r.get("title", ""),
                                   text=r.get("text", ""), note=r.get("note", "")))
    return out


def parse_csv(content: str, filename: str = "") -> list[NormalizedItem]:
    reader = csv.DictReader(io.StringIO(content))
    cols = {c.lower() for c in (reader.fieldnames or [])}
    if {"id", "permalink"} <= cols:
        hint = "t1" if "comment" in filename.lower() else "t3"
        return reddit.parse_export_csv(content, kind_hint=hint)
    if "url" in cols:
        return [manual_item(url=r.get("url", ""), title=r.get("title", ""), text=r.get("text", ""))
                for r in reader if r.get("url")]
    raise ConnectorError("CSV musi mieć kolumny `id,permalink` (eksport Reddit) albo `url[,title,text]`.")
