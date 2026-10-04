"""CVs are PDFs, and each page becomes an image the AI reads."""

import asyncio
import io
import json

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

from backend import ai
from backend.cv_import import MAX_PDF_PAGES, pdf_pages


def pdf(*pages):
    stream = io.BytesIO()
    document = canvas.Canvas(stream)
    for lines in pages:
        for index, line in enumerate(lines):
            document.drawString(40, 760 - 20 * index, line)
        document.showPage()
    document.save()
    return stream.getvalue()


def test_each_pdf_page_becomes_a_legible_image():
    pages = pdf_pages(pdf(["Ada Applicant", "Built Python APIs."], ["Page two content"]), "cv.pdf")
    assert len(pages) == 2
    image = Image.open(io.BytesIO(pages[0]))
    assert image.format == "PNG" and max(image.size) == 2000


def test_blank_oversized_and_broken_pdfs_are_rejected_before_any_ai_request():
    with pytest.raises(HTTPException, match="no visible content"):
        pdf_pages(pdf([]), "blank.pdf")
    with pytest.raises(HTTPException, match=f"at most {MAX_PDF_PAGES} pages"):
        pdf_pages(pdf(*[["Page"]] * (MAX_PDF_PAGES + 1)), "long.pdf")
    with pytest.raises(HTTPException, match="could not be read"):
        pdf_pages(b"%PDF-1.4 not really a PDF", "broken.pdf")


def test_password_protected_pdf_has_actionable_error():
    writer = PdfWriter(clone_from=io.BytesIO(pdf(["Private CV"])))
    writer.encrypt("secret")
    stream = io.BytesIO()
    writer.write(stream)
    with pytest.raises(HTTPException, match="password protected"):
        pdf_pages(stream.getvalue(), "locked.pdf")


@pytest.mark.parametrize("filename,content,content_type", [
    ("cv.docx", b"PK\x03\x04synthetic Word file", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("cv.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1synthetic", "application/msword"),
    ("cv.txt", b"Ada Applicant built Python APIs.", "text/plain"),
])
def test_other_file_types_are_asked_to_be_saved_as_pdf(filename, content, content_type):
    with pytest.raises(HTTPException, match="as a PDF") as error:
        pdf_pages(content, filename, content_type)
    assert error.value.status_code == 415


def test_compatible_provider_receives_pages_as_images_and_rejection_means_unsupported(monkeypatch):
    requests, status = [], [200]
    original_client = httpx.AsyncClient

    def handle(request):
        requests.append(json.loads(request.content))
        if status[0] != 200:
            return httpx.Response(status[0], json={"error": "image input is not supported"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"lines": ["Ada Applicant"]}'}}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: original_client(*args, **{**kwargs, "transport": httpx.MockTransport(handle)}))
    settings = {"provider": "compatible", "base_url": "http://127.0.0.1:9999/v1", "model": "vision-model"}
    assert asyncio.run(ai.transcribe_pdf(settings, [b"png-bytes"])) == ["Ada Applicant"]
    text, picture = requests[0]["messages"][1]["content"]
    assert json.loads(text["text"]) == {"page_count": 1}
    assert picture == {"type": "image_url", "image_url": {"url": "data:image/png;base64,cG5nLWJ5dGVz", "detail": "high"}}

    status[0] = 400
    with pytest.raises(ai.ImagesUnsupported):
        asyncio.run(ai.transcribe_pdf(settings, [b"png-bytes"]))
