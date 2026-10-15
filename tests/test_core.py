"""Unit tests for the parts that don't need a browser:  python -m pytest -v"""
import os, sys, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("ALLOW_PRIVATE", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from PIL import Image, ImageDraw
import checker

def test_timestamps_dates_and_tokens_are_ignored():
    a = "Updated 10:42:01 AM on 2026-10-15\nSession a8f3c91e0b7d44e2a1c9f00b3e6d7a21\nPrice: ₹999"
    b = "Updated 11:05:59 PM on 2026-10-16\nSession 0f1e2d3c4b5a69788796a5b4c3d2e1f0\nPrice: ₹999"
    assert checker.normalize(a) == checker.normalize(b)

def test_relative_times_and_counters():
    a, b = "Posted 5 minutes ago\n1,204 views", "Posted 2 hours ago\n1,377 views"
    assert checker.normalize(a, "medium") == checker.normalize(b, "medium")
    assert checker.normalize(a, "high") != checker.normalize(b, "high")   # high sensitivity keeps counters

def test_real_change_is_detected():
    assert checker.normalize("Price: ₹999") != checker.normalize("Price: ₹799")

def test_low_sensitivity_ignores_numbers_and_needs_3_lines():
    assert checker.normalize("Price: ₹999", "low") == checker.normalize("Price: ₹799", "low")
    assert checker.min_lines("low") == 3 and checker.min_lines("high") == 1

def test_stable_drops_lines_that_change_every_load():
    first = "Title\nQuote: be kind\nPrice 10"
    second = "Title\nQuote: stay curious\nPrice 10"
    assert checker.stable(first, second) == "Title\nPrice 10"

def test_changes_and_summary():
    added, removed = checker.changes("A\nB\nC", "A\nC\nD")
    assert added == ["D"] and removed == ["B"]
    assert checker.summarize(added, removed).startswith("1 line(s) added, 1 removed")

def test_keyword_only_alerts():
    assert checker.keyword_hit("", ["anything"])
    assert checker.keyword_hit("price, stock", ["Out of STOCK now"])
    assert not checker.keyword_hit("price", ["New blog post"])

@pytest.mark.parametrize("bad", ["ftp://example.com", "https://user:pass@example.com", "http://127.0.0.1:5000",
                                 "http://localhost/admin", "http://192.168.1.1", "not a url"])
def test_rejects_non_public_urls(bad):
    with pytest.raises(ValueError):
        checker.validate_url(bad)

def test_url_is_normalised(monkeypatch):
    monkeypatch.setattr(checker, "ALLOW_PRIVATE", True)   # skip DNS lookup so the test works offline
    assert checker.validate_url("https://Example.com#top") == "https://example.com/"

def test_screenshot_diff_marks_changed_area(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    Image.new("RGB", (200, 200), "white").save(a)
    img = Image.new("RGB", (200, 200), "white"); ImageDraw.Draw(img).rectangle([50, 50, 90, 90], fill="black"); img.save(b)
    assert checker.shot_diff(a, b, tmp_path / "d.png") > 0
    assert checker.shot_diff(a, a, tmp_path / "same.png") == 0
