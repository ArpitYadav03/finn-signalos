"""PDF text extraction with page provenance (PyMuPDF)."""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf


def extract_pages(path: Path, max_pages: int = 400) -> list[tuple[int, str]]:
    """Return [(1-based page number, cleaned text)]. Empty pages are kept out."""
    pages: list[tuple[int, str]] = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            text = page.get_text("text")
            text = text.replace(" ", " ").replace("ﬀ", "ff").replace("ﬁ", "fi").replace("ﬂ", "fl")
            # PyMuPDF sometimes emits private-use glyphs for ligatures (e.g. 'diƯicult'); normalise the common one.
            text = text.replace("Ư", "ff")
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if text:
                pages.append((i + 1, text))
    return pages


def page_count(path: Path) -> int:
    with pymupdf.open(path) as doc:
        return doc.page_count
