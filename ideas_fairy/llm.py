"""Thin OpenAI-compatible client for a local llama-server (/v1/chat/completions)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

log = logging.getLogger("ideas_fairy.llm")


class LLMError(RuntimeError):
    pass


class LlamaClient:
    def __init__(self, base_url: str, model: str, api_key: str = "",
                 temperature: float = 0.4, timeout_s: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.timeout_s = timeout_s

    def health(self) -> bool:
        """Best-effort reachability check against the models endpoint."""
        try:
            r = httpx.get(f"{self.base_url}/models", timeout=10.0,
                          headers=self._headers())
            return r.status_code < 500
        except httpx.HTTPError:
            return False

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def chat(self, system: str, user: str, *, json_mode: bool = False,
             max_tokens: int = 2048) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", json=payload,
                           headers=self._headers(), timeout=self.timeout_s)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"llama-server request failed: {exc}") from exc
        data = r.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"unexpected llama-server response shape: {exc}") from exc


def extract_json(text: str) -> Any:
    """Pull the first JSON object/array out of a model response, tolerating fences."""
    text = text.strip()
    # Strip code fences if present.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Fast path.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find the outermost {...} or [...].
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError("no parseable JSON found in model output")
