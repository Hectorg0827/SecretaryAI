import logging
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".pdf"}


class FileWatcher:
    def __init__(
        self,
        watch_dirs: list[str],
        company_id: str,
        db,
        on_file: Callable,
    ):
        self._watch_dirs = watch_dirs
        self._company_id = company_id
        self._db = db
        self._on_file = on_file

    async def start(self) -> None:
        """Watch all configured directories and call on_file for each new/changed file."""
        try:
            from watchfiles import awatch
        except ImportError:
            log.error("watchfiles not installed; file watcher disabled")
            return

        valid_dirs = [d for d in self._watch_dirs if Path(d).is_dir()]
        if not valid_dirs:
            log.warning("FileWatcher: no valid directories to watch for company %s", self._company_id)
            return

        log.info("FileWatcher: watching %s for company %s", valid_dirs, self._company_id)
        async for changes in awatch(*valid_dirs):
            for _change_type, path in changes:
                await self._handle(path)

    async def run_once(self) -> None:
        """Single pass — scan directories for any existing files and ingest them."""
        for dir_path in self._watch_dirs:
            p = Path(dir_path)
            if not p.is_dir():
                continue
            for file_path in p.iterdir():
                if file_path.is_file():
                    await self._handle(str(file_path))

    async def _handle(self, path: str) -> None:
        file = Path(path)
        # Skip hidden and temp files
        if file.name.startswith(".") or file.name.startswith("~$"):
            return
        if file.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return

        log.info("FileWatcher: ingesting %s", path)
        try:
            from app.file_ingestion.parsers import (
                detect_report_type,
                normalize_inventory_report,
                normalize_order_report,
                parse_csv,
                parse_excel,
                parse_pdf_text,
            )

            suffix = file.suffix.lower()
            rows: list[dict] = []
            raw_text: str = ""

            if suffix == ".csv":
                rows = parse_csv(path)
            elif suffix in {".xlsx", ".xls"}:
                rows = parse_excel(path)
            elif suffix == ".pdf":
                raw_text = parse_pdf_text(path)

            headers = list(rows[0].keys()) if rows else []
            report_type = detect_report_type(file.name, headers)

            normalized: list[dict] = []
            if report_type == "inventory":
                normalized = normalize_inventory_report(rows)
            elif report_type == "orders":
                normalized = normalize_order_report(rows)
            else:
                normalized = rows

            result = {
                "company_id": self._company_id,
                "filename": file.name,
                "report_type": report_type,
                "rows": normalized,
                "raw_text": raw_text,
                "row_count": len(normalized),
            }
            await self._on_file(self._company_id, result)

        except Exception as exc:
            log.exception("FileWatcher: failed to ingest %s: %s", path, exc)
