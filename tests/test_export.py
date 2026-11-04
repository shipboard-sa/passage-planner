"""Tests for passage_planner.export (Markdown -> HTML / docx)."""

from __future__ import annotations

from pathlib import Path

from docx import Document

from passage_planner.export import to_docx, to_html, write_briefing

SAMPLE = """# Passage Briefing — Test

Intro paragraph with **bold** and `code`.

## 1. Executive

- first bullet
- second bullet

| A | B |
|---|---|
| 1 | 2 |
| 3 | 4 |

> Wake the captain if unsure.

---

CRITICAL NOTICE: disclaimer text.
"""


def test_to_html_structure() -> None:
    html = to_html(SAMPLE, title="Test & <Brief>")
    assert "<title>Test &amp; &lt;Brief&gt;</title>" in html
    assert "<h1>" in html and "<h2>" in html
    assert "<table>" in html and "<th>A</th>" in html
    assert "<blockquote>" in html
    assert "<strong>bold</strong>" in html


def test_to_docx_roundtrips(tmp_path: Path) -> None:
    out = to_docx(SAMPLE, tmp_path / "b.docx", title="Test")
    assert out.exists() and out.stat().st_size > 2000

    doc = Document(str(out))
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    assert any("Passage Briefing" in h for h in headings)
    assert any(h.strip() == "1. Executive" for h in headings)

    assert len(doc.tables) == 1
    table = doc.tables[0]
    assert table.rows[0].cells[0].text == "A"
    assert table.rows[2].cells[1].text == "4"

    all_text = "\n".join(p.text for p in doc.paragraphs)
    assert "first bullet" in all_text
    assert "Wake the captain if unsure." in all_text
    assert "CRITICAL NOTICE" in all_text


def test_write_briefing_emits_three_files(tmp_path: Path) -> None:
    paths = write_briefing(SAMPLE, tmp_path, "passage_001", "Test Briefing")
    assert set(paths) == {"markdown", "html", "docx"}
    for p in paths.values():
        assert p.exists() and p.stat().st_size > 0
    assert paths["markdown"].read_text(encoding="utf-8") == SAMPLE
