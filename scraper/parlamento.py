#!/usr/bin/env python3
"""
Scraper do Parlamento Português:
  1. Agenda Parlamentar  — agenda.parlamento.pt
  2. Últimas Iniciativas — parlamento.pt/Paginas/UltimasIniciativasEntradas.aspx
"""

import json
import re
import sys
import hashlib
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from html.parser import HTMLParser
import urllib.request
import urllib.error

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("parlamento_scraper")

BASE_DIR = Path(__file__).parent.parent
KEYWORDS_FILE = BASE_DIR / "keywords" / "clients.json"
DATA_FILE = BASE_DIR / "data" / "results.json"
DATA_FILE.parent.mkdir(exist_ok=True)

AGENDA_BASE = "https://agenda.parlamento.pt"
AGENDA_SECTION = f"{AGENDA_BASE}/Index?handler=SectionContents"

INICIATIVAS_URL = "https://www.parlamento.pt/Paginas/UltimasIniciativasEntradas.aspx"
PARLAMENTO_BASE = "https://www.parlamento.pt"

_HEADERS = {
    "User-Agent": "PublicAffairsMonitor/1.0",
    "Accept": "text/html, application/xhtml+xml, */*",
}


# ── helpers ──────────────────────────────────────────────────────────────────

def _get(url: str, extra_headers: dict | None = None) -> str:
    """GET a URL and return the response body as text."""
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


# ── Agenda Parlamentar parser ────────────────────────────────────────────────

class AgendaParser(HTMLParser):
    """
    Parses the HTML fragment returned by /Index?handler=SectionContents.
    Each event is a card with id="card_<N>" containing:
      - section name (data-section attr or header text)
      - time, title, description, location
    """

    def __init__(self):
        super().__init__()
        self.events = []
        self._current = None
        self._capture = None
        self._depth = 0
        self._in_card = False
        self._card_section = ""
        self._buf = []

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)

        # Detect card containers: <div id="card_123" ...>
        div_id = attr.get("id", "")
        if tag == "div" and div_id.startswith("card_"):
            self._in_card = True
            self._current = {
                "card_id": div_id.replace("card_", ""),
                "section": "",
                "time": "",
                "title": "",
                "description": "",
                "location": "",
            }

        # Section headers: <div class="..." data-section="...">
        if tag == "div" and "data-section" in attr:
            self._card_section = attr.get("data-section", "")

        if not self._in_card:
            return

        cls = attr.get("class", "")

        # Time: usually in a <span> or <small> with specific classes
        if tag in ("span", "small") and ("content-time" in cls or "badge" in cls):
            self._capture = "time"
            self._buf = []

        # Title: <h5>, <h6>, or <a> with card-title-like classes
        if tag in ("h5", "h6") and "card" in cls:
            self._capture = "title"
            self._buf = []
        if tag == "a" and "content-title" in cls:
            self._capture = "title"
            self._buf = []

        # Description: <p> or <div> with description/summary class
        if tag in ("p", "div") and ("content-desc" in cls or "content-summ" in cls):
            self._capture = "description"
            self._buf = []

    def handle_endtag(self, tag):
        if self._capture and tag in ("span", "small", "h5", "h6", "a", "p", "div"):
            text = " ".join(self._buf).strip()
            if text and self._current:
                if not self._current[self._capture]:
                    self._current[self._capture] = text
            self._capture = None
            self._buf = []

        # End of card
        if tag == "div" and self._in_card and self._current:
            # We detect card end heuristically — when we see a full card
            pass

    def handle_data(self, data):
        if self._capture:
            self._buf.append(data.strip())

    def close(self):
        super().close()
        # Flush any pending card
        if self._current and (self._current.get("title") or self._current.get("description")):
            self._current["section"] = self._card_section
            self.events.append(self._current)


