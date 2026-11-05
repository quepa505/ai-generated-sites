from __future__ import annotations

from types import SimpleNamespace

import pytest

from inspiro.config import Settings
from inspiro.db import Database


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=tmp_path / "test.db", analysis_concurrency=2)


@pytest.fixture
def db(settings):
    return Database(settings.db_path)


class FakeMessages:
    """Atrapa client.beta.messages: zapisuje wywołania, zwraca przygotowane odpowiedzi."""

    def __init__(self):
        self.parse_calls: list[dict] = []
        self.create_calls: list[dict] = []
        self.parse_results: list = []
        self.create_results: list = []

    def parse(self, **kw):
        self.parse_calls.append(kw)
        res = self.parse_results.pop(0) if len(self.parse_results) > 1 else self.parse_results[0]
        if isinstance(res, Exception):
            raise res
        if callable(res):
            res = res(kw)
        return SimpleNamespace(stop_reason="end_turn", stop_details=None, parsed_output=res)

    def create(self, **kw):
        # Kopia historii z chwili wywołania (lista jest potem modyfikowana w miejscu).
        self.create_calls.append({**kw, "messages": [dict(m) for m in kw["messages"]]})
        res = self.create_results.pop(0)
        if isinstance(res, Exception):
            raise res
        return res


class FakeClient:
    def __init__(self):
        self.beta = SimpleNamespace(messages=FakeMessages())


@pytest.fixture
def fake_client():
    return FakeClient()
