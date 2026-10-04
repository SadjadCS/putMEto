"""Content and pagination regressions for HTML previews and exported resumes."""

from copy import deepcopy
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path

from pypdf import PdfReader
import pytest

from backend.resumes import build_resume, render_html, render_pdf


FIXTURE = Path(__file__).parent / "fixtures" / "resume_layout.json"


class ResumeText(HTMLParser):
    """Read visible resume content without depending on presentation markup."""

    def __init__(self, markup):
        super().__init__()
        self.in_main = False
        self.tags = []
        self.content = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == "main":
            self.in_main = True

    def handle_endtag(self, tag):
        if tag == "main":
            self.in_main = False

    def handle_data(self, text):
        if self.in_main:
            self.content.append(text)

    @property
    def text(self):
        return " ".join(" ".join(self.content).split())


def pdf_pages(resume):
    return PdfReader(BytesIO(render_pdf(resume))).pages


def normalized(text):
    return " ".join(text.split())


def test_html_and_pdf_preserve_all_resume_sections_and_reviewed_wording():
    resume = json.loads(FIXTURE.read_text())
    source = deepcopy(resume)
    html_text = ResumeText(render_html(resume)).text
    pdf_text = normalized("\n".join(page.extract_text() for page in pdf_pages(resume)))

    for text in (html_text, pdf_text):
        for value in resume["profile"].values():
            assert normalized(value) in text
        for item in resume["items"]:
            for key in ("title", "organization", "start", "end"):
                if item.get(key):
                    assert item[key] in text
            for line in (item.get("enhanced") or item["original"]).splitlines():
                assert line in text
        for skill in resume["skills"]:
            assert skill in text
        assert "Original wording replaced" not in text
        assert text.index("Northstar Labs") < text.index("Fieldwork Analytics")
    assert resume == source, "Rendering must not modify the saved resume."


def test_user_markup_is_text_in_html_and_pdf():
    literal = '<img src=x onerror="window.bad=true"> R&D <script>alert(1)</script>'
    resume = {
        "profile": {"name": literal, "summary": "A & B < C > D"},
        "items": [{"kind": "project", "title": literal, "organization": literal, "original": literal}],
        "skills": [literal],
    }
    document = ResumeText(render_html(resume))
    assert literal in document.text
    assert "A & B < C > D" in document.text
    assert not any(tag in {"script", "img", "iframe"} for tag, _ in document.tags)
    assert not any("onerror" in attrs for _, attrs in document.tags)
    text = normalized("\n".join(page.extract_text() for page in pdf_pages(resume)))
    assert literal in text
    assert "A & B < C > D" in text


def test_optional_fields_do_not_create_blank_sections_or_empty_lists():
    resume = {
        "profile": {"name": "Avery Applicant"},
        "items": [{"kind": "education", "title": "Master of Science"}],
        "skills": [],
    }
    document = ResumeText(render_html(resume))
    assert "Master of Science" in document.text
    assert not any(tag == "ul" for tag, _ in document.tags)
    for text in (document.text, normalized("\n".join(page.extract_text() for page in pdf_pages(resume)))):
        assert "Avery Applicant" in text
        assert "Master of Science" in text
        assert "None" not in text
        assert "Experience" not in text and "EXPERIENCE" not in text
        assert "Projects" not in text and "PROJECTS" not in text


def test_long_entries_paginate_without_losing_or_duplicating_content():
    long_bullet = " ".join(
        f"Checkpoint{i:03d} delivered reliable applications with cross-functional teams."
        for i in range(180)
    )
    resume = {
        "profile": {"name": "Long Resume", "headline": "Software Engineer"},
        "items": [
            {"kind": "experience", "title": "Lead Engineer", "original": long_bullet},
            {"kind": "experience", "title": "Second Role", "organization": "Second Employer", "original": "AfterLongEntry preserved."},
            {"kind": "project", "title": "Final Project", "original": "ProjectContent preserved."},
            {"kind": "education", "title": "Final Degree", "original": "EducationContent preserved."},
            {"kind": "publication", "title": "Final Paper", "original": "PublicationContent preserved."},
        ],
        "skills": ["FinalSkillMarker"],
    }
    pages = pdf_pages(resume)
    assert len(pages) >= 3
    page_texts = [page.extract_text() for page in pages]
    text = normalized("\n".join(page_texts))
    assert "Checkpoint000" in page_texts[0], "A long first bullet should start in the available first-page space."
    for index in range(180):
        assert text.count(f"Checkpoint{index:03d}") == 1
    for marker in ("AfterLongEntry", "ProjectContent", "EducationContent", "PublicationContent", "FinalSkillMarker"):
        assert text.count(marker) == 1
    assert all(page_text.strip() for page_text in page_texts), "Pagination must not create empty pages."
    # The template's section order.
    markers = ["Checkpoint179", "AfterLongEntry", "ProjectContent", "PublicationContent", "FinalSkillMarker", "EducationContent"]
    assert [text.index(marker) for marker in markers] == sorted(text.index(marker) for marker in markers)


