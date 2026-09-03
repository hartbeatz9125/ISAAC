from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from urllib.parse import urljoin


@dataclass(frozen=True)
class ExtractedPage:
    title: str
    final_url: str
    text: str


class _ReadableHTML(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts: list[str] = []
        self.blocks: list[str] = []
        self.meta_description = ""
        self.canonical = ""
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs):
        tag = tag.casefold()
        values = {str(key).casefold(): str(value or "") for key, value in attrs}
        if tag in {"script", "style", "noscript", "svg", "canvas", "template"}:
            self._hidden += 1
            return
        if self._hidden:
            return
        if tag == "meta" and values.get("name", "").casefold() in {"description", "twitter:description"}:
            self.meta_description = self.meta_description or values.get("content", "")
        if tag == "link" and "canonical" in values.get("rel", "").casefold().split():
            self.canonical = urljoin(self.base_url, values.get("href", ""))
        if tag == "title" or tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote"}:
            self._capture = tag
            self._buffer = []

    def handle_endtag(self, tag: str):
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "svg", "canvas", "template"} and self._hidden:
            self._hidden -= 1
            return
        if self._hidden or tag != self._capture:
            return
        value = re.sub(r"\s+", " ", " ".join(self._buffer)).strip()
        if value:
            if tag == "title":
                self.title_parts.append(value)
            else:
                self.blocks.append(value)
        self._capture = None
        self._buffer = []

    def handle_data(self, data: str):
        if not self._hidden and self._capture:
            self._buffer.append(data)


def extract_page(body: bytes, content_type: str, final_url: str, max_chars: int = 24_000) -> ExtractedPage:
    text = body.decode("utf-8", errors="replace")
    if content_type == "text/plain":
        readable = re.sub(r"\s+", " ", text).strip()[:max_chars]
        return ExtractedPage(final_url, final_url, readable)
    parser = _ReadableHTML(final_url)
    parser.feed(text)
    blocks = []
    if parser.meta_description:
        blocks.append(re.sub(r"\s+", " ", parser.meta_description).strip())
    blocks.extend(parser.blocks)
    deduplicated = []
    seen = set()
    for block in blocks:
        normalized = block.casefold()
        if normalized in seen or len(block) < 20:
            continue
        seen.add(normalized)
        deduplicated.append(block)
    readable = "\n\n".join(deduplicated)[:max_chars]
    title = parser.title_parts[0] if parser.title_parts else final_url
    # Canonical metadata is untrusted page content. The citation identity is
    # always the actual validated final response URL.
    return ExtractedPage(title[:300], final_url, readable)
