"""Structure-aware chunking.

* Transcripts are split into speaker turns first, so every chunk knows *who* said it and whether they are
  management, an analyst or the moderator. This matters: an analyst asking "is the 25% target intact?" is
  not management guidance.
* Other documents are chunked by paragraph within a page so each chunk keeps its page number, and table
  rows on one page stay together instead of being cut mid-row.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SPEAKER_RE = re.compile(r"^([A-Z][A-Za-z.'’\-]*(?: [A-Z][A-Za-z.'’\-]*){0,4})\s*:\s*(.*)$")
NOT_SPEAKERS = {"note", "disclaimer", "page", "date", "time", "subject", "sub", "ref", "dear sir", "dear sirs",
                "email", "e-mail", "encl", "website", "corporate participants", "conference call participants",
                "participants", "management", "analysts", "speakers", "agenda", "attendees", "tel", "fax", "cin", "symbol", "registered office", "scrip code"}
ANALYST_INTRO = re.compile(
    r"(?:question|questions)\s+(?:is|comes)?\s*(?:from|is from)\s+(?:the line of\s+)?(?:Mr\.?|Ms\.?|Mrs\.?)?\s*"
    r"([A-Z][A-Za-z.'\-]+(?: [A-Z][A-Za-z.'\-]+){0,3})",
)


@dataclass
class Chunk:
    page_number: int
    content: str
    section: str | None = None
    speaker: str | None = None
    speaker_role: str | None = None


def _split_words(text: str, target: int, overlap: int = 30) -> list[str]:
    words = text.split()
    if len(words) <= target:
        return [text.strip()] if text.strip() else []
    out, i = [], 0
    while i < len(words):
        out.append(" ".join(words[i : i + target]))
        i += target - overlap
    return out


NAME_LINE = re.compile(r"^([A-Z][A-Za-z.'’\-]+(?: [A-Z][A-Za-z.'’\-]+){1,3})$")


def _clean_lines(pages: list[tuple[int, str]]):
    for pno, text in pages:
        for line in text.splitlines():
            line = line.strip()
            if not line or re.match(r"^Page \d+ of \d+$", line) or "©" in line:
                continue
            yield pno, line


def chunk_transcript(pages: list[tuple[int, str]], target_words: int = 170) -> list[Chunk]:
    lines = list(_clean_lines(pages))
    # Two transcript conventions: "Name: text" and a name alone on its own line. A label only counts as a
    # speaker if it recurs (or is the moderator) — this rejects cover-letter noise like "Membership No:".
    colon = {}
    alone = {}
    for _, line in lines:
        m = SPEAKER_RE.match(line)
        if m and m.group(1).lower() not in NOT_SPEAKERS and len(m.group(1)) <= 45:
            colon[m.group(1).strip()] = colon.get(m.group(1).strip(), 0) + 1
        m2 = NAME_LINE.match(line)
        if m2 and len(line) <= 35 and line.lower() not in NOT_SPEAKERS:
            alone[line] = alone.get(line, 0) + 1

    def is_speaker(name: str, counts: dict) -> bool:
        if re.search(r"\b(no|number|din|pan|tel|fax|regd|limited|ltd|india|sensitivity|classification|confidential|internal|public)\b", name, re.I):
            return False  # "Membership No:", "Polycab India Limited" page headers, etc.
        if counts is colon:
            return counts.get(name, 0) >= 1
        return counts.get(name, 0) >= 2 or bool(re.search(r"moderator|operator", name, re.I))

    use_alone = len([n for n in colon if is_speaker(n, colon)]) < 3  # only when the colon convention is absent

    turns: list[tuple[str | None, int, list[str]]] = []
    speaker, start_page, buf = None, pages[0][0] if pages else 1, []
    for pno, line in lines:
        m = SPEAKER_RE.match(line)
        name, rest = None, None
        if m and is_speaker(m.group(1).strip(), colon):
            name, rest = m.group(1).strip(), m.group(2)
        elif use_alone and line in alone and is_speaker(line, alone):
            name, rest = line, ""
        if name:
            if buf:
                turns.append((speaker, start_page, buf))
            speaker, start_page, buf = name, pno, [rest] if rest else []
        else:
            buf.append(line)
    if buf:
        turns.append((speaker, start_page, buf))

    full = " ".join(" ".join(b) for _, _, b in turns)
    analysts = {m.group(1).strip() for m in ANALYST_INTRO.finditer(full)}
    analyst_first = {a.split()[0].lower() for a in analysts}

    def role(name: str | None) -> str | None:
        if not name:
            return None
        n = name.lower()
        if "moderator" in n or "operator" in n:
            return "moderator"
        if name in analysts or n.split()[0] in analyst_first:
            return "analyst"
        return "management"

    chunks: list[Chunk] = []
    for spk, pno, buf in turns:
        text = re.sub(r"\s+", " ", " ".join(buf)).strip()
        for part in _split_words(text, target_words):
            chunks.append(Chunk(pno, part, section="Q&A" if analysts and spk else None, speaker=spk,
                                speaker_role=role(spk)))
    return chunks


def _heading(text: str) -> str | None:
    for line in text.splitlines()[:6]:
        s = line.strip()
        if 4 <= len(s) <= 80 and (s.isupper() or re.match(r"^([A-Z][a-z&’']+\s?){1,7}$", s)):
            return s.title() if s.isupper() else s
    return None


def chunk_document(pages: list[tuple[int, str]], target_words: int = 220) -> list[Chunk]:
    chunks: list[Chunk] = []
    section = None
    for pno, text in pages:
        section = _heading(text) or section
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        buf: list[str] = []
        count = 0
        for p in paras:
            n = len(p.split())
            if count + n > target_words and buf:
                chunks.append(Chunk(pno, re.sub(r"[ \t]+", " ", "\n".join(buf)), section=section))
                buf, count = [], 0
            if n > target_words * 1.6:
                for part in _split_words(p, target_words):
                    chunks.append(Chunk(pno, part, section=section))
                continue
            buf.append(p)
            count += n
        if buf:
            chunks.append(Chunk(pno, re.sub(r"[ \t]+", " ", "\n".join(buf)), section=section))
    return [c for c in chunks if len(c.content.split()) >= 8]
