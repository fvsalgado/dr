#!/usr/bin/env python3
from __future__ import annotations

from news_common import get_html, listing_items, make_news_item

URL = "https://eco.sapo.pt/ultimas/"


def fetch_latest(limit: int = 40) -> list[dict]:
    html = get_html(URL)
    links = listing_items(URL, html, limit=limit * 3)
    out: list[dict] = []
    for title, url in links:
        if "eco.sapo.pt" not in url:
            continue
        out.append(make_news_item("news-eco", "ECO", title, url))
        if len(out) >= limit:
            break
    return out

