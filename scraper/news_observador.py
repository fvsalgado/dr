#!/usr/bin/env python3
from __future__ import annotations

from news_common import get_html, listing_items, make_news_item

URL = "https://observador.pt/ultimas/"


def fetch_latest(limit: int = 40) -> list[dict]:
    html = get_html(URL, fallback_urls=["https://observador.pt/"], retries=2, timeout_s=30)
    links = listing_items(URL, html, limit=limit * 3)
    out: list[dict] = []
    for title, url in links:
        if "observador.pt" not in url:
            continue
        out.append(make_news_item("news-observador", "Observador", title, url))
        if len(out) >= limit:
            break
    return out
