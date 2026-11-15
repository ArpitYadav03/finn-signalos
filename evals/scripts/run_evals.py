"""FINN evaluation harness. Writes measured results to evals/reports/latest.md — nothing here is hand-entered.

  python evals/scripts/run_evals.py --offline   # classification, guidance extraction, numeric golden cases
  python evals/scripts/run_evals.py             # + grounding/latency checks against the local DB (real documents)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import finance  # noqa: E402
from app.services.classifier import classify  # noqa: E402
from app.services.guidance import extract_guidance  # noqa: E402

DATA = ROOT / "evals" / "datasets"
REPORT = ROOT / "evals" / "reports" / "latest.md"


def eval_classification() -> dict:
    rows = json.loads((DATA / "classification.json").read_text())
    misses = []
    for r in rows:
        got = classify(r["title"], r.get("summary")).category.value
        if got != r["expected"]:
            misses.append({"title": r["title"], "expected": r["expected"], "got": got})
    real = [r for r in rows if r["source"].startswith("real")]
    return {"n": len(rows), "n_real": len(real), "accuracy": 1 - len(misses) / len(rows), "misses": misses}


def eval_guidance() -> dict:
    rows = json.loads((DATA / "guidance.json").read_text())
    tp = fp = fn = 0
    details = []
    for r in rows:
        ref = date.fromisoformat(r.get("ref", "2026-05-15"))
        items = extract_guidance(r["text"], ref, question_context=r.get("context"), default_margin=r.get("default_margin"))
        exp = r["expect"]
        if exp is None:
            bad = [g for g in items if g.guidance_type == "quantitative"]
            fp += len(bad)
            details.append((r["source"], "ok" if not bad else f"FALSE POSITIVE {bad[0].metric}"))
            continue
        match = [g for g in items if g.metric == exp["metric"] and g.guidance_type == exp["type"]]
        ok = bool(match)
        if ok and "min" in exp:
            ok = match[0].target_min == exp["min"] and match[0].target_max == exp["max"]
        if ok and "value" in exp:
            ok = match[0].target_value == exp["value"]
        if ok and "period" in exp:
            ok = match[0].target_period == exp["period"]
        if ok and "horizon" in exp:
            ok = match[0].horizon == exp["horizon"]
        if ok and "also" in r:
            ok = any(g.metric == r["also"]["metric"] and g.target_value == r["also"]["value"] for g in items)
        tp += ok
        fn += not ok
        details.append((r["source"], "ok" if ok else f"MISS got {[(g.metric, g.guidance_type, g.target_min, g.target_max, g.target_value, g.target_period) for g in items]}"))
    return {"n": len(rows), "tp": tp, "fp": fp, "fn": fn, "precision": tp / (tp + fp) if tp + fp else 1.0,
            "recall": tp / (tp + fn) if tp + fn else 1.0, "details": details}


def eval_numeric() -> dict:
    cases = [
        ("YoY growth", finance.yoy_pct(112, 100).value, 12.0),
        ("QoQ decline", finance.qoq_pct(90, 100).value, -10.0),
        ("Loss narrowing", finance.yoy_pct(-50, -100).value, 50.0),
        ("Zero base refused", finance.yoy_pct(5, 0).value, None),
        ("Sign flip refused", finance.yoy_pct(5, -5).value, None),
        ("EBITDA margin", finance.margin_pct(18815, 73188).value, 25.7078),
        ("FCF", finance.free_cash_flow(1000, -350).value, 650.0),
        ("Daily return", finance.daily_return_pct(95, 100).value, -5.0),
        ("Volume ratio", finance.volume_ratio(300, [100.0] * 20).value, 3.0),
        ("Lakh to crore", finance.to_crore(1.60, "lakh"), 0.016),
    ]
    ok = 0
    rows = []
    for name, got, exp in cases:
        good = (got is None and exp is None) or (got is not None and exp is not None and abs(got - exp) < 1e-3)
        ok += good
        rows.append((name, got, exp, good))
    return {"n": len(cases), "correct": ok, "rows": rows}


def eval_grounding() -> dict:
    """Runs real research questions over the local DB and checks every citation resolves to stored text."""
    from app.agents.research_agent import research
    from app.core.db import SessionLocal
    from app.models import Company, DocumentChunk

    db = SessionLocal()
    questions = [("TCS", "What did management promise, and did they deliver?"),
                 ("TCS", "What are the main risks management talks about?"),
                 ("INFY", "What is the operating margin guidance?"),
                 ("ITC", "Is cash flow keeping up with reported profit?"),
                 ("POLYCAB", "What did management say about margins?")]
    out = []
    for t, q in questions:
        c = db.query(Company).filter_by(ticker=t).first()
        if not c:
            continue
        t0 = time.perf_counter()
        r = research(db, c, q)
        ms = (time.perf_counter() - t0) * 1000
        ev = {e["id"]: e for e in r["evidence"]}
        markers = re.findall(r"\[(E\d+)\]", r["answer_markdown"])
        valid = 0
        for m in markers:
            e = ev.get(m)
            if e and e.get("chunk_id"):
                ch = db.get(DocumentChunk, e["chunk_id"])
                if ch and ch.page_number == e["page_number"] and ch.content[:80] in ch.content:
                    valid += 1
        # Verbatim check: every quoted span in the answer must exist in some cited chunk.
        quotes = re.findall(r"“([^”]{20,})”", r["answer_markdown"])
        chunk_text = " ".join(re.sub(r"\s+", " ", db.get(DocumentChunk, e["chunk_id"]).content) for e in r["evidence"] if e.get("chunk_id"))
        verbatim = sum(1 for qt in quotes if qt.rstrip("…").strip()[:60] in chunk_text)
        out.append({"ticker": t, "question": q, "mode": r["mode"], "latency_ms": round(ms), "citations": len(markers),
                    "valid_citations": valid, "quotes": len(quotes), "verbatim_quotes": verbatim, "evidence": len(r["evidence"])})
    db.close()
    return {"runs": out}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    cls, gd, num = eval_classification(), eval_guidance(), eval_numeric()
    lines = [f"# FINN evaluation report\n\nGenerated {datetime.now():%Y-%m-%d %H:%M} by `evals/scripts/run_evals.py`. All numbers are measured, not estimated.\n",
             f"## Announcement classification (rules-v1)\n\n{cls['n']} labelled titles ({cls['n_real']} real BSE titles from the portfolio, rest synthetic but typical).\n",
             f"**Accuracy: {cls['accuracy']:.1%}**" + ("" if not cls["misses"] else f" — misses: {cls['misses']}") + "\n",
             f"## Guidance extraction (rules-v1)\n\n{gd['n']} cases incl. analyst questions, macro data, stake %, past tense and a prompt-injection line.\n",
             f"**Precision {gd['precision']:.1%}, recall {gd['recall']:.1%}** (tp={gd['tp']}, fp={gd['fp']}, fn={gd['fn']})\n",
             "| Case | Result |\n|---|---|"] + [f"| {s} | {d} |" for s, d in gd["details"]] + [
             f"\n## Deterministic numeric checks\n\n**{num['correct']}/{num['n']} correct**\n", "| Check | Got | Expected | OK |\n|---|---|---|---|"] + [
             f"| {n} | {g} | {e} | {'✓' if ok else '✗'} |" for n, g, e, ok in num["rows"]]
    if not args.offline:
        gr = eval_grounding()
        lines += ["\n## Research grounding (real indexed documents, local DB)\n",
                  "| Ticker | Question | Mode | Latency | Citations valid | Quotes verbatim | Evidence items |", "|---|---|---|---|---|---|---|"]
        for r in gr["runs"]:
            lines.append(f"| {r['ticker']} | {r['question']} | {r['mode']} | {r['latency_ms']} ms | {r['valid_citations']}/{r['citations']} | {r['verbatim_quotes']}/{r['quotes']} | {r['evidence']} |")
        llm_runs = [r for r in gr["runs"] if r["mode"].startswith("llm")]
        lines.append(f"\nModes: {len(llm_runs)}/{len(gr['runs'])} runs answered by an LLM (model shown per row; fallback chain in use), "
                     "the rest extractive. Token usage and estimated cost per run are recorded in the agent_runs table "
                     "(Gemini free tier: no charge)." if llm_runs else "\nLLM cost for these runs: $0.00 (deterministic mode; no API key configured).")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    if cls["accuracy"] < 0.9 or num["correct"] != num["n"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
