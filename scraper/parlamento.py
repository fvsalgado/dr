#!/usr/bin/env python3
"""
Scraper do Parlamento Português:
  1. Agenda Parlamentar  — agenda.parlamento.pt (com pesquisa por intervalo de datas)
  2. Últimas Iniciativas — parlamento.pt/Paginas/UltimasIniciativasEntradas.aspx
"""

import json
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
log = logging.getLogger("parlamento_scraper")

BASE_DIR = Path(__file__).parent.parent
KEYWORDS_FILE = BASE_DIR / "keywords" / "clients.json"
DATA_FILE = BASE_DIR / "data" / "results.json"
DATA_FILE.parent.mkdir(exist_ok=True)

AGENDA_BASE = "https://agenda.parlamento.pt"
AGENDA_SECTION = f"{AGENDA_BASE}/Index?handler=SectionContents"
AGENDA_SEARCH = f"{AGENDA_BASE}/Index?handler=SearchContents"

INICIATIVAS_URL = "https://www.parlamento.pt/Paginas/UltimasIniciativasEntradas.aspx"
INICIATIVA_DETAIL_URL = "https://www.parlamento.pt/ActividadeParlamentar/Paginas/DetalheIniciativa.aspx"
PARLAMENTO_BASE = "https://www.parlamento.pt"

_HEADERS = {
    "User-Agent": "PublicAffairsMonitor/1.0",
    "Accept": "text/html, application/xhtml+xml, */*",
}


# ── helpers ──────────────────────────────────────────────────────────────────

def _get(url: str, params: dict | None = None, extra_headers: dict | None = None) -> str:
    """GET a URL and return the response body as text."""
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    headers = {**_HEADERS, **(extra_headers or {})}
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


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


def entry_id(source: str, source_id: str) -> str:
    key = f"{source}_{source_id}"
    return hashlib.md5(key.encode()).hexdigest()


def strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    clean = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', clean).strip()


# ── keyword matching (reuses dre.py logic) ───────────────────────────────────

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


# ── Agenda Parlamentar ───────────────────────────────────────────────────────

