#!/usr/bin/env python3
"""
Scraper de fontes noticiosas (headline-only), normalizado para data/results.json.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from news_publituris import fetch_latest as fetch_publituris
from news_eco import fetch_latest as fetch_eco
from news_expresso import fetch_latest as fetch_expresso
from news_ambienteonline import fetch_latest as fetch_ambienteonline
from news_ambitur import fetch_latest as fetch_ambitur

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("news_scraper")

BASE_DIR = Path(__file__).parent.parent
KEYWORDS_FILE = BASE_DIR / "keywords" / "clients.json"
DATA_FILE = BASE_DIR / "data" / "results.json"
DATA_FILE.parent.mkdir(exist_ok=True)
UNFILTERED_NEWS_PER_SOURCE = 10
NEWS_FETCHERS = [
    ("Publituris", "news-publituris", fetch_publituris),
    ("ECO", "news-eco", fetch_eco),
    ("Expresso", "news-expresso", fetch_expresso),
    ("Ambiente Online", "news-ambienteonline", fetch_ambienteonline),
    ("Ambitur", "news-ambitur", fetch_ambitur),
]


def load_keywords() -> list[dict]:
    with open(KEYWORDS_FILE, encoding="utf-8") as f:
        return json.load(f)["clients"]


def load_existing_results() -> dict:
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            log.warning("Could not parse %s, starting fresh", DATA_FILE)
    return {"last_updated": None, "entries": []}


def save_results(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info("Saved %d entries to %s", len(data["entries"]), DATA_FILE)


def build_pattern(keywords: list[str]) -> re.Pattern:
    escaped = [re.escape(kw) for kw in sorted(keywords, key=len, reverse=True)]
    return re.compile(
        r"(?<![a-záàãâéèêíìîóòõôúùûçñ])(" + "|".join(escaped) + r")(?![a-záàãâéèêíìîóòõôúùûçñ])",
        re.IGNORECASE,
    )


def match_clients(text: str, clients: list[dict]) -> list[dict]:
    matches = []
    for client in clients:
        pattern = build_pattern(client["keywords"])
        found = list(set(m.group(0).lower() for m in pattern.finditer(text)))
        if found:
            matches.append({
                "id": client["id"],
                "name": client["name"],
                "color": client["color"],
                "matched_keywords": sorted(found),
            })
    return matches


def entry_id(source: str, source_id: str) -> str:
    key = f"{source}_{source_id}"
    return hashlib.md5(key.encode()).hexdigest()


def configured_news_sources() -> list[str]:
    return [source for _, source, _ in NEWS_FETCHERS]


def run():
    clients = load_keywords()
    existing = load_existing_results()
    existing_ids = {e["id"] for e in existing["entries"]}
    all_new: list[dict] = []

    for label, _, fetch in NEWS_FETCHERS:
        added_unfiltered = 0
        try:
            raw_items = fetch(limit=50)
        except Exception as e:
            log.warning("%s fetch error: %s", label, e)
            continue
        log.info("%s: %d items", label, len(raw_items))

        for item in raw_items:
            matched = match_clients(item["full_text"], clients)
            include_unfiltered = not matched and added_unfiltered < UNFILTERED_NEWS_PER_SOURCE

            eid = entry_id(item["source"], item["source_id"])
            if eid in existing_ids:
                continue
            if not matched and not include_unfiltered:
                continue

            entry = {
                "id": eid,
                "source": item["source"],
                "series": item.get("series", "NEWS"),
                "date": item["date"],
                "type": item["type"],
                "content_kind": "news",
                "number": item.get("number", ""),
                "issuer": item.get("issuer", ""),
                "title": item["title"],
                "summary": item.get("summary", ""),
                "url": item["url"],
                "clients": matched,
                "scraped_at": datetime.now(tz=timezone.utc).isoformat(),
            }
            if include_unfiltered:
                entry["clients"] = []
                entry["summary"] = item.get("summary", "") or "Notícia sem match de cliente (incluída para cobertura por fonte)."
                added_unfiltered += 1
            all_new.append(entry)
            existing_ids.add(eid)

    log.info("Found %d new relevant news entries", len(all_new))
    existing["entries"] = all_new + existing["entries"]
    existing["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
    existing["entry_count"] = len(existing["entries"])
    save_results(existing)
    print(json.dumps({"new_entries": len(all_new)}, ensure_ascii=False))
    return all_new


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        log.error("Fatal news scraper error: %s", e)
        sys.exit(1)

