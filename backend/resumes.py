"""The resume template (see template.docx), shared by the HTML preview and PDF export.

Layout: a centered header, navy section titles over a rule, "Title | Organization"
entry headings with italic right-aligned dates, and US Letter pages set in Calibri
(or Carlito, its metric-compatible open-source twin, bundled in static/fonts).
Description lines keep the CV's own structure: "•" lines are bullets, "◦" lines
are sub-bullets, and other lines become an italic intro or a bold-italic
sub-heading. Text wrapped in **double asterisks** is bold.
"""

import copy
import hashlib
import html
import io
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Frame, HRFlowable, PageTemplate, Paragraph, Table, TableStyle


# The template's section order.
SECTIONS = [("summary", "Summary"), ("experience", "Experience"), ("project", "Projects"),
            ("publication", "Publications"), ("skills", "Skills"), ("education", "Education")]
# Section names applicant tracking systems recognize; the CV's own title is used when it is one of these.
ATS_SECTION_NAMES = {"summary", "professional summary", "profile", "professional profile", "about", "experience",
                     "work experience", "professional experience", "employment", "employment history", "work history",
                     "projects", "selected projects", "personal projects", "key projects", "publications",
                     "selected publications", "skills", "technical skills", "core skills", "education", "certifications"}
# Skills are grouped under the CV's own category labels, falling back to their kind.
# Groups follow the order skills first appear, so tailored relevance still leads.
SKILL_GROUPS = {"technique": "Expertise", "language": "Languages", "framework": "Frameworks & libraries",
                "database": "Databases", "platform": "Cloud & platforms", "tool": "Tools"}
FILLER_WORDS = {"a", "an", "and", "as", "at", "for", "in", "of", "on", "the", "to", "with"}
BULLET = re.compile(r"^(?:[•●▪■]\s*|[-–—*]\s+)")
SUB_BULLET = re.compile(r"^[◦○▫□]\s*")
FONTS = Path(__file__).resolve().parents[1] / "static" / "fonts"

# Measurements from template.docx, in points unless noted.
MARGIN_X_IN, MARGIN_Y_IN = 0.57, 0.42
BODY_SIZE, LEADING = 10, 12.2  # Single line spacing for Calibri.
NAME_SIZE, HEADLINE_SIZE, CONTACT_SIZE, SECTION_SIZE, ENTRY_SIZE = 20, 11, 9.5, 11, 10.5
NAVY, GRAY, LINK = "#1F3864", "#404040", "#1F4E99"
SEPARATOR = "  |  "  # "  |  ", keeping the bar with the text before it.