def fetch_agenda(target_date: date) -> list[dict]:
    """
    Fetch the parliamentary agenda for today from agenda.parlamento.pt.
    The SectionContents endpoint returns the current day's agenda as HTML.
    """
    log.info("Fetching Agenda Parlamentar...")

    try:
        html = _get(AGENDA_SECTION)
    except Exception as e:
        log.warning("Agenda fetch error: %s", e)
        return []

    if not html or len(html.strip()) < 50:
        log.info("Agenda: empty response")
        return []

    # The agenda HTML contains card divs; parse them
    # Strategy: use regex to extract card blocks, then parse details
    results = []
    date_str = target_date.isoformat()

    # Find all card blocks: <div id="card_XXX" ...> ... </div>
    # Each card represents an agenda event
    card_pattern = re.compile(
        r'<div[^>]*\bid=["\']card_(\d+)["\'][^>]*>(.*?)</div>\s*</div>\s*</div>',
        re.DOTALL | re.IGNORECASE,
    )

    # Broader pattern: find all collapse sections which contain event details
    section_pattern = re.compile(
        r'<div[^>]*\bdata-section=["\'](\d+)["\'][^>]*>.*?'
        r'<div[^>]*\bclass=["\'][^"\']*card-header[^"\']*["\'][^>]*>(.*?)</div>',
        re.DOTALL | re.IGNORECASE,
    )

    # Extract individual event items from the HTML
    # Pattern: look for content items with titles and descriptions
    item_pattern = re.compile(
        r'<div[^>]*\bid=["\']card_(\d+)["\'][^>]*>'
        r'(.*?)'
        r'(?=<div[^>]*\bid=["\']card_\d+["\']|$)',
        re.DOTALL | re.IGNORECASE,
    )

    for m in item_pattern.finditer(html):
        card_id = m.group(1)
        block = m.group(2)

        # Extract title — typically in <h5>, <h6>, <a>, or <strong> tags
        title = ""
        for tp in [
            r'<(?:h5|h6)[^>]*class=["\'][^"\']*card[^"\']*["\'][^>]*>(.*?)</(?:h5|h6)>',
            r'<a[^>]*>(.*?)</a>',
            r'<strong[^>]*>(.*?)</strong>',
        ]:
            tm = re.search(tp, block, re.DOTALL | re.IGNORECASE)
            if tm:
                title = strip_html(tm.group(1))
                if len(title) > 5:
                    break

        # Extract description/summary
        desc = ""
        for dp in [
            r'<(?:p|div)[^>]*class=["\'][^"\']*(?:content-desc|content-summ|card-text)[^"\']*["\'][^>]*>(.*?)</(?:p|div)>',
            r'<p[^>]*>(.*?)</p>',
        ]:
            dm = re.search(dp, block, re.DOTALL | re.IGNORECASE)
            if dm:
                desc = strip_html(dm.group(1))
                if len(desc) > 5:
                    break

        # Extract time
        time_str = ""
        time_m = re.search(
            r'<(?:span|small|div)[^>]*class=["\'][^"\']*(?:content-time|badge)[^"\']*["\'][^>]*>(.*?)</(?:span|small|div)>',
            block, re.DOTALL | re.IGNORECASE,
        )
        if time_m:
            time_str = strip_html(time_m.group(1))

        # Extract location
        location = ""
        loc_m = re.search(
            r'<(?:span|small|div)[^>]*class=["\'][^"\']*(?:content-local|location)[^"\']*["\'][^>]*>(.*?)</(?:span|small|div)>',
            block, re.DOTALL | re.IGNORECASE,
        )
        if loc_m:
            location = strip_html(loc_m.group(1))

        if not title and not desc:
            continue

        full_text = f"{title} {desc} {location}"
        results.append({
            "source": "parlamento-agenda",
            "source_id": card_id,
            "date": date_str,
            "type": "Agenda Parlamentar",
            "number": "",
            "issuer": "",
            "title": title or "(Sem título)",
            "summary": desc,
            "time": time_str,
            "location": location,
            "url": AGENDA_BASE,
            "full_text": full_text,
        })

    log.info("Agenda Parlamentar: %d events found", len(results))
    return results


# ── Últimas Iniciativas parser ───────────────────────────────────────────────

