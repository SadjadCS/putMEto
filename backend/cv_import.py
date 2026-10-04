"""Turn an uploaded PDF CV into page images for the AI to read.

The AI reads the document itself, including its layout and scanned pages;
PutMeTo never extracts or rewrites the CV's text on its own.
"""

import io
import threading

from fastapi import HTTPException


MAX_PDF_PAGES = 30
PAGE_PIXELS = 2000  # Long side of each rendered page: small print stays legible.
NOT_A_PDF = "Upload your CV as a PDF. In Word, Pages, or Google Docs, save or download it as a PDF first."
UNREADABLE = "This PDF could not be read. Export a fresh PDF and try again."
# PDFium is not thread-safe, and uploads are read in a thread pool.
_PDFIUM_LOCK = threading.Lock()


def pdf_pages(content: bytes, filename: str, content_type: str = "") -> list[bytes]:
    """PNG images of each page of the uploaded PDF."""
    import pypdfium2 as pdfium

    # Exported PDFs can have no extension or a browser-supplied filename. Detect
    # their actual header instead of rejecting a valid PDF based on its name.
    filename = filename.strip().lower()
    content_type = content_type.split(";", 1)[0].strip().lower()
    if not (b"%PDF-" in content[:1024] or filename.endswith(".pdf") or content_type == "application/pdf"):
        raise HTTPException(415, NOT_A_PDF)
    with _PDFIUM_LOCK:
        try:
            document = pdfium.PdfDocument(content)
        except pdfium.PdfiumError as exc:
            if "password" in str(exc).lower():
                raise HTTPException(422, "This PDF is password protected. Upload an unlocked copy.") from exc
            raise HTTPException(422, UNREADABLE) from exc
        try:
            if len(document) > MAX_PDF_PAGES:
                raise HTTPException(422, f"Upload a CV with at most {MAX_PDF_PAGES} pages.")
            pages, visible = [], False
            for page in document:
                width, height = page.get_size()
                image = page.render(scale=PAGE_PIXELS / max(width, height, 1)).to_pil()
                # A page with no dark pixels has nothing for the AI to read.
                visible = visible or image.convert("L").getextrema()[0] < 245
                stream = io.BytesIO()
                image.save(stream, format="PNG")
                pages.append(stream.getvalue())
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, UNREADABLE) from exc
        finally:
            document.close()
    if not visible:
        raise HTTPException(422, "This PDF has no visible content. Check that you chose the right file.")
    return pages