def _parse_agenda_html(html: str) -> list[dict]:
    """
    Parse the agenda HTML returned by SectionContents or SearchContents.
    Events are in collapsible cards with id="collapse_<N>" containing
    date, time, title (committee/section), description, location.
    """
    results = []

    # Each agenda item is wrapped in a collapse div: <div id="collapse_XXXXX" ...>
    # preceded by a card-header with the event title
    # Strategy: split by card_ blocks and extract content from each

    # Pattern 1: Find card blocks by card_<id> or collapse_<id>
    card_pattern = re.compile(
        r'<div[^>]*\bid=["\'](?:card|collapse)_(\d+)["\'][^>]*>'
        r'(.*?)'
        r'(?=<div[^>]*\bid=["\'](?:card|collapse)_\d+["\']|$)',
        re.DOTALL | re.IGNORECASE,
    )

    for m in card_pattern.finditer(html):
        card_id = m.group(1)
        block = m.group(2)

        # Skip blocks that are too short (just wrappers)
        if len(block.strip()) < 30:
            continue

        # ── Extract event title (committee name, event name) ──
        title = ""
        # Try card-header, h5, h6, strong, then any link text
        for tp in [
            r'<div[^>]*class=["\'][^"\']*card-header[^"\']*["\'][^>]*>(.*?)</div>',
            r'<(?:h5|h6)[^>]*>(.*?)</(?:h5|h6)>',
            r'<a[^>]*class=["\'][^"\']*content-title[^"\']*["\'][^>]*>(.*?)</a>',
            r'<strong>(.*?)</strong>',
            r'<a[^>]*>(.*?)</a>',
        ]:
            tm = re.search(tp, block, re.DOTALL | re.IGNORECASE)
            if tm:
                candidate = strip_html(tm.group(1))
                if len(candidate) > 3 and candidate not in ("Expandir", "Esconder"):
                    title = candidate
                    break

        # ── Extract description / summary ──
        # Collect ALL text content from <p>, <li>, <span> elements
        desc_parts = []

        # Look for explicit description containers
        for dp in [
            r'<(?:p|div)[^>]*class=["\'][^"\']*(?:content-desc|content-summ|card-text|content-body)[^"\']*["\'][^>]*>(.*?)</(?:p|div)>',
            r'<div[^>]*class=["\'][^"\']*card-body[^"\']*["\'][^>]*>(.*?)</div>',
        ]:
            for dm in re.finditer(dp, block, re.DOTALL | re.IGNORECASE):
                text = strip_html(dm.group(1))
                if len(text) > 10 and text != title:
                    desc_parts.append(text)

        # Also grab list items (agenda items often listed as <li>)
        for li_m in re.finditer(r'<li[^>]*>(.*?)</li>', block, re.DOTALL | re.IGNORECASE):
            text = strip_html(li_m.group(1))
            if len(text) > 10:
                desc_parts.append(text)

        # Grab all <p> content as fallback
        if not desc_parts:
            for pm in re.finditer(r'<p[^>]*>(.*?)</p>', block, re.DOTALL | re.IGNORECASE):
                text = strip_html(pm.group(1))
                if len(text) > 10 and text != title:
                    desc_parts.append(text)

        desc = " | ".join(desc_parts) if desc_parts else ""

        # ── Extract time ──
        time_str = ""
        time_m = re.search(
            r'<(?:span|small|div)[^>]*class=["\'][^"\']*(?:content-time|badge)[^"\']*["\'][^>]*>(.*?)</(?:span|small|div)>',
            block, re.DOTALL | re.IGNORECASE,
        )
        if time_m:
            time_str = strip_html(time_m.group(1))
        if not time_str:
            # Try to find time patterns like HH:MM or HHhMM
            time_m2 = re.search(r'\b(\d{1,2}[h:]\d{2})\b', block)
            if time_m2:
                time_str = time_m2.group(1)

        # ── Extract date from the block ──
        date_str = ""
        # Look for dateContent_ divs or date patterns
        date_m = re.search(r'<div[^>]*id=["\']dateContent_[^"\']*["\'][^>]*>(.*?)</div>', block, re.DOTALL)
        if date_m:
            date_text = strip_html(date_m.group(1))
            # Try to parse Portuguese date: "terça, 3 de março de 2026"
            iso_m = re.search(r'(\d{4})-(\d{2})-(\d{2})', date_text)
            if iso_m:
                date_str = iso_m.group(0)
        # Try startDate JS variables
        if not date_str:
            sdate_m = re.search(r'_startDate\s*=\s*["\'](\d{4}-\d{2}-\d{2})', block)
            if sdate_m:
                date_str = sdate_m.group(1)

        # ── Extract location ──
        location = ""
        loc_m = re.search(
            r'<(?:span|small|div)[^>]*class=["\'][^"\']*(?:content-local|location)[^"\']*["\'][^>]*>(.*?)</(?:span|small|div)>',
            block, re.DOTALL | re.IGNORECASE,
        )
        if loc_m:
            location = strip_html(loc_m.group(1))
        # Fallback: look for "Local:" or "Sala" patterns
        if not location:
            loc_m2 = re.search(r'(?:Local|Sala)[:\s]+([^<\n]+)', block, re.IGNORECASE)
            if loc_m2:
                location = strip_html(loc_m2.group(1))

        if not title and not desc:
            continue

        full_text = f"{title} {desc} {location}"
        results.append({
            "card_id": card_id,
            "date": date_str,
            "title": title or "(Sem título)",
            "summary": desc,
            "time": time_str,
            "location": location,
            "full_text": full_text,
        })

    return results


def fetch_agenda(start_date: date, end_date: date) -> list[dict]:
    """
    Fetch parliamentary agenda for a date range using the SearchContents endpoint.
    Falls back to SectionContents for today's data.
    """
    results = []
    date_str_end = end_date.isoformat()

    # Fetch with SearchContents (supports date ranges)
    log.info("Fetching Agenda Parlamentar: %s → %s", start_date, end_date)

    # Process in weekly chunks to avoid timeouts / overly large responses
    chunk_start = start_date
    while chunk_start <= end_date:
        chunk_end = min(chunk_start + timedelta(days=6), end_date)
        cs = f"{chunk_start.isoformat()} 00:00:00"
        ce = f"{chunk_end.isoformat()} 23:59:59"

        log.info("  Agenda chunk: %s → %s", chunk_start, chunk_end)

        try:
            html = _get(AGENDA_SEARCH, params={
                "contentStart": cs,
                "contentEnd": ce,
                "timezoneOffset": "0",
            })
        except Exception as e:
            log.warning("  Agenda search error (%s → %s): %s", chunk_start, chunk_end, e)
            chunk_start = chunk_end + timedelta(days=1)
            continue

        if not html or len(html.strip()) < 50:
            log.info("  Agenda: empty response for %s → %s", chunk_start, chunk_end)
            chunk_start = chunk_end + timedelta(days=1)
            continue

        events = _parse_agenda_html(html)
        for ev in events:
            # Use chunk date range if no date was extracted
            if not ev["date"]:
                ev["date"] = chunk_start.isoformat()
            results.append({
                "source": "parlamento-agenda",
                "source_id": ev["card_id"],
                "date": ev["date"],
                "type": "Agenda Parlamentar",
            "content_kind": "event",
                "number": "",
                "issuer": "",
                "title": ev["title"],
                "summary": ev["summary"],
                "url": AGENDA_BASE,
                "full_text": ev["full_text"],
            })

        log.info("  Agenda chunk: %d events", len(events))
        chunk_start = chunk_end + timedelta(days=1)

    # Also fetch today's SectionContents as a supplement (more reliable for today)
    if end_date >= date.today():
        try:
            html = _get(AGENDA_SECTION)
            if html and len(html.strip()) >= 50:
                today_events = _parse_agenda_html(html)
                existing_ids = {r["source_id"] for r in results}
                for ev in today_events:
                    if ev["card_id"] not in existing_ids:
                        if not ev["date"]:
                            ev["date"] = date.today().isoformat()
                        results.append({
                            "source": "parlamento-agenda",
                            "source_id": ev["card_id"],
                            "date": ev["date"],
                            "type": "Agenda Parlamentar",
                            "content_kind": "event",
                            "number": "",
                            "issuer": "",
                            "title": ev["title"],
                            "summary": ev["summary"],
                            "url": AGENDA_BASE,
                            "full_text": ev["full_text"],
                        })
                log.info("  Agenda today supplement: +%d events", len(today_events))
        except Exception as e:
            log.warning("  Agenda SectionContents fallback error: %s", e)

    log.info("Agenda Parlamentar: %d total events", len(results))
    return results


