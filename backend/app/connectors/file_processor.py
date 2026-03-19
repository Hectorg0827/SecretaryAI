"""
File processor — parses PDF and Excel depletion reports from sub-distributors
and other uploaded business documents.
"""
import io
from pathlib import Path
from typing import Optional

import pandas as pd


def parse_excel_depletion_report(file_bytes: bytes, sheet_name: Optional[str] = None) -> list[dict]:
    """
    Parse a sub-distributor depletion report from Excel.
    Returns a list of depletion records: [{product, qty_sold, period, distributor}].
    """
    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet_name or 0, header=0)
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

    records = []
    for _, row in df.iterrows():
        record = row.dropna().to_dict()
        if record:
            records.append(record)
    return records


def parse_pdf_document(file_bytes: bytes) -> str:
    """
    Extract text from a PDF (customs documents, invoices, etc.).
    Returns raw text for the AI to analyze.
    """
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text.strip()
    except Exception as e:
        return f"[PDF parse error: {e}]"


def detect_file_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".xlsx", ".xls", ".csv"}:
        return "spreadsheet"
    if suffix == ".pdf":
        return "pdf"
    return "unknown"
