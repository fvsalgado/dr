#!/usr/bin/env python3
"""
Publituris: artigos via sitemap oficial (robots.txt → sitemap-index.xml).
A página /rss é Next.js (HTML); o feed RSS em bruto não é fiável.
"""
from __future__ import annotations

import logging
import re
import urllib.request
from datetime import date
from urllib.parse import unquote

from news_common import make_news_item, strip_html

log = logging.getLogger("news_publituris")

SITEMAP_INDEX = "https://www.publituris.pt/sitemap-index.xml"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Accept": "application/xml,text/xml,*/*;q=0.8",
}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=_HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _loc_urls(xml: str) -> list[str]:
    return [unquote(m.group(1).strip()) for m in re.finditer(r"<loc>([^<]+)</loc>", xml, re.IGNORECASE)]


def _slug_to_title(slug: str) -> str:
    slug = strip_html(unquote(slug)).strip("-")
    if not slug:
        return ""
    parts = [p for p in slug.split("-") if p]
    if not parts:
        return ""
    return " ".join(w.capitalize() if len(w) > 2 else w.upper() for w in parts)


def _parse_article(url: str) -> tuple[str, str, str] | None:
    m = re.search(
        r"https?://(?:www\.)?publituris\.pt/(\d{4})/(\d{2})/(\d{2})/([^/?#]+)/?$",
        url,
        re.IGNORECASE,
    )
    if m:
        y, mo, d, slug = m.groups()
        title = _slug_to_title(slug)
        if len(title) < 8:
            return None
        return (f"{y}-{mo}-{d}", title, url.split("#")[0])

    m2 = re.search(r"https?://(?:www\.)?publituris\.pt/opiniao/([^/?#]+)/?$", url, re.IGNORECASE)
    if m2:
        title = "Opinião: " + _slug_to_title(m2.group(1))
        if len(title) < 12:
            return None
        return (date.today().isoformat(), title, url.split("#")[0])

    return None


def fetch_latest(limit: int = 80) -> list[dict]:
    try:
        index_xml = _get(SITEMAP_INDEX)
    except Exception as e:
        log.warning("Publituris sitemap index error: %s", e)
        return []

    child_maps = _loc_urls(index_xml)
    if not child_maps:
        log.warning("Publituris: sitemap-index sem <loc>")
        return []

    candidates: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for sm_url in child_maps:
        if "sitemap" not in sm_url.lower():
            continue
        try:
            sm_xml = _get(sm_url)
        except Exception as e:
            log.warning("Publituris child sitemap %s: %s", sm_url, e)
            continue
        for loc in _loc_urls(sm_xml):
            if loc in seen:
                continue
            seen.add(loc)
            parsed = _parse_article(loc)
            if parsed:
                candidates.append(parsed)

    candidates.sort(key=lambda x: x[0], reverse=True)

    out: list[dict] = []
    for date_str, title, link in candidates:
        row = make_news_item("news-publituris", "Publituris", title, link, summary="")
        row["date"] = date_str
        out.append(row)
        if len(out) >= limit:
            break

    log.info("Publituris (sitemap): %d items", len(out))
    return out
