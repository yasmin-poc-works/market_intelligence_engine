"""PDF upload validation and storage (<= 10 files, <= 20 MB each)."""
import re
from pathlib import Path

from fastapi import UploadFile

from errors import ApiError

MAX_FILES = 10
MAX_BYTES = 20 * 1024 * 1024


def safe_name(filename: str | None, index: int) -> str:
    base = Path((filename or "").replace("\\", "/")).name
    stem = base[:-4] if base.lower().endswith(".pdf") else base
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or f"document_{index}"
    return stem + ".pdf"


async def save_pdfs(files: list[UploadFile], dest: Path) -> list[str]:
    """Validate and store the PDFs; returns stored file names (used as document ids)."""
    files = [f for f in files if f.filename]  # browsers send an empty part when nothing is chosen
    if len(files) > MAX_FILES:
        raise ApiError(422, f"At most {MAX_FILES} PDF files are allowed", "too_many_files")

    payloads: list[tuple[str, bytes]] = []
    for i, f in enumerate(files, 1):
        data = await f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ApiError(413, f'"{f.filename}" exceeds the {MAX_BYTES // (1024 * 1024)} MB limit', "file_too_large")
        if not data.startswith(b"%PDF"):
            raise ApiError(415, f'"{f.filename}" is not a PDF file', "not_a_pdf")
        payloads.append((safe_name(f.filename, i), data))

    names: list[str] = []
    if payloads:
        dest.mkdir(parents=True, exist_ok=True)
    for i, (name, data) in enumerate(payloads, 1):
        unique = f"{i}_{name}"  # avoids clashes between same-named uploads
        (dest / unique).write_bytes(data)
        names.append(unique)
    return names
