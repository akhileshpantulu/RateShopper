from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from hotelrate.config import Settings


class FirecrawlClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger(self.__class__.__name__)

    async def scrape_url(self, url: str) -> dict[str, Any]:
        """Scrape a single URL; returns dict with 'markdown' and/or 'html' on success."""
        if not self.settings.firecrawl_enabled:
            return {}
        base = self.settings.firecrawl_base_url.rstrip("/")
        endpoint = f"{base}/v1/scrape"
        timeout = self.settings.firecrawl_timeout_seconds
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if getattr(self.settings, "firecrawl_api_key", None):
            key = (self.settings.firecrawl_api_key or "").strip()
            if key:
                headers["Authorization"] = f"Bearer {key}"
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.post(
                    endpoint,
                    json={"url": url, "formats": ["markdown", "html"]},
                    headers=headers,
                )
        except Exception as exc:
            self.logger.debug("firecrawl scrape failed url=%s err=%s", url, exc)
            return {}
        if resp.status_code != 200:
            self.logger.debug(
                "firecrawl scrape non-200 url=%s status=%s body=%s",
                url,
                resp.status_code,
                (resp.text or "")[:300],
            )
            return {}
        try:
            payload: dict[str, Any] = resp.json()
        except Exception:
            return {}
        if not payload.get("success"):
            return {}
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, str)}

    async def map_url(self, url: str) -> list[str]:
        if not self.settings.firecrawl_enabled:
            return []

        endpoint = f"{self.settings.firecrawl_base_url.rstrip('/')}/v1/map"
        timeout = self.settings.firecrawl_timeout_seconds

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.post(endpoint, json={"url": url})
        except Exception as exc:
            self.logger.debug("firecrawl map request failed url=%s err=%s", url, exc)
            return []

        if resp.status_code != 200:
            self.logger.debug(
                "firecrawl map non-200 url=%s status=%d body=%s",
                url,
                resp.status_code,
                resp.text[:300],
            )
            return []

        try:
            payload: dict[str, Any] = resp.json()
        except Exception:
            return []

        if not payload.get("success"):
            return []

        links = payload.get("links", [])
        if not isinstance(links, list):
            return []

        out: list[str] = []
        for item in links:
            if isinstance(item, str) and item.startswith("http"):
                out.append(item)
        return out

    async def crawl_url(
        self,
        url: str,
        *,
        max_depth: int = 5,
        limit: int = 12,
        poll_rounds: int = 5,
        poll_interval_seconds: float = 1.4,
    ) -> list[str]:
        if not self.settings.firecrawl_enabled:
            return []

        base = self.settings.firecrawl_base_url.rstrip("/")
        endpoint = f"{base}/v1/crawl"
        timeout = self.settings.firecrawl_timeout_seconds

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                start = await client.post(
                    endpoint,
                    json={"url": url, "maxDepth": max_depth, "limit": limit},
                )
                if start.status_code != 200:
                    return []
                payload = start.json()
                if not payload.get("success"):
                    return []
                crawl_id = str(payload.get("id", "")).strip()
                if not crawl_id:
                    return []

                status_url = f"{endpoint}/{crawl_id}"
                links: list[str] = []
                for _ in range(poll_rounds):
                    resp = await client.get(status_url)
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                    if not data.get("success"):
                        break
                    batch = data.get("data", [])
                    if isinstance(batch, list):
                        for item in batch:
                            if not isinstance(item, dict):
                                continue
                            page_url = item.get("url")
                            if isinstance(page_url, str) and page_url.startswith("http"):
                                links.append(page_url)
                            markdown = item.get("markdown")
                            if isinstance(markdown, str):
                                for candidate in self._extract_links_from_markdown(markdown):
                                    links.append(candidate)

                    if str(data.get("status", "")).lower() in {"completed", "failed", "cancelled"}:
                        break
                    await asyncio.sleep(poll_interval_seconds)
        except Exception as exc:
            self.logger.debug("firecrawl crawl request failed url=%s err=%s", url, exc)
            return []

        deduped: list[str] = []
        seen: set[str] = set()
        for link in links:
            if link in seen:
                continue
            seen.add(link)
            deduped.append(link)
        return deduped

    def _extract_links_from_markdown(self, markdown: str) -> list[str]:
        found = re.findall(r"\((https?://[^)\\s]+)\)", markdown)
        out: list[str] = []
        for item in found:
            candidate = item.strip()
            if candidate.startswith("http"):
                out.append(candidate)
        return out