def build_resume(state: dict, job: dict | None = None) -> dict:
    skills = [skill for skill in state["skills"] if skill.get("confirmed")]
    return {
        "profile": copy.deepcopy(state["profile"]),
        "items": copy.deepcopy([item for item in state["items"] if item.get("confirmed")]),
        "skills": [skill["name"] for skill in skills],
        "skill_groups": {skill["name"]: group for skill in skills
                         if (group := skill.get("category") or SKILL_GROUPS.get(skill.get("kind")))},
        "section_titles": dict(state.get("section_titles") or {}),
        "job_title": (job or {}).get("title", ""),
        "company": (job or {}).get("company", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _text(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _rich(value: str, bold_open: str, bold_close: str) -> str:
    """Escaped text with **bold** spans; unmatched asterisks stay as written."""
    return re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", lambda match: bold_open + match.group(1) + bold_close, _text(value))


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def resume_source(item: dict) -> str:
    """The entry's own wording (or a rephrasing the user approved)."""
    return item.get("enhanced") or item.get("original") or ""


def resume_text(item: dict) -> str:
    """What resumes show: the Google XYZ version while it matches the entry's wording, else that wording."""
    if "shown" in item:
        return item["shown"]  # A job's resume, using the posting's names for your terms.
    source = resume_source(item)
    if item.get("xyz") and item.get("xyz_basis") == text_hash(source):
        return item["xyz"]
    return source


def _words(value: str) -> set[str]:
    return {word for word in re.findall(r"[^\W_]+", value.casefold()) if word not in FILLER_WORDS}


def _blocks(item: dict) -> list[tuple[str, str]]:
    """Description lines as ("intro" | "subhead" | "bullet" | "sub", text), skipping lines that only restate the heading."""
    heading = _words(f'{item.get("title") or ""} {item.get("organization") or ""}')
    parsed = []
    for line in resume_text(item).splitlines():
        line = line.strip()
        if SUB_BULLET.match(line):
            kind, line = "sub", SUB_BULLET.sub("", line)
        elif BULLET.match(line):
            kind, line = "bullet", BULLET.sub("", line)
        else:
            kind = "plain"
        line = line.strip()
        if line and not _words(line) <= heading:
            parsed.append((kind, line))
    if not any(kind != "plain" for kind, _ in parsed):
        return [("bullet", line) for _, line in parsed]
    # Around bullets, a sentence introduces the entry and a phrase names a sub-project.
    return [(("intro" if line.rstrip("*").endswith((".", "!", "?")) else "subhead") if kind == "plain" else kind, line)
            for kind, line in parsed]


def _section_title(resume: dict, kind: str, default: str) -> str:
    """The CV's own heading for a section when an ATS recognizes it, otherwise the standard name."""
    title = " ".join(str((resume.get("section_titles") or {}).get(kind) or "").split())
    return title if title.casefold() in ATS_SECTION_NAMES else default


def _dates(item: dict) -> str:
    return " – ".join(str(item.get(key) or "") for key in ("start", "end") if item.get(key))


def _heading(item: dict) -> str:
    return SEPARATOR.join(str(item.get(key)) for key in ("title", "organization") if item.get(key))


def _citation(item: dict) -> str:
    """Publications are listed as citations, as in the template."""
    parts = [str(item.get(key)) for key in ("title", "organization") if item.get(key)]
    if _dates(item):
        parts.append(_dates(item))
    return ". ".join(part.rstrip(".") for part in parts) + "."


def _skill_groups(resume: dict) -> list[tuple[str, list[str]]]:
    """Labelled rows when skills span several known groups; otherwise one unlabelled row."""
    labels = resume.get("skill_groups") or {}
    groups: dict[str, list[str]] = {}
    for skill in resume["skills"]:
        groups.setdefault(labels.get(skill) or "Other", []).append(skill)
    other = groups.pop("Other", None)
    if len(groups) < 2:
        return [("", list(resume["skills"]))] if resume["skills"] else []
    return [*groups.items(), *([("Other", other)] if other else [])]


def _contact(profile: dict) -> list[tuple[str, str]]:
    """Contact values in the template's order, with a safe link target or an empty href."""
    entries = []
    for key in ("location", "phone", "email", "website"):
        value = str(profile.get(key) or "").strip()
        if not value:
            continue
        href = ""
        if key == "email" and re.fullmatch(r"[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+", value):
            href = f"mailto:{value}"
        elif key == "website" and re.fullmatch(r"https?://\S+", value, re.I):
            href = value
        elif key == "website" and re.fullmatch(r"[\w-]+(?:\.[\w-]+)+(?:/\S*)?", value):
            href = f"https://{value}"
        entries.append((value, href))
    return entries


def _font_face(weight: int, style: str, name: str) -> str:
    return f"@font-face{{font-family:Carlito;font-weight:{weight};font-style:{style};src:url(/static/fonts/Carlito-{name}.ttf) format('truetype')}}"


CSS = "".join(_font_face(*face) for face in ((400, "normal", "Regular"), (700, "normal", "Bold"), (400, "italic", "Italic"), (700, "italic", "BoldItalic"))) + f'''
*{{box-sizing:border-box}}body{{margin:0;background:#eceef1;color:#000;font:{BODY_SIZE}pt/{LEADING}pt Calibri,Carlito,"Segoe UI",Arial,sans-serif;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
main{{width:8.5in;max-width:calc(100% - 40px);min-height:11in;margin:16px auto 40px;padding:{MARGIN_Y_IN}in {MARGIN_X_IN}in;background:#fff;box-shadow:0 4px 30px #1f386414;border:1px solid #e1e4ea}}
a{{color:inherit;text-decoration:none}}a:hover{{text-decoration:underline}}p{{margin:0}}strong{{font-weight:700}}
header{{text-align:center}}h1{{font-size:{NAME_SIZE}pt;line-height:{NAME_SIZE * 1.22:.1f}pt;font-weight:700;color:{NAVY};margin:0 0 1pt;overflow-wrap:anywhere}}
.headline{{font-size:{HEADLINE_SIZE}pt;line-height:{HEADLINE_SIZE * 1.22:.1f}pt;color:{GRAY};margin-bottom:1pt;overflow-wrap:anywhere}}
.contact{{font-size:{CONTACT_SIZE}pt;line-height:{CONTACT_SIZE * 1.22:.1f}pt;margin-bottom:2pt}}.contact>*{{overflow-wrap:anywhere}}.contact>*+*::before{{content:'\\a0\\a0|\\a0\\a0';white-space:pre}}
h2{{font-size:{SECTION_SIZE}pt;line-height:{SECTION_SIZE * 1.22:.1f}pt;font-weight:700;letter-spacing:1pt;text-transform:uppercase;color:{NAVY};margin:8.5pt 0 3.5pt;padding-bottom:1pt;border-bottom:.75pt solid {NAVY};break-after:avoid}}
.summary{{text-align:justify;white-space:pre-line;overflow-wrap:anywhere}}
.entry-heading{{display:flex;justify-content:space-between;align-items:baseline;gap:10pt;margin:4pt 0 1pt;break-after:avoid}}
h3{{flex:1 1 auto;min-width:0;font-size:{ENTRY_SIZE}pt;line-height:{LEADING}pt;font-weight:700;margin:0;overflow-wrap:anywhere}}
.dates{{flex:0 0 auto;max-width:34%;font-style:italic;color:{GRAY};text-align:right;overflow-wrap:anywhere}}
.intro{{font-style:italic;color:{GRAY};margin-bottom:1.5pt;overflow-wrap:anywhere}}
.subhead{{font-weight:700;font-style:italic;color:{GRAY};margin:2.5pt 0 1pt;break-after:avoid;overflow-wrap:anywhere}}
ul{{list-style:none;margin:0;padding:0}}li{{position:relative;padding-left:15pt;margin-bottom:.4pt;overflow-wrap:anywhere;orphans:2;widows:2}}li::before{{content:'•';position:absolute;left:4pt}}
li.sub{{padding-left:36pt;margin-bottom:.2pt}}li.sub::before{{content:'◦';left:25pt}}
.skills p{{margin-bottom:1pt;overflow-wrap:anywhere}}
.toolbar{{max-width:8.5in;margin:20px auto 0;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:0 1px;font:12px/1.5 -apple-system,"Segoe UI",Arial,sans-serif;color:#5b6472}}.toolbar strong{{font-weight:600;color:#1d2330}}.toolbar-actions{{display:flex;gap:8px}}.toolbar a,.toolbar button{{display:inline-block;padding:9px 14px;border:1px solid #cfd4dc;border-radius:6px;background:#fff;color:#1d2330;font:inherit;font-weight:600;text-decoration:none;cursor:pointer}}.toolbar a{{background:{NAVY};border-color:{NAVY};color:white}}.toolbar a:focus-visible,.toolbar button:focus-visible{{outline:3px solid #8fa3c7;outline-offset:3px}}
@page{{size:letter;margin:{MARGIN_Y_IN}in {MARGIN_X_IN}in}}@media print{{body{{background:#fff}}main{{width:auto;max-width:none;min-height:0;padding:0;margin:0;border:0;box-shadow:none}}.toolbar{{display:none}}header{{break-inside:avoid}}a:hover{{text-decoration:none}}}}
@media screen and (max-width:700px){{main{{max-width:100%;width:100%;margin:14px 0 0;padding:24px 18px;min-height:100vh;border:0;box-shadow:none}}.toolbar{{margin:16px 18px 0;flex-wrap:wrap}}h1{{font-size:18pt;line-height:22pt}}.entry-heading{{flex-direction:column;align-items:flex-start;gap:0}}.dates{{text-align:left;max-width:100%}}}}
'''


def _html_blocks(blocks: list[tuple[str, str]]) -> str:
    parts, bullets = [], []
    for kind, line in blocks + [("end", "")]:
        if kind in {"bullet", "sub"}:
            bullets.append(f'<li{" class=\"sub\"" if kind == "sub" else ""}>{_rich(line, "<strong>", "</strong>")}</li>')
            continue
        if bullets:
            parts.append(f'<ul>{"".join(bullets)}</ul>')
            bullets = []
        if kind in {"intro", "subhead"}:
            parts.append(f'<p class="{kind}">{_rich(line, "<strong>", "</strong>")}</p>')
    return "".join(parts)


def render_html(resume: dict) -> str:
    profile = resume["profile"]
    contact = "".join(f'<a href="{_text(href)}">{_text(value)}</a>' if href else f'<span>{_text(value)}</span>'
                      for value, href in _contact(profile))
    sections = []
    for kind, title in SECTIONS:
        if kind == "summary":
            body = f'<p class="summary">{_rich(profile["summary"], "<strong>", "</strong>")}</p>' if profile.get("summary") else ""
        elif kind == "skills":
            body = "".join(f'<p>{f"<strong>{_text(label)}:</strong> " if label else ""}{", ".join(_text(skill) for skill in skills)}</p>'
                           for label, skills in _skill_groups(resume))
            body = f'<div class="skills">{body}</div>' if body else ""
        elif kind == "publication":
            items = [item for item in resume["items"] if item["kind"] == kind]
            body = _html_blocks([block for item in items for block in [("bullet", _citation(item))] + [("sub", line) for _, line in _blocks(item)]])
        else:
            entries = []
            for item in (item for item in resume["items"] if item["kind"] == kind):
                dates = f'<span class="dates">{_text(_dates(item))}</span>' if _dates(item) else ""
                entries.append(f'<article><div class="entry-heading"><h3>{_text(_heading(item))}</h3>{dates}</div>{_html_blocks(_blocks(item))}</article>')
            body = "".join(entries)
        if body:
            sections.append(f'<section><h2>{_text(_section_title(resume, kind, title))}</h2>{body}</section>')
    name = _text(profile.get("name") or "Your name")
    headline = f'<p class="headline">{_text(profile["headline"])}</p>' if profile.get("headline") else ""
    target = " at ".join(_text(part) for part in (resume.get("job_title"), resume.get("company")) if part)
    label = f"Tailored for <strong>{target}</strong>" if target else "<strong>Resume preview</strong>"
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} — Resume</title><style>{CSS}</style></head><body><nav class="toolbar" aria-label="Resume actions"><span>{label}</span><div class="toolbar-actions"><button type="button" onclick="window.print()">Print</button><a href="resume.pdf" download>Download PDF</a></div></nav>
<main><header><h1>{name}</h1>{headline}{f'<div class="contact">{contact}</div>' if contact else ''}</header>{"".join(sections)}</main></body></html>'''


@lru_cache(maxsize=1)
def _fonts() -> tuple[str, str, str, str]:
    """Carlito (regular, bold, italic, bold italic), falling back to Helvetica if the bundled files are missing."""
    names = ("ResumeSans", "ResumeSans-Bold", "ResumeSans-Italic", "ResumeSans-BoldItalic")
    try:
        for name, style in zip(names, ("Regular", "Bold", "Italic", "BoldItalic")):
            pdfmetrics.registerFont(TTFont(name, str(FONTS / f"Carlito-{style}.ttf")))
    except Exception:
        return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"
    # Lets <b> and <i> inside a paragraph switch to the right face.
    registerFontFamily(names[0], normal=names[0], bold=names[1], italic=names[2], boldItalic=names[3])
    return names


def render_pdf(resume: dict) -> bytes:
    output = io.BytesIO()
    profile = resume["profile"]
    font, bold, italic, bold_italic = _fonts()
    margin_x, margin_y = MARGIN_X_IN * inch, MARGIN_Y_IN * inch
    document = BaseDocTemplate(
        output, pagesize=letter, rightMargin=margin_x, leftMargin=margin_x, topMargin=margin_y, bottomMargin=margin_y,
        title=f'{profile.get("name") or "Resume"} — Resume', author=profile.get("name", ""),
    )
    frame = Frame(document.leftMargin, document.bottomMargin, document.width, document.height,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id="resume")
    document.addPageTemplates(PageTemplate(id="resume", frames=[frame]))
    styles = getSampleStyleSheet()
    body = ParagraphStyle("ResumeBody", parent=styles["BodyText"], fontName=font, fontSize=BODY_SIZE, leading=LEADING,
                          textColor=colors.black, spaceBefore=0, spaceAfter=0, alignment=TA_LEFT,
                          splitLongWords=True, allowWidows=0, allowOrphans=0)

    def style(name, **changes):
        return ParagraphStyle(name, parent=body, **changes)

    centered = {"alignment": 1, "keepWithNext": True}
    name_style = style("Name", fontName=bold, fontSize=NAME_SIZE, leading=NAME_SIZE * 1.22, spaceAfter=1, textColor=colors.HexColor(NAVY), **centered)
    headline_style = style("Headline", fontSize=HEADLINE_SIZE, leading=HEADLINE_SIZE * 1.22, spaceAfter=1, textColor=colors.HexColor(GRAY), **centered)
    contact_style = style("Contact", fontSize=CONTACT_SIZE, leading=CONTACT_SIZE * 1.22, spaceAfter=2, **centered)
    section_style = style("Section", fontName=bold, fontSize=SECTION_SIZE, leading=SECTION_SIZE * 1.22, spaceBefore=8.5,
                          textColor=colors.HexColor(NAVY), keepWithNext=True)
    summary_style = style("Summary", alignment=TA_JUSTIFY)
    date_style = style("Dates", fontName=italic, textColor=colors.HexColor(GRAY), alignment=TA_RIGHT)
    intro_style = style("Intro", fontName=italic, textColor=colors.HexColor(GRAY), spaceAfter=1.5)
    subhead_style = style("Subhead", fontName=bold_italic, textColor=colors.HexColor(GRAY), spaceBefore=2.5, spaceAfter=1, keepWithNext=True)
    bullet_style = style("Bullet", leftIndent=15, bulletIndent=4, bulletFontName=font, spaceAfter=0.4)
    sub_style = style("SubBullet", leftIndent=36, bulletIndent=25, bulletFontName=font, spaceAfter=0.2)
    skill_style = style("Skills", spaceAfter=1)

    story = []

    def first_paragraph(paragraph):
        # Keep headings with the opening lines, not an entire long paragraph.
        # ReportLab normally groups all of the next paragraph with keepWithNext,
        # which can otherwise push a multi-page paragraph off a mostly empty page.
        parts = paragraph.split(document.width, paragraph.style.leading * 3)
        if len(parts) > 1:
            parts[0].spaceBefore = paragraph.getSpaceBefore()
            for part in parts[1:]:
                part.spaceBefore = 0
            story.extend(parts)
        else:
            story.append(paragraph)

    def section(title):
        title = _text(_section_title(resume, next((kind for kind, name in SECTIONS if name == title), ""), title))
        rule = HRFlowable(width="100%", thickness=0.75, color=colors.HexColor(NAVY), spaceBefore=1, spaceAfter=3.5)
        rule.keepWithNext = True
        story.extend([Paragraph(title.upper(), section_style), rule])

    def blocks(entries):
        for index, (kind, line) in enumerate(entries):
            text = _rich(line, "<b>", "</b>")
            if kind == "intro":
                paragraph = Paragraph(text, intro_style)
            elif kind == "subhead":
                paragraph = Paragraph(text, subhead_style)
            else:
                paragraph = Paragraph(text, sub_style if kind == "sub" else bullet_style, bulletText="◦" if kind == "sub" else "•")
            if index == 0:
                first_paragraph(paragraph)
            else:
                story.append(paragraph)

    def heading(item):
        paragraph = Paragraph(f'<font name="{bold}" size="{ENTRY_SIZE}">{_text(_heading(item))}</font>', body)
        dates = _dates(item)
        if not dates:
            paragraph.style = style("EntryOnly", spaceBefore=4, spaceAfter=1)
            return paragraph
        # Size the date column to its text so the heading gets the rest of the line.
        date_width = min(pdfmetrics.stringWidth(dates, italic, BODY_SIZE) + 2, document.width * .34)
        return Table([[paragraph, Paragraph(_text(dates), date_style)]], colWidths=[document.width - date_width, date_width],
                     style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                       ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (0, 0), 10),
                                       ("RIGHTPADDING", (1, 0), (1, 0), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                                       # Align the smaller date text with the heading's baseline.
                                       ("TOPPADDING", (1, 0), (1, 0), 0.5),
                                       ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]),
                     spaceBefore=4, spaceAfter=1)

    story.append(Paragraph(_text(profile.get("name") or "Your name"), name_style))
    if profile.get("headline"):
        story.append(Paragraph(_text(profile["headline"]), headline_style))
    contact = SEPARATOR.join(f'<a href="{_text(href)}">{_text(value)}</a>' if href else _text(value) for value, href in _contact(profile))
    if contact:
        story.append(Paragraph(contact, contact_style))
    for kind, title in SECTIONS:
        if kind == "summary":
            if profile.get("summary"):
                section(title)
                first_paragraph(Paragraph(_rich(profile["summary"], "<b>", "</b>").replace("\n", "<br/>"), summary_style))
        elif kind == "skills":
            rows = _skill_groups(resume)
            if rows:
                section(title)
                for index, (label, skills) in enumerate(rows):
                    prefix = f"<b>{_text(label)}:</b> " if label else ""
                    row = Paragraph(prefix + ", ".join(_text(skill) for skill in skills), skill_style)
                    if index == 0:
                        first_paragraph(row)
                    else:
                        story.append(row)
        elif kind == "publication":
            items = [item for item in resume["items"] if item["kind"] == kind]
            if items:
                section(title)
                blocks([block for item in items for block in [("bullet", _citation(item))] + [("sub", line) for _, line in _blocks(item)]])
        else:
            items = [item for item in resume["items"] if item["kind"] == kind]
            if not items:
                continue
            section(title)
            for item in items:
                entries = _blocks(item)
                entry = heading(item)
                entry.keepWithNext = bool(entries)
                story.append(entry)
                blocks(entries)
    document.build(story)
    return output.getvalue()