def test_pdf_preserves_long_titles_dates_and_unbroken_urls():
    title = "Principal Engineer for International Research and Information Systems " * 4
    url = "https://example.com/" + "long-path-component-" * 25
    resume = {
        "profile": {"name": "Long Fields", "website": url},
        "items": [{
            "kind": "experience", "title": title,
            "organization": "International Research Partnership " * 5,
            "start": "September 2018", "end": "September 2026", "original": "CompleteEntryMarker",
        }],
        "skills": ["InformationSystemsArchitecture" * 8],
    }
    text = normalized("\n".join(page.extract_text() for page in pdf_pages(resume)))
    assert normalized(title) in text
    # Line wrapping can insert whitespace inside unbroken words and URLs.
    compact = "".join(text.split())
    assert url in compact
    assert resume["skills"][0] in compact
    assert "September 2018" in text and "September 2026" in text
    assert "CompleteEntryMarker" in text


@pytest.mark.parametrize("section", ["summary", "skills"])
def test_long_profile_sections_flow_across_pages(section):
    phrases = [f"ProfileMarker{index:03d} building reliable applications with international teams." for index in range(140)]
    resume = {"profile": {"name": "Long Profile"}, "items": [], "skills": []}
    if section == "summary":
        resume["profile"]["summary"] = " ".join(phrases)
    else:
        resume["skills"] = phrases
    pages = pdf_pages(resume)
    assert len(pages) >= 2
    texts = [page.extract_text() for page in pages]
    assert "ProfileMarker000" in texts[0]
    text = " ".join(texts)
    for index in range(140):
        assert text.count(f"ProfileMarker{index:03d}") == 1


def test_description_lines_that_only_restate_the_heading_are_omitted():
    resume = {
        "profile": {"name": "Avery Applicant"},
        "items": [
            {"kind": "education", "title": "Master of Science in Computer Science", "organization": "Clemson University",
             "original": "Master of Science in Computer Science, Clemson University."},
            {"kind": "experience", "title": "Founder", "organization": "Picsun",
             "original": "Founder of Picsun.\nGrew Picsun to 40,000 monthly users."},
        ],
        "skills": [],
    }
    document = ResumeText(render_html(resume))
    pdf_text = normalized("\n".join(page.extract_text() for page in pdf_pages(resume)))
    for text in (document.text, pdf_text):
        assert "Clemson University." not in text
        assert "Founder of Picsun" not in text
        assert "Grew Picsun to 40,000 monthly users." in text
    assert sum(tag == "ul" for tag, _ in document.tags) == 1


def test_skills_are_grouped_by_kind_in_relevance_order():
    resume = {
        "profile": {"name": "Avery Applicant"},
        "items": [],
        "skills": ["PostgreSQL", "Python", "Retrieval-augmented generation", "SQL", "Mystery skill"],
        "skill_groups": {"PostgreSQL": "Databases", "Python": "Languages", "SQL": "Languages",
                         "Retrieval-augmented generation": "Expertise"},
    }
    expected = "Databases: PostgreSQL Languages: Python, SQL Expertise: Retrieval-augmented generation Other: Mystery skill"
    assert expected in ResumeText(render_html(resume)).text
    assert expected in normalized("\n".join(page.extract_text() for page in pdf_pages(resume)))


def test_skills_without_several_kinds_stay_one_unlabelled_line():
    resume = {"profile": {"name": "Avery Applicant"}, "items": [], "skills": ["Python", "SQL"], "skill_groups": {"Python": "Languages"}}
    for text in (ResumeText(render_html(resume)).text, normalized(pdf_pages(resume)[0].extract_text())):
        assert "Python, SQL" in text
        assert "Languages:" not in text and "Other:" not in text


