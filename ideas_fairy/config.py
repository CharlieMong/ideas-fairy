"""Configuration loading. Non-secret knobs from YAML; secrets from env only."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no external dep). Does not overwrite existing env."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path

    # --- convenience accessors -------------------------------------------------
    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.raw
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def path(self, *keys: str, default: str = "") -> Path:
        val = self.get(*keys, default=default)
        p = Path(val)
        return p if p.is_absolute() else (self.root / p)

    # --- secrets ---------------------------------------------------------------
    def env(self, name: str, default: str = "") -> str:
        return os.environ.get(name, default)

    def discord_webhook(self) -> str:
        """Resolve the Discord webhook from env, then optional secret file.

        Returns an empty string if unset. The value is never logged by callers.
        """
        env_name = self.get("discord", "webhook_env", default="IDEAS_FAIRY_DISCORD_WEBHOOK")
        val = os.environ.get(env_name, "").strip()
        if val:
            return val
        secret_file = self.get("discord", "secret_file", default="")
        if secret_file:
            sf = Path(secret_file)
            if not sf.is_absolute():
                sf = self.root / sf
            if sf.exists():
                return sf.read_text(encoding="utf-8").strip()
        return ""

    def llm_settings(self) -> dict[str, Any]:
        base = self.env(self.get("llm", "base_url_env", default="IDEAS_FAIRY_LLM_BASE_URL"),
                        "http://localhost:8080/v1")
        model = self.env(self.get("llm", "model_env", default="IDEAS_FAIRY_LLM_MODEL"),
                         "local-model")
        api_key = self.env(self.get("llm", "api_key_env", default="IDEAS_FAIRY_LLM_API_KEY"), "")
        return {
            "base_url": base.rstrip("/"),
            "model": model,
            "api_key": api_key,
            "temperature": float(self.get("llm", "temperature", default=0.4)),
            "timeout_s": float(self.get("llm", "request_timeout_s", default=180)),
            "max_context_items": int(self.get("llm", "max_context_items", default=120)),
        }


def load_config(root: str | Path | None = None) -> Config:
    root = Path(root) if root else Path.cwd()
    _load_dotenv(root / ".env")

    cfg_path = root / "config.yaml"
    data: dict[str, Any] = {}
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    # Local override merges shallowly over the base config.
    local = root / "config.local.yaml"
    if local.exists():
        overlay = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        data = _deep_merge(data, overlay)

    return Config(raw=data, root=root)


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
