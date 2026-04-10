#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import time
import re
import urllib.parse
import urllib.request
from datetime import date

_HEADERS = {
    "User-Agent": "PublicAffairsMonitor/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def get_html(url: str, fallback_urls: list[str] | None = None, retries: int = 2, timeout_s: int = 30) -> str:
    urls = [url] + [u for u in (fallback_urls or []) if u and u != url]
    last_exc: Exception | None = None
    for current_url in urls:
        for attempt in range(retries + 1):
            req = urllib.request.Request(current_url, headers=_HEADERS, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    return _decode_bytes(resp.read())
            except Exception as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                break
    if last_exc:
        raise last_exc
    raise RuntimeError("Could not fetch any URL")


def strip_html(text: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_url(base_url: str, href: str) -> str:
    return urllib.parse.urljoin(base_url, href.strip())


def link_id(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def listing_items(base_url: str, html_text: str, limit: int = 80) -> list[tuple[str, str]]:
    seen: set[str] = set()
    items: list[tuple[str, str]] = []
    for m in re.finditer(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html_text, re.IGNORECASE | re.DOTALL):
        href = m.group(1).strip()
        if not href or href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            continue
        title = strip_html(m.group(2))
        if len(title) < 20:
            continue
        url = normalize_url(base_url, href)
        if url in seen:
            continue
        if any(x in url.lower() for x in ("/tag/", "/categoria/", "/category/", "/autor/", "/author/")):
            continue
        seen.add(url)
        items.append((title, url))
        if len(items) >= limit:
            break
    return items


def make_news_item(source: str, issuer: str, title: str, url: str, summary: str = "") -> dict:
    full_text = f"{title} {summary} {issuer}".strip()
    return {
        "source": source,
        "source_id": link_id(url),
        "date": date.today().isoformat(),
        "series": "NEWS",
        "type": "Notícia",
        "number": "",
        "issuer": issuer,
        "title": title,
        "summary": summary,
        "url": url,
        "full_text": full_text,
    }


def extract_article_meta(html_text: str) -> dict:
    def _meta(prop: str) -> str:
        m = re.search(
            rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)["\']',
            html_text,
            re.IGNORECASE,
        )
        return html.unescape(m.group(1).strip()) if m else ""

    image_url = _meta("og:image") or _meta("twitter:image")
    published_at = _meta("article:published_time") or _meta("date") or _meta("publish_date")
    article_section = _meta("article:section")
    return {
        "image_url": image_url,
        "published_at": published_at,
        "article_section": article_section,
    }

