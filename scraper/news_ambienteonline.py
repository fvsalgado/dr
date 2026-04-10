#!/usr/bin/env python3
from __future__ import annotations

from urllib.parse import urlparse

from news_common import get_html, listing_items, make_news_item

URL = "https://www.ambienteonline.pt/"

_SKIP_FIRST_SEGMENTS = frozenset({
    "jornal", "opiniao", "opinião", "iniciativas", "podcasts", "login", "destaques",
    "contactos", "termos", "politica", "política", "subscrever", "newsletter",
})


def _is_article_url(url: str) -> bool:
    p = urlparse(url)
    if "ambienteonline.pt" not in (p.netloc or "").lower():
        return False
    parts = [x for x in p.path.strip("/").split("/") if x]
    if len(parts) < 2:
        return False
    if parts[0].lower() in _SKIP_FIRST_SEGMENTS:
        return False
    return True


def fetch_latest(limit: int = 40) -> list[dict]:
    html = get_html(URL)
    links = listing_items(URL, html, limit=limit * 4)
    out: list[dict] = []
    for title, url in links:
        if "ambienteonline.pt" not in url or not _is_article_url(url):
            continue
        if len(title) < 24:
            continue
        out.append(make_news_item("news-ambienteonline", "Ambiente Online", title, url))
        if len(out) >= limit:
            break
    return out

