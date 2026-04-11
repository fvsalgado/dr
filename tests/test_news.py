"""Tests for news scraper matching and ids."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scraper"))

import news  # noqa: E402


def test_entry_id_stable():
    a = news.entry_id("news-eco", "abc123")
    b = news.entry_id("news-eco", "abc123")
    assert a == b
    assert len(a) == 32


def test_match_clients_finds_keyword():
    clients = [
        {
            "id": "c1",
            "name": "Test",
            "color": "#000",
            "keywords": ["gás", "energia"],
        }
    ]
    hits = news.match_clients("Preço do gás natural sobe", clients)
    assert len(hits) == 1
    assert hits[0]["id"] == "c1"
    assert "gás" in hits[0]["matched_keywords"]


def test_match_clients_respects_word_boundary():
    clients = [{"id": "c1", "name": "T", "color": "#000", "keywords": ["gás"]}]
    assert news.match_clients("gasoso no mercado", clients) == []
