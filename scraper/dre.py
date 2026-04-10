#!/usr/bin/env python3
"""
Scraper do Diário da República (1ª e 2ª série)
Usa a API interna do dre.pt (POST/JSON) e o feed RSS do diariodarepublica.pt
"""

import json
import re
import sys
import hashlib
import logging
import os
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

DRE_BASE = "https://dre.pt"

# OutSystems endpoints moved behind redirects in 2026.
# If you call the old dre.pt endpoints with urllib, a 301 redirect can convert POST→GET,
# causing HTTP 405. We preserve POST across redirects in _post().
DRE_API_BASE = "https://diariodarepublica.pt/dr/screenservices/dr"
DRE_EP_CALENDAR = f"{DRE_API_BASE}/Home/home/DataActionGetDRByDataCalendario"
DRE_EP_DIPLOMAS = f"{DRE_API_BASE}/Legislacao_Conteudos/ListaDiplomas/DataActionGetDados"

RSS_FEEDS = {
    1: "http://files.diariodarepublica.pt/rss/serie1-html.xml",
    2: "http://files.diariodarepublica.pt/rss/serie2-html.xml",
}

_HEADERS = {
    "User-Agent": "PublicAffairsMonitor/1.0",
    "Content-Type": "application/json; charset=utf-8",
    "X-CSRFToken": "scraper",   # any non-empty string is accepted by the server
}

_SERIES_LABEL = {1: "Série I", 2: "Série II"}

def _load_version_info() -> dict:
    """
    diariodarepublica.pt now requires a non-empty 'versionInfo' object.
    If the API starts returning {"hasApiVersionChanged": true} with empty data,
    set DRE_VERSION_INFO_JSON to the value observed in browser network calls.
    """
    raw = os.environ.get("DRE_VERSION_INFO_JSON", "").strip()
    if not raw:
        # Default: compatible with the older dre.pt API envelope; may require override.
        return {"hasModuleVersionChanged": True, "hasApiVersionChanged": False}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Invalid DRE_VERSION_INFO_JSON (not JSON). Using default versionInfo.")
        return {"hasModuleVersionChanged": True, "hasApiVersionChanged": False}
    if not isinstance(parsed, dict) or not parsed:
        log.warning("Invalid DRE_VERSION_INFO_JSON (must be a non-empty object). Using default versionInfo.")
        return {"hasModuleVersionChanged": True, "hasApiVersionChanged": False}
    return parsed


DRE_VERSION_INFO = _load_version_info()


# ── helpers ───────────────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER_NO_REDIRECT = urllib.request.build_opener(_NoRedirect)


