"""Word imports preserve text and reject malformed or excessive archives."""

import io
import struct
import zipfile

import pytest
from fastapi import HTTPException

from backend import documents


WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
STRICT_WORD = "http://purl.oclc.org/ooxml/wordprocessingml/main"


def document(body, namespace=WORD):
    return f'<w:document xmlns:w="{namespace}"><w:body>{body}</w:body></w:document>'.encode()


def paragraph(text):
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def docx(parts):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.parametrize("namespace", [WORD, STRICT_WORD])
def test_docx_preserves_paragraph_table_and_run_order(namespace):
    body = (
        paragraph("Example Applicant")
        + '<w:p><w:r><w:t>Skills</w:t><w:tab/><w:t>Python</w:t><w:br/><w:t>SQL</w:t></w:r></w:p>'
        + "<w:tbl><w:tr><w:tc>" + paragraph("Vector search") + "</w:tc><w:tc>" + paragraph("PostgreSQL") + "</w:tc></w:tr></w:tbl>"
        + paragraph("Education")
    )
    result = documents.extract_docx(docx({"word/document.xml": document(body, namespace)}))
    assert result == "Example Applicant\nSkills\tPython\nSQL\nVector search\tPostgreSQL\nEducation"


def test_docx_reads_optional_headers_and_footers_without_following_links():
    parts = {
        "word/document.xml": document('<w:p><w:hyperlink><w:r><w:t>Displayed link</w:t></w:r></w:hyperlink></w:p>'),
        "word/header1.xml": f'<w:hdr xmlns:w="{WORD}">{paragraph("Contact details")}</w:hdr>',
        "word/footer1.xml": f'<w:ftr xmlns:w="{WORD}">{paragraph("Footer")}</w:ftr>',
        "word/_rels/document.xml.rels": '<Relationships><Relationship Target="https://example.invalid/private" TargetMode="External"/></Relationships>',
        "word/vbaProject.bin": b"not executed or parsed",
        "../../outside.txt": b"not extracted",
    }
    assert documents.extract_docx(docx(parts)) == "Contact details\nDisplayed link\nFooter"


def test_docx_ignores_deleted_text_and_field_instructions():
    body = '<w:p><w:del><w:r><w:delText>Deleted claim</w:delText></w:r></w:del><w:r><w:instrText>HYPERLINK secret</w:instrText><w:t>Current text</w:t></w:r></w:p>'
    assert documents.extract_docx(docx({"word/document.xml": document(body)})) == "Current text"


def test_docx_keeps_paragraph_boundaries_inside_text_boxes():
    body = '<w:p><w:r><w:drawing><w:txbxContent>' + paragraph("Technical Skills") + paragraph("Python and SQL") + '</w:txbxContent></w:drawing></w:r></w:p>'
    result = documents.extract_docx(docx({"word/document.xml": document(body)}))
    assert result.splitlines() == ["Technical Skills", "", "Python and SQL"]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_docx_rejects_dtd_and_entities_in_all_xml_encodings(encoding):
    xml = f'<?xml version="1.0" encoding="{encoding}"?><!DOCTYPE document [<!ENTITY injected "Claim">]><w:document xmlns:w="{WORD}"><w:body><w:p><w:r><w:t>&injected;</w:t></w:r></w:p></w:body></w:document>'
    with pytest.raises(HTTPException, match="unsupported XML declarations") as error:
        documents.extract_docx(docx({"word/document.xml": xml.encode(encoding)}))
    assert error.value.status_code == 422


@pytest.mark.parametrize("parts", [None, {}, {"word/document.xml": b"<broken"}, {"word/document.xml": b"<document/>"}])
def test_docx_rejects_invalid_archives_and_document_xml(parts):
    content = b"not a zip" if parts is None else docx(parts)
    with pytest.raises(HTTPException) as error:
        documents.extract_docx(content)
    assert error.value.status_code == 422
    assert ".docx" in error.value.detail


def test_docx_rejects_encrypted_zip_parts():
    content = bytearray(docx({"word/document.xml": document(paragraph("Text"))}))
    central = content.index(b"PK\x01\x02")
    flags = struct.unpack_from("<H", content, central + 8)[0]
    struct.pack_into("<H", content, central + 8, flags | 1)
    with pytest.raises(HTTPException, match="encrypted"):
        documents.extract_docx(bytes(content))


def test_docx_bounds_archive_parts_and_expanded_size(monkeypatch):
    content = docx({"word/document.xml": document(paragraph("Text")), "word/media/large.bin": b"0" * 2000})
    monkeypatch.setattr(documents, "MAX_ARCHIVE_FILES", 1)
    with pytest.raises(HTTPException, match="too many"):
        documents.extract_docx(content)
    monkeypatch.setattr(documents, "MAX_ARCHIVE_FILES", 2000)
    monkeypatch.setattr(documents, "MAX_EXPANDED_BYTES", 1000)
    with pytest.raises(HTTPException, match="expands beyond"):
        documents.extract_docx(content)


def test_docx_bounds_combined_selected_xml(monkeypatch):
    main = document(paragraph("Example"))
    header = f'<w:hdr xmlns:w="{WORD}">{paragraph("Contact")}</w:hdr>'.encode()
    monkeypatch.setattr(documents, "MAX_XML_BYTES", len(main) + len(header) - 1)
    with pytest.raises(HTTPException, match="too much document XML"):
        documents.extract_docx(docx({"word/document.xml": main, "word/header1.xml": header}))


def test_docx_rejects_more_than_70000_extracted_characters():
    with pytest.raises(HTTPException, match="70,000"):
        documents.extract_docx(docx({"word/document.xml": document(paragraph("x" * 70001))}))


def test_docx_blank_content_is_left_for_the_common_import_validation():
    assert documents.extract_docx(docx({"word/document.xml": document("<w:p/>")})) == ""


def test_docx_rejects_excessively_deep_xml():
    body = "<w:sdt>" * 260 + paragraph("Text") + "</w:sdt>" * 260
    with pytest.raises(HTTPException, match="too complex"):
        documents.extract_docx(docx({"word/document.xml": document(body)}))
