"""CLI: python -m inspiro <polecenie>

  serve                 uruchom aplikację webową
  sync [reddit|x|all]   pobierz nowe zapisane treści
  analyze [--limit N]   przeanalizuj nowe zakładki
  ideas [--count N] [--focus TEKST]   wygeneruj nowe pomysły
  auto                  pełny cykl (do crona): sync -> analyze -> ideas
  import PLIK           zaimportuj plik (JSON/CSV/TXT)
  demo                  wczytaj przykładowe dane (bez kluczy API)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import load_settings


def _services():
    from .services import Services
    return Services(load_settings())


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _progress(done: int, total: int, msg: str) -> None:
    print(f"[{done}/{total}] {msg}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inspiro", description="Agent Twoich zakładek z Reddita i X")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="uruchom aplikację webową")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)

    p = sub.add_parser("sync", help="pobierz nowe zapisane treści")
    p.add_argument("source", nargs="?", default="all", choices=["all", "reddit", "x"])

    p = sub.add_parser("analyze", help="przeanalizuj nowe zakładki")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--retry-errors", action="store_true")

    p = sub.add_parser("ideas", help="wygeneruj nowe pomysły")
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--focus", default="")

    sub.add_parser("auto", help="sync + analyze + ideas (np. z crona)")

    p = sub.add_parser("import", help="zaimportuj plik")
    p.add_argument("path", type=Path)

    sub.add_parser("demo", help="wczytaj przykładowe dane")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.cmd == "serve":
        import uvicorn

        from .web.app import create_app
        svc = _services()
        if args.host not in ("127.0.0.1", "localhost") and not svc.settings.password:
            print("UWAGA: aplikacja jest dostępna w sieci bez hasła — ustaw INSPIRO_PASSWORD.",
                  file=sys.stderr)
        uvicorn.run(create_app(svc), host=args.host, port=args.port)
        return 0

    svc = _services()
    if args.cmd == "sync":
        _print(svc.sync(args.source))
    elif args.cmd == "analyze":
        _print(svc.analyzer.analyze_pending(limit=args.limit, retry_errors=args.retry_errors,
                                            progress=_progress))
    elif args.cmd == "ideas":
        _print(svc.ideas.generate(count=args.count, focus=args.focus))
    elif args.cmd == "auto":
        _print(svc.auto())
    elif args.cmd == "import":
        from .connectors.files import parse_file
        items = parse_file(args.path.name, args.path.read_text(encoding="utf-8"))
        _print({"parsed": len(items), **svc.db.add_items(items)})
    elif args.cmd == "demo":
        from .demo import load_demo
        _print(load_demo(svc.db))
    return 0


if __name__ == "__main__":
    sys.exit(main())
