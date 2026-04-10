#!/usr/bin/env python3
from __future__ import annotations

from news_common import fetch_rss_as_news_items

# Feed oficial Impresa (a página /ultimas é sobrecarregada de navegação; /rss dá 404)
FEED_URL = (
    "https://rss.impresa.pt/feed/latest/expresso.rss"
    "?limit=100&type=ARTICLE%2CSTREAM%2CVIDEO%2CGALLERY%2CEVENT"
)


def fetch_latest(limit: int = 80) -> list[dict]:
    return fetch_rss_as_news_items(
        FEED_URL,
        source="news-expresso",
        issuer="Expresso",
        link_must_contain="expresso.pt",
        limit=limit,
    )
