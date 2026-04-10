#!/usr/bin/env python3
from __future__ import annotations

from news_common import fetch_rss_as_news_items

FEED_URL = "https://observador.pt/rss/"


def fetch_latest(limit: int = 80) -> list[dict]:
    return fetch_rss_as_news_items(
        FEED_URL,
        source="news-observador",
        issuer="Observador",
        link_must_contain="observador.pt",
        limit=limit,
    )
