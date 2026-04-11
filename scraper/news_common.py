#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import logging
import time
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timezone
from email.utils import parsedate_to_datetime

log = logging.getLogger("news_common")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PublicAffairsMonitor/1.1; +https://github.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.5",
}

# Word "opinião" in titles (Portuguese letters); mirrors JS isOpinionTitle for dashboard.
_OPINION_TITLE_RE = re.compile(
    r"(?<![0-9A-Za-záàãâéèêíìîóòõôúùûçñÁÀÃÂÉÈÊÍÌÎÓÒÕÔÚÙÛÇÑ])"
    r"opinião"
    r"(?![0-9A-Za-záàãâéèêíìîóòõôúùûçñÁÀÃÂÉÈÊÍÌÎÓÒÕÔÚÙÛÇÑ])",
    re.IGNORECASE,
)


def is_opinion_title(title: str) -> bool:
    t = (title or "").strip()
    return bool(t and _OPINION_TITLE_RE.search(t))


def apply_opinion_rubric(item: dict) -> dict:
    """Set article_section and type when the headline is an opinion piece."""
    out = dict(item)
    if is_opinion_title(out.get("title", "")):
        out["article_section"] = "Opinião"
        out["type"] = "Opinião"
    return out


def canonical_url(url: str) -> str:
    """Normalize URL for duplicate detection (fragment stripped, host lowercased, trailing slash on path)."""
    raw = (url or "").strip()
    if not raw:
        return ""
    raw, _frag = urllib.parse.urldefrag(raw)
    p = urllib.parse.urlparse(raw)
    if not p.netloc:
        return raw
    scheme = (p.scheme or "https").lower()
    netloc = p.netloc.lower()
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urllib.parse.urlunparse((scheme, netloc, path, p.params, p.query, ""))