def test_contact_links_only_use_safe_targets():
    resume = {"profile": {"name": "Avery Applicant", "email": "avery@example.com", "website": "avery.example.com/work"},
              "items": [], "skills": []}
    links = [attrs["href"] for tag, attrs in ResumeText(render_html(resume)).tags if tag == "a" and attrs.get("href") != "resume.pdf"]
    assert links == ["mailto:avery@example.com", "https://avery.example.com/work"]
    annotations = [annotation.get_object()["/A"]["/URI"] for annotation in pdf_pages(resume)[0]["/Annots"]]
    assert annotations == ["mailto:avery@example.com", "https://avery.example.com/work"]

    resume["profile"].update(email="not an email", website="javascript:alert(1)")
    links = [attrs["href"] for tag, attrs in ResumeText(render_html(resume)).tags if tag == "a" and attrs.get("href") != "resume.pdf"]
    assert links == []
    assert "/Annots" not in pdf_pages(resume)[0]
    assert "javascript:alert(1)" in ResumeText(render_html(resume)).text


def test_pdf_uses_the_templates_letter_page_and_font_without_a_footer():
    resume = json.loads(FIXTURE.read_text())
    pages = pdf_pages(resume)
    assert len(pages) == 1
    assert [round(float(value)) for value in pages[0].mediabox[2:]] == [612, 792], "US Letter, as in template.docx"
    fonts = {str(font["/BaseFont"]) for font in pages[0]["/Resources"]["/Font"].values()}
    assert any("Carlito" in font for font in fonts), fonts
    assert "Page 1" not in pages[0].extract_text()
    resume.update(job_title="Staff Engineer", company="R&D Works")
    assert "Tailored for <strong>Staff Engineer at R&amp;D Works</strong>" in render_html(resume)


def test_cv_skill_categories_label_groups_before_skill_kinds():
    state = {"profile": {"name": "Avery Applicant"}, "items": [], "skills": [
        {"name": "LangGraph", "kind": "framework", "category": "Agentic AI", "confirmed": True},
        {"name": "Python", "kind": "language", "confirmed": True},
        {"name": "Unreviewed", "kind": "tool", "category": "Tools I use", "confirmed": False},
    ]}
    resume = build_resume(state)
    assert resume["skill_groups"] == {"LangGraph": "Agentic AI", "Python": "Languages"}
    assert "Agentic AI: LangGraph Languages: Python" in ResumeText(render_html(resume)).text


def test_description_lines_keep_the_cvs_structure_like_the_template():
    resume = {
        "profile": {"name": "Sam Example", "location": "Athens, GA", "phone": "(555) 010-0200", "email": "sam@example.com"},
        "items": [{"kind": "experience", "title": "AI Specialist", "organization": "Clemson University", "start": "Jul. 2025", "end": "Present",
                   "original": "Sole engineer delivering agentic AI systems for partners.\n"
                               "SCDOT AI Contract Assistant (South Carolina Department of Transportation)\n"
                               "• Built an agentic system grounded in **3,500 historical contracts**.\n"
                               "◦ Hominto Kit: floor plans from one image.\n"
                               "• Kept **unmatched asterisks* as written."}],
        "skills": [],
    }
    markup = render_html(resume)
    assert '<p class="intro">Sole engineer delivering agentic AI systems for partners.</p>' in markup
    assert '<p class="subhead">SCDOT AI Contract Assistant (South Carolina Department of Transportation)</p>' in markup
    assert "<li>Built an agentic system grounded in <strong>3,500 historical contracts</strong>.</li>" in markup
    assert '<li class="sub">Hominto Kit: floor plans from one image.</li>' in markup
    assert "<li>Kept **unmatched asterisks* as written.</li>" in markup
    assert "<h3>AI Specialist\u00a0\u00a0|\u00a0 Clemson University</h3>" in markup
    text = ResumeText(markup).text
    assert text.index("Athens, GA") < text.index("(555) 010-0200") < text.index("sam@example.com"), "Location, phone, email"
    pdf_text = normalized(pdf_pages(resume)[0].extract_text())
    for expected in ("Sole engineer delivering agentic AI systems for partners.", "SCDOT AI Contract Assistant",
                     "Built an agentic system grounded in 3,500 historical contracts.", "Hominto Kit: floor plans from one image."):
        assert expected in pdf_text
    assert "**" not in pdf_text.replace("**unmatched", "")


def test_publications_are_listed_as_citations():
    resume = {"profile": {"name": "Sam Example"}, "skills": [], "items": [
        {"kind": "publication", "title": "Math Reasoning in LLMs", "organization": "arXiv", "start": "2026", "original": ""}]}
    assert "<li>Math Reasoning in LLMs. arXiv. 2026.</li>" in render_html(resume)
    assert "Math Reasoning in LLMs. arXiv. 2026." in normalized(pdf_pages(resume)[0].extract_text())
