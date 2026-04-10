#!/usr/bin/env python3
from __future__ import annotations

from news_common import fetch_rss_as_news_items

# /ultimas devolve 404; o feed RSS lista as últimas notícias
FEED_URL = "https://www.jornaldenegocios.pt/rss"


def fetch_latest(limit: int = 80) -> list[dict]:
    return fetch_rss_as_news_items(
        FEED_URL,
        source="news-jornaldenegocios",
        issuer="Jornal de Negocios",
        link_must_contain="jornaldenegocios.pt",
        limit=limit,
    )
