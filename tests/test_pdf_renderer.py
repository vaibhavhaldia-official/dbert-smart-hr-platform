"""Cross-platform PDF generation (AC-1, AC-3, AC-4)."""

import importlib
import os
import shutil
from pathlib import Path

import pytest

import pdf_renderer


def _extract_text(pdf_path):
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


HAS_LIBREOFFICE = pdf_renderer.find_soffice() is not None


class TestStrategyDetection:
    def test_strategy_is_one_of_two(self):
        assert pdf_renderer.detect_strategy() in ("libreoffice", "reportlab")

    def test_env_forces_reportlab(self, monkeypatch):
        """AC-3: the forced choice is honoured."""
        monkeypatch.setenv("GAYATRI_PDF_ENGINE", "reportlab")
        assert pdf_renderer.detect_strategy() == "reportlab"

    @pytest.mark.skipif(not HAS_LIBREOFFICE, reason="LibreOffice not installed")
    def test_env_forces_libreoffice(self, monkeypatch):
        monkeypatch.setenv("GAYATRI_PDF_ENGINE", "libreoffice")
        assert pdf_renderer.detect_strategy() == "libreoffice"

    def test_unknown_engine_falls_back_to_autodetect(self, monkeypatch):
        monkeypatch.setenv("GAYATRI_PDF_ENGINE", "nonsense")
        assert pdf_renderer.detect_strategy() in ("libreoffice", "reportlab")

    def test_strategy_detail_shape(self):
        detail = pdf_renderer.strategy_detail()
        assert set(detail) == {"strategy", "soffice_path", "forced", "full_fidelity"}
        assert detail["full_fidelity"] is (detail["strategy"] == "libreoffice")


class TestNoWindowsDependency:
    def test_no_win32_imports_anywhere(self):
        """AC-4: no Windows-only import survives in any source file."""
        root = Path(__file__).resolve().parent.parent
        offenders = []
        for py in root.glob("*.py"):
            text = py.read_text(encoding="utf-8", errors="replace")
            for token in ("win32com", "pythoncom", "pywin32"):
                if token in text:
                    offenders.append("%s contains %s" % (py.name, token))
        assert offenders == [], offenders

    def test_requirements_have_no_pywin32(self):
        """AC-4: the dependency is gone, so installs work off-Windows.

        Comments mentioning pywin32 are fine; only actual requirement lines
        would break a non-Windows install.
        """
        root = Path(__file__).resolve().parent.parent
        for name in ("requirements.txt", "requirements-dev.txt", "pyproject.toml"):
            path = root / name
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                code = line.split("#")[0].strip()
                assert "pywin32" not in code, "%s still requires pywin32: %s" % (name, line)

    def test_offer_letter_sender_imports(self):
        """AC-4: the module imports cleanly on this platform."""
        import offer_letter_sender
        assert hasattr(offer_letter_sender, "generate_offer_pdf")


class TestReportlabStrategy:
    def test_produces_valid_pdf(self, sample_docx, tmp_path, monkeypatch):
        monkeypatch.setenv("GAYATRI_PDF_ENGINE", "reportlab")
        importlib.reload(pdf_renderer)
        out = tmp_path / "out.pdf"
        pdf_renderer.convert_docx_to_pdf(sample_docx, out)
        assert out.exists() and out.stat().st_size > 0
        assert out.read_bytes()[:4] == b"%PDF"

    def test_content_is_preserved(self, sample_docx, tmp_path, monkeypatch):
        """AC-1: substituted values appear in the extracted text."""
        monkeypatch.setenv("GAYATRI_PDF_ENGINE", "reportlab")
        importlib.reload(pdf_renderer)
        out = tmp_path / "content.pdf"
        pdf_renderer.convert_docx_to_pdf(sample_docx, out)
        text = _extract_text(out)
        assert "Offer Letter" in text
        assert "{name}" in text  # template not yet substituted at this layer

    def test_missing_source_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GAYATRI_PDF_ENGINE", "reportlab")
        importlib.reload(pdf_renderer)
        with pytest.raises(pdf_renderer.PDFRenderError):
            pdf_renderer.convert_docx_to_pdf(tmp_path / "nope.docx", tmp_path / "o.pdf")

    def test_creates_missing_output_directory(self, sample_docx, tmp_path, monkeypatch):
        monkeypatch.setenv("GAYATRI_PDF_ENGINE", "reportlab")
        importlib.reload(pdf_renderer)
        out = tmp_path / "nested" / "deeper" / "out.pdf"
        pdf_renderer.convert_docx_to_pdf(sample_docx, out)
        assert out.exists()


@pytest.mark.skipif(not HAS_LIBREOFFICE, reason="LibreOffice not installed")
class TestLibreOfficeStrategy:
    def test_produces_valid_pdf(self, sample_docx, tmp_path, monkeypatch):
        monkeypatch.setenv("GAYATRI_PDF_ENGINE", "libreoffice")
        importlib.reload(pdf_renderer)
        out = tmp_path / "lo.pdf"
        pdf_renderer.convert_docx_to_pdf(sample_docx, out)
        assert out.exists()
        assert out.read_bytes()[:4] == b"%PDF"

    def test_concurrent_conversions_do_not_collide(self, sample_docx, tmp_path, monkeypatch):
        """Isolated profiles let parallel conversions succeed."""
        import threading
        monkeypatch.setenv("GAYATRI_PDF_ENGINE", "libreoffice")
        importlib.reload(pdf_renderer)
        errors, outputs = [], []

        def worker(i):
            try:
                out = tmp_path / ("c%d.pdf" % i)
                pdf_renderer.convert_docx_to_pdf(sample_docx, out)
                outputs.append(out)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], "concurrent conversion errors: %r" % errors
        assert all(o.exists() for o in outputs)

    def test_timeout_raises_render_error(self, sample_docx, tmp_path, monkeypatch):
        monkeypatch.setenv("GAYATRI_PDF_ENGINE", "libreoffice")
        importlib.reload(pdf_renderer)
        with pytest.raises(pdf_renderer.PDFRenderError):
            pdf_renderer.convert_docx_to_pdf(sample_docx, tmp_path / "t.pdf", timeout=0.001)


class TestPlaceholderSubstitution:
    def test_all_placeholders_replaced(self, sample_docx, tmp_path):
        """AC-1: no {token} may remain in generated output."""
        from offer_letter_sender import _replace_placeholders_in_docx

        replacements = {
            "{name}": "Rahul Sharma",
            "{email}": "rahul@example.com",
            "{internship}": "Full Stack Development",
            "{start_date}": "01-Oct-2026",
            "{stipend_total}": "18,000",
        }
        doc = _replace_placeholders_in_docx(str(sample_docx), replacements)
        out_docx = tmp_path / "filled.docx"
        doc.save(str(out_docx))

        os.environ["GAYATRI_PDF_ENGINE"] = "reportlab"
        importlib.reload(pdf_renderer)
        out_pdf = tmp_path / "filled.pdf"
        pdf_renderer.convert_docx_to_pdf(out_docx, out_pdf)
        text = _extract_text(out_pdf)

        assert "Rahul Sharma" in text
        assert "rahul@example.com" in text
        assert "Full Stack Development" in text
        for token in replacements:
            assert token not in text, "unreplaced placeholder %s" % token
