#!/usr/bin/env python3
"""
Envia o digest diário via Mailgun.
Variáveis de ambiente necessárias:
  MAILGUN_API_KEY  — chave da API Mailgun
  MAILGUN_DOMAIN   — domínio Mailgun (ex: mg.teudominio.pt)
  EMAIL_FROM       — endereço de envio
  EMAIL_TO         — destinatários separados por vírgula
"""

import base64
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger("digest")
BASE_DIR = Path(__file__).parent.parent
DATA_FILE = BASE_DIR / "data" / "results.json"

CLIENT_COLORS = {
    "ryanair": "#073590",
    "expedia-group": "#0057A8",
    "ryanair-expedia": "#0050A0",
    "bimbo": "#E30613",
    "dow-portugal": "#CC0000",
    "gasib": "#F5820A",
    "kaspersky": "#006F51",
}


def load_todays_entries() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)
    today = date.today().isoformat()
    return [e for e in data.get("entries", []) if e.get("date", "").startswith(today)]


_SOURCE_LABELS = {
    "dre": "DRE",
    "dre-rss": "DRE",
    "parlamento-agenda": "Agenda Parlamentar",
    "parlamento-iniciativas": "Iniciativas Parlamentares",
    "news-publituris": "Publituris",
    "news-eco": "ECO",
    "news-expresso": "Expresso",
    "news-ambienteonline": "Ambiente Online",
    "news-ambitur": "Ambitur",
}


def _source_label(entry: dict) -> str:
    src = entry.get("source", "")
    label = _SOURCE_LABELS.get(src, src)
    series = entry.get("series", "")
    return f"{label} {series}".strip() if series else label


def _content_kind_label(entry: dict) -> str:
    kind = (entry.get("content_kind") or "").strip().lower()
    if not kind:
        src = entry.get("source", "")
        if src == "parlamento-agenda":
            kind = "event"
        elif src.startswith("news-"):
            kind = "news"
        elif src.startswith("dre"):
            kind = "act"
    return {"event": "Evento", "news": "Notícia", "act": "Ato", "initiative": "Iniciativa"}.get(kind, "")


def build_html(entries: list[dict], report_date: str) -> str:
    if not entries:
        body = "<p style='color:#666'>Sem publicações relevantes hoje.</p>"
    else:
        # Group by client
        by_client: dict[str, list] = {}
        for e in entries:
            for c in e.get("clients", []):
                by_client.setdefault(c["id"], {"info": c, "entries": []})
                by_client[c["id"]]["entries"].append(e)

        sections = []
        for cid, group in by_client.items():
            info = group["info"]
            color = info.get("color", "#333")
            rows = ""
            for e in group["entries"]:
                kw_pills = " ".join(
                    f'<span style="background:#f0f0f0;border-radius:3px;padding:1px 6px;font-size:11px;margin:2px 2px 0 0;display:inline-block">{k}</span>'
                    for k in (next((c["matched_keywords"] for c in e["clients"] if c["id"] == cid), []))
                )
                rows += f"""
                <tr>
                  <td style="padding:10px 12px;border-bottom:1px solid #f0f0f0;vertical-align:top">
                    <div style="font-size:11px;color:#888;margin-bottom:3px">{_source_label(e)} · {_content_kind_label(e) or e['type']} · {e['issuer']}</div>
                    <a href="{e['url']}" style="color:{color};font-weight:600;font-size:13px;text-decoration:none">{e['title'] or 'Ver publicação'}</a>
                    <div style="font-size:12px;color:#555;margin-top:4px">{e['summary'][:200] + '…' if len(e.get('summary','')) > 200 else e.get('summary','')}</div>
                    <div style="margin-top:6px">{kw_pills}</div>
                  </td>
                </tr>"""

            sections.append(f"""
            <div style="margin-bottom:28px">
              <div style="background:{color};color:#fff;padding:8px 14px;border-radius:6px 6px 0 0;font-weight:700;font-size:13px;letter-spacing:.5px">
                {info['name'].upper()} — {len(group['entries'])} publicação(ões)
              </div>
              <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e8e8e8;border-top:none;border-radius:0 0 6px 6px">
                {rows}
              </table>
            </div>""")

        body = "\n".join(sections)

    total = len(entries)
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;max-width:680px;margin:0 auto;padding:20px;color:#333">
  <div style="border-bottom:3px solid #1a1a2e;padding-bottom:14px;margin-bottom:24px">
    <div style="font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#888">Monitor de Public Affairs</div>
    <div style="font-size:22px;font-weight:700;color:#1a1a2e;margin-top:4px">DRE + Parlamento</div>
    <div style="font-size:13px;color:#555;margin-top:2px">{report_date} · {total} publicação(ões) relevante(s)</div>
  </div>
  {body}
  <div style="margin-top:32px;padding-top:14px;border-top:1px solid #eee;font-size:11px;color:#aaa">
    Monitor de Public Affairs · gerado automaticamente · <a href="https://github.com" style="color:#aaa">dashboard</a>
  </div>
</body>
</html>"""


def send_mailgun(html: str, subject: str):
    api_key = os.environ.get("MAILGUN_API_KEY", "")
    domain = os.environ.get("MAILGUN_DOMAIN", "")
    from_addr = os.environ.get("EMAIL_FROM", f"monitor@{domain}")
    to_addrs = os.environ.get("EMAIL_TO", "")

    if not all([api_key, domain, to_addrs]):
        log.warning("Mailgun not configured — skipping email send")
        # Windows terminals often default to cp1252; write UTF-8 bytes directly.
        sys.stdout.buffer.write((html + "\n").encode("utf-8", errors="backslashreplace"))
        return

    credentials = base64.b64encode(f"api:{api_key}".encode()).decode()
    payload = urllib.parse.urlencode({
        "from": from_addr,
        "to": to_addrs,
        "subject": subject,
        "html": html,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"https://api.mailgun.net/v3/{domain}/messages",
        data=payload,
        headers={"Authorization": f"Basic {credentials}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            log.info("Email sent: %s", resp.status)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        log.error("Mailgun error %s: %s", e.code, body)
        if e.code == 403 and "not allowed to send" in body:
            log.warning("Mailgun free-tier restriction — add recipients or upgrade plan")
            return
        raise


def run():
    today = date.today()
    entries = load_todays_entries()
    log.info("Sending digest for %s — %d entries", today, len(entries))

    report_date = today.strftime("%d de %B de %Y").replace(
        "January","janeiro").replace("February","fevereiro").replace("March","março").replace(
        "April","abril").replace("May","maio").replace("June","junho").replace(
        "July","julho").replace("August","agosto").replace("September","setembro").replace(
        "October","outubro").replace("November","novembro").replace("December","dezembro")

    html = build_html(entries, report_date)
    subject = f"[PA Monitor] {today.isoformat()} — {len(entries)} publicação(ões) relevante(s)"
    send_mailgun(html, subject)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
