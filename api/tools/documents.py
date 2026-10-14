"""PDF extraction and structural chunking. Docling reads the layout (headings, paragraphs,
tables, page numbers); small neighbouring pieces under the same heading are merged into
passages of a few hundred tokens. If Docling cannot run (for example its models cannot be
downloaded), pypdf extracts plain page text instead and the problem is logged."""
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger("insightforge")

TARGET_CHARS = 1200  # merge pieces up to about this size
MAX_CHARS = 2000  # split anything longer than this
MIN_CHARS = 40  # a finished chunk shorter than this carries no usable claim
MIN_PIECE_CHARS = 15  # stray page numbers, bullets and the like


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    parts, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if current and len(current) + len(sentence) > TARGET_CHARS:
            parts.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return parts


def merge_pieces(pieces: list[dict[str, Any]], filename: str) -> list[dict[str, Any]]:
    """pieces: {text, page_number, section_heading} in reading order -> passage chunks."""
    chunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for piece in pieces:
        for text in _split_long(piece["text"].strip()):
            if len(text) < MIN_PIECE_CHARS:
                continue
            same = (
                current is not None
                and current["page_number"] == piece["page_number"]  # a chunk never spans pages: exact citations
                and current["section_heading"] == piece["section_heading"]
                and len(current["text"]) + len(text) <= TARGET_CHARS
            )
            if same:
                current["text"] += "\n" + text
                continue
            if current is not None:
                chunks.append(current)
            current = {
                "text": text,
                "source_filename": filename,
                "page_number": piece["page_number"],
                "section_heading": piece["section_heading"],
            }
    if current is not None:
        chunks.append(current)
    return [c for c in chunks if len(c["text"]) >= MIN_CHARS]


@lru_cache
def _docling_converter():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions(do_ocr=False)  # text PDFs; OCR models are a large extra download
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})


def _docling_pieces(path: Path) -> list[dict[str, Any]]:
    from docling.chunking import HierarchicalChunker

    doc = _docling_converter().convert(str(path)).document
    pieces = []
    for chunk in HierarchicalChunker().chunk(doc):
        pages = sorted({p.page_no for item in chunk.meta.doc_items for p in item.prov})
        headings = chunk.meta.headings or []
        pieces.append(
            {
                "text": chunk.text,
                "page_number": pages[0] if pages else 1,
                "section_heading": headings[-1] if headings else "",
            }
        )
    return pieces


def _pypdf_pieces(path: Path) -> list[dict[str, Any]]:
    from pypdf import PdfReader

    pieces = []
    for number, page in enumerate(PdfReader(str(path)).pages, 1):
        for paragraph in re.split(r"\n\s*\n", page.extract_text() or ""):
            if paragraph.strip():
                pieces.append({"text": re.sub(r"\s+", " ", paragraph), "page_number": number, "section_heading": ""})
    return pieces


def extract_chunks(path: Path, *, use_docling: bool = True) -> list[dict[str, Any]]:
    """Chunks of one PDF: {text, source_filename, page_number, section_heading}."""
    pieces: list[dict[str, Any]] = []
    if use_docling:
        try:
            pieces = _docling_pieces(path)
        except Exception as exc:
            log.warning("Docling failed on %s (%s: %s); falling back to pypdf", path.name, type(exc).__name__, exc)
    if not pieces:
        pieces = _pypdf_pieces(path)
    return merge_pieces(pieces, display_name(path.name))


def display_name(stored_name: str) -> str:
    """Uploads are stored as '<n>_<name>.pdf'; show the name the user recognises."""
    return re.sub(r"^\d+_", "", stored_name)
