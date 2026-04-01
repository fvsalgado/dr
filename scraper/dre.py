#!/usr/bin/env python3
"""
Scraper do Diário da República (1ª e 2ª série)
Usa a API pública do DRE: https://dre.pt/api/
"""

import json
import os
import re
import sys
import hashlib
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
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


# ── helpers ───────────────────────────────────────────────────────────────────

def fetch_json(url: str, params: dict | None = None) -> dict:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "PublicAffairsMonitor/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


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
        "full_text": f"{title} {summary} {doc_type} {issuer}",
    }


# ── main ──────────────────────────────────────────────────────────────────────

def run(target_date: date | None = None):
    if target_date is None:
        target_date = date.today()

    log.info("Running DRE scraper for %s", target_date)

    clients = load_keywords()
    existing = load_existing_results()
    existing_ids = {e["id"] for e in existing["entries"]}

    new_entries = []

    for series in [1, 2]:
        items = fetch_dre_day(target_date, series)
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
            new_entries.append(entry)
            existing_ids.add(eid)

    log.info("Found %d new relevant entries", len(new_entries))

    existing["entries"] = new_entries + existing["entries"]
    existing["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
    existing["entry_count"] = len(existing["entries"])

    save_results(existing)
    return new_entries


if __name__ == "__main__":
    # Accept optional date argument: YYYY-MM-DD
    target = None
    if len(sys.argv) > 1:
        try:
            target = date.fromisoformat(sys.argv[1])
        except ValueError:
            log.error("Invalid date: %s. Use YYYY-MM-DD.", sys.argv[1])
            sys.exit(1)

    new = run(target)
    print(json.dumps({"new_entries": len(new)}, ensure_ascii=False))
