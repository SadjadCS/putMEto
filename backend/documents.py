"""Read bounded DOCX text in memory without extracting files or following links."""

import io
import re
import xml.etree.ElementTree as ET
import zipfile

from fastapi import HTTPException


MAX_ARCHIVE_FILES = 2000
MAX_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_XML_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 70000
WORD_NAMESPACES = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://purl.oclc.org/ooxml/wordprocessingml/main",
}
EXTRA_PART = re.compile(r"word/(header|footer)(\d*)\.xml\Z")


def _reject(message: str) -> None:
    raise HTTPException(422, message)


class _DocumentTree(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.nodes = 0

    def doctype(self, name, pubid, system):
        # Parser callbacks catch declarations in UTF-8 and UTF-16 alike, before
        # entity expansion. ElementTree never resolves external relationships.
        _reject("This DOCX contains unsupported XML declarations or entities. Export a fresh .docx or .txt file.")

    def start(self, tag, attrs):
        self.depth += 1
        self.nodes += 1
        if self.depth > 256 or self.nodes > 200000:
            _reject("This DOCX is too complex to read. Export a simpler .docx or a UTF-8 .txt file.")
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)


def _tag(element: ET.Element) -> str | None:
    if not isinstance(element.tag, str) or not element.tag.startswith("{"):
        return None
    namespace, _, name = element.tag[1:].partition("}")
    return name if namespace in WORD_NAMESPACES else None


def _inline(element: ET.Element):
    tag = _tag(element)
    if tag in {"del", "instrText", "delText"}:
        return
    if tag == "t":
        yield element.text or ""
    elif tag == "tab":
        yield "\t"
    elif tag in {"br", "cr"}:
        yield "\n"
    elif tag == "noBreakHyphen":
        yield "\u2011"
    elif tag == "softHyphen":
        yield "\u00ad"
    else:
        if tag == "p":
            # Text boxes can contain paragraphs inside an outer paragraph.
            # Keep their boundaries instead of joining skill names together.
            yield "\n"
        for child in element:
            yield from _inline(child)
        if tag == "p":
            yield "\n"


def _children_with_tag(element: ET.Element, wanted: str):
    """Handle content-control wrappers without also traversing nested tables."""
    for child in element:
        tag = _tag(child)
        if tag == wanted:
            yield child
        elif tag not in {"tbl", "tr", "tc", "p", "del"}:
            yield from _children_with_tag(child, wanted)


def _blocks(element: ET.Element):
    tag = _tag(element)
    if tag == "del":
        return
    if tag == "p":
        text = "".join(_inline(element)).strip()
        if text:
            yield text
    elif tag == "tbl":
        for row in _children_with_tag(element, "tr"):
            cells = ["\n".join(_blocks(cell)).strip() for cell in _children_with_tag(row, "tc")]
            if any(cells):
                yield "\t".join(cells)
    else:
        for child in element:
            yield from _blocks(child)


def extract_docx(content: bytes) -> str:
    """Return ordered paragraphs/table text; invalid or oversized files get 422.

    Only the main document and optional header/footer XML are read. Relationships,
    embedded files, external links, images, and macros are never opened or run.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_FILES:
                _reject("This DOCX contains too many archive parts. Export a simpler .docx or a UTF-8 .txt file.")
            if any(entry.flag_bits & 1 for entry in entries):
                _reject("This DOCX is encrypted. Upload an unlocked copy or a UTF-8 .txt file.")
            if sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
                _reject("This DOCX expands beyond the 50 MB limit. Remove large embedded content or export a .txt file.")
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)):
                _reject("This DOCX contains duplicate archive parts. Export a fresh .docx or .txt file.")
            if "word/document.xml" not in names:
                _reject("This file is not a readable DOCX document. Save it as a Word .docx file or export UTF-8 .txt.")
            extras = [name for name in names if EXTRA_PART.fullmatch(name)]
            extras.sort(key=lambda name: (int(EXTRA_PART.fullmatch(name).group(2) or 0), name))
            selected = [name for name in extras if "/header" in name] + ["word/document.xml"] + [name for name in extras if "/footer" in name]
            if sum(archive.getinfo(name).file_size for name in selected) > MAX_XML_BYTES:
                _reject("This DOCX has too much document XML. Export a simpler .docx or a UTF-8 .txt file.")
            pieces, total, read_bytes = [], 0, 0
            for name in selected:
                with archive.open(name) as part:
                    xml = part.read(MAX_XML_BYTES - read_bytes + 1)
                read_bytes += len(xml)
                if read_bytes > MAX_XML_BYTES:
                    _reject("This DOCX has too much document XML. Export a simpler .docx or a UTF-8 .txt file.")
                root = ET.fromstring(xml, parser=ET.XMLParser(target=_DocumentTree()))
                expected = "document" if name == "word/document.xml" else "hdr" if "/header" in name else "ftr"
                if _tag(root) != expected:
                    _reject("This DOCX has invalid document XML. Export a fresh .docx or a UTF-8 .txt file.")
                for text in _blocks(root):
                    total += len(text) + (1 if pieces else 0)
                    if total > MAX_TEXT_CHARS:
                        _reject("This CV is too long. Upload at most 70,000 characters of text.")
                    pieces.append(text)
            return "\n".join(pieces).strip()
    except HTTPException:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ET.ParseError, OSError, ValueError, RuntimeError, NotImplementedError, RecursionError) as exc:
        _reject("This DOCX could not be read. Export a fresh, unlocked .docx file or upload UTF-8 .txt.")
