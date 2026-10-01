"""Tavily-backed general web search.

Returns a normalized list of {"title", "url", "snippet"} dicts.
Any failure returns an empty list so the caller can degrade gracefully.
"""

import os

from .cache import Cache


class WebSearch:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.enabled = bool(cfg.get("web_enabled", False))
        self.provider = cfg.get("search_provider", "none")
        self.cache = Cache(
            cfg.get("cache_path", ".cache/web.sqlite"),
            cfg.get("cache_ttl_hours", 168),
        )

    def search(self, query: str, k: int | None = None) -> list[dict]:
        """Search the web. Returns [] on any error or if disabled."""
        if not self.enabled or self.provider in (None, "", "none"):
            return []

        k = int(k or self.cfg.get("max_results", 5))
        key = f"search::{self.provider}::{query}::{k}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        try:
            if self.provider == "tavily":
                results = self._tavily(query, k)
            else:
                results = []
        except Exception as e:
            print(f"[WebSearch] provider={self.provider} failed: {e}")
            results = []

        self.cache.put(key, results)
        return results

    # ── providers ────────────────────────────────────────────

    def _tavily(self, q: str, k: int) -> list[dict]:
        from tavily import TavilyClient

        api_key_env = self.cfg.get("api_key_env", "TAVILY_API_KEY")
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(f"env {api_key_env} not set")

        client = TavilyClient(api_key=api_key)
        allow = self.cfg.get("allow_domains") or None

        r = client.search(
            query=q,
            max_results=k,
            include_domains=allow,
        )
        return [
            {
                "title": x.get("title", ""),
                "url": x.get("url", ""),
                "snippet": x.get("content", "") or x.get("snippet", ""),
            }
            for x in r.get("results", [])
        ]