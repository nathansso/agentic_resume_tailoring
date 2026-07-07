"""Document text extraction with an optional docling backend.

docling's layout-aware conversion depends on PyTorch and loads layout models
at parse time — far too heavy for low-memory deployments (the 512 MB Fly.io
VM gets OOM-killed). docling is therefore a full-only dependency: when it is
not installed, extraction falls back to pypdf / python-docx plain-text, which
is sufficient because the extracted text is handed to an LLM parser anyway.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def docling_available() -> bool:
    try:
        import docling.document_converter  # noqa: F401
        return True
    except ImportError:
        return False


def extract_markdown(file_path: str) -> str:
    """Extract document text as markdown-ish plain text.

    Uses docling when installed; otherwise the lightweight fallback.
    """
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        logger.info("docling not installed — using lightweight text extraction")
        return extract_text_lightweight(file_path)
    result = DocumentConverter().convert(file_path)
    if result.status != "success":
        raise RuntimeError(f"Docling failed: {result.errors}")
    return result.document.export_to_markdown()


def extract_text_lightweight(file_path: str) -> str:
    """Plain-text extraction without docling: pypdf for PDF, python-docx for DOCX."""
    suffix = Path(file_path).suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        pages = (page.extract_text() or "" for page in reader.pages)
        return "\n".join(p for p in pages if p.strip())
    if suffix == ".docx":
        import docx
        document = docx.Document(file_path)
        return "\n".join(p.text for p in document.paragraphs if p.text.strip())
    return Path(file_path).read_text(encoding="utf-8", errors="replace")
