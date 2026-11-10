"""Text recognition (OCR) for scanned PDFs and photos: opt-in, bounded, stateless."""
from __future__ import annotations

import io
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from lola_fakes import CO_A, OWNER, seed

from app.api.deps import get_db
from app.auth.jwt import create_access_token
from app.lola import ocr
from app.lola.file_extraction import ExtractionRejected, extract
from app.main import app


def scanned_pdf(text: str = "INVOICE 4471 TOTAL 120.50", pages: int = 1) -> bytes:
    """An image-only PDF (no text layer), like a scanner produces."""
    imgs = []
    for _ in range(pages):
        img = Image.new("L", (1400, 400), 255)
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=64)
        except TypeError:                                # very old Pillow
            font = ImageFont.load_default()
        d.text((40, 150), text, fill=0, font=font)
        imgs.append(img)
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def png(w: int = 400, h: int = 120) -> bytes:
    buf = io.BytesIO()
    Image.new("L", (w, h), 255).save(buf, format="PNG")
    return buf.getvalue()


def png_claiming(w: int, h: int) -> bytes:
    """A PNG whose header claims a huge size (decompression bomb shape)."""
    data = bytearray(png())
    ihdr = data[16:29]
    ihdr[0:8] = struct.pack(">II", w, h)
    data[16:29] = ihdr
    data[29:33] = struct.pack(">I", zlib.crc32(b"IHDR" + bytes(ihdr)) & 0xFFFFFFFF)
    return bytes(data)


class FakePrograms:
    """Stands in for pdftoppm / tesseract and records how they were called."""

    def __init__(self, text: str = "INVOICE 4471 TOTAL 120.50", fail: str | None = None):
        self.calls: list[list[str]] = []
        self.envs: list[dict] = []
        self.text = text
        self.fail = fail
        self.tmpdirs: list[Path] = []

    def run(self, args, **kw):
        args = [Path(args[0]).name, *args[1:]]          # /usr/bin/tesseract → tesseract
        self.calls.append(list(args))
        self.envs.append(kw.get("env") or {})
        assert kw.get("shell") in (None, False)
        if self.fail == "timeout":
            raise subprocess.TimeoutExpired(args, kw.get("timeout"))
        if self.fail == "error":
            return subprocess.CompletedProcess(args, 1, b"", b"boom")
        if args[0] == "pdftoppm":
            first, last = int(args[args.index("-f") + 1]), int(args[args.index("-l") + 1])
            prefix = Path(args[-1])
            self.tmpdirs.append(prefix.parent)
            for i in range(first, last + 1):
                (prefix.parent / f"p-{i:02d}.png").write_bytes(png())
            return subprocess.CompletedProcess(args, 0, b"", b"")
        self.tmpdirs.append(Path(args[1]).parent)
        return subprocess.CompletedProcess(args, 0, self.text.encode(), b"")


@pytest.fixture
def ocr_on(monkeypatch):
    monkeypatch.setenv("LOLA_OCR", "1")
    monkeypatch.setattr(ocr.shutil, "which", lambda name: f"/usr/bin/{name}")
    fake = FakePrograms()
    monkeypatch.setattr(ocr.subprocess, "run", fake.run)
    return fake


class TestSwitchedOff:
    def test_scanned_pdf_refused_when_off(self, monkeypatch):
        monkeypatch.delenv("LOLA_OCR", raising=False)
        with pytest.raises(ExtractionRejected) as e:
            extract("scan.pdf", scanned_pdf())
        assert e.value.code == "no_text_in_pdf"

    def test_scanned_pdf_refused_when_programs_missing(self, monkeypatch):
        monkeypatch.setenv("LOLA_OCR", "1")
        monkeypatch.setattr(ocr.shutil, "which", lambda name: None)
        with pytest.raises(ExtractionRejected) as e:
            extract("scan.pdf", scanned_pdf())
        assert e.value.code == "no_text_in_pdf"

    def test_photo_refused_when_off(self, monkeypatch):
        monkeypatch.delenv("LOLA_OCR", raising=False)
        with pytest.raises(ExtractionRejected) as e:
            extract("slip.png", png())
        assert e.value.code == "ocr_off"


