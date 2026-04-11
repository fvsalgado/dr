"""Tests for shared news helpers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scraper"))

from news_common import apply_opinion_rubric, canonical_url, is_opinion_title  # noqa: E402


def test_is_opinion_title():
    assert is_opinion_title("Opinião: o futuro")
    assert is_opinion_title("Crónica — opinião sobre o tema")
    assert not is_opinion_title("Sem relação com o tema")
    assert not is_opinion_title("")


def test_apply_opinion_rubric():
    base = {"title": "Opinião: teste", "type": "Notícia", "article_section": "Economia"}
    out = apply_opinion_rubric(base)
    assert out["article_section"] == "Opinião"
    assert out["type"] == "Opinião"


def test_canonical_url_strips_fragment_and_lowercases_host():
    u = canonical_url("HTTPS://Example.com/path/?x=1#frag")
    assert "#" not in u
    assert "example.com" in u


def test_canonical_url_empty():
    assert canonical_url("") == ""
