"""Źródła zakładek: Reddit, X, pliki."""

from __future__ import annotations

from .base import ConnectorError, NormalizedItem


def sync_reddit(settings, db, http=None) -> dict:
    from .reddit import RedditClient
    items = RedditClient(settings, http).fetch_saved(is_known=lambda eid: db.has_item("reddit", eid))
    return {"source": "reddit", **db.add_items(items)}


def sync_x(settings, db, http=None) -> dict:
    from .x import XClient
    items = XClient(settings, db, http).fetch_bookmarks(is_known=lambda eid: db.has_item("x", eid))
    return {"source": "x", **db.add_items(items)}


__all__ = ["ConnectorError", "NormalizedItem", "sync_reddit", "sync_x"]
