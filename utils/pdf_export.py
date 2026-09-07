"""
Renders a Markdown travel briefing (the same text shown in the
"Briefing" tab of the UI) into a downloadable PDF.

Kept as its own module so app.py doesn't get cluttered with PDF
layout details.
"""

import io
from datetime import datetime, timezone

import markdown2
from fpdf import FPDF


# fpdf2's built-in core fonts (Helvetica, Times, Courier) can only
# render true Latin-1 -- code points 0-255. Emoji obviously fall
# outside that, but so does a lot of ordinary LLM output: smart
# quotes, em/en dashes, ellipses, bullets, the rupee sign, arrows.
# Without this, any of those crash write_html() with
# FPDFUnicodeEncodingException. Map the common ones to readable
# ASCII first, then drop anything still unrepresentable (emoji,
# CJK, etc.) instead of raising.
_PDF_SAFE_REPLACEMENTS = {
    "\u2018": "'", "\u2019": "'",     # smart single quotes
    "\u201c": '"', "\u201d": '"',     # smart double quotes
    "\u2013": "-", "\u2014": "--",    # en dash, em dash
    "\u2026": "...",                  # ellipsis
    "\u2022": "-",                    # bullet
    "\u00a0": " ",                    # non-breaking space
    "\u20b9": "Rs. ",                 # rupee sign
    "\u2192": "->", "\u2190": "<-",   # arrows
}


def _sanitize_for_pdf(text: str) -> str:
    if not text:
        return text
    for src, dst in _PDF_SAFE_REPLACEMENTS.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", errors="ignore").decode("latin-1")


class _BriefingPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(140, 140, 140)
        self.cell(0, 8, "SHOGUN -- Travel Briefing", align="L")
        self.set_font("Helvetica", "", 9)
        self.cell(
            0, 8,
            datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            align="R",
            new_x="LMARGIN", new_y="NEXT",
        )
        self.set_draw_color(210, 210, 210)
        self.line(10, 16, 200, 16)
        self.ln(6)
        self.set_text_color(20, 20, 20)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def build_briefing_pdf(markdown_text: str, thread_id: str | None = None) -> bytes:
    """
    Converts markdown_text (the LLM's final "answer" field) into a
    formatted PDF and returns the raw PDF bytes, ready to stream
    back as a file download.
    """
    if not markdown_text or not markdown_text.strip():
        markdown_text = "_No briefing content was available to export._"

    markdown_text = _sanitize_for_pdf(markdown_text)

    html = markdown2.markdown(
        markdown_text,
        extras=["fenced-code-blocks", "tables", "break-on-newline"],
    )

    pdf = _BriefingPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(left=18, top=22, right=18)
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.set_text_color(20, 20, 20)

    pdf.write_html(html)

    if thread_id:
        pdf.ln(8)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(150, 150, 150)
        pdf.cell(0, 6, f"Thread: {_sanitize_for_pdf(str(thread_id))}")

    # fpdf2's output() can return a bytearray depending on version;
    # normalize to bytes for FastAPI's Response/StreamingResponse.
    out = pdf.output()
    return bytes(out)