def _post(url: str, payload: dict, *, timeout_s: int = 30, max_redirects: int = 5) -> dict:
    """POST a JSON payload and return the parsed response, preserving POST on redirects."""
    body = json.dumps(payload).encode("utf-8")
    headers = dict(_HEADERS)
    # Helps when the server is picky; harmless otherwise.
    headers.setdefault("Origin", "https://diariodarepublica.pt")
    headers.setdefault("Referer", "https://diariodarepublica.pt/")

    redirects = 0
    while True:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with _OPENER_NO_REDIRECT.open(req, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location", "")
                if not loc:
                    raise
                redirects += 1
                if redirects > max_redirects:
                    raise RuntimeError(f"Too many redirects while POSTing to DRE (last={url})")
                url = urllib.parse.urljoin(url, loc)
                continue
            raise


def _parse_json_out(raw: dict) -> dict:
    """Extract and parse the nested Json_Out string from the API envelope."""
    json_out = raw.get("data", {}).get("Json_Out", "")
    if not json_out:
        return {}
    return json.loads(json_out)


def fetch_xml(url: str) -> ElementTree.Element | None:
    req = urllib.request.Request(url, headers={"User-Agent": "PublicAffairsMonitor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return ElementTree.fromstring(resp.read())
    except Exception as e:
        log.warning("RSS fetch error (%s): %s", url, e)
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
    key = f"{entry['source_id']}_{entry['series']}"
    return hashlib.md5(key.encode()).hexdigest()


# ── keyword matching ──────────────────────────────────────────────────────────

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


# ── DRE API ───────────────────────────────────────────────────────────────────

def _calendar_payload(date_str: str) -> dict:
    return {
        "versionInfo": DRE_VERSION_INFO,
        "viewName": "Home.home",
        "screenData": {
            "variables": {
                "DataCalendario": date_str,
                # Must be >= the date we query, otherwise the API ignores our date
                "DataUltimaPublicacao": "2099-12-31",
            }
        },
        "clientVariables": {},
    }


def _diplomas_payload(diario_id: int) -> dict:
    return {
        "versionInfo": DRE_VERSION_INFO,
        "viewName": "Legislacao_Conteudos.ListaDiplomas",
        "screenData": {
            "variables": {
                "DiarioId": diario_id,
            }
        },
        "clientVariables": {},
    }


def fetch_dre_day(target_date: date, series: int) -> list[dict]:
    """
    Fetch all acts published on target_date for a given series using the
    real DRE POST API:
      1. Get the list of Diários published on that date
      2. For each Diário matching the requested series, fetch its acts
    """
    date_str = target_date.strftime("%Y-%m-%d")
    series_label = _SERIES_LABEL[series]
    results = []

    # Step 1 — list of Diários for this date
    try:
        raw = _post(DRE_EP_CALENDAR, _calendar_payload(date_str))
        cal_data = _parse_json_out(raw)
    except Exception as e:
        log.warning("DRE calendar error (series=%s, date=%s): %s", series, date_str, e)
        return []

    if not cal_data:
        # diariodarepublica.pt currently returns empty data if versionInfo is outdated.
        # Keep the scraper running (RSS fallback for today), but surface a clear hint.
        vi = raw.get("versionInfo", {}) if isinstance(raw, dict) else {}
        if vi.get("hasApiVersionChanged") is True:
            log.warning(
                "DRE API version mismatch (hasApiVersionChanged=true). "
                "Set DRE_VERSION_INFO_JSON from the browser network payload to restore full results."
            )
        return []

    hits = cal_data.get("hits", {}).get("hits", [])
    if not hits:
        log.info("Series %s — %s: no Diários published", series, date_str)
        return []

    # Step 2 — acts for each matching Diário
    for hit in hits:
        src = hit.get("_source", {})
        title = src.get("conteudoTitle", "")
        if series_label not in title:
            continue

        diario_id = src.get("dbId")
        if not diario_id:
            continue

        try:
            raw2 = _post(DRE_EP_DIPLOMAS, _diplomas_payload(diario_id))
            dip_data = _parse_json_out(raw2)
        except Exception as e:
            log.warning("DRE diplomas error (diario_id=%s): %s", diario_id, e)
            continue

        acts = dip_data.get("hits", {}).get("hits", [])
        for act in acts:
            s = act.get("_source", {})
            doc_id   = str(s.get("dbId") or act.get("_id", "")).split("_")[0]
            title_   = (s.get("titulo") or s.get("title") or "").strip()
            summary  = (s.get("sumario") or s.get("summary") or "").strip()
            doc_type = (s.get("tipo") or s.get("tipoDoc") or "").strip()
            issuer   = (s.get("emissor") or s.get("entidade") or "").strip()
            number   = str(s.get("numero") or s.get("number") or "").strip()
            url      = f"{DRE_BASE}/dre/legislacao/{doc_id}" if doc_id else DRE_BASE

            results.append({
                "source": "dre",
                "series": f"{series}ª Série",
                "source_id": doc_id,
                "date": date_str,
                "type": doc_type,
                "number": number,
                "issuer": issuer,
                "title": title_,
                "summary": summary,
                "url": url,
                "eli": None,
                "full_text": f"{title_} {summary} {doc_type} {issuer}",
            })

    log.info("Series %s — %s: %d items", series, date_str, len(results))
    return results


# ── RSS feed (fallback for today) ─────────────────────────────────────────────

def fetch_rss_day(target_date: date, series: int) -> list[dict]:
    """
    Fallback: fetch from the RSS feed (only contains the most recent publication).
    Used when the POST API returns nothing for today.
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
        desc_el  = item.find("description")
        link_el  = item.find("link")

        title_text = (title_el.text or "").strip() if title_el is not None else ""
        desc_text  = (desc_el.text  or "").strip() if desc_el  is not None else ""
        link_text  = (link_el.text  or "").strip() if link_el  is not None else ""

        if date_str not in title_text:
            continue

        doc_type = ""
        number   = ""
        type_match = re.match(r'^([^-–]+?)(?:\s*[-–])', title_text)
        if type_match:
            type_and_num = type_match.group(1).strip()
            num_match = re.match(r'^(.+?)\s+n\.º?\s*(.+)$', type_and_num, re.IGNORECASE)
            if num_match:
                doc_type = num_match.group(1).strip()
                number   = num_match.group(2).strip()
            else:
                doc_type = type_and_num

        summary = re.sub(r'<[^>]+>', ' ', desc_text).strip()
        summary = re.sub(r'\s+', ' ', summary)

        sid = re.search(r'/(\d+)/?$', link_text)
        source_id = sid.group(1) if sid else hashlib.md5(link_text.encode()).hexdigest()

        results.append({
            "source": "dre-rss",
            "series": f"{series}ª Série",
            "source_id": str(source_id),
            "date": date_str,
            "type": doc_type,
            "number": number,
            "issuer": "",
            "title": title_text,
            "summary": summary,
            "url": link_text,
            "eli": None,
            "full_text": f"{title_text} {summary} {doc_type}",
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

    clients      = load_keywords()
    existing     = load_existing_results()
    existing_ids = {e["id"] for e in existing["entries"]}
    all_new      = []

    for current_date in date_range:
        log.info("── Scraping %s ──", current_date)
        is_today = (current_date == date.today())

        for series in [1, 2]:
            items = fetch_dre_day(current_date, series)

            # For today, fall back to RSS if the API returned nothing yet
            if not items and is_today:
                log.info("Falling back to RSS for series %s on %s", series, current_date)
                items = fetch_rss_day(current_date, series)

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
                    "content_kind": "act",
                    "number": item["number"],
                    "issuer": item["issuer"],
                    "title": item["title"],
                    "summary": item["summary"],
                    "url": item["url"],
                    "clients": matched,
                    "scraped_at": datetime.now(tz=timezone.utc).isoformat(),
                }
                all_new.append(entry)
                existing_ids.add(eid)

    log.info("Found %d new relevant entries across %d days", len(all_new), days)

    existing["entries"]      = all_new + existing["entries"]
    existing["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
    existing["entry_count"]  = len(existing["entries"])

    save_results(existing)
    return all_new


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
