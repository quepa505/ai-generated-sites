"""Czat z agentem: pętla tool-use nad Messages API z trwałą, dopisywaną historią."""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from . import prompts
from .llm import LLM, LLMRefusal, cached_system
from .tools import TOOL_SPECS, ToolExecutor, context_snapshot

log = logging.getLogger(__name__)

MAX_STEPS = 16


def _block_to_dict(block: Any) -> dict:
    if isinstance(block, dict):
        return block
    return block.to_dict(exclude_none=True)


def _strip_thinking(messages: list[dict]) -> list[dict]:
    """Awaryjne odzyskanie po 400 o niepasującej sygnaturze bloku thinking."""
    out = []
    for m in messages:
        content = m["content"]
        if m["role"] == "assistant" and isinstance(content, list):
            content = [b for b in content if b.get("type") not in ("thinking", "redacted_thinking")]
            if not content:
                content = [{"type": "text", "text": "(…)"}]
        out.append({**m, "content": content})
    return out


class ChatAgent:
    def __init__(self, db, llm: LLM, settings):
        self.db, self.llm, self.s = db, llm, settings
        self.executor = ToolExecutor(db)

    @property
    def tools(self) -> list[dict]:
        tools = list(TOOL_SPECS)
        if self.s.chat_web_search:
            tools.append({"type": "web_search_20260209", "name": "web_search", "max_uses": 5})
        return tools

    @property
    def system(self) -> list[dict]:
        # Zamrożony system prompt (bez dat, statystyk itp.) -> stabilny cache i ważne bloki thinking.
        return cached_system(prompts.CHAT_SYSTEM)

    def _call(self, messages: list[dict]):
        return self.llm.create(model=self.s.model, system=self.system, messages=messages,
                               tools=self.tools, effort=self.s.chat_effort)

    def send(self, conversation_id: int, text: str) -> dict:
        conv = self.db.get_conversation(conversation_id)
        if conv is None:
            raise KeyError(f"Brak rozmowy {conversation_id}")
        messages: list[dict] = conv["messages"]
        title = None
        if not messages:
            content: Any = [
                {"type": "text", "text": context_snapshot(self.db)},
                {"type": "text", "text": text},
            ]
            title = text.strip().split("\n", 1)[0][:60] or "Rozmowa"
        else:
            content = text
        messages.append({"role": "user", "content": content})

        events: list[dict] = []
        reply_parts: list[str] = []
        stripped_once = False
        try:
            for _ in range(MAX_STEPS):
                try:
                    resp = self._call(messages)
                except anthropic.BadRequestError as e:
                    if "thinking" in str(e) and not stripped_once:
                        log.warning("Bloki thinking odrzucone — usuwam je z historii i ponawiam.")
                        messages[:] = _strip_thinking(messages)
                        stripped_once = True
                        continue
                    raise
                LLM.check(resp)
                assistant = [_block_to_dict(b) for b in resp.content]
                messages.append({"role": "assistant", "content": assistant})
                reply_parts.extend(b["text"] for b in assistant if b.get("type") == "text" and b.get("text"))
                for b in assistant:
                    if b.get("type") == "server_tool_use":
                        events.append({"tool": b.get("name"), "input": b.get("input"), "server": True})

                if resp.stop_reason == "pause_turn":
                    continue
                if resp.stop_reason != "tool_use":
                    break

                results = []
                for b in assistant:
                    if b.get("type") != "tool_use":
                        continue
                    output, is_error = self.executor.run(b["name"], b.get("input") or {})
                    events.append({"tool": b["name"], "input": b.get("input"), "error": is_error,
                                   "result_preview": output[:300]})
                    results.append({"type": "tool_result", "tool_use_id": b["id"],
                                    "content": output, "is_error": is_error})
                messages.append({"role": "user", "content": results})
            else:
                reply_parts.append("_(Przerwano: agent wykonał zbyt wiele kroków.)_")
        except LLMRefusal as e:
            reply_parts.append(f"_{e}_")
        finally:
            self.db.save_conversation(conversation_id, messages, title=title)

        return {"reply": "\n\n".join(reply_parts).strip(), "events": events,
                "conversation_id": conversation_id}


def display_messages(messages: list[dict]) -> list[dict]:
    """Historia API -> lista wiadomości do wyświetlenia w UI."""
    out: list[dict] = []
    for m in messages:
        content = m["content"]
        if m["role"] == "user":
            if isinstance(content, str):
                out.append({"role": "user", "text": content})
                continue
            texts = [b["text"] for b in content if b.get("type") == "text"
                     and not b["text"].startswith(prompts.CONTEXT_MARKER)]
            if texts:
                out.append({"role": "user", "text": "\n".join(texts)})
            continue
        text = "\n\n".join(b["text"] for b in content if b.get("type") == "text" and b.get("text"))
        tools = [{"tool": b["name"], "input": b.get("input")} for b in content
                 if b.get("type") in ("tool_use", "server_tool_use")]
        if out and out[-1]["role"] == "assistant":
            prev = out[-1]
            prev["text"] = "\n\n".join(filter(None, [prev["text"], text]))
            prev["tools"].extend(tools)
        else:
            out.append({"role": "assistant", "text": text, "tools": tools})
    return out

