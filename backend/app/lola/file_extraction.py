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
* legacy binary .xls is refused.
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
    kind: str                       # "pdf" | "xlsx"
    pages: int = 0
    sheets: list[str] = field(default_factory=list)
    truncated: bool = False


def sniff(data: bytes) -> str:
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        return "zip"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "ole"                # legacy .xls / .doc
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
        raise ExtractionRejected("no_text_in_pdf")     # scanned image: needs OCR (not offered)
    return Extracted(text=text, mime="text/plain", kind="pdf", pages=total, truncated=truncated)


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
    raise ExtractionRejected("unsupported_type")
