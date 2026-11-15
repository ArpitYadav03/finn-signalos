"""Post-hoc grounding checks on LLM answers (deterministic, no second model call).

1. Citation check: every [E#] must refer to evidence actually retrieved in this turn; others are removed.
2. Number check: every figure in the answer (₹ amounts, percentages, decimals, large numbers) must appear in
   some tool output, allowing for rounding. Figures that don't are listed as a caveat so the user knows which
   numbers to treat with care. This catches the classic failure: the model "remembering" a number.
"""
from __future__ import annotations

import re

NUM_RE = re.compile(r"(?<![\w\[])(?:₹\s?)?[-+−]?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|pp|cr|crore|bn|lakh|x|×))?")


def normalize_citations(text: str) -> str:
    """Models often group citations: "[E8, E14]" or "[E2; E5]" -> "[E8][E14]" so each becomes a clickable chip."""
    return re.sub(r"\[\s*((?:E\d+\s*[,;]\s*)+E\d+)\s*\]",
                  lambda m: "".join(f"[{x.strip()}]" for x in re.split(r"[,;]", m.group(1))), text)


def validate_citations(text: str, valid_ids: set[str]) -> tuple[str, list[str]]:
    bad = set(re.findall(r"\[(E\d+)\]", text)) - valid_ids
    if not bad:
        return text, []
    text = re.sub(r"\[(E\d+)\]", lambda m: m.group(0) if m.group(1) in valid_ids else "", text)
    return text, [f"Removed {len(bad)} citation(s) that did not match evidence retrieved for this answer."]


def _numbers_in(text: str) -> list[float]:
    out = []
    for m in re.finditer(r"[-−]?\d[\d,]*(?:\.\d+)?", text):
        try:
            out.append(float(m.group(0).replace(",", "").replace("−", "-")))
        except ValueError:
            pass
    return out


def _is_trivial(token: str, value: float) -> bool:
    t = token.strip()
    if re.search(r"%|pp|₹|cr|crore|bn|lakh|x|×|\.", t):
        return False
    if 1990 <= value <= 2040:   # years
        return True
    return abs(value) < 32      # counts, day numbers, list indices


def unverified_numbers(answer: str, tool_outputs: list[str]) -> list[str]:
    # Ignore citation markers, period labels and dates in the answer.
    clean = re.sub(r"\[(E\d+|M)\]", " ", answer)
    clean = re.sub(r"\bQ[1-4]\s?FY\s?\d{2,4}\b|\bFY\s?\d{2,4}\b|\bH[12]FY\d{2}\b", " ", clean)
    clean = re.sub(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w* \d{4}\b", " ", clean)
    corpus = re.sub(r"\\u[0-9a-fA-F]{4}", " ", " ".join(tool_outputs))  # neutralise any JSON unicode escapes
    known = _numbers_in(corpus)
    known_abs = {round(abs(k), 6) for k in known}
    flagged = []
    for m in NUM_RE.finditer(clean):
        token = m.group(0)
        nums = _numbers_in(token)
        if not nums:
            continue
        v = nums[0]
        if _is_trivial(token, v):
            continue
        decimals = len(token.split(".")[1].rstrip("%ppcrorebnlakhx× ")) if "." in token else 0
        tol = 0.5 * 10 ** (-decimals) + 1e-9
        av = abs(v)
        if round(av, 6) in known_abs or any(abs(av - abs(k)) <= tol for k in known):
            continue
        flagged.append(token.strip())
    return list(dict.fromkeys(flagged))[:8]