def fetch_iniciativas() -> list[dict]:
    """
    Fetch the latest parliamentary initiatives from parlamento.pt.
    The page is server-rendered SharePoint HTML with a repeating structure:
      <div class="row home_calendar hc-detail">
        <div class="col-xs-2"><p class="date">DD.MM</p><p class="time">YYYY</p></div>
        <div class="col-xs-10">
          <a href="...DetalheIniciativa.aspx?BID=NNNN"><p class="title">...</p></a>
          <p class="desc">...</p>
        </div>
      </div>
    """
    log.info("Fetching Últimas Iniciativas...")

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

        # Extract date: <p class="date">DD.MM</p> and <p class="time">YYYY</p>
        day_month = ""
        year = ""
        dm = re.search(r'<p[^>]*class=["\']date["\'][^>]*>\s*([\d.]+)\s*</p>', block, re.IGNORECASE)
        ym = re.search(r'<p[^>]*class=["\']time["\'][^>]*>\s*(\d{4})\s*</p>', block, re.IGNORECASE)
        if dm:
            day_month = dm.group(1).strip()
        if ym:
            year = ym.group(1).strip()

        # Build ISO date
        date_str = ""
        if day_month and year:
            parts = day_month.split(".")
            if len(parts) == 2:
                date_str = f"{year}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"

        # Extract link and title
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

        # Also try title from the <a> title attribute
        if not title:
            title_attr_m = re.search(r"<a[^>]*title=['\"]([^'\"]+)['\"]", block, re.IGNORECASE)
            if title_attr_m:
                t = title_attr_m.group(1).strip()
                if t != "Detalhe de Iniciativa":
                    title = t

        # Extract description
        desc = ""
        desc_m = re.search(r'<p[^>]*class=["\']desc["\'][^>]*>\s*(.*?)\s*</p>', block, re.DOTALL | re.IGNORECASE)
        if desc_m:
            desc = strip_html(desc_m.group(1))

        # Extract BID from URL for dedup
        bid = ""
        bid_m = re.search(r'BID=(\d+)', link)
        if bid_m:
            bid = bid_m.group(1)

        if not title and not desc:
            continue

        # Extract party from title: e.g. "Projeto de Lei 553/XVII/1 [L]" -> "[L]"
        party = ""
        party_m = re.search(r'\[([^\]]+)\]\s*$', title)
        if party_m:
            party = party_m.group(1)

        # Extract initiative type from title: e.g. "Projeto de Lei", "Proposta de Lei"
        ini_type = ""
        type_m = re.search(r'^(Projeto de (?:Lei|Resolução)|Proposta de Lei|Projeto de Voto|Petição)', title, re.IGNORECASE)
        if type_m:
            ini_type = type_m.group(1)

        full_text = f"{title} {desc} {party}"
        source_id = bid or hashlib.md5(f"{title}_{date_str}".encode()).hexdigest()

        results.append({
            "source": "parlamento-iniciativas",
            "source_id": source_id,
            "date": date_str,
            "type": ini_type or "Iniciativa Parlamentar",
            "number": "",
            "issuer": f"[{party}]" if party else "",
            "title": title,
            "summary": desc,
            "url": link or INICIATIVAS_URL,
            "full_text": full_text,
        })

    log.info("Últimas Iniciativas: %d items found", len(results))
    return results


# ── main ─────────────────────────────────────────────────────────────────────

def run(target_date: date | None = None):
    """
    Scrape both Agenda Parlamentar and Últimas Iniciativas.
    Results are merged into the shared data/results.json.
    """
    if target_date is None:
        target_date = date.today()

    log.info("Running Parlamento scraper for %s", target_date)

    clients = load_keywords()
    existing = load_existing_results()
    existing_ids = {e["id"] for e in existing["entries"]}
    all_new = []

    # 1. Agenda Parlamentar (only has today's data)
    agenda_items = fetch_agenda(target_date)
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

    # 2. Últimas Iniciativas
    ini_items = fetch_iniciativas()
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
        help="Target date in YYYY-MM-DD format (default: today)",
    )
    args = parser.parse_args()

    target = None
    if args.date:
        try:
            target = date.fromisoformat(args.date)
        except ValueError:
            log.error("Invalid date: %s. Use YYYY-MM-DD.", args.date)
            sys.exit(1)

    new = run(target_date=target)
    print(json.dumps({"new_entries": len(new)}, ensure_ascii=False))