# ── Últimas Iniciativas ──────────────────────────────────────────────────────

def _fetch_iniciativa_detail(bid: str) -> str:
    """
    Fetch the detail page for a single initiative and extract additional text.
    Returns the full text content found on the page.
    """
    url = f"{INICIATIVA_DETAIL_URL}?BID={bid}"
    try:
        html = _get(url)
    except Exception as e:
        log.debug("  Detail fetch error (BID=%s): %s", bid, e)
        return ""

    # Extract text from the main content area
    # The detail page has metadata in a table/list format
    text_parts = []

    # Title/description area
    for pattern in [
        r'<div[^>]*class=["\'][^"\']*ms-rtestate-field[^"\']*["\'][^>]*>(.*?)</div>',
        r'<td[^>]*class=["\'][^"\']*ms-vb[^"\']*["\'][^>]*>(.*?)</td>',
        r'<div[^>]*id=["\'][^"\']*WebPart[^"\']*["\'][^>]*>(.*?)</div>',
    ]:
        for m in re.finditer(pattern, html, re.DOTALL | re.IGNORECASE):
            text = strip_html(m.group(1))
            if len(text) > 15:
                text_parts.append(text)

    return " ".join(text_parts)


def fetch_iniciativas(start_date: date, end_date: date, fetch_details: bool = True) -> list[dict]:
    """
    Fetch the latest parliamentary initiatives from parlamento.pt.
    Filters by date range and optionally fetches detail pages.
    """
    log.info("Fetching Últimas Iniciativas: %s → %s", start_date, end_date)

    try:
        html = _get(INICIATIVAS_URL)
    except Exception as e:
        log.warning("Iniciativas fetch error: %s", e)
        return []

    results = []

    # Pattern to match each initiative block
    block_pattern = re.compile(
        r'<div[^>]*class=["\'][^"\']*hc-detail[^"\']*["\'][^>]*>'
        r'(.*?)'
        r'(?=<div[^>]*class=["\'][^"\']*hc-detail[^"\']*["\']|</div>\s*</div>\s*</div>\s*$)',
        re.DOTALL | re.IGNORECASE,
    )

    for m in block_pattern.finditer(html):
        block = m.group(1)

        # ── Extract date ──
        day_month = ""
        year = ""
        dm = re.search(r'<p[^>]*class=["\']date["\'][^>]*>\s*([\d.]+)\s*</p>', block, re.IGNORECASE)
        ym = re.search(r'<p[^>]*class=["\']time["\'][^>]*>\s*(\d{4})\s*</p>', block, re.IGNORECASE)
        if dm:
            day_month = dm.group(1).strip()
        if ym:
            year = ym.group(1).strip()

        date_str = ""
        if day_month and year:
            parts = day_month.split(".")
            if len(parts) == 2:
                date_str = f"{year}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"

        # Filter by date range
        if date_str:
            try:
                entry_date = date.fromisoformat(date_str)
                if entry_date < start_date or entry_date > end_date:
                    continue
            except ValueError:
                pass

        # ── Extract link, title ──
        link = ""
        title = ""
        link_m = re.search(
            r'<a[^>]*href=["\']([^"\']*DetalheIniciativa[^"\']*)["\'][^>]*>.*?'
            r'<p[^>]*class=["\']title["\'][^>]*>\s*(.*?)\s*</p>',
            block, re.DOTALL | re.IGNORECASE,
        )
        if link_m:
            link = link_m.group(1).strip()
            title = strip_html(link_m.group(2))
            if link.startswith("/"):
                link = PARLAMENTO_BASE + link

        # ── Extract description from <p class="desc"> ──
        desc = ""
        desc_m = re.search(r'<p[^>]*class=["\']desc["\'][^>]*>\s*(.*?)\s*</p>', block, re.DOTALL | re.IGNORECASE)
        if desc_m:
            desc = strip_html(desc_m.group(1))

        # ── Extract extended description from <a title="..."> attribute ──
        # Some initiatives have a longer description in the title attribute
        extended_desc = ""
        title_attr_m = re.search(r'<a[^>]*title=[\'"]([^\'"]{40,})[\'"]', block, re.IGNORECASE)
        if title_attr_m:
            extended_desc = title_attr_m.group(1).strip()

        # Use the longest description available
        if extended_desc and len(extended_desc) > len(desc):
            desc = extended_desc

        # ── Extract BID for dedup and detail fetching ──
        bid = ""
        bid_m = re.search(r'BID=(\d+)', link)
        if bid_m:
            bid = bid_m.group(1)

        if not title and not desc:
            continue

        # ── Extract party from title ──
        party = ""
        party_m = re.search(r'\[([^\]]+)\]\s*$', title)
        if party_m:
            party = party_m.group(1)

        # ── Extract initiative type ──
        ini_type = ""
        type_m = re.search(
            r'^(Projeto de (?:Lei|Resolução)|Proposta de Lei|Projeto de Voto|Petição)',
            title, re.IGNORECASE,
        )
        if type_m:
            ini_type = type_m.group(1)

        full_text = f"{title} {desc} {party}"
        source_id = bid or hashlib.md5(f"{title}_{date_str}".encode()).hexdigest()

        results.append({
            "source": "parlamento-iniciativas",
            "source_id": source_id,
            "bid": bid,
            "date": date_str,
            "type": ini_type or "Iniciativa Parlamentar",
            "content_kind": "initiative",
            "number": "",
            "issuer": f"[{party}]" if party else "",
            "title": title,
            "summary": desc,
            "url": link or INICIATIVAS_URL,
            "full_text": full_text,
        })

    log.info("Últimas Iniciativas: %d items in date range", len(results))

    # Fetch detail pages for extra content (authors, attachments, etc.)
    if fetch_details:
        detail_count = 0
        for item in results:
            bid = item.get("bid", "")
            if not bid:
                continue
            detail_text = _fetch_iniciativa_detail(bid)
            if detail_text:
                # Append detail text to summary and full_text for richer matching
                if detail_text not in item["summary"]:
                    if item["summary"]:
                        item["summary"] = item["summary"] + " — " + detail_text[:500]
                    else:
                        item["summary"] = detail_text[:500]
                    item["full_text"] = f"{item['title']} {item['summary']} {item.get('issuer', '')}"
                detail_count += 1
        log.info("  Fetched %d detail pages", detail_count)

    return results


