from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
import time

from .extract import ExtractedPage, extract_page
from .fetcher import PublicWebFetcher, WebFetchError
from .search import BingRSSSearch, DuckDuckGoHTMLSearch, FallbackSearchBackend, SearchBackend, SearchResult, WikipediaOpenSearch
from .security import UnsafeWebTarget, validate_public_url


class WebResearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResearchBundle:
    query: str
    results: tuple[SearchResult, ...]
    pages: tuple[ExtractedPage, ...]
    search_ms: float
    read_ms: float

    @property
    def sources(self) -> list[tuple[str, str]]:
        pages = [(page.title, page.final_url) for page in self.pages]
        return pages or [(result.title, result.url) for result in self.results]


class WebResearchService:
    def __init__(self, *, fetcher: PublicWebFetcher | None = None, search_backend: SearchBackend | None = None):
        self.fetcher = fetcher or PublicWebFetcher()
        self.search_backend = search_backend or FallbackSearchBackend([
            DuckDuckGoHTMLSearch(self.fetcher),
            BingRSSSearch(self.fetcher),
            WikipediaOpenSearch(self.fetcher),
        ])

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        return await asyncio.to_thread(self._safe_search, query, limit)

    def _safe_search(self, query: str, limit: int) -> list[SearchResult]:
        results = self.search_backend.search(query, limit)
        safe = []
        for result in results:
            try:
                validate_public_url(result.url, self.fetcher.resolver)
            except UnsafeWebTarget:
                continue
            safe.append(result)
        return safe

    async def read(self, url: str) -> ExtractedPage:
        response = await asyncio.to_thread(self.fetcher.fetch, url)
        if response.content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
            raise WebResearchError("The public URL is not a supported readable webpage")
        page = extract_page(response.body, response.content_type, response.final_url)
        if not page.text:
            raise WebResearchError("No readable public text was found")
        return page

    async def research(self, query: str, *, result_limit: int = 5, page_limit: int = 3) -> ResearchBundle:
        started = time.perf_counter()
        try:
            results = await self.search(query, result_limit)
        except Exception as error:
            raise WebResearchError(f"Search failed: {error}") from error
        search_ms = (time.perf_counter() - started) * 1000
        if not results:
            raise WebResearchError("Search returned no public results")
        read_started = time.perf_counter()
        pages: list[ExtractedPage] = []
        for result in results[:page_limit]:
            try:
                pages.append(await self.read(result.url))
            except (WebFetchError, WebResearchError, ValueError):
                continue
        return ResearchBundle(query, tuple(results), tuple(pages), search_ms, (time.perf_counter() - read_started) * 1000)


_EXPLICIT_RESEARCH = re.compile(r"\b(?:browse|find (?:me )?(?:information|info)|look (?:it|this|that|something)?\s*up|research|search (?:online|the (?:internet|web)|for)|check (?:online|the web))\b", re.I)
_CURRENT_INFO = re.compile(r"\b(?:breaking|current(?:ly)?|latest|live|news|price|release date|schedule|score|status|today|tonight|this (?:week|month|year)|weather)\b", re.I)
_PUBLIC_URL = re.compile(r"https?://[^\s<>\"]+", re.I)


def needs_research(message: str) -> bool:
    return bool(_EXPLICIT_RESEARCH.search(message) or _CURRENT_INFO.search(message) or (_PUBLIC_URL.search(message) and re.search(r"\b(?:read|open|summarize|check)\b", message, re.I)))


def research_query(message: str) -> str:
    cleaned = re.sub(r"\b(?:please|can you|could you|would you)\b", " ", message, flags=re.I)
    cleaned = re.sub(r"\b(?:browse|look (?:it|this|that)?\s*up|research|search (?:online|the (?:internet|web)|for)|check (?:online|the web))\b", " ", cleaned, flags=re.I)
    return re.sub(r"\s+", " ", cleaned).strip(" .?!:") or message.strip()


def public_url(message: str) -> str | None:
    match = _PUBLIC_URL.search(message)
    return match.group(0).rstrip(".,;!?)'") if match else None
