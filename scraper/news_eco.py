#!/usr/bin/env python3
from __future__ import annotations

from news_common import fetch_rss_as_news_items

FEED_URL = "https://eco.sapo.pt/feed/"


def fetch_latest(limit: int = 80) -> list[dict]:
    return fetch_rss_as_news_items(
        FEED_URL,
        source="news-eco",
        issuer="ECO",
        link_must_contain="eco.sapo.pt",
        limit=limit,
    )
