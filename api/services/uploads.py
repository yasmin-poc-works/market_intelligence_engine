"""PDF upload and PDF-link validation and storage (<= 10 documents in total, <= 20 MB each)."""
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx
from fastapi import UploadFile

from errors import ApiError
from services.netguard import check_public_host

MAX_FILES = 10
MAX_BYTES = 20 * 1024 * 1024
FETCH_TIMEOUT = 30.0
MAX_REDIRECTS = 3

PdfFetcher = Callable[[str], Awaitable[bytes]]  # url -> PDF bytes, raises ApiError


def safe_name(filename: str | None, index: int) -> str:
    base = Path((filename or "").replace("\\", "/")).name
    stem = base[:-4] if base.lower().endswith(".pdf") else base
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or f"document_{index}"
    return stem + ".pdf"


def parse_pdf_url(url: str) -> str:
    """Normalize a document link; only http(s) links with a host are accepted."""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ApiError(422, f'"{url[:100]}" is not a valid http(s) link', "invalid_url")
    return url


_check_public_host = check_public_host  # module-level name so tests can replace it


async def fetch_pdf(url: str) -> bytes:
    """Download a PDF from a public link: redirects re-checked, size capped, content verified."""
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=False) as client:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await _check_public_host(urlparse(current).hostname or "")
            data = bytearray()
            try:
                async with client.stream("GET", current, headers={"Accept": "application/pdf,*/*;q=0.5"}) as resp:
                    if resp.is_redirect and resp.headers.get("location"):
                        current = parse_pdf_url(urljoin(current, resp.headers["location"]))
                        continue
                    if resp.status_code >= 400:
                        raise ApiError(502, f'"{url[:100]}" returned HTTP {resp.status_code}', "fetch_failed")
                    async for chunk in resp.aiter_bytes():
                        data += chunk
                        if len(data) > MAX_BYTES:
                            raise ApiError(
                                413, f'"{url[:100]}" exceeds the {MAX_BYTES // (1024 * 1024)} MB limit', "file_too_large"
                            )
            except httpx.HTTPError:
                raise ApiError(502, f'Could not download "{url[:100]}"', "fetch_failed") from None
            if not data.startswith(b"%PDF"):
                raise ApiError(415, f'"{url[:100]}" is not a direct link to a PDF file', "not_a_pdf")
            return bytes(data)
    raise ApiError(502, f'Too many redirects for "{url[:100]}"', "fetch_failed")


def name_from_url(url: str, index: int) -> str:
    return safe_name(unquote(Path(urlparse(url).path).name) or None, index)


async def save_pdf_links(urls: list[str], fetcher: PdfFetcher, dest: Path, start_index: int) -> list[str]:
    """Fetch each linked PDF and store it next to the uploads; returns the stored file names."""
    names: list[str] = []
    for offset, url in enumerate(dict.fromkeys(parse_pdf_url(u) for u in urls if u.strip())):
        data = await fetcher(url)
        dest.mkdir(parents=True, exist_ok=True)
        index = start_index + offset + 1
        unique = f"{index}_{name_from_url(url, index)}"
        (dest / unique).write_bytes(data)
        names.append(unique)
    return names


def copy_documents(src: Path, dest: Path, names: list[str], rename_clashes: bool = False) -> list[str]:
    """Copy stored PDFs from one run's folder to another's; returns the names they have there."""
    copied: list[str] = []
    for name in names:
        file = src / name
        if not file.is_file():
            continue
        dest.mkdir(parents=True, exist_ok=True)
        target = name
        if rename_clashes:
            plain = re.sub(r"^\d+_", "", name)
            n = 0
            while (dest / target).exists():
                n += 1
                target = f"{n}_{plain}"  # clash with a file already in this run: take the next free index
        shutil.copy2(file, dest / target)
        copied.append(target)
    return copied


async def save_pdfs(files: list[UploadFile], dest: Path, start_index: int = 0) -> list[str]:
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
    for i, (name, data) in enumerate(payloads, start_index + 1):
        unique = f"{i}_{name}"  # avoids clashes between same-named uploads
        (dest / unique).write_bytes(data)
        names.append(unique)
    return names
