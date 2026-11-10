"""
Read user-attached files SAFELY and check them against data the caller may see.

Files are untrusted data:
* their text never chooses intents, tools or actions (those come only from the
  user's own message), never reaches memory, and is never persisted;
* text that looks like instructions is flagged and otherwise ignored;
* spreadsheet formulas (=, +, -, @ at the start of a cell) are neutralised
  before display, and control characters are stripped;
* size, row, column and cell limits bound the work.

Cross-checks go through Lola's normal authorisation: inventory is read via the
inventory tool (denied → not checked), customers are matched only against
accounts the caller can see, and hidden and non-existent customers are reported
identically ("not among the accounts you can access").

Supported now: CSV / TSV / TXT / Markdown / JSON sent as text by the client.
PDF and Excel need a binary upload path (not implemented yet) and are reported
as unsupported, never silently ignored.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from app.lola.contracts import (
    Department, EvidenceRef, LolaStatus, PermissionDecision, ResourceType, Sensitivity,
)
from app.lola.tools import LolaTool, ToolContext, ToolOutput

MAX_TEXT_CHARS = 200_000
MAX_ROWS = 2_000
MAX_COLUMNS = 50
MAX_CELL = 500
PREVIEW_ROWS = 20
TEXT_EXTS = (".csv", ".tsv", ".txt", ".md", ".json")

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INSTRUCTION_LIKE = re.compile(
    r"\b(ignore (all|any|the)? ?(previous|prior|above) instructions|you are now|system prompt|"
    r"disregard (your|the) rules|approve (all|every)|send (it|this|an email) to|transfer \$|"
    r"ignora (las|todas las)? ?instrucciones|apru[eé]ba(lo|las|los)? todo)\b", re.IGNORECASE)
_SKU_COLS = ("sku", "item", "item_id", "product_code", "code", "codigo", "código", "articulo", "artículo")
_PRODUCT_COLS = ("product", "product_name", "description", "name", "producto", "descripcion", "descripción")
_QTY_COLS = ("qty", "quantity", "cases", "units", "cantidad", "cajas", "unidades")
_CUSTOMER_COLS = ("customer", "customer_name", "account", "client", "cliente", "cuenta")


class AttachmentRejected(ValueError):
    pass


def supported(name: str, mime: str, text: Optional[str]) -> bool:
    return text is not None and (name.lower().endswith(TEXT_EXTS) or mime.startswith("text/")
                                 or "json" in mime or "csv" in mime)


def clean_cell(value: Any) -> str:
    s = _CONTROL.sub("", str(value if value is not None else "")).strip()[:MAX_CELL]
    # Formula injection: a leading =, +, -, @ (or tab/CR) can execute when the
    # value is later pasted or exported into a spreadsheet.
    if s[:1] in ("=", "+", "-", "@") and not re.fullmatch(r"[-+]?\d[\d,]*(\.\d+)?", s):
        s = "'" + s
    return s


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9áéíóúñ]+", "_", h.strip().lower()).strip("_")


@dataclass
class ParsedAttachment:
    name: str
    kind: str                      # "table" | "text"
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    excerpt: str = ""
    instruction_like: bool = False
    warnings: list[str] = field(default_factory=list)


def parse(name: str, mime: str, text: Optional[str]) -> ParsedAttachment:
    if text is None:
        raise AttachmentRejected("unsupported")
    if len(text) > MAX_TEXT_CHARS:
        raise AttachmentRejected("too_large")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    flagged = bool(_INSTRUCTION_LIKE.search(text))
    low = name.lower()
    if low.endswith(".json") or "json" in mime:
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, list) and data and all(isinstance(r, dict) for r in data[: MAX_ROWS + 1]):
            cols: list[str] = []
            for r in data[:MAX_ROWS]:
                for k in r:
                    if k not in cols and len(cols) < MAX_COLUMNS:
                        cols.append(str(k))
            rows = [{c: clean_cell(r.get(c)) for c in cols} for r in data[:MAX_ROWS]]
            return ParsedAttachment(name, "table", [clean_cell(c) for c in cols], rows, len(data),
                                    len(data) > MAX_ROWS, instruction_like=flagged)
    first_line = text.split("\n", 1)[0]
    prose_file = low.endswith((".txt", ".md", ".pdf"))
    looks_tabular = (low.endswith((".csv", ".tsv")) or "csv" in mime or "\t" in first_line
                     or (not prose_file and "," in first_line))
    if looks_tabular:
        delimiter = "\t" if (low.endswith(".tsv") or "\t" in first_line) else ","
        try:
            reader = csv.reader(io.StringIO(text), delimiter=delimiter)
            header = next(reader, [])
            cols = [clean_cell(h) or f"column_{i + 1}" for i, h in enumerate(header[:MAX_COLUMNS])]
            rows, total = [], 0
            for raw in reader:
                if not any(c.strip() for c in raw):
                    continue
                total += 1
                if len(rows) < MAX_ROWS:
                    rows.append({cols[i]: clean_cell(raw[i]) if i < len(raw) else "" for i in range(len(cols))})
            if cols:
                p = ParsedAttachment(name, "table", cols, rows, total, total > MAX_ROWS, instruction_like=flagged)
                if len(header) > MAX_COLUMNS:
                    p.warnings.append("columns_truncated")
                return p
        except csv.Error:
            pass
    excerpt = _CONTROL.sub("", text)[:1500]
    return ParsedAttachment(name, "text", excerpt=excerpt, row_count=text.count("\n") + 1,
                            instruction_like=flagged)


def _find_col(columns: list[str], names: tuple[str, ...]) -> Optional[str]:
    normed = {_norm(c): c for c in columns}
    for n in names:
        if n in normed:
            return normed[n]
    for n in names:
        for k, c in normed.items():
            if k.startswith(n) or k.endswith("_" + n):
                return c
    return None


def _num(v: str) -> Optional[Decimal]:
    s = str(v).replace(",", "").replace("$", "").strip().lstrip("'")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


class AttachmentTool(LolaTool):
    """Analyse ONE attachment. Instantiated per file by the orchestrator."""

    name = "files.attachment"
    department = Department.GENERAL
    resource = ResourceType.ATTACHMENT
    sensitivity = Sensitivity.INTERNAL
    description = "Reads a user-attached CSV/TXT/JSON file and cross-checks it against permitted data."
    needs_adapter = False

    def __init__(self, parsed: ParsedAttachment):
        self.parsed = parsed

    async def fetch(self, ctx: ToolContext, args: dict, decision: PermissionDecision) -> ToolOutput:
        p = self.parsed
        data: dict[str, Any] = {"file": p.name, "kind": p.kind, "row_count": p.row_count, "truncated": p.truncated,
                                "instruction_like": p.instruction_like}
        warnings = list(p.warnings)
        if p.instruction_like:
            warnings.append("instruction_like")
        if p.kind == "text":
            data["excerpt"] = p.excerpt
        else:
            data["columns"] = p.columns
            data["preview"] = p.rows[:PREVIEW_ROWS]
            totals = {}
            for c in p.columns:
                vals = [_num(r.get(c, "")) for r in p.rows]
                nums = [v for v in vals if v is not None]
                if nums and len(nums) >= max(1, int(0.8 * len([r for r in p.rows if r.get(c)]))):
                    totals[c] = str(sum(nums))
            data["totals"] = totals
            data["inventory_check"] = await self._inventory_check(ctx, p)
            data["customer_check"] = self._customer_check(ctx, p)
        evidence = [EvidenceRef(source_system="user_upload", record_type="attachment", record_id=p.name[:120],
                                confidence=50, note="Provided by the user; not verified")]
        return ToolOutput(data=data, summary=f"{p.name}: {p.row_count} row(s)", evidence=evidence,
                          status=LolaStatus.ANSWERED, warnings=warnings)

    async def _inventory_check(self, ctx: ToolContext, p: ParsedAttachment) -> Optional[dict]:
        sku_col = _find_col(p.columns, _SKU_COLS)
        prod_col = _find_col(p.columns, _PRODUCT_COLS)
        if not (sku_col or prod_col):
            return None
        from app.lola.tools import InventoryTool
        inv = await InventoryTool().run(ctx, {})
        if inv.status != LolaStatus.ANSWERED:
            return {"status": inv.status.value, "reason": inv.summary}
        items = (inv.data or {}).get("items", [])
        by_sku = {str(i.get("sku") or "").strip().lower(): i for i in items if i.get("sku")}
        by_id = {str(i.get("qb_id") or "").strip().lower(): i for i in items if i.get("qb_id")}
        by_name = {str(i.get("product_name") or "").strip().lower(): i for i in items if i.get("product_name")}
        qty_col = _find_col(p.columns, _QTY_COLS)
        matched, short, unknown = [], [], []
        for r in p.rows:
            key_sku = str(r.get(sku_col, "") if sku_col else "").strip().lower()
            key_name = str(r.get(prod_col, "") if prod_col else "").strip().lower()
            item = by_sku.get(key_sku) or by_id.get(key_sku) or by_name.get(key_name)
            label = r.get(sku_col) if sku_col else r.get(prod_col)
            if not item:
                if label:
                    unknown.append(str(label))
                continue
            want = _num(r.get(qty_col, "")) if qty_col else None
            on_hand = item.get("total_qty")
            row = {"file_value": str(label), "product_name": item.get("product_name"), "on_hand": on_hand,
                   "requested": str(want) if want is not None else None}
            matched.append(row)
            if want is not None and on_hand is not None and Decimal(str(on_hand)) < want:
                short.append(row)
        return {"status": "answered", "matched": len(matched), "short": short[:20], "unknown": unknown[:20],
                "unknown_count": len(unknown), "rows": matched[:50]}

    def _customer_check(self, ctx: ToolContext, p: ParsedAttachment) -> Optional[dict]:
        col = _find_col(p.columns, _CUSTOMER_COLS)
        if not col:
            return None
        visible = {}
        for row in ctx.authorizer.account_rows():
            key = str(row.get("qb_customer_id") or row.get("id") or "")
            if key and ctx.authorizer.can_see_account(key) and row.get("name"):
                visible[str(row["name"]).strip().lower()] = str(row["name"])
        found, not_visible = [], []
        for r in p.rows:
            name = str(r.get(col, "")).strip()
            if not name:
                continue
            (found if name.lower() in visible else not_visible).append(name)
        return {"matched": sorted(set(visible[n.lower()] for n in found)), "not_visible_count": len(set(not_visible))}
