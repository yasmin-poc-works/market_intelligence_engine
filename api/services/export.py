"""Markdown and PDF export. PDF uses fpdf2 (pure Python, no system libraries) with the
built-in Latin-1 fonts, so typographic Unicode is mapped to plain equivalents first."""
import re

from fpdf import FPDF

_MAP = {
    "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
    "‑": "-", "‐": "-", "…": "...", "•": "-", " ": " ", " ": " ",
}


def _latin1(text: str) -> str:
    return "".join(_MAP.get(c, c) for c in text).encode("latin-1", "replace").decode("latin-1")


def _plain(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    return _latin1(re.sub(r"(?<!\*)\*(?!\s)(.+?)\*(?!\*)|_(.+?)_", lambda m: m.group(1) or m.group(2), text))


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "report"


def markdown_to_pdf(markdown: str) -> bytes:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, margin=18)
    pdf.set_margins(18, 18, 18)
    pdf.add_page()
    for raw in markdown.split(chr(10)):
        line = raw.rstrip()
        pdf.set_x(pdf.l_margin)
        if not line.strip():
            pdf.ln(3)
        elif line.startswith("# "):
            pdf.set_font("Helvetica", "B", 20)
            pdf.multi_cell(0, 9, _plain(line[2:]), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
        elif line.startswith("## "):
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 14)
            pdf.multi_cell(0, 8, _plain(line[3:]), new_x="LMARGIN", new_y="NEXT")
        elif re.match(r"^\*[^*].*\*$", line.strip()):
            pdf.set_font("Helvetica", "I", 10)
            pdf.multi_cell(0, 6, _plain(line), new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.set_font("Helvetica", "", 10.5)
            text = ("- " + line[2:]) if line.startswith("- ") else line
            pdf.multi_cell(0, 5.5, _plain(text), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
