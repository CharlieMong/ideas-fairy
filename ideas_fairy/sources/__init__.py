"""Pluggable, read-only source collectors."""
from __future__ import annotations

from ..config import Config
from .base import Source
from .arxiv import ArxivSource
from .github import GithubSource
from .rss import RssSource
from .reddit import RedditSource
from .nvd import NvdSource
from .websearch import WebSearchSource

_REGISTRY = {
    "arxiv": ArxivSource,
    "github": GithubSource,
    "rss": RssSource,
    "reddit": RedditSource,
    "nvd": NvdSource,
    "websearch": WebSearchSource,
}


def build_sources(cfg: Config) -> list[Source]:
    """Instantiate every enabled source from config."""
    out: list[Source] = []
    srcs = cfg.get("sources", default={}) or {}
    for name, klass in _REGISTRY.items():
        scfg = srcs.get(name, {}) or {}
        if scfg.get("enabled", False):
            out.append(klass(name=name, cfg=cfg, scfg=scfg))
    return out