class TestScannedPdf:
    def test_text_is_recognised_and_flagged(self, ocr_on):
        out = extract("scan.pdf", scanned_pdf())
        assert out.ocr is True and out.kind == "pdf" and "4471" in out.text
        tools = [c[0] for c in ocr_on.calls]
        assert tools == ["pdftoppm", "tesseract"]

    def test_pages_and_size_are_capped(self, ocr_on):
        out = extract("scan.pdf", scanned_pdf(pages=ocr.MAX_OCR_PAGES + 3))
        render = ocr_on.calls[0]
        assert render[render.index("-l") + 1] == str(ocr.MAX_OCR_PAGES)
        assert render[render.index("-scale-to") + 1] == str(ocr.RENDER_MAX_PX)
        assert len([c for c in ocr_on.calls if c[0] == "tesseract"]) == ocr.MAX_OCR_PAGES
        assert out.truncated is True and out.pages == ocr.MAX_OCR_PAGES + 3

    def test_a_pdf_with_text_never_runs_ocr(self, ocr_on):
        from test_lola_file_extraction import make_pdf
        out = extract("po.pdf", make_pdf(["Olive Oil x 50"]))
        assert out.ocr is False and ocr_on.calls == []

    def test_programs_get_no_secrets(self, ocr_on, monkeypatch):
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "secret-value")
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy")
        extract("scan.pdf", scanned_pdf())
        for env in ocr_on.envs:
            assert "SUPABASE_SERVICE_KEY" not in env and "HTTPS_PROXY" not in env
            assert env.get("OMP_THREAD_LIMIT") == "1"

    def test_nothing_is_left_on_disk(self, ocr_on):
        extract("scan.pdf", scanned_pdf())
        assert ocr_on.tmpdirs and all(not d.exists() for d in ocr_on.tmpdirs)

    def test_slow_file_is_stopped(self, ocr_on):
        ocr_on.fail = "timeout"
        with pytest.raises(ExtractionRejected) as e:
            extract("scan.pdf", scanned_pdf())
        assert e.value.code == "too_slow"

    def test_program_error(self, ocr_on):
        ocr_on.fail = "error"
        with pytest.raises(ExtractionRejected) as e:
            extract("scan.pdf", scanned_pdf())
        assert e.value.code == "ocr_failed"

    def test_blank_scan(self, ocr_on):
        ocr_on.text = "   \n  "
        with pytest.raises(ExtractionRejected) as e:
            extract("scan.pdf", scanned_pdf())
        assert e.value.code == "ocr_no_text"

    def test_busy_worker_refuses_instead_of_queueing(self, ocr_on):
        held = [ocr._slots.acquire(blocking=False) for _ in range(ocr.MAX_CONCURRENT)]
        try:
            assert all(held)
            with pytest.raises(ExtractionRejected) as e:
                extract("scan.pdf", scanned_pdf())
            assert e.value.code == "ocr_busy" and ocr_on.calls == []
        finally:
            for _ in held:
                ocr._slots.release()


class TestOnlyKnownProgramsWithSafeArguments:
    def test_unknown_program_refused(self, ocr_on, tmp_path):
        with pytest.raises(ocr.OCRUnavailable):
            ocr._command("sh", ["-c", "id"], tmp_path)

    def test_program_runs_from_its_absolute_path(self, ocr_on, tmp_path):
        assert ocr._command("tesseract", [str(tmp_path / "a.png"), "stdout"], tmp_path)[0] == "/usr/bin/tesseract"

    @pytest.mark.parametrize("bad", ["a; rm -rf /", "$(id)", "a b", "`id`", "x|y", "\nnew"])
    def test_shell_like_arguments_refused(self, ocr_on, tmp_path, bad):
        with pytest.raises(ocr.OCRFailed):
            ocr._command("tesseract", [bad], tmp_path)

    def test_paths_outside_the_private_folder_refused(self, ocr_on, tmp_path):
        with pytest.raises(ocr.OCRFailed):
            ocr._command("tesseract", ["/etc/passwd", "stdout"], tmp_path)
        with pytest.raises(ocr.OCRFailed):
            ocr._command("tesseract", [str(tmp_path / ".." / "escape.png")], tmp_path)

    def test_no_shell_is_ever_used(self, ocr_on):
        extract("scan.pdf", scanned_pdf())
        assert ocr_on.calls                                   # FakePrograms asserts shell is falsy


