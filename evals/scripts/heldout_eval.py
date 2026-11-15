"""Held-out evaluation: retrieval variants, end-to-end research quality, materiality, latency and actual usage/cost.

  python evals/scripts/heldout_eval.py --retrieval        # offline, no model calls (BM25 variants)
  python evals/scripts/heldout_eval.py --research         # end-to-end; uses the configured model (real cost)
  python evals/scripts/heldout_eval.py --materiality
  python evals/scripts/heldout_eval.py --all

Writes evals/reports/heldout.md and heldout.json. Every number is measured; a metric with no evaluated items is reported
as "unmeasured", never as 100%.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.models import AgentRun, Announcement, Company, Document, DocumentChunk  # noqa: E402

DATA = ROOT / "evals" / "datasets"
OUT_MD = ROOT / "evals" / "reports" / "heldout.md"
OUT_JSON = ROOT / "evals" / "reports" / "heldout.json"
STOP = {"which", "their", "there", "these", "those", "about", "after", "before", "would", "could", "should", "while",
        "being", "other", "company", "quarter", "years", "within", "during", "where", "management"}


def pct(n: int, d: int) -> str:
    return "unmeasured (n=0)" if d == 0 else f"{100 * n / d:.1f}% ({n}/{d})"


def gold_chunks(db, company_id: int, phrases: list[str]) -> set[int]:
    if not phrases:
        return set()
    q = select(DocumentChunk.id, DocumentChunk.content).join(Document, Document.id == DocumentChunk.document_id) \
        .where(Document.company_id == company_id, Document.status == "indexed")
    return {cid for cid, text in db.execute(q).all() if all(p.lower() in text.lower() for p in phrases)}


# ------------------------------------------------------------------ retrieval

VARIANTS = {
    "keyword (current BM25)": {},
    "contextual chunks": {"contextual": True},
    "keyword + proximity rerank": {"rerank": True},
    "contextual + rerank": {"contextual": True, "rerank": True},
}


def eval_retrieval(db) -> dict:
    from app.rag import retriever

    cases = json.loads((DATA / "heldout_retrieval.json").read_text())["cases"]
    res = {v: {"hits5": 0, "hits10": 0, "rr": [], "ms": [], "misses": []} for v in VARIANTS}
    usable, skipped = [], []
    for c in cases:
        co = db.scalars(select(Company).where(Company.ticker == c["ticker"])).first()
        gold = gold_chunks(db, co.id, c["gold_phrases"]) if co else set()
        if not gold:
            skipped.append({"id": c["id"], "reason": "gold passage not in the indexed corpus"})
            continue
        usable.append(c["id"])
        for name, kw in VARIANTS.items():
            t0 = time.perf_counter()
            hits = retriever.search(db, co.id, c["question"], k=10, **kw)
            res[name]["ms"].append((time.perf_counter() - t0) * 1000)
            ids = [h.chunk.id for h in hits]
            rank = next((i + 1 for i, x in enumerate(ids) if x in gold), None)
            res[name]["hits5"] += bool(rank and rank <= 5)
            res[name]["hits10"] += bool(rank)
            res[name]["rr"].append(1 / rank if rank else 0.0)
            if not rank or rank > 5:
                res[name]["misses"].append({"id": c["id"], "ticker": c["ticker"], "question": c["question"],
                                            "rank": rank, "top1": re.sub(r"\s+", " ", hits[0].chunk.content[:140]) if hits else None})
    n = len(usable)
    total = db.query(DocumentChunk).count()
    emb = db.query(DocumentChunk).filter(DocumentChunk.embedding.isnot(None)).count()
    out = {"n": n, "skipped": skipped, "variants": {}, "embedding_coverage": {"chunks": total, "with_embeddings": emb}}
    for name, r in res.items():
        out["variants"][name] = {"recall@5": r["hits5"] / n if n else None, "recall@10": r["hits10"] / n if n else None,
                                 "mrr@10": sum(r["rr"]) / n if n else None, "hits5": r["hits5"], "hits10": r["hits10"],
                                 "p50_ms": statistics.median(r["ms"]) if r["ms"] else None, "misses": r["misses"]}
    base = out["variants"]["keyword (current BM25)"]
    best = max(out["variants"].items(), key=lambda kv: (kv[1]["recall@5"] or 0, kv[1]["mrr@10"] or 0))
    gain = (best[1]["hits5"] - base["hits5"]) if n else 0
    out["decision"] = {
        "best": best[0], "recall@5_gain_cases": gain,
        "adopt": best[0] != "keyword (current BM25)" and gain >= 2 and best[1]["mrr@10"] >= base["mrr@10"],
        "rule": "adopt a variant only if it finds the gold passage in the top 5 for at least 2 more cases and MRR does not drop",
    }
    out["hybrid"] = ("not evaluated: only {:.1%} of chunks have embeddings (provider free-tier quota), so a hybrid comparison "
                     "would measure missing vectors, not the method").format(emb / total if total else 0)
    return out


# ------------------------------------------------------------------ end-to-end research

ABSTAIN_RX = re.compile(r"insufficient evidence|not (?:available|disclosed|found|mentioned|provided|covered)|"
                        r"(?:does|do|did) not (?:disclose|mention|provide|report|have|contain|operate)|no (?:information|data|mention|"
                        r"earnings[- ]call|transcripts?|disclosure)|cannot|can't|unable to|only uses the company's own|not part of|isn't", re.I)
MARK = re.compile(r"\[(E\d+|F\d+|C\d+)\]")


def _sentences(md: str) -> list[str]:
    out = []
    for line in md.split("\n"):
        line = line.strip()
        if not line or re.match(r"^(\*\*)?(limits?|limitations)", line, re.I) or line.startswith("#"):
            continue
        line = re.sub(r"^[-*•]\s+(\*\*[^*]{1,60}\*\*:?\s*)?", "", line)
        out += [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line) if len(s.split()) >= 4]
    return out


def _words(t: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z]{4,}", t.lower()) if w not in STOP}


def eval_research(db) -> dict:
    from app.agents import claims as cl
    from app.agents.research_agent import research
    from app.llm.client import get_llm

    llm = get_llm()
    cases = json.loads((DATA / "heldout_research.json").read_text())["cases"]
    rows = []
    for c in cases:
        co = db.scalars(select(Company).where(Company.ticker == c["ticker"])).first()
        t0 = time.perf_counter()
        try:
            r = research(db, co, c["question"])
            err = None
        except Exception as e:  # noqa: BLE001
            r, err = None, str(e)[:200]
        ms = (time.perf_counter() - t0) * 1000
        if r is None:
            rows.append({"id": c["id"], "error": err, "latency_ms": ms})
            continue
        run = db.scalars(select(AgentRun).where(AgentRun.trace_id == r["trace_id"])).first()
        ans = r["answer_markdown"]
        sents = _sentences(ans)
        cited = [s for s in sents if MARK.search(s)]
        ev = {e["id"]: e for e in r["evidence"]}
        passages = {}
        for eid, e in ev.items():
            ch = db.get(DocumentChunk, e["chunk_id"]) if e.get("chunk_id") else None
            passages[eid] = ch.content if ch else (e.get("relevant_excerpt") or "")
        doc_sents = [s for s in sents if re.search(r"\[E\d+\]", s)]
        supported = [s for s in doc_sents if len(_words(MARK.sub("", s)) & set().union(*[_words(passages.get(m, "")) for m in re.findall(r"\[(E\d+)\]", s)])) >= 3]
        figs_total = figs_ref = 0
        for s in sents:
            toks = cl.tokens(cl.PERIOD.sub(" ", cl.DATES.sub(" ", MARK.sub(" ", s))))
            figs_total += len(toks)
            if MARK.search(s):
                figs_ref += len(toks)
        gold = gold_chunks(db, co.id, c["gold_phrases"])
        gold_cited = bool(gold & {e.get("chunk_id") for e in r["evidence"]}) if gold else None
        abstained = (ans.startswith(cl.INSUFFICIENT[:30]) or (bool(ABSTAIN_RX.search(ans)) and figs_total == 0))
        v = r.get("verification") or {}
        rows.append({
            "id": c["id"], "ticker": c["ticker"], "expect": c["expect"], "question": c["question"], "mode": r["mode"],
            "latency_ms": round(ms), "sentences": len(sents), "cited_sentences": len(cited),
            "doc_sentences": len(doc_sents), "doc_supported_lexical": len(supported),
            "figures": figs_total, "figures_with_reference": figs_ref,
            "validator_checked": v.get("figures_checked", 0), "validator_removed": len(v.get("removed", [])),
            "explanations_removed": len(v.get("explanations_removed", [])), "abstained": abstained,
            "gold_cited": gold_cited, "removed_examples": [x["text"][:120] + " — " + x["reason"][:80] for x in v.get("removed", [])][:2],
            "answer_head": re.sub(r"\s+", " ", ans)[:220],
            "tokens_in": run.tokens_in if run else 0, "tokens_out": run.tokens_out if run else 0,
            "llm_calls": run.llm_calls if run else 0, "cost_usd": run.estimated_cost_usd if run else 0.0,
        })
    ok = [x for x in rows if "error" not in x]
    ans_cases = [x for x in ok if x["expect"] == "answer"]
    abs_cases = [x for x in ok if x["expect"] == "abstain"]
    lat = sorted(x["latency_ms"] for x in rows)
    agg = {
        "model": (llm.model_for("reasoning") if llm else None), "n_cases": len(cases), "n_completed": len(ok),
        "errors": [x for x in rows if "error" in x],
        "modes": {m: sum(1 for x in ok if x["mode"] == m) for m in {x["mode"] for x in ok}},
        "citation_coverage": (sum(x["cited_sentences"] for x in ok), sum(x["sentences"] for x in ok)),
        "document_claim_support_lexical": (sum(x["doc_supported_lexical"] for x in ok), sum(x["doc_sentences"] for x in ok)),
        "numerical_attribution": (sum(x["figures_with_reference"] for x in ok), sum(x["figures"] for x in ok)),
        "validator": {"checked": sum(x["validator_checked"] for x in ok), "statements_removed": sum(x["validator_removed"] for x in ok),
                      "explanations_removed": sum(x["explanations_removed"] for x in ok)},
        "gold_retrieved_and_cited": (sum(1 for x in ans_cases if x["gold_cited"]), sum(1 for x in ans_cases if x["gold_cited"] is not None)),
        "abstention_correct": (sum(1 for x in abs_cases if x["abstained"]), len(abs_cases)),
        "false_abstention": (sum(1 for x in ans_cases if x["abstained"]), len(ans_cases)),
        "latency_ms": {"p50": statistics.median(lat) if lat else None, "p90": lat[int(0.9 * (len(lat) - 1))] if lat else None, "max": max(lat) if lat else None},
        "usage": {"tokens_in": sum(x["tokens_in"] for x in ok), "tokens_out": sum(x["tokens_out"] for x in ok),
                  "llm_calls": sum(x["llm_calls"] for x in ok), "estimated_cost_usd": round(sum(x["cost_usd"] for x in ok), 4)},
    }
    return {"aggregate": agg, "cases": rows}


# ------------------------------------------------------------------ materiality

def eval_materiality(db) -> dict:
    labels = json.loads((DATA / "heldout_materiality.json").read_text())["labels"]
    rows, cm = [], {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for prefix, want in labels.items():
        a = db.scalars(select(Announcement).where(Announcement.content_hash.like(f"{prefix}%"))).first()
        if a is None or a.analysis is None:
            rows.append({"key": prefix, "status": "missing"})
            continue
        got = "prominent" if a.analysis.importance_level in ("high", "medium") else "routine"
        k = {("prominent", "prominent"): "tp", ("routine", "prominent"): "fp", ("prominent", "routine"): "fn", ("routine", "routine"): "tn"}[(want, got)]
        cm[k] += 1
        rows.append({"key": prefix, "ticker": a.company.ticker, "title": a.title[:80], "label": want, "finn": got,
                     "level": a.analysis.importance_level, "score": a.analysis.importance_score, "ok": want == got})
    n = cm["tp"] + cm["fp"] + cm["fn"] + cm["tn"]
    return {"n": n, "confusion": cm, "errors": [r for r in rows if r.get("ok") is False], "missing": [r for r in rows if r.get("status")],
            "accuracy": (cm["tp"] + cm["tn"]) / n if n else None,
            "missed_material": (cm["fn"], cm["fn"] + cm["tp"]), "false_alarms": (cm["fp"], cm["fp"] + cm["tn"])}


# ------------------------------------------------------------------ report

def write(report: dict) -> None:
    prev = json.loads(OUT_JSON.read_text()) if OUT_JSON.exists() else {}
    prev.update(report)
    OUT_JSON.write_text(json.dumps(prev, indent=1, default=str, ensure_ascii=False))
    L = ["# FINN held-out evaluation", "", f"Generated {time.strftime('%Y-%m-%d %H:%M')} by `evals/scripts/heldout_eval.py`. "
         "All numbers measured on the local database; metrics with no evaluated items say 'unmeasured'.", ""]
    rt = prev.get("retrieval")
    if rt:
        L += ["## Retrieval (offline, no model calls)", "", f"{rt['n']} held-out questions with a gold passage present in the corpus "
              f"(skipped {len(rt['skipped'])}: {', '.join(s['id'] for s in rt['skipped']) or 'none'}).", "",
              "| Variant | Recall@5 | Recall@10 | MRR@10 | p50 latency |", "|---|---|---|---|---|"]
        for name, v in rt["variants"].items():
            L.append(f"| {name} | {pct(v['hits5'], rt['n'])} | {pct(v['hits10'], rt['n'])} | {v['mrr@10']:.3f} | {v['p50_ms']:.0f} ms |")
        d = rt["decision"]
        L += ["", f"**Decision:** best = {d['best']} (+{d['recall@5_gain_cases']} cases in top 5 vs keyword). Rule: {d['rule']}. "
              f"Adopt: **{'yes' if d['adopt'] else 'no — keep keyword BM25'}**.", "", f"Hybrid: {rt['hybrid']}.", "",
              "Keyword misses (gold not in top 5):", ""]
        for m in rt["variants"]["keyword (current BM25)"]["misses"]:
            L.append(f"- {m['id']} {m['ticker']}: “{m['question']}” — gold rank {m['rank'] or '>10'}; top result: “{m['top1']}…”")
        L.append("")
    rs = prev.get("research")
    if rs:
        a = rs["aggregate"]
        f = lambda t: pct(t[0], t[1])  # noqa: E731
        L += ["## End-to-end research", "", f"{a['n_completed']}/{a['n_cases']} cases completed; model `{a['model']}`; modes {a['modes']}.", "",
              "| Metric | Result |", "|---|---|",
              f"| Citation coverage (answer sentences with ≥1 reference) | {f(a['citation_coverage'])} |",
              f"| Numerical attribution (figures in final answers that carry a reference) | {f(a['numerical_attribution'])} |",
              f"| Document-claim support (lexical proxy: ≥3 shared content words with cited passage) | {f(a['document_claim_support_lexical'])} |",
              f"| Gold passage retrieved and cited (answerable cases with gold) | {f(a['gold_retrieved_and_cited'])} |",
              f"| Correct abstention (unanswerable cases) | {f(a['abstention_correct'])} |",
              f"| False abstention (answerable cases) | {f(a['false_abstention'])} |",
              f"| Validator: figures checked / statements removed / explanations removed | {a['validator']['checked']} / {a['validator']['statements_removed']} / {a['validator']['explanations_removed']} |",
              f"| Latency p50 / p90 / max | {a['latency_ms']['p50']:.0f} / {a['latency_ms']['p90']:.0f} / {a['latency_ms']['max']:.0f} ms |",
              f"| Actual usage | {a['usage']['llm_calls']} model calls, {a['usage']['tokens_in']:,} input + {a['usage']['tokens_out']:,} output tokens, est. ${a['usage']['estimated_cost_usd']} |",
              "", "Per case:", "", "| Case | Ticker | Expect | Mode | Cited/sent. | Figures ref'd | Gold cited | Abstained | Removed | ms |", "|---|---|---|---|---|---|---|---|---|---|"]
        for x in rs["cases"]:
            if "error" in x:
                L.append(f"| {x['id']} | | | error: {x['error']} | | | | | | {x['latency_ms']:.0f} |")
                continue
            L.append(f"| {x['id']} | {x['ticker']} | {x['expect']} | {x['mode']} | {x['cited_sentences']}/{x['sentences']} | "
                     f"{x['figures_with_reference']}/{x['figures']} | {'—' if x['gold_cited'] is None else 'yes' if x['gold_cited'] else 'no'} | "
                     f"{'yes' if x['abstained'] else 'no'} | {x['validator_removed']} | {x['latency_ms']} |")
        fails = [x for x in rs["cases"] if "error" not in x and ((x["expect"] == "abstain") != x["abstained"] or x["gold_cited"] is False or x["removed_examples"])]
        if fails:
            L += ["", "Failure examples:", ""]
            for x in fails[:8]:
                why = []
                if (x["expect"] == "abstain") != x["abstained"]:
                    why.append("expected abstention" if x["expect"] == "abstain" else "abstained on an answerable question")
                if x["gold_cited"] is False:
                    why.append("gold passage not cited")
                if x["removed_examples"]:
                    why.append("validator removed: " + "; ".join(x["removed_examples"]))
                L.append(f"- {x['id']} {x['ticker']} “{x['question']}” — {', '.join(why)}. Answer began: “{x['answer_head'][:160]}…”")
        L.append("")
    mt = prev.get("materiality")
    if mt:
        cm = mt["confusion"]
        L += ["## Materiality", "", f"{mt['n']} randomly sampled real filings, developer-labelled before viewing FINN's scores "
              f"({len(mt['missing'])} missing).", "",
              f"Accuracy {pct(cm['tp'] + cm['tn'], mt['n'])}; missed material {pct(*mt['missed_material'])}; false alarms {pct(*mt['false_alarms'])}.", ""]
        for e in mt["errors"]:
            L.append(f"- {e['ticker']}: “{e['title']}” — labelled {e['label']}, FINN {e['level']} ({e['score']})")
        L.append("")
    L += ["## Caveats", "", "- Labels were written by the developer, not independent analysts; sample sizes are small and shown with every rate.",
          "- Document-claim support is a lexical proxy, not semantic entailment.",
          "- Cost is FINN's own estimate from token counts and configured prices; the provider bill is authoritative."]
    OUT_MD.write_text("\n".join(L) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--retrieval", action="store_true")
    ap.add_argument("--research", action="store_true")
    ap.add_argument("--materiality", action="store_true")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    db = SessionLocal()
    rep = {}
    if a.retrieval or a.all:
        rep["retrieval"] = eval_retrieval(db)
    if a.materiality or a.all:
        rep["materiality"] = eval_materiality(db)
    if a.research or a.all:
        rep["research"] = eval_research(db)
    write(rep)
    print(OUT_MD.read_text())


if __name__ == "__main__":
    main()
