"""Render a briefing Markdown string to the formats the owner actually works in:
a styled self-contained HTML file (opens clean in Word and Acrobat) and a
native ``.docx``.

The Markdown produced by :mod:`passage_planner.briefing` uses a fixed subset —
ATX headings, paragraphs, one level of bullet / ordered list, GFM pipe tables,
thematic breaks, block quotes, inline ``**bold**`` and ``code`` — and this
renderer targets exactly that subset. It is not a general Markdown converter.

PDF: open the ``.docx`` in Word (or the ``.html`` in Acrobat) and export. No
headless PDF engine is assumed to be installed.
"""

from __future__ import annotations

import html as _html
from pathlib import Path

from docx import Document
from docx.document import Document as DocxDocument
from docx.shared import Pt
from docx.text.paragraph import Paragraph
from markdown_it import MarkdownIt
from markdown_it.token import Token

_MD = MarkdownIt("commonmark").enable("table")

_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>
  @page {{ margin: 2cm; }}
  body {{ font-family: Georgia, 'Times New Roman', serif; font-size: 11pt;
         line-height: 1.4; color: #1a1a1a; max-width: 46em; margin: 2em auto; padding: 0 1em; }}
  h1 {{ font-size: 20pt; border-bottom: 2px solid #333; padding-bottom: .2em; }}
  h2 {{ font-size: 15pt; margin-top: 1.6em; border-bottom: 1px solid #bbb; padding-bottom: .15em; }}
  h3 {{ font-size: 12pt; margin-top: 1.2em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 10pt; }}
  th, td {{ border: 1px solid #999; padding: 4px 7px; text-align: left; vertical-align: top; }}
  th {{ background: #ececec; }}
  blockquote {{ border-left: 3px solid #888; margin: 1em 0; padding: .3em 1em;
               background: #f6f6f6; }}
  code {{ font-family: Consolas, 'Courier New', monospace; background: #eee; padding: 0 3px; }}
  hr {{ border: 0; border-top: 1px solid #ccc; margin: 2em 0; }}
  .disclaimer {{ font-size: 9.5pt; color: #444; }}
</style></head><body>
{body}
</body></html>
"""


def to_html(markdown: str, title: str = "Passage Briefing") -> str:
    body = _MD.render(markdown)
    return _HTML_TEMPLATE.format(title=_html.escape(title), body=body)


# ---------------------------------------------------------------------------
# docx
# ---------------------------------------------------------------------------


def _add_inline(paragraph: Paragraph, children: list[Token] | None) -> None:
    if not children:
        return
    bold = False
    for tok in children:
        if tok.type == "strong_open":
            bold = True
        elif tok.type == "strong_close":
            bold = False
        elif tok.type == "code_inline":
            run = paragraph.add_run(tok.content)
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        elif tok.type in ("text", "html_inline"):
            run = paragraph.add_run(tok.content)
            run.bold = bold
        elif tok.type == "softbreak":
            paragraph.add_run(" ")


def _inline_text(children: list[Token] | None) -> str:
    if not children:
        return ""
    return "".join(t.content for t in children if t.type in ("text", "code_inline"))


def to_docx(markdown: str, path: str | Path, title: str = "Passage Briefing") -> Path:
    tokens = _MD.parse(markdown)
    doc = Document()
    doc.core_properties.title = title

    i = 0
    n = len(tokens)
    list_ordered: list[bool] = []

    while i < n:
        tok = tokens[i]
        t = tok.type

        if t == "heading_open":
            level = int(tok.tag[1])
            inline = tokens[i + 1] if i + 1 < n else None
            para = doc.add_heading(level=min(level, 4))
            para.text = ""
            _add_inline(para, inline.children if inline else None)
            i += 3
            continue

        if t == "paragraph_open":
            inline = tokens[i + 1] if i + 1 < n else None
            style = "List Bullet" if (list_ordered and not list_ordered[-1]) else (
                "List Number" if list_ordered else None
            )
            para = doc.add_paragraph(style=style) if style else doc.add_paragraph()
            _add_inline(para, inline.children if inline else None)
            i += 3
            continue

        if t == "bullet_list_open":
            list_ordered.append(False)
            i += 1
            continue
        if t == "ordered_list_open":
            list_ordered.append(True)
            i += 1
            continue
        if t in ("bullet_list_close", "ordered_list_close"):
            if list_ordered:
                list_ordered.pop()
            i += 1
            continue

        if t == "hr":
            doc.add_paragraph().add_run("_" * 40)
            i += 1
            continue

        if t == "blockquote_open":
            i += 1
            while i < n and tokens[i].type != "blockquote_close":
                if tokens[i].type == "inline":
                    para = doc.add_paragraph(style="Intense Quote")
                    _add_inline(para, tokens[i].children)
                i += 1
            i += 1
            continue

        if t == "table_open":
            i = _emit_table(doc, tokens, i)
            continue

        i += 1

    out = Path(path)
    doc.save(str(out))
    return out


def _emit_table(doc: DocxDocument, tokens: list[Token], start: int) -> int:
    rows: list[list[str]] = []
    header: list[str] = []
    i = start + 1
    current: list[str] = []
    in_header = False
    while i < len(tokens) and tokens[i].type != "table_close":
        tt = tokens[i].type
        if tt == "thead_open":
            in_header = True
        elif tt == "thead_close":
            in_header = False
        elif tt == "tr_open":
            current = []
        elif tt == "tr_close":
            if in_header:
                header = current
            else:
                rows.append(current)
        elif tt in ("th_open", "td_open"):
            inline = tokens[i + 1] if i + 1 < len(tokens) else None
            current.append(_inline_text(inline.children if inline else None).strip())
            i += 1
        i += 1

    ncols = len(header) or (len(rows[0]) if rows else 0)
    if ncols == 0:
        return i + 1
    table = doc.add_table(rows=0, cols=ncols)
    table.style = "Light Grid Accent 1"
    if header:
        cells = table.add_row().cells
        for c, txt in zip(cells, header, strict=False):
            c.text = txt
            for p in c.paragraphs:
                for r in p.runs:
                    r.bold = True
    for row in rows:
        cells = table.add_row().cells
        for c, txt in zip(cells, row, strict=False):
            c.text = txt
    doc.add_paragraph()
    return i + 1


def write_briefing(
    markdown: str, out_dir: str | Path, stem: str, title: str
) -> dict[str, Path]:
    """Write ``<stem>.md``, ``.html`` and ``.docx`` into ``out_dir``."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    md_path = d / f"{stem}.md"
    html_path = d / f"{stem}.html"
    docx_path = d / f"{stem}.docx"
    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(to_html(markdown, title), encoding="utf-8")
    to_docx(markdown, docx_path, title)
    return {"markdown": md_path, "html": html_path, "docx": docx_path}