def _decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def get_html(url: str, fallback_urls: list[str] | None = None, retries: int = 2, timeout_s: int = 30) -> str:
    urls = [url] + [u for u in (fallback_urls or []) if u and u != url]
    last_exc: Exception | None = None
    for current_url in urls:
        for attempt in range(retries + 1):
            req = urllib.request.Request(current_url, headers=_HEADERS, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    return _decode_bytes(resp.read())
            except Exception as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(min(6.0, 0.6 * (2**attempt)))
                    continue
                break
    if last_exc:
        raise last_exc
    raise RuntimeError("Could not fetch any URL")


def strip_html(text: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_url(base_url: str, href: str) -> str:
    return urllib.parse.urljoin(base_url, href.strip())


def link_id(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def listing_items(base_url: str, html_text: str, limit: int = 80) -> list[tuple[str, str]]:
    seen: set[str] = set()
    items: list[tuple[str, str]] = []
    for m in re.finditer(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html_text, re.IGNORECASE | re.DOTALL):
        href = m.group(1).strip()
        if not href or href.startswith("#") or href.startswith("javascript:") or href.startswith("mailto:"):
            continue
        title = strip_html(m.group(2))
        if len(title) < 20:
            continue
        url = normalize_url(base_url, href)
        if url in seen:
            continue
        if any(x in url.lower() for x in ("/tag/", "/categoria/", "/category/", "/autor/", "/author/")):
            continue
        seen.add(url)
        items.append((title, url))
        if len(items) >= limit:
            break
    return items


def make_news_item(
    source: str,
    issuer: str,
    title: str,
    url: str,
    summary: str = "",
    *,
    item_date: str | None = None,
    published_at: str = "",
) -> dict:
    full_text = f"{title} {summary} {issuer}".strip()
    return {
        "source": source,
        "source_id": link_id(url),
        "date": item_date or date.today().isoformat(),
        "series": "NEWS",
        "type": "Notícia",
        "number": "",
        "issuer": issuer,
        "title": title,
        "summary": summary,
        "url": url,
        "full_text": full_text,
        "published_at": published_at,
    }


def _xml_local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def _rss_child_text(parent: ET.Element, *local_names: str) -> str:
    want = set(local_names)
    for ch in parent:
        if _xml_local_name(ch.tag) not in want:
            continue
        parts = []
        if ch.text:
            parts.append(ch.text.strip())
        for sub in ch:
            if sub.text:
                parts.append(sub.text.strip())
            if sub.tail:
                parts.append(sub.tail.strip())
        inner = " ".join(p for p in parts if p).strip()
        if inner:
            return inner
    return ""


def rfc2822_to_iso_utc(pub: str) -> str:
    pub = (pub or "").strip()
    if not pub:
        return ""
    try:
        dt = parsedate_to_datetime(pub)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def fetch_rss_xml(url: str, retries: int = 2, timeout_s: int = 35) -> str:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=_HEADERS, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read()
            for enc in ("utf-8-sig", "utf-8", "iso-8859-1", "windows-1252"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(min(6.0, 0.6 * (2**attempt)))
                continue
            break
    if last_exc:
        raise last_exc
    raise RuntimeError("fetch_rss_xml: no response")


def parse_rss_channel_items(
    xml_text: str,
    *,
    link_must_contain: str,
    limit: int,
) -> list[tuple[str, str, str, str]]:
    """Return (title, link, summary_plain, published_iso_utc)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning("RSS parse error: %s", e)
        return []

    channel = None
    if _xml_local_name(root.tag) == "rss":
        for ch in root:
            if _xml_local_name(ch.tag) == "channel":
                channel = ch
                break
    elif _xml_local_name(root.tag) == "feed":
        channel = root

    if channel is None:
        return []

    out: list[tuple[str, str, str, str]] = []
    needle = (link_must_contain or "").lower()

    for el in channel:
        if _xml_local_name(el.tag) != "item":
            continue
        title = _rss_child_text(el, "title")
        link = _rss_child_text(el, "link")
        if not link:
            guid = _rss_child_text(el, "guid")
            if guid.startswith("http://") or guid.startswith("https://"):
                link = guid
            else:
                for ch in el:
                    if _xml_local_name(ch.tag) != "link":
                        continue
                    href = ch.attrib.get("href", "").strip()
                    if href:
                        link = href
                        break
        desc = _rss_child_text(el, "description", "encoded", "summary")
        summary = strip_html(desc)[:2000] if desc else ""
        pub_raw = _rss_child_text(el, "pubDate", "published", "updated")
        pub_iso = rfc2822_to_iso_utc(pub_raw) if pub_raw else ""

        if len(title) < 8 or not link:
            continue
        if needle and needle not in link.lower():
            continue
        out.append((title.strip(), link.strip().split("#")[0], summary.strip(), pub_iso))
        if len(out) >= limit:
            break

    return out


def fetch_rss_as_news_items(
    feed_url: str,
    *,
    source: str,
    issuer: str,
    link_must_contain: str,
    limit: int = 80,
) -> list[dict]:
    xml_text = fetch_rss_xml(feed_url)
    rows = parse_rss_channel_items(
        xml_text, link_must_contain=link_must_contain, limit=limit * 2
    )
    items: list[dict] = []
    seen: set[str] = set()
    for title, link, summary, pub_iso in rows:
        curl = canonical_url(link)
        if link in seen or (curl and curl in seen):
            continue
        seen.add(link)
        if curl:
            seen.add(curl)
        d = pub_iso[:10] if len(pub_iso) >= 10 else date.today().isoformat()
        items.append(
            make_news_item(
                source,
                issuer,
                title,
                link,
                summary=summary,
                item_date=d,
                published_at=pub_iso,
            )
        )
        if len(items) >= limit:
            break
    return items


def extract_article_meta(html_text: str) -> dict:
    def _meta(prop: str) -> str:
        m = re.search(
            rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)["\']',
            html_text,
            re.IGNORECASE,
        )
        return html.unescape(m.group(1).strip()) if m else ""

    def _meta_content_first(prop: str) -> str:
        m = re.search(
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(prop)}["\']',
            html_text,
            re.IGNORECASE,
        )
        return html.unescape(m.group(1).strip()) if m else ""

    image_url = _meta("og:image") or _meta("twitter:image") or _meta_content_first("og:image")
    json_ld_pub = ""
    m_ld = re.search(
        r'"datePublished"\s*:\s*"([^"]+)"',
        html_text,
        re.IGNORECASE,
    )
    if m_ld:
        json_ld_pub = html.unescape(m_ld.group(1).strip())
    published_at = (
        _meta("article:published_time")
        or _meta("date")
        or _meta("publish_date")
        or json_ld_pub
    )
    article_section = _meta("article:section")
    description = _meta("og:description") or _meta("description") or _meta_content_first("og:description")
    description = strip_html(description)[:2000] if description else ""
    return {
        "image_url": image_url,
        "published_at": published_at,
        "article_section": article_section,
        "description": description,
    }

