import csv
import io
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Canonical column name maps for normalisation
_INVENTORY_COLUMN_MAP: dict[str, str] = {
    # product_name variants
    "item": "product_name",
    "product": "product_name",
    "product name": "product_name",
    "item name": "product_name",
    "description": "product_name",
    # sku variants
    "sku": "sku",
    "item number": "sku",
    "item #": "sku",
    "upc": "sku",
    "part number": "sku",
    # quantity variants
    "qty": "quantity",
    "quantity": "quantity",
    "on hand": "quantity",
    "qty on hand": "quantity",
    "stock": "quantity",
    "total qty": "quantity",
    "units": "quantity",
    # location variants
    "location": "location",
    "warehouse": "location",
    "bin": "location",
    "site": "location",
}

_ORDER_COLUMN_MAP: dict[str, str] = {
    # customer_name variants
    "customer": "customer_name",
    "customer name": "customer_name",
    "client": "customer_name",
    "account": "customer_name",
    "buyer": "customer_name",
    # order_date variants
    "date": "order_date",
    "order date": "order_date",
    "invoice date": "order_date",
    "ship date": "order_date",
    # total_amount variants
    "total": "total_amount",
    "amount": "total_amount",
    "order total": "total_amount",
    "invoice total": "total_amount",
    "net": "total_amount",
    # status variants
    "status": "status",
    "order status": "status",
    "fulfillment status": "status",
}

_REPORT_TYPE_KEYWORDS: dict[str, list[str]] = {
    "inventory": ["inventory", "stock", "warehouse", "on_hand", "onhand", "qty"],
    "orders": ["order", "invoice", "sales", "shipment"],
    "depletion_report": ["depletion", "deplete", "movement", "velocity"],
    "customs": ["customs", "cbp", "import", "entry", "broker", "duty"],
}


def parse_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return [{k.strip(): v.strip() for k, v in row.items()} for row in reader]


def parse_excel(path: str, sheet: int = 0) -> list[dict]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[sheet]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h).strip() if h is not None else f"col_{i}" for i, h in enumerate(rows[0])]
    result = []
    for row in rows[1:]:
        if any(cell is not None for cell in row):
            result.append({headers[i]: (str(v).strip() if v is not None else "") for i, v in enumerate(row)})
    wb.close()
    return result


def parse_pdf_text(path: str) -> str:
    try:
        import PyPDF2
    except ImportError:
        import pypdf as PyPDF2  # type: ignore

    text_parts: list[str] = []
    with open(path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            text_parts.append(page.extract_text() or "")
    return "\n".join(text_parts)


def detect_report_type(filename: str, headers: list[str]) -> str:
    name_lower = Path(filename).stem.lower().replace("-", "_").replace(" ", "_")
    combined = name_lower + " " + " ".join(h.lower() for h in headers)

    for report_type, keywords in _REPORT_TYPE_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            return report_type
    return "unknown"


def _normalize_rows(rows: list[dict[str, Any]], column_map: dict[str, str]) -> list[dict]:
    normalized: list[dict] = []
    for row in rows:
        out: dict = {}
        for raw_key, value in row.items():
            canonical = column_map.get(raw_key.lower().strip())
            if canonical:
                out[canonical] = value
        normalized.append(out)
    return normalized


def normalize_inventory_report(rows: list[dict]) -> list[dict]:
    return _normalize_rows(rows, _INVENTORY_COLUMN_MAP)


def normalize_order_report(rows: list[dict]) -> list[dict]:
    return _normalize_rows(rows, _ORDER_COLUMN_MAP)


def parse_file(path: str) -> dict | None:
    """
    Parse a supported file and return a result dict ready for ingestion.

    Returns a dict with keys: filename, report_type, rows, raw_text, row_count.
    Returns None if the file cannot be parsed (unsupported format, read error).
    """
    from app.file_ingestion.watcher import SUPPORTED_EXTENSIONS
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        return None

    try:
        rows: list[dict] = []
        raw_text: str = ""
        if suffix == ".csv":
            rows = parse_csv(path)
        elif suffix in {".xlsx", ".xls"}:
            rows = parse_excel(path)
        elif suffix == ".pdf":
            raw_text = parse_pdf_text(path)

        headers = list(rows[0].keys()) if rows else []
        report_type = detect_report_type(p.name, headers)

        if report_type == "inventory":
            rows = normalize_inventory_report(rows)
        elif report_type == "orders":
            rows = normalize_order_report(rows)

        return {
            "filename": p.name,
            "report_type": report_type,
            "rows": rows,
            "raw_text": raw_text,
            "row_count": len(rows),
        }
    except Exception as exc:
        log.warning("parse_file: failed for %s: %s", path, exc)
        return None
