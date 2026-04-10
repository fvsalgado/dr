#!/usr/bin/env python3
"""
Scraper de fontes noticiosas (headline-only), normalizado para data/results.json.
Só grava entradas em que o texto (título + resumo + issuer) faz match a keywords de cliente.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import hashlib
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from news_publituris import fetch_latest as fetch_publituris
from news_eco import fetch_latest as fetch_eco
from news_expresso import fetch_latest as fetch_expresso
from news_ambienteonline import fetch_latest as fetch_ambienteonline
from news_ambitur import fetch_latest as fetch_ambitur
from news_observador import fetch_latest as fetch_observador
from news_jornaldenegocios import fetch_latest as fetch_jornaldenegocios
from news_common import get_html, extract_article_meta
from source_meta import source_brand

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("news_scraper")

BASE_DIR = Path(__file__).parent.parent
KEYWORDS_FILE = BASE_DIR / "keywords" / "clients.json"
DATA_FILE = BASE_DIR / "data" / "results.json"
BACKUP_FILE = BASE_DIR / "data" / "results.backup.json"
DATA_FILE.parent.mkdir(exist_ok=True)
DEFAULT_NEWS_MAX_AGE_DAYS = 7.0
NEWS_FETCHERS = [
    ("Publituris", "news-publituris", fetch_publituris),
    ("ECO", "news-eco", fetch_eco),
    ("Expresso", "news-expresso", fetch_expresso),
    ("Ambiente Online", "news-ambienteonline", fetch_ambienteonline),
    ("Ambitur", "news-ambitur", fetch_ambitur),
    ("Observador", "news-observador", fetch_observador),
    ("Jornal de Negocios", "news-jornaldenegocios", fetch_jornaldenegocios),
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
    if BACKUP_FILE.exists():
        try:
            with open(BACKUP_FILE, encoding="utf-8") as f:
                data = json.load(f)
                log.warning("Using backup file: %s", BACKUP_FILE)
                return data
        except (json.JSONDecodeError, ValueError):
            pass
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
                "logo_url": client.get("logo_url", ""),
                "matched_keywords": sorted(found),
            })
    return matches


def entry_id(source: str, source_id: str) -> str:
    key = f"{source}_{source_id}"
    return hashlib.md5(key.encode()).hexdigest()


def configured_news_sources() -> list[str]:
    return [source for _, source, _ in NEWS_FETCHERS]


def _selected_fetchers(selected_sources: list[str] | None) -> list[tuple[str, str, object]]:
    if not selected_sources:
        return NEWS_FETCHERS
    allowed = set(selected_sources)
    return [item for item in NEWS_FETCHERS if item[1] in allowed]


def _enrich_news_item(item: dict) -> dict:
    source = item.get("source", "")
    brand = source_brand(source)
    article_meta: dict = {"image_url": "", "published_at": "", "article_section": "", "description": ""}
    try:
        html_text = get_html(item["url"], retries=1, timeout_s=20)
        article_meta = extract_article_meta(html_text)
    except Exception as exc:
        log.debug("Metadata extraction failed for %s: %s", item.get("url", ""), exc)
    out = dict(item)
    out["source_label"] = brand["label"]
    out["source_logo_url"] = brand["logo_url"]
    out["image_url"] = (article_meta.get("image_url") or "").strip() or out.get("image_url", "")
    meta_pub = (article_meta.get("published_at") or "").strip()
    out["published_at"] = meta_pub or (out.get("published_at") or "").strip()
    out["article_section"] = (article_meta.get("article_section") or "").strip() or out.get("article_section", "")
    meta_desc = (article_meta.get("description") or "").strip()
    summary = (out.get("summary") or "").strip() or meta_desc
    out["summary"] = summary
    out["full_text"] = f"{out.get('title', '')} {summary} {out.get('issuer', '')}".strip()
    return out


def _parse_item_datetime(item: dict) -> datetime | None:
    pub = (item.get("published_at") or "").strip()
    if pub:
        try:
            dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    d = (item.get("date") or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", d)
    if m:
        try:
            y, mo, day = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return datetime(y, mo, day, tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _within_max_age(item: dict, max_age_days: float) -> bool:
    if max_age_days <= 0:
        return True
    dt = _parse_item_datetime(item)
    if dt is None:
        return True
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)
    return dt >= cutoff


def run(selected_sources: list[str] | None = None, max_age_days: float = DEFAULT_NEWS_MAX_AGE_DAYS):
    clients = load_keywords()
    existing = load_existing_results()
    existing_ids = {e["id"] for e in existing["entries"]}
    all_new: list[dict] = []
    fetchers = _selected_fetchers(selected_sources)
    if not fetchers:
        raise ValueError("No valid news sources selected")

    for label, source_id, fetch in fetchers:
        try:
            raw_items = fetch(limit=150)
        except Exception as e:
            log.warning("%s fetch error: %s", label, e)
            continue
        log.info("%s: %d items", label, len(raw_items))

        for item in raw_items:
            item = _enrich_news_item(item)
            if not _within_max_age(item, max_age_days):
                continue
            matched = match_clients(item["full_text"], clients)
            if not matched:
                continue

            eid = entry_id(source_id, item["source_id"])
            if eid in existing_ids:
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
                "published_at": item.get("published_at", ""),
                "image_url": item.get("image_url", ""),
                "source_logo_url": item.get("source_logo_url", ""),
                "source_label": item.get("source_label", item.get("source", "")),
                "article_section": item.get("article_section", ""),
            }
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
        parser = argparse.ArgumentParser(description="Scrape news sources")
        parser.add_argument(
            "--sources",
            default="all",
            help="Comma-separated source IDs (default: all). Ex: news-eco,news-expresso",
        )
        parser.add_argument(
            "--max-age-days",
            type=float,
            default=DEFAULT_NEWS_MAX_AGE_DAYS,
            help="Incluir apenas notícias com data de publicação nas últimas N dias (0 = sem filtro). Padrão: 7",
        )
        args = parser.parse_args()
        selected = None
        if args.sources and args.sources.lower() != "all":
            selected = [s.strip() for s in args.sources.split(",") if s.strip()]
        run(selected_sources=selected, max_age_days=args.max_age_days)
    except Exception as e:
        log.error("Fatal news scraper error: %s", e)
        sys.exit(1)

