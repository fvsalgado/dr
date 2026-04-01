#!/usr/bin/env python3
"""
Scraper do Diário da República (1ª e 2ª série)
Usa a API pública do DRE e o feed RSS do diariodarepublica.pt
"""

import json
import os
import re
import sys
import hashlib
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree
import urllib.request
import urllib.parse
import urllib.error

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("dre_scraper")

BASE_DIR = Path(__file__).parent.parent
KEYWORDS_FILE = BASE_DIR / "keywords" / "clients.json"
DATA_FILE = BASE_DIR / "data" / "results.json"
DATA_FILE.parent.mkdir(exist_ok=True)

DRE_API = "https://dre.pt/rest/legislacao/pesquisa"
DRE_BASE = "https://dre.pt"

RSS_FEEDS = {
    1: "http://files.diariodarepublica.pt/rss/serie1-html.xml",
    2: "http://files.diariodarepublica.pt/rss/serie2-html.xml",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def fetch_json(url: str, params: dict | None = None) -> dict:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "PublicAffairsMonitor/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_xml(url: str) -> ElementTree.Element | None:
    """Fetch and parse an XML feed, returning the root element or None on error."""
    req = urllib.request.Request(url, headers={"User-Agent": "PublicAffairsMonitor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return ElementTree.fromstring(resp.read())
    except Exception as e:
        log.warning("RSS fetch error (%s): %s", url, e)
        return None


def fetch_eli(detail_url: str) -> str | None:
    """Try to extract the ELI URL from a diariodarepublica.pt detail page."""
    try:
        req = urllib.request.Request(detail_url, headers={"User-Agent": "PublicAffairsMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # ELI links look like: https://data.dre.pt/eli/...
        m = re.search(r'(https://data\.dre\.pt/eli/[^\s"\'<>]+)', html)
        return m.group(1) if m else None
    except Exception:
        return None


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


def entry_id(entry: dict) -> str:
    """Stable ID for deduplication."""
    key = f"{entry['source_id']}_{entry['series']}"
    return hashlib.md5(key.encode()).hexdigest()


# ── keyword matching ──────────────────────────────────────────────────────────

def build_pattern(keywords: list[str]) -> re.Pattern:
    """Case-insensitive regex OR of all keywords."""
    escaped = [re.escape(kw) for kw in sorted(keywords, key=len, reverse=True)]
    return re.compile(r"(?<![a-záàãâéèêíìîóòõôúùûçñ])(" + "|".join(escaped) + r")(?![a-záàãâéèêíìîóòõôúùûçñ])", re.IGNORECASE)


def match_clients(text: str, clients: list[dict]) -> list[dict]:
    """Return list of {id, name, color, matched_keywords} for each matching client."""
    matches = []
    for client in clients:
        pattern = build_pattern(client["keywords"])
        found = list(set(m.group(0).lower() for m in pattern.finditer(text)))
        if found:
            matches.append({
                "id": client["id"],
                "name": client["name"],
                "color": client["color"],
                "matched_keywords": sorted(found)
            })
    return matches


# ── DRE API ───────────────────────────────────────────────────────────────────

def fetch_dre_day(target_date: date, series: int) -> list[dict]:
    """
    Fetch all acts published on target_date for a given series (1 or 2).
    DRE API returns paginated results; we iterate until exhausted.
    """
    date_str = target_date.strftime("%Y-%m-%d")
    results = []
    page = 1

    while True:
        params = {
            "tipo": "",
            "numero": "",
            "serie": series,
            "dataInicio": date_str,
            "dataFim": date_str,
            "pagina": page,
            "perPagina": 25,
        }
        try:
            data = fetch_json(DRE_API, params)
        except urllib.error.HTTPError as e:
            log.warning("DRE API HTTP error %s (series=%s, page=%s)", e.code, series, page)
            break
        except Exception as e:
            log.warning("DRE API error: %s", e)
            break

        items = data.get("items") or data.get("results") or []
        if not items:
            break

        for item in items:
            results.append(parse_dre_item(item, series))

        total = data.get("total", 0)
        if page * 25 >= total:
            break
        page += 1

    log.info("Series %s — %s: %d items", series, date_str, len(results))
    return results


def parse_dre_item(item: dict, series: int) -> dict:
    """Normalise a raw DRE API item into our internal format."""
    # Build readable URL
    doc_id = item.get("id") or item.get("dreid") or ""
    url = f"{DRE_BASE}/dre/legislacao/{doc_id}" if doc_id else DRE_BASE

    title = (item.get("titulo") or item.get("title") or "").strip()
    summary = (item.get("sumario") or item.get("summary") or item.get("texto") or "").strip()
    doc_type = (item.get("tipo") or item.get("type") or "").strip()
    pub_date = (item.get("data") or item.get("date") or "").strip()
    issuer = (item.get("emissor") or item.get("issuer") or "").strip()
    number = str(item.get("numero") or item.get("number") or "").strip()

    return {
        "source": "dre",
        "series": f"{series}ª Série",
        "source_id": str(doc_id),
        "date": pub_date,
        "type": doc_type,
        "number": number,
        "issuer": issuer,
        "title": title,
        "summary": summary,
        "url": url,
        "eli": None,
        "full_text": f"{title} {summary} {doc_type} {issuer}",
    }


# ── RSS feed ─────────────────────────────────────────────────────────────────

def fetch_rss_day(target_date: date, series: int) -> list[dict]:
    """
    Fetch acts from the diariodarepublica.pt RSS feed for a given series.
    The RSS feed contains the latest day's publications.
    We filter items to match target_date.
    """
    url = RSS_FEEDS.get(series)
    if not url:
        return []

    root = fetch_xml(url)
    if root is None:
        return []

    date_str = target_date.strftime("%Y-%m-%d")
    results = []

    for item in root.iter("item"):
        title_el = item.find("title")
        desc_el = item.find("description")
        link_el = item.find("link")

        title_text = (title_el.text or "").strip() if title_el is not None else ""
        desc_text = (desc_el.text or "").strip() if desc_el is not None else ""
        link_text = (link_el.text or "").strip() if link_el is not None else ""

        # Filter by date — the title contains the date in YYYY-MM-DD format
        if date_str not in title_text:
            continue

        # Parse title: e.g. "Portaria n.º 137/2026/1 - Diário da República n.º 64/2026, Série I de 2026-04-01"
        doc_type = ""
        number = ""
        type_match = re.match(r'^([^-–]+?)(?:\s*[-–])', title_text)
        if type_match:
            type_and_num = type_match.group(1).strip()
            num_match = re.match(r'^(.+?)\s+n\.º?\s*(.+)$', type_and_num, re.IGNORECASE)
            if num_match:
                doc_type = num_match.group(1).strip()
                number = num_match.group(2).strip()
            else:
                doc_type = type_and_num

        # Extract issuer from description (usually the first line/part)
        # Description format: "Emitido por: XYZ\nSumário: ..."
        issuer = ""
        summary = desc_text
        # Clean HTML tags from description
        summary = re.sub(r'<[^>]+>', ' ', summary).strip()
        summary = re.sub(r'\s+', ' ', summary)

        # Build a stable source_id from the link
        source_id = re.search(r'/(\d+)/?$', link_text)
        source_id = source_id.group(1) if source_id else hashlib.md5(link_text.encode()).hexdigest()

        results.append({
            "source": "dre-rss",
            "series": f"{series}ª Série",
            "source_id": str(source_id),
            "date": date_str,
            "type": doc_type,
            "number": number,
            "issuer": issuer,
            "title": title_text,
            "summary": summary,
            "url": link_text,
            "eli": None,
            "full_text": f"{title_text} {summary} {doc_type} {issuer}",
        })

    log.info("RSS Series %s — %s: %d items", series, date_str, len(results))
    return results


# ── main ──────────────────────────────────────────────────────────────────────

def run(target_date: date | None = None, days: int = 30):
    """
    Scrape DRE for the last `days` days ending on `target_date` (inclusive).
    Defaults to the last 30 days up to today.
    """
    if target_date is None:
        target_date = date.today()

    date_range = [target_date - timedelta(days=i) for i in range(days)]
    log.info(
        "Running DRE scraper for %d days: %s → %s",
        days,
        date_range[-1],
        date_range[0],
    )

    clients = load_keywords()
    existing = load_existing_results()
    existing_ids = {e["id"] for e in existing["entries"]}

    all_new_entries = []

    for current_date in date_range:
        log.info("── Scraping %s ──", current_date)
        for series in [1, 2]:
            items = fetch_dre_day(current_date, series)
            for item in items:
                matched = match_clients(item["full_text"], clients)
                if not matched:
                    continue

                eid = entry_id(item)
                if eid in existing_ids:
                    log.debug("Skip duplicate: %s", eid)
                    continue

                entry = {
                    "id": eid,
                    "source": item["source"],
                    "series": item["series"],
                    "date": item["date"],
                    "type": item["type"],
                    "number": item["number"],
                    "issuer": item["issuer"],
                    "title": item["title"],
                    "summary": item["summary"],
                    "url": item["url"],
                    "clients": matched,
                    "scraped_at": datetime.utcnow().isoformat() + "Z",
                }
                all_new_entries.append(entry)
                existing_ids.add(eid)

    log.info("Found %d new relevant entries across %d days", len(all_new_entries), days)

    existing["entries"] = all_new_entries + existing["entries"]
    existing["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
    existing["entry_count"] = len(existing["entries"])

    save_results(existing)
    return all_new_entries


if __name__ == "__main__":
    # Usage: dre.py [YYYY-MM-DD] [--days N]
    # Examples:
    #   dre.py                        → last 30 days up to today
    #   dre.py 2026-03-01             → last 30 days up to 2026-03-01
    #   dre.py 2026-03-01 --days 7    → last 7 days up to 2026-03-01
    #   dre.py --days 60              → last 60 days up to today
    import argparse

    parser = argparse.ArgumentParser(description="Scrape Diário da República")
    parser.add_argument(
        "date",
        nargs="?",
        default=None,
        help="End date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to look back (default: 30)",
    )
    args = parser.parse_args()

    target = None
    if args.date:
        try:
            target = date.fromisoformat(args.date)
        except ValueError:
            log.error("Invalid date: %s. Use YYYY-MM-DD.", args.date)
            sys.exit(1)

    new = run(target_date=target, days=args.days)
    print(json.dumps({"new_entries": len(new)}, ensure_ascii=False))
