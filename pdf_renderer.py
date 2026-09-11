"""
pdf_renderer.py — Platform-neutral DOCX to PDF conversion.

Phase 1 (AC-1, AC-3, AC-4): replaces the Windows-only MS Word COM automation
that previously lived in offer_letter_sender.py. Every platform-specific concern
is isolated behind convert_docx_to_pdf().

Strategies, in preference order:

  libreoffice  Runs `soffice --headless --convert-to pdf` as a subprocess.
               Renders the existing sampleoffer.docx template faithfully on
               Windows, macOS and Linux, so candidate-facing output does not
               change. This is the primary path.

  reportlab    Re-emits the document's text content as a clean single-column
               PDF. Content is correct but layout is simplified. This is a
               degraded fallback for machines without LibreOffice, and it warns
               loudly at startup rather than failing silently (AC-3).

Set GAYATRI_PDF_ENGINE=libreoffice|reportlab to force a strategy.
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("pdf_renderer")

LIBREOFFICE = "libreoffice"
REPORTLAB = "reportlab"


class PDFRenderError(RuntimeError):
    """Raised when a DOCX could not be converted to a PDF."""


# ============================================================
# STRATEGY DETECTION
# ============================================================

def _soffice_candidates():
    """Return plausible LibreOffice executable locations for this platform."""
    names = ["soffice", "libreoffice"]
    extra = []
    if sys.platform == "darwin":
        extra = [
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        ]
    elif sys.platform.startswith("win"):
        extra = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
    else:
        extra = [
            "/usr/bin/soffice",
            "/usr/bin/libreoffice",
            "/snap/bin/libreoffice",
            "/opt/libreoffice/program/soffice",
        ]
    return names, extra


def find_soffice():
    """Locate the LibreOffice binary, or return None.

    An explicit GAYATRI_SOFFICE_PATH wins, so an operator with a non-standard
    install does not need this function to guess correctly.
    """
    explicit = os.environ.get("GAYATRI_SOFFICE_PATH")
    if explicit:
        p = Path(explicit).expanduser()
        if p.exists():
            return str(p)
        logger.warning("GAYATRI_SOFFICE_PATH=%s does not exist; ignoring.", explicit)

    names, extra = _soffice_candidates()
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for path in extra:
        if Path(path).exists():
            return path
    return None


def detect_strategy():
    """Resolve the active strategy name, honouring an explicit override."""
    forced = os.environ.get("GAYATRI_PDF_ENGINE", "").strip().lower()
    if forced in (LIBREOFFICE, REPORTLAB):
        return forced
    if forced:
        logger.warning(
            "GAYATRI_PDF_ENGINE=%r is not recognised; auto-detecting instead.", forced
        )
    return LIBREOFFICE if find_soffice() else REPORTLAB


# Resolved once at import so the startup warning is emitted exactly once (AC-3).
STRATEGY = detect_strategy()
SOFFICE_PATH = find_soffice() if STRATEGY == LIBREOFFICE else None

if STRATEGY == REPORTLAB:
    logger.warning(
        "PDF engine: reportlab (LibreOffice not found). Offer letters will be "
        "generated with simplified layout rather than the DOCX template. "
        "Install LibreOffice for full-fidelity output, or set "
        "GAYATRI_SOFFICE_PATH if it is installed in a non-standard location."
    )
else:
    logger.info("PDF engine: libreoffice (%s)", SOFFICE_PATH)


def active_strategy():
    """Return the strategy name in use — shown on the dashboard settings page."""
    return STRATEGY


def strategy_detail():
    """Return a dict describing the active engine, for diagnostics."""
    return {
        "strategy": STRATEGY,
        "soffice_path": SOFFICE_PATH,
        "forced": bool(os.environ.get("GAYATRI_PDF_ENGINE")),
        "full_fidelity": STRATEGY == LIBREOFFICE,
    }


# ============================================================
# LIBREOFFICE STRATEGY
# ============================================================

def _convert_via_libreoffice(docx_path, pdf_path, timeout):
    """Convert using headless LibreOffice.

    Two details matter for correctness:

    1. Each invocation gets its own -env:UserInstallation profile directory.
       Concurrent soffice processes sharing a profile will hang or fail, and
       offer campaigns run in a daemon thread while the operator may also be
       using the dashboard.

    2. LibreOffice ignores the requested output filename and always writes
       <stem>.pdf into --outdir, so we convert into a temp directory and move
       the result to the caller's chosen path.
    """
    soffice = SOFFICE_PATH or find_soffice()
    if not soffice:
        raise PDFRenderError(
            "LibreOffice strategy selected but no soffice binary was found."
        )

    docx_path = Path(docx_path)
    pdf_path = Path(pdf_path)

    workdir = Path(tempfile.mkdtemp(prefix="gayatri_lo_"))
    profile_dir = workdir / "profile"
    outdir = workdir / "out"
    outdir.mkdir(parents=True, exist_ok=True)

    try:
        cmd = [
            soffice,
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            "--nofirststartwizard",
            "-env:UserInstallation=%s" % profile_dir.as_uri(),
            "--convert-to", "pdf",
            "--outdir", str(outdir),
            str(docx_path),
        ]
        try:
            proc = subprocess.run(
                cmd,
                timeout=timeout,
                capture_output=True,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise PDFRenderError(
                "LibreOffice timed out after %ss converting %s"
                % (timeout, docx_path.name)
            )
        except OSError as exc:
            raise PDFRenderError("Failed to launch LibreOffice: %s" % exc)

        produced = outdir / (docx_path.stem + ".pdf")
        if not produced.exists():
            stderr = (proc.stderr or b"").decode("utf-8", "replace").strip()
            stdout = (proc.stdout or b"").decode("utf-8", "replace").strip()
            raise PDFRenderError(
                "LibreOffice did not produce a PDF for %s (exit %s). "
                "stdout=%r stderr=%r"
                % (docx_path.name, proc.returncode, stdout[:500], stderr[:500])
            )

        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(produced), str(pdf_path))
        return pdf_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ============================================================
# REPORTLAB FALLBACK STRATEGY
# ============================================================

def _iter_docx_blocks(docx_path):
    """Yield (kind, payload) for paragraphs and tables in document order.

    python-docx exposes paragraphs and tables as separate collections, which
    loses their interleaving; walking the body XML preserves reading order so
    the fallback PDF does not reorder the letter.
    """
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(docx_path))
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            para = Paragraph(child, doc)
            yield ("p", para.text)
        elif tag == "tbl":
            table = Table(child, doc)
            rows = []
            for row in table.rows:
                rows.append([cell.text.strip() for cell in row.cells])
            yield ("table", rows)


def _convert_via_reportlab(docx_path, pdf_path):
    """Render the DOCX's text content as a clean single-column PDF."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
        )
    except ImportError as exc:
        raise PDFRenderError(
            "reportlab is required for the fallback PDF engine but is not "
            "installed (%s). Install it, or install LibreOffice." % exc
        )

    from xml.sax.saxutils import escape

    docx_path = Path(docx_path)
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "GayatriBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=15,
        alignment=TA_LEFT,
        spaceAfter=4,
    )
    cell_style = ParagraphStyle(
        "GayatriCell", parent=body_style, fontSize=9.5, leading=13, spaceAfter=0
    )

    story = []
    for kind, payload in _iter_docx_blocks(docx_path):
        if kind == "p":
            text = (payload or "").strip()
            if text:
                story.append(Paragraph(escape(text), body_style))
            else:
                story.append(Spacer(1, 5))
        elif kind == "table" and payload:
            data = [
                [Paragraph(escape(c or ""), cell_style) for c in row]
                for row in payload
            ]
            table = Table(data, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b0b0b0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(Spacer(1, 5))
            story.append(table)
            story.append(Spacer(1, 5))

    if not story:
        story.append(Paragraph("(empty document)", body_style))

    SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=pdf_path.stem,
    ).build(story)

    if not pdf_path.exists():
        raise PDFRenderError("reportlab did not produce %s" % pdf_path)
    return pdf_path


# ============================================================
# PUBLIC API
# ============================================================

def convert_docx_to_pdf(docx_path, pdf_path, timeout=None):
    """Convert a DOCX file to PDF using the active strategy.

    Returns the Path to the generated PDF. Raises PDFRenderError on failure;
    callers in the campaign loop treat that as a per-recipient failure and
    continue with the next candidate.
    """
    if timeout is None:
        timeout = int(os.environ.get("GAYATRI_PDF_TIMEOUT", "120") or 120)

    docx_path = Path(docx_path)
    pdf_path = Path(pdf_path)

    if not docx_path.exists():
        raise PDFRenderError("Source document not found: %s" % docx_path)

    if STRATEGY == LIBREOFFICE:
        return _convert_via_libreoffice(docx_path, pdf_path, timeout)
    return _convert_via_reportlab(docx_path, pdf_path)
