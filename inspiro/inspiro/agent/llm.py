"""Wspólny dostęp do Claude API (anthropic SDK 2.x)."""

from __future__ import annotations

from typing import Any

import anthropic

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    pass


class LLMRefusal(LLMError):
    pass


def is_config_error(e: Exception) -> bool:
    """Błędy, których ponawianie dla kolejnych zakładek nie ma sensu (klucz, uprawnienia)."""
    return isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)) or \
        "authentication method" in str(e)


def describe_error(e: Exception) -> str:
    if isinstance(e, anthropic.AuthenticationError) or "authentication method" in str(e):
        return ("Brak lub nieprawidłowy klucz Claude API — ustaw ANTHROPIC_API_KEY w pliku .env "
                "i zrestartuj aplikację.")
    if isinstance(e, anthropic.PermissionDeniedError):
        return f"Klucz Claude API nie ma dostępu do tego modelu lub funkcji: {e.message}"
    if isinstance(e, anthropic.RateLimitError):
        return "Przekroczony limit zapytań Claude API — spróbuj za chwilę."
    if isinstance(e, anthropic.APIConnectionError):
        return "Brak połączenia z Claude API — sprawdź internet."
    if isinstance(e, anthropic.APIStatusError):
        return f"Claude API: HTTP {e.status_code}: {e.message}"
    if isinstance(e, LLMError):
        return str(e)
    return f"{type(e).__name__}: {e}"


def cached_system(*blocks: str) -> list[dict]:
    """System prompt jako bloki; cache_control na ostatnim -> stały prefiks trafia do cache."""
    out = [{"type": "text", "text": b} for b in blocks if b]
    if out:
        out[-1]["cache_control"] = {"type": "ephemeral"}
    return out


class LLM:
    def __init__(self, settings, client: Any = None):
        self.s = settings
        self._client = client

    @property
    def client(self):
        if self._client is None:
            # Klucz: ANTHROPIC_API_KEY (lub profil `ant auth login`).
            self._client = anthropic.Anthropic(max_retries=4)
        return self._client

    def _extra(self, model: str) -> dict:
        # Przy odmowie klasyfikatora bezpieczeństwa API samo ponowi zapytanie na
        # rekomendowanym modelu zastępczym. Haiku nie ma fallbacku po stronie serwera.
        if self.s.server_fallback and not model.startswith("claude-haiku"):
            return {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        return {}

    @staticmethod
    def check(resp) -> None:
        if resp.stop_reason == "refusal":
            cat = getattr(resp.stop_details, "category", None) if resp.stop_details else None
            raise LLMRefusal(f"Model odmówił odpowiedzi (kategoria: {cat or 'brak'}).")
        if resp.stop_reason == "max_tokens":
            raise LLMError("Odpowiedź modelu została ucięta (max_tokens).")

    def parse(self, *, model: str, system: list[dict], user: str | list, output_format: type,
              effort: str, max_tokens: int = 16000):
        """Structured output -> zwalidowana instancja Pydantic."""
        resp = self.client.beta.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=output_format,
            output_config={"effort": effort},
            **self._extra(model),
        )
        self.check(resp)
        if resp.parsed_output is None:
            raise LLMError("Model nie zwrócił poprawnego JSON-a.")
        return resp.parsed_output, resp

    def create(self, *, model: str, system: list[dict], messages: list, tools: list,
               effort: str, max_tokens: int = 16000):
        return self.client.beta.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools,
            output_config={"effort": effort},
            **self._extra(model),
        )
