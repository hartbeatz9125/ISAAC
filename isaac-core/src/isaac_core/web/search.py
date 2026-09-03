from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import json
from urllib.parse import parse_qs, quote_plus, unquote, urlsplit
from xml.etree import ElementTree

from .fetcher import PublicWebFetcher


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchBackend:
    name = "abstract"

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        raise NotImplementedError


class _DuckHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._result: dict[str, str] | None = None
        self._capture: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = {str(key): str(value or "") for key, value in attrs}
        classes = set(values.get("class", "").split())
        if tag == "a" and classes.intersection({"result__a", "result-link"}):
            self._result = {"title": "", "url": values.get("href", ""), "snippet": ""}
            self._capture, self._parts = "title", []
        elif self._result is not None and classes.intersection({"result__snippet", "result-snippet"}):
            self._capture, self._parts = "snippet", []

    def handle_endtag(self, tag):
        if self._result is None or not self._capture:
            return
        if (self._capture == "title" and tag == "a") or (self._capture == "snippet" and tag in {"a", "div", "span", "td"}):
            self._result[self._capture] = " ".join(self._parts).strip()
            if self._capture == "snippet":
                self.results.append(self._result)
                self._result = None
            self._capture, self._parts = None, []

    def handle_data(self, data):
        if self._capture:
            self._parts.append(data)


def _destination(value: str) -> str:
    value = unquote(value)
    if value.startswith("//"):
        value = "https:" + value
    parsed = urlsplit(value)
    if parsed.hostname and parsed.hostname.endswith("duckduckgo.com"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return value


class DuckDuckGoHTMLSearch(SearchBackend):
    name = "duckduckgo-html"

    def __init__(self, fetcher: PublicWebFetcher):
        self.fetcher = fetcher

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        query = query.strip()
        if not query or len(query) > 500:
            raise ValueError("Search query must contain 1-500 characters")
        response = self.fetcher.fetch(f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}")
        parser = _DuckHTMLParser()
        parser.feed(response.body.decode("utf-8", errors="replace"))
        results = []
        for item in parser.results:
            url = _destination(item["url"])
            if not url.startswith(("http://", "https://")):
                continue
            results.append(SearchResult(item["title"][:300], url, item["snippet"][:800]))
            if len(results) >= min(max(limit, 1), 10):
                break
        return results


class BingRSSSearch(SearchBackend):
    name = "bing-rss"

    def __init__(self, fetcher: PublicWebFetcher):
        self.fetcher = fetcher

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        response = self.fetcher.fetch(f"https://www.bing.com/search?q={quote_plus(query)}&format=rss")
        root = ElementTree.fromstring(response.body)
        results = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            url = (item.findtext("link") or "").strip()
            snippet = (item.findtext("description") or "").strip()
            if title and url.startswith(("http://", "https://")):
                results.append(SearchResult(title[:300], url, snippet[:800]))
            if len(results) >= min(max(limit, 1), 10):
                break
        return results


class WikipediaOpenSearch(SearchBackend):
    name = "wikipedia-opensearch"

    def __init__(self, fetcher: PublicWebFetcher):
        self.fetcher = fetcher

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        response = self.fetcher.fetch(f"https://en.wikipedia.org/w/api.php?action=opensearch&search={quote_plus(query)}&limit={min(max(limit, 1), 10)}&namespace=0&format=json")
        payload = json.loads(response.body.decode("utf-8"))
        if not isinstance(payload, list) or len(payload) < 4:
            return []
        return [SearchResult(str(title)[:300], str(url), str(snippet)[:800]) for title, snippet, url in zip(payload[1], payload[2], payload[3]) if str(url).startswith(("http://", "https://"))]


class FallbackSearchBackend(SearchBackend):
    name = "fallback-chain"

    def __init__(self, backends: list[SearchBackend]):
        self.backends = backends

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        last_error = None
        for backend in self.backends:
            try:
                results = backend.search(query, limit)
                if results:
                    return results
            except Exception as error:
                last_error = error
        if last_error:
            raise last_error
        return []
