"""Konfiguracja aplikacji: zmienne środowiskowe + opcjonalny plik .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """Minimalny parser .env (KEY=VALUE). Nie nadpisuje już ustawionych zmiennych."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "tak", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass
class Settings:
    db_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "inspiro.db")

    # --- Claude ---
    model: str = "claude-opus-5-5"            # czat z agentem i generowanie pomysłów
    analysis_model: str = "claude-opus-5-5"   # analiza pojedynczych zakładek
    analysis_effort: str = "low"
    chat_effort: str = "medium"
    ideas_effort: str = "high"
    analysis_concurrency: int = 3
    server_fallback: bool = True              # fallbacks="default" przy odmowie klasyfikatora
    chat_web_search: bool = False             # serwerowe narzędzie web_search w czacie

    # --- Reddit (aplikacja typu "script") ---
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_username: str = ""
    reddit_password: str = ""
    reddit_user_agent: str = "inspiro/0.1 (personal bookmarks agent)"

    # --- X (OAuth 2.0 PKCE) ---
    x_client_id: str = ""
    x_client_secret: str = ""                 # tylko dla "confidential client"
    x_redirect_uri: str = "http://127.0.0.1:8000/auth/x/callback"

    # --- Serwer ---
    password: str = ""                        # jeśli ustawione: HTTP Basic auth na całej aplikacji

    @property
    def reddit_configured(self) -> bool:
        return all([self.reddit_client_id, self.reddit_client_secret,
                    self.reddit_username, self.reddit_password])

    @property
    def x_configured(self) -> bool:
        return bool(self.x_client_id)


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file or PROJECT_ROOT / ".env")
    env = os.environ.get
    db = env("INSPIRO_DB")
    return Settings(
        db_path=Path(db) if db else PROJECT_ROOT / "data" / "inspiro.db",
        model=env("INSPIRO_MODEL") or "claude-opus-5-5",
        analysis_model=env("INSPIRO_ANALYSIS_MODEL") or env("INSPIRO_MODEL") or "claude-opus-5-5",
        analysis_effort=env("INSPIRO_ANALYSIS_EFFORT") or "low",
        chat_effort=env("INSPIRO_CHAT_EFFORT") or "medium",
        ideas_effort=env("INSPIRO_IDEAS_EFFORT") or "high",
        analysis_concurrency=_int("INSPIRO_ANALYSIS_CONCURRENCY", 3),
        server_fallback=_bool("INSPIRO_SERVER_FALLBACK", True),
        chat_web_search=_bool("INSPIRO_WEB_SEARCH", False),
        reddit_client_id=env("REDDIT_CLIENT_ID", ""),
        reddit_client_secret=env("REDDIT_CLIENT_SECRET", ""),
        reddit_username=env("REDDIT_USERNAME", ""),
        reddit_password=env("REDDIT_PASSWORD", ""),
        reddit_user_agent=env("REDDIT_USER_AGENT") or "inspiro/0.1 (personal bookmarks agent)",
        x_client_id=env("X_CLIENT_ID", ""),
        x_client_secret=env("X_CLIENT_SECRET", ""),
        x_redirect_uri=env("X_REDIRECT_URI") or "http://127.0.0.1:8000/auth/x/callback",
        password=env("INSPIRO_PASSWORD", ""),
    )