class TestPhotos:
    def test_png_is_recognised(self, ocr_on):
        out = extract("slip.png", png())
        assert out.ocr is True and out.kind == "image" and "4471" in out.text
        assert [c[0] for c in ocr_on.calls] == ["tesseract"]

    def test_jpeg_is_recognised(self, ocr_on):
        buf = io.BytesIO()
        Image.new("RGB", (300, 100), "white").save(buf, format="JPEG")
        assert extract("slip.jpg", buf.getvalue()).ocr is True

    def test_huge_image_refused_before_decoding(self, ocr_on):
        with pytest.raises(ExtractionRejected) as e:
            extract("bomb.png", png_claiming(100_000, 100_000))
        assert e.value.code == "image_too_large" and ocr_on.calls == []

    def test_large_photo_over_our_cap_refused(self, ocr_on):
        with pytest.raises(ExtractionRejected) as e:
            extract("big.png", png_claiming(7_000, 7_000))     # 49 MP: under Pillow's limit, over ours
        assert e.value.code == "image_too_large" and ocr_on.calls == []

    def test_busy_worker_refuses_photos_before_decoding(self, ocr_on, monkeypatch):
        from PIL import ImageOps
        decoded = []
        monkeypatch.setattr(ImageOps, "exif_transpose", lambda im: decoded.append(1) or im)
        held = [ocr._slots.acquire(blocking=False) for _ in range(ocr.MAX_CONCURRENT)]
        try:
            with pytest.raises(ExtractionRejected) as e:
                extract("slip.png", png())
            assert e.value.code == "ocr_busy" and decoded == [] and ocr_on.calls == []
        finally:
            for _ in held:
                ocr._slots.release()
        assert extract("slip.png", png()).ocr is True and decoded == [1]

    def test_broken_image(self, ocr_on):
        with pytest.raises(ExtractionRejected) as e:
            extract("bad.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
        assert e.value.code == "unreadable_image" and ocr_on.calls == []


class TestEndpoint:
    @pytest.fixture
    def db(self):
        d = seed()
        app.dependency_overrides[get_db] = lambda: d
        yield d
        app.dependency_overrides.clear()

    def _h(self, **extra):
        tok = create_access_token({"sub": OWNER, "company_id": CO_A, "role": "owner"})
        return {"Authorization": "Bearer " + tok, **extra}

    def test_response_carries_flag_and_caution(self, db, ocr_on):
        r = TestClient(app).post("/api/lola/files", headers=self._h(),
                                 files={"file": ("scan.pdf", scanned_pdf(), "application/pdf")})
        assert r.status_code == 200
        body = r.json()
        assert body["ocr"] is True and "4471" in body["text"] and "Check numbers" in body["note"]

    def test_spanish_caution(self, db, ocr_on):
        r = TestClient(app).post("/api/lola/files", headers=self._h(**{"Accept-Language": "es-MX,es;q=0.9"}),
                                 files={"file": ("foto.jpg", png(), "image/png")})
        assert r.status_code == 200 and "reconocimiento de texto" in r.json()["note"]

    def test_normal_pdf_has_no_caution(self, db, ocr_on):
        from test_lola_file_extraction import make_pdf
        r = TestClient(app).post("/api/lola/files", headers=self._h(),
                                 files={"file": ("po.pdf", make_pdf(["Olive Oil x 50"]), "application/pdf")})
        assert r.status_code == 200 and r.json()["ocr"] is False and r.json()["note"] is None

    def test_photo_when_off_is_a_plain_spanish_message(self, db, monkeypatch):
        monkeypatch.delenv("LOLA_OCR", raising=False)
        r = TestClient(app).post("/api/lola/files", headers=self._h(**{"Accept-Language": "es"}),
                                 files={"file": ("foto.png", png(), "image/png")})
        assert r.status_code == 422 and "no está activado" in r.json()["detail"]


@pytest.mark.skipif(not (shutil.which("tesseract") and shutil.which("pdftoppm")),
                    reason="tesseract / poppler not installed")
def test_real_ocr_reads_a_scan(monkeypatch):
    monkeypatch.setenv("LOLA_OCR", "1")
    out = extract("scan.pdf", scanned_pdf())
    assert out.ocr is True and "4471" in out.text
