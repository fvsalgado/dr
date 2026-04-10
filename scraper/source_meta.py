#!/usr/bin/env python3
from __future__ import annotations

from urllib.parse import urlparse

SOURCE_META = {
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
    "news-publituris": {"label": "Publituris", "logo_url": "https://www.publituris.pt/wp-content/uploads/2022/11/logo-publituris.svg"},
    "news-eco": {"label": "ECO", "logo_url": "https://eco.sapo.pt/wp-content/themes/eco/img/logo-eco.svg"},
    "news-expresso": {"label": "Expresso", "logo_url": "https://expresso.pt/static/img/logos/expresso.svg"},
    "news-ambienteonline": {"label": "Ambiente Online", "logo_url": "https://www.ambienteonline.pt/wp-content/uploads/2020/03/logo-ambiente-online.png"},
    "news-ambitur": {"label": "Ambitur", "logo_url": "https://www.ambitur.pt/wp-content/uploads/2017/07/logo-ambitur.png"},
    "news-observador": {"label": "Observador", "logo_url": "https://observador.pt/wp-content/themes/observador/images/observador-logo.svg"},
    "news-jornaldenegocios": {"label": "Jornal de Negocios", "logo_url": "https://www.jornaldenegocios.pt/assets/img/logo-negocios.svg"},
}


def source_brand(source: str) -> dict:
    meta = SOURCE_META.get(source, {})
    host = urlparse(source if "://" in source else f"https://{source}").netloc
    return {
        "label": meta.get("label", source),
        "logo_url": meta.get("logo_url", ""),
        "host": host,
    }
