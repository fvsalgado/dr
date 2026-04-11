#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

_BASE_DIR = Path(__file__).resolve().parent.parent
_SOURCE_LABELS_FILE = _BASE_DIR / "keywords" / "source_labels.json"

# Fallback if JSON is missing (tests / partial checkout).
_LEGACY_META = {
    "dre": {"label": "Diario da Republica", "logo_url": "https://dre.pt/o/dre-theme/images/logo-dre.png"},
    "dre-rss": {"label": "Diario da Republica", "logo_url": "https://dre.pt/o/dre-theme/images/logo-dre.png"},
    "parlamento-agenda": {
        "label": "Assembleia da Republica",
        "logo_url": "https://www.parlamento.pt/style%20library/images/logo_assembleia_republica.png",
    },
    "parlamento-iniciativas": {
        "label": "Assembleia da Republica",
        "logo_url": "https://www.parlamento.pt/style%20library/images/logo_assembleia_republica.png",
    },
    "news-publituris": {
        "label": "Publituris",
        "logo_url": "https://www.publituris.pt/wp-content/uploads/2022/11/logo-publituris.svg",
    },
    "news-eco": {"label": "ECO", "logo_url": "https://eco.sapo.pt/wp-content/themes/eco/img/logo-eco.svg"},
    "news-expresso": {"label": "Expresso", "logo_url": "https://expresso.pt/static/img/logos/expresso.svg"},
    "news-ambienteonline": {
        "label": "Ambiente Online",
        "logo_url": "https://www.ambienteonline.pt/wp-content/uploads/2020/03/logo-ambiente-online.png",
    },
    "news-ambitur": {"label": "Ambitur", "logo_url": "https://www.ambitur.pt/wp-content/uploads/2017/07/logo-ambitur.png"},
    "news-observador": {
        "label": "Observador",
        "logo_url": "https://observador.pt/wp-content/themes/observador/images/observador-logo.svg",
    },
    "news-jornaldenegocios": {
        "label": "Jornal de Negocios",
        "logo_url": "https://www.jornaldenegocios.pt/assets/img/logo-negocios.svg",
    },
}


def _load_source_meta() -> dict[str, dict[str, str]]:
    merged: dict[str, dict[str, str]] = {k: dict(v) for k, v in _LEGACY_META.items()}
    if not _SOURCE_LABELS_FILE.exists():
        return merged
    try:
        with open(_SOURCE_LABELS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        for key, val in (data.get("sources") or {}).items():
            if isinstance(val, dict):
                merged[key] = {
                    "label": str(val.get("label", key)),
                    "logo_url": str(val.get("logo_url", "")),
                }
    except (json.JSONDecodeError, OSError):
        pass
    return merged


SOURCE_META = _load_source_meta()


def source_brand(source: str) -> dict:
    meta = SOURCE_META.get(source, {})
    host = urlparse(source if "://" in source else f"https://{source}").netloc
    return {
        "label": meta.get("label", source),
        "logo_url": meta.get("logo_url", ""),
        "host": host,
    }