# ── main ─────────────────────────────────────────────────────────────────────

def run(target_date: date | None = None, days: int = 30):
    """
    Scrape both Agenda Parlamentar and Últimas Iniciativas for the last N days.
    Results are merged into the shared data/results.json.
    """
    if target_date is None:
        target_date = date.today()

    start_date = target_date - timedelta(days=days - 1)

    log.info(
        "Running Parlamento scraper for %d days: %s → %s",
        days, start_date, target_date,
    )

    clients = load_keywords()
    existing = load_existing_results()
    existing_ids = {e["id"] for e in existing["entries"]}
    all_new = []

    # 1. Agenda Parlamentar (with date range search)
    agenda_items = fetch_agenda(start_date, target_date)
    for item in agenda_items:
        matched = match_clients(item["full_text"], clients)
        if not matched:
            continue

        eid = entry_id(item["source"], item["source_id"])
        if eid in existing_ids:
            continue

        entry = {
            "id": eid,
            "source": item["source"],
            "series": "",
            "date": item["date"],
            "type": item["type"],
            "content_kind": item.get("content_kind", "event"),
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

    # 2. Últimas Iniciativas (with date filtering and detail pages)
    ini_items = fetch_iniciativas(start_date, target_date)
    for item in ini_items:
        matched = match_clients(item["full_text"], clients)
        if not matched:
            continue

        eid = entry_id(item["source"], item["source_id"])
        if eid in existing_ids:
            continue

        entry = {
            "id": eid,
            "source": item["source"],
            "series": "",
            "date": item["date"],
            "type": item["type"],
            "content_kind": item.get("content_kind", "initiative"),
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

    log.info("Found %d new relevant Parlamento entries", len(all_new))

    existing["entries"] = all_new + existing["entries"]
    existing["last_updated"] = datetime.now(tz=timezone.utc).isoformat()
    existing["entry_count"] = len(existing["entries"])

    save_results(existing)
    return all_new


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape Parlamento (Agenda + Iniciativas)")
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
