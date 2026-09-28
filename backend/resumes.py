"""A single-column resume template shared by HTML preview and PDF export."""

import copy
import html
import io
from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


SECTIONS = [("experience", "Experience"), ("project", "Projects"), ("education", "Education"), ("publication", "Publications")]


def build_resume(state: dict, job: dict | None = None) -> dict:
    return {
        "profile": copy.deepcopy(state["profile"]),
        "items": copy.deepcopy([item for item in state["items"] if item.get("confirmed")]),
        "skills": [skill["name"] for skill in state["skills"] if skill.get("confirmed")],
        "job_title": (job or {}).get("title", ""),
        "company": (job or {}).get("company", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _text(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _description(value: str) -> list[str]:
    return [line.strip().lstrip("•–- ") for line in value.splitlines() if line.strip()]


def render_html(resume: dict) -> str:
    profile = resume["profile"]
    contact = " · ".join(_text(profile.get(key)) for key in ("email", "phone", "location", "website") if profile.get(key))
    sections = []
    if profile.get("summary"):
        sections.append(f'<section><h2>Profile</h2><p>{_text(profile["summary"])}</p></section>')
    for kind, title in SECTIONS:
        entries = []
        for item in resume["items"]:
            if item["kind"] != kind:
                continue
            dates = " – ".join(_text(item.get(key)) for key in ("start", "end") if item.get(key))
            organization = f'<p class="organization">{_text(item.get("organization"))}</p>' if item.get("organization") else ""
            bullets = "".join(f"<li>{_text(line)}</li>" for line in _description(item.get("enhanced") or item.get("original", "")))
            entries.append(f'<article><div class="entry-heading"><h3>{_text(item["title"])}</h3><span>{dates}</span></div>{organization}<ul>{bullets}</ul></article>')
        if entries:
            sections.append(f'<section><h2>{title}</h2>{"".join(entries)}</section>')
    if resume["skills"]:
        sections.append(f'<section><h2>Technical skills</h2><p>{" · ".join(_text(skill) for skill in resume["skills"])}</p></section>')
    name = _text(profile.get("name") or "Your name")
    headline = f'<p class="headline">{_text(profile["headline"])}</p>' if profile.get("headline") else ""
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} — Resume</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#eef0f3;color:#18202b;font:10.5pt/1.5 Arial,Helvetica,sans-serif}}
main{{max-width:210mm;min-height:297mm;margin:32px auto;padding:18mm;background:white;box-shadow:0 3px 24px #0001}}
h1{{font-size:26pt;line-height:1.15;margin:0 0 5px;letter-spacing:-.5px}}.headline{{font-size:12pt;margin:0 0 7px}}
.contact{{font-size:9pt;color:#465060;overflow-wrap:anywhere}}header{{padding-bottom:16px;border-bottom:2px solid #263a4c}}
section{{margin-top:20px}}h2{{font-size:10pt;letter-spacing:1.1px;text-transform:uppercase;margin:0 0 10px;border-bottom:1px solid #d9dfe5;padding-bottom:5px}}
h3{{font-size:11pt;margin:0}}p{{margin:4px 0}}article{{margin-bottom:15px}}.entry-heading{{display:flex;align-items:baseline;justify-content:space-between;gap:12px}}
.entry-heading span{{font-size:9pt;white-space:nowrap;color:#465060}}.organization{{font-weight:500;color:#465060}}ul{{padding-left:17px;margin:6px 0 0}}li{{margin:3px 0}}
.toolbar{{max-width:210mm;margin:20px auto 0;text-align:right}}button{{padding:9px 15px;border:1px solid #ccd2da;border-radius:6px;background:white;cursor:pointer}}
@page{{size:A4;margin:16mm}}@media print{{body{{background:white}}main{{padding:0;margin:0;box-shadow:none;max-width:none;min-height:0}}.toolbar{{display:none}}h2,.entry-heading{{break-after:avoid}}li{{break-inside:avoid}}}}
@media(max-width:700px){{main{{margin:0;padding:22px;min-height:100vh}}.toolbar{{margin:12px}}.entry-heading{{display:block}}}}
</style></head><body><div class="toolbar"><button onclick="window.print()">Print / save as PDF</button></div>
<main><header><h1>{name}</h1>{headline}<div class="contact">{contact}</div></header>{"".join(sections)}</main></body></html>'''


def _font() -> str:
    if "ResumeSans" in pdfmetrics.getRegisteredFontNames():
        return "ResumeSans"
    candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            try:
                pdfmetrics.registerFont(TTFont("ResumeSans", str(path)))
                return "ResumeSans"
            except Exception:
                continue
    return "Helvetica"


def render_pdf(resume: dict) -> bytes:
    output = io.BytesIO()
    profile = resume["profile"]
    font = _font()
    document = SimpleDocTemplate(
        output, pagesize=A4, rightMargin=.72 * inch, leftMargin=.72 * inch,
        topMargin=.65 * inch, bottomMargin=.65 * inch,
        title=f'{profile.get("name") or "Resume"} — Resume', author=profile.get("name", ""),
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle("ResumeBody", parent=styles["BodyText"], fontName=font, fontSize=9.5, leading=14, spaceAfter=4, alignment=TA_LEFT)
    name_style = ParagraphStyle("ResumeName", parent=body, fontSize=23, leading=29, spaceAfter=6)
    section_style = ParagraphStyle("ResumeSection", parent=body, fontSize=11, leading=15, spaceBefore=14, spaceAfter=8, textColor=colors.HexColor("#233a4c"), keepWithNext=True)
    title_style = ParagraphStyle("ResumeTitle", parent=body, fontSize=10.5, leading=15, spaceBefore=7, keepWithNext=True)
    meta_style = ParagraphStyle("ResumeMeta", parent=body, textColor=colors.HexColor("#4b5563"), fontSize=9, leading=13, keepWithNext=True)
    bullet_style = ParagraphStyle("ResumeBullet", parent=body, leftIndent=10, firstLineIndent=-7)
    story = [Paragraph(_text(profile.get("name") or "Your name"), name_style)]
    if profile.get("headline"):
        story.append(Paragraph(_text(profile["headline"]), body))
    contact = " · ".join(_text(profile.get(key)) for key in ("email", "phone", "location", "website") if profile.get(key))
    if contact:
        story.append(Paragraph(contact, body))
    if profile.get("summary"):
        story.extend([Paragraph("PROFILE", section_style), Paragraph(_text(profile["summary"]), body)])
    for kind, title in SECTIONS:
        items = [item for item in resume["items"] if item["kind"] == kind]
        if not items:
            continue
        story.append(Paragraph(title.upper(), section_style))
        for item in items:
            story.append(Paragraph(_text(item["title"]), title_style))
            dates = " – ".join(str(item.get(key) or "") for key in ("start", "end") if item.get(key))
            meta = " | ".join(part for part in (item.get("organization", ""), dates) if part)
            if meta:
                story.append(Paragraph(_text(meta), meta_style))
            for line in _description(item.get("enhanced") or item.get("original", "")):
                story.append(Paragraph("• " + _text(line), bullet_style))
            story.append(Spacer(1, 3))
    if resume["skills"]:
        story.extend([Paragraph("TECHNICAL SKILLS", section_style), Paragraph(" · ".join(_text(skill) for skill in resume["skills"]), body)])
    document.build(story)
    return output.getvalue()
