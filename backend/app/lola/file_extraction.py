"""
Stateless text extraction for PDF and Excel (.xlsx) attachments.

The bytes are processed in memory and discarded: nothing is written to disk,
stored, or logged. The result is plain text (PDF) or CSV (Excel) that the
client sends back inline with the user's question, where the normal
attachment rules apply (untrusted data, never instructions, never stored).

Defences:
* size cap and file type decided by CONTENT (magic bytes), not by name;
* PDF: encrypted files refused, at most MAX_PDF_PAGES pages, text capped;
* XLSX: zip-bomb guard (entry count, total uncompressed size, compression
  ratio), macro-enabled workbooks refused, first sheet only, cached VALUES only
  (formulas are never evaluated), row/column caps;
* legacy binary .xls is refused;
* scanned PDFs and photos (PNG/JPEG) are read with text recognition only when
  ``LOLA_OCR=1`` and the OCR programs are installed (see ``app.lola.ocr``);
  such results carry ``ocr=True`` so clients ask the user to check numbers.
"""
from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field

from app.lola.attachments import MAX_COLUMNS, MAX_ROWS, MAX_TEXT_CHARS, clean_cell

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_ZIP_ENTRIES = 2_000
MAX_ZIP_UNCOMPRESSED = 50 * 1024 * 1024
MAX_ZIP_RATIO = 100


class ExtractionRejected(ValueError):
    """The file can't be read safely; ``code`` explains why (stable, client-facing)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Extracted:
    text: str
    mime: str                       # "text/plain" (PDF) or "text/csv" (Excel)
    kind: str                       # "pdf" | "xlsx" | "image"
    pages: int = 0
    sheets: list[str] = field(default_factory=list)
    truncated: bool = False
    ocr: bool = False               # text came from text recognition: check numbers


def sniff(data: bytes) -> str:
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        return "zip"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "ole"                # legacy .xls / .doc
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    return "unknown"


def _pdf(data: bytes) -> Extracted:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ExtractionRejected("encrypted_pdf")
        pages = reader.pages
        total = len(pages)
        parts: list[str] = []
        size = 0
        truncated = total > MAX_PDF_PAGES
        for page in list(pages)[:MAX_PDF_PAGES]:
            text = page.extract_text() or ""
            parts.append(text)
            size += len(text)
            if size >= MAX_TEXT_CHARS:
                truncated = True
                break
    except ExtractionRejected:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError):
        raise ExtractionRejected("unreadable_pdf") from None
    text = "\n".join(parts)[:MAX_TEXT_CHARS]
    if not text.strip():
        return _scanned_pdf(data, total)                # no text layer: a scanned image
    return Extracted(text=text, mime="text/plain", kind="pdf", pages=total, truncated=truncated)


def _ocr_errors(fn):
    from app.lola import ocr

    try:
        return fn()
    except ocr.OCRUnavailable:
        return None
    except ocr.OCRFailed as exc:
        raise ExtractionRejected(exc.code) from None


def _scanned_pdf(data: bytes, total: int) -> Extracted:
    from app.lola import ocr

    got = _ocr_errors(lambda: ocr.pdf_text(data, total))
    if got is None:
        raise ExtractionRejected("no_text_in_pdf")
    text, _read, truncated = got
    if len(text) > MAX_TEXT_CHARS:
        text, truncated = text[:MAX_TEXT_CHARS], True
    if not text.strip():
        raise ExtractionRejected("ocr_no_text")
    return Extracted(text=text, mime="text/plain", kind="pdf", pages=total, truncated=truncated, ocr=True)


def _image(data: bytes) -> Extracted:
    from app.lola import ocr

    text = _ocr_errors(lambda: ocr.image_text(data))
    if text is None:
        raise ExtractionRejected("ocr_off")
    truncated = len(text) > MAX_TEXT_CHARS
    text = text[:MAX_TEXT_CHARS]
    if not text.strip():
        raise ExtractionRejected("ocr_no_text")
    return Extracted(text=text, mime="text/plain", kind="image", pages=1, truncated=truncated, ocr=True)


def _zip_guard(data: bytes) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ExtractionRejected("unreadable_xlsx") from None
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise ExtractionRejected("unsafe_xlsx")
    total = sum(i.file_size for i in infos)
    packed = max(1, sum(i.compress_size for i in infos))
    if total > MAX_ZIP_UNCOMPRESSED or total / packed > MAX_ZIP_RATIO:
        raise ExtractionRejected("unsafe_xlsx")
    names = {i.filename for i in infos}
    if "xl/workbook.xml" not in names:
        raise ExtractionRejected("not_xlsx")
    if "xl/vbaProject.bin" in names:
        raise ExtractionRejected("macro_xlsx")
    return zf


def _xlsx(data: bytes) -> Extracted:
    import openpyxl

    _zip_guard(data).close()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except Exception:
        raise ExtractionRejected("unreadable_xlsx") from None
    try:
        sheets = list(wb.sheetnames)
        if not sheets:
            raise ExtractionRejected("empty_xlsx")
        ws = wb.worksheets[0]
        out = io.StringIO()
        writer = csv.writer(out)
        rows = 0
        truncated = False
        for row in ws.iter_rows(values_only=True):
            cells = list(row[:MAX_COLUMNS])
            if not any(c is not None and str(c).strip() for c in cells):
                continue
            if rows > MAX_ROWS:          # header + MAX_ROWS data rows
                truncated = True
                break
            writer.writerow([clean_cell(c) for c in cells])
            rows += 1
            if out.tell() >= MAX_TEXT_CHARS:
                truncated = True
                break
    finally:
        wb.close()
    text = out.getvalue()[:MAX_TEXT_CHARS]
    if not text.strip():
        raise ExtractionRejected("empty_xlsx")
    return Extracted(text=text, mime="text/csv", kind="xlsx", sheets=sheets, truncated=truncated)


def extract(name: str, data: bytes) -> Extracted:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ExtractionRejected("too_large")
    kind = sniff(data)
    if kind == "pdf":
        return _pdf(data)
    if kind == "zip":
        return _xlsx(data)
    if kind == "ole":
        raise ExtractionRejected("legacy_xls")
    if kind in ("png", "jpeg"):
        return _image(data)
    raise ExtractionRejected("unsupported_type")
