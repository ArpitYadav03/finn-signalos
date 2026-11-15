# FINN held-out evaluation

Generated 2026-11-15 12:40 by `evals/scripts/heldout_eval.py`. All numbers measured on the local database; metrics with no evaluated items say 'unmeasured'.

## Retrieval (offline, no model calls)

24 held-out questions with a gold passage present in the corpus (skipped 0: none).

| Variant | Recall@5 | Recall@10 | MRR@10 | p50 latency |
|---|---|---|---|---|
| keyword (current BM25) | 45.8% (11/24) | 58.3% (14/24) | 0.254 | 97 ms |
| contextual chunks | 45.8% (11/24) | 58.3% (14/24) | 0.255 | 97 ms |
| keyword + proximity rerank | 45.8% (11/24) | 54.2% (13/24) | 0.327 | 94 ms |
| contextual + rerank | 45.8% (11/24) | 54.2% (13/24) | 0.327 | 108 ms |

**Decision:** best = keyword + proximity rerank (+0 cases in top 5 vs keyword). Rule: adopt a variant only if it finds the gold passage in the top 5 for at least 2 more cases and MRR does not drop. Adopt: **no — keep keyword BM25**.

Hybrid: not evaluated: only 1.0% of chunks have embeddings (provider free-tier quota), so a hybrid comparison would measure missing vectors, not the method.

Keyword misses (gold not in top 5):

- R02 ULTRACEMCO: “What dividend did the board recommend?” — gold rank >10; top result: “I'll let the Board decide that at the end of the year. I can't take a decision on my own. But the Board has taken a decision to keep reviewi…”
- R03 ULTRACEMCO: “What did management say about cement prices and performance?” — gold rank 7; top result: “So firstly, Rahul, I like the echo that you talked about. Always good to hear good performance from as many people. Coming to your first que…”
- R04 PIDILITIND: “What was underlying volume growth in the quarter?” — gold rank 10; top result: “Thank you, Pranav, and good afternoon, and a warm welcome to everybody on the call. I'll just keep my opening comments brief. And the Board …”
- R06 SUNPHARMA: “How did the innovative medicines business perform?” — gold rank >10; top result: “hand it over to Kirti, who will share the performance of our Global Innovative Medicines business and India business.…”
- R07 NTPC: “How much generation capacity was added in the year?” — gold rank >10; top result: “Thank you for a very comprehensive answer. My second question is for the FY32 to FY37 period, you mentioned RE capacity addition of about 76…”
- R10 BHARTIARTL: “What was average revenue per user this quarter?” — gold rank >10; top result: “followed by an update on our strategic priorities. Let me start with mobile. This quarter, we added 3.3 million revenue earning customers an…”
- R11 MARUTI: “What were total sales volumes including exports?” — gold rank 7; top result: “the process. So, the management is focused all the time that all of our tier 1, tier 2, tier 3 suppliers keep quality high, because we have …”
- R12 ICICIBANK: “What was the net interest margin?” — gold rank >10; top result: “and year ended March 31, 2026. I would like to highlight some key numbers: First, moving on to profit and capital A. Profit and capital 1. N…”
- R13 BAJFINANCE: “How fast did assets under management grow?” — gold rank >10; top result: “not changing, I must say to you, the longer-term growth guidance of 23% to 25%. I think it's important to make that point clear because that…”
- R16 ASIANPAINT: “How much cash did operations generate in FY26?” — gold rank >10; top result: “Hello, and good evening, everyone. Welcome to Asian Paints Q4 and FY26 Investor Call. I am Lakshya Sharma from the Investor Relations team, …”
- R17 ITC: “How did the cigarettes business perform?” — gold rank >10; top result: “| 11 | Key Highlights ▪Cigarettes: Focused execution of strategic & calibrated response to navigate the unprecedented tax increase, balanci…”
- R19 DIXON: “How is the mobile phone business doing?” — gold rank >10; top result: “We still feel that we should be able to have a margin of somewhere between 2.8% to 3.1%, 3.2% in our mobile phone business. That's how it's …”
- R22 HAVELLS: “How did Lloyd perform?” — gold rank >10; top result: “The others division registered net revenues of H 1,727 crores during FY 2025-26 with contribution margins at 16.2% compared with net revenue…”

## End-to-end research

14/14 cases completed; model `gemini-3.5-flash`; modes {'llm:gemini-3.1-flash-lite': 2, 'llm:gemini-3.5-flash-lite': 12}.

| Metric | Result |
|---|---|
| Citation coverage (answer sentences with ≥1 reference) | 92.6% (50/54) |
| Numerical attribution (figures in final answers that carry a reference) | 100.0% (58/58) |
| Document-claim support (lexical proxy: ≥3 shared content words with cited passage) | 82.5% (33/40) |
| Gold passage retrieved and cited (answerable cases with gold) | 50.0% (4/8) |
| Correct abstention (unanswerable cases) | 75.0% (3/4) |
| False abstention (answerable cases) | 10.0% (1/10) |
| Validator: figures checked / statements removed / explanations removed | 64 / 5 / 0 |
| Latency p50 / p90 / max | 5904 / 10917 / 36524 ms |
| Actual usage | 45 model calls, 150,311 input + 3,816 output tokens, est. $0.0546 |

Per case:

| Case | Ticker | Expect | Mode | Cited/sent. | Figures ref'd | Gold cited | Abstained | Removed | ms |
|---|---|---|---|---|---|---|---|---|---|
| E01 | ULTRACEMCO | answer | llm:gemini-3.5-flash-lite | 2/2 | 3/3 | yes | no | 1 | 8338 |
| E02 | ULTRACEMCO | answer | llm:gemini-3.5-flash-lite | 3/4 | 1/1 | — | no | 0 | 9111 |
| E03 | PIDILITIND | answer | llm:gemini-3.5-flash-lite | 5/5 | 6/6 | no | no | 0 | 6455 |
| E04 | ASIANPAINT | answer | llm:gemini-3.1-flash-lite | 5/6 | 15/15 | — | no | 0 | 32845 |
| E05 | ICICIBANK | answer | llm:gemini-3.5-flash-lite | 4/4 | 9/9 | no | no | 1 | 10917 |
| E06 | TATASTEEL | answer | llm:gemini-3.5-flash-lite | 4/4 | 5/5 | yes | no | 0 | 3099 |
| E07 | BHARTIARTL | answer | llm:gemini-3.5-flash-lite | 2/2 | 0/0 | no | yes | 2 | 8831 |
| E08 | POLYCAB | answer | llm:gemini-3.5-flash-lite | 5/5 | 4/4 | yes | no | 0 | 3414 |
| E09 | SUNPHARMA | answer | llm:gemini-3.5-flash-lite | 6/6 | 11/11 | no | no | 0 | 5217 |
| E10 | HAVELLS | answer | llm:gemini-3.5-flash-lite | 4/4 | 0/0 | yes | no | 0 | 2738 |
| A01 | ULTRACEMCO | abstain | llm:gemini-3.1-flash-lite | 4/4 | 0/0 | — | yes | 0 | 36524 |
| A02 | PIDILITIND | abstain | llm:gemini-3.5-flash-lite | 4/4 | 4/4 | — | no | 0 | 3127 |
| A03 | TCS | abstain | llm:gemini-3.5-flash-lite | 1/2 | 0/0 | — | yes | 0 | 2529 |
| A04 | ITC | abstain | llm:gemini-3.5-flash-lite | 1/2 | 0/0 | — | yes | 1 | 5354 |

Failure examples:

- E01 ULTRACEMCO “How did fuel and raw material costs change this quarter?” — validator removed: Rose ~13% quarter-on-quarter , with fuel rates climbing to ₹2.09 per kcal and fuel cost per bag moving from ₹45 to ₹51.5 — ₹2.09: no cited source contains this figure with the same metric, period and uni. Answer began: “Management stated in the Q2 FY27 earnings call that fuel and raw material costs rose 13% and 3% quarter-on-quarter, respectively [E7]. * **Raw Material Costs:**…”
- E03 PIDILITIND “What was underlying volume growth in the quarter?” — gold passage not cited. Answer began: “Management reported that the standalone underlying volume growth (UVG) for the first half (H1 FY27) was 11.0% [E13]. * **H1 FY27 UVG:** Standalone underlying vo…”
- E05 ICICIBANK “What was the net interest margin?” — gold passage not cited, validator removed: ₹55,992 crore, up +16.21% YoY from ₹48,181 crore . — ₹55,992 crore: value ₹55,992 crore ≠ ₹48,181 crore. Answer began: “ICICI Bank's reported net interest margin (NIM) was 4.30% in Q2FY26 [E1], and management stated that NIM has more or less stabilised around the 4.2%–4.3% range …”
- E07 BHARTIARTL “What was average revenue per user this quarter?” — abstained on an answerable question, gold passage not cited, validator removed: Reported at ₹60,878 crore for Q2 FY27, up 16.75% YoY and 4.0% QoQ . — ₹60,878 crore: unit ₹ crore does not match %; Reported at ₹34,946 crore for Q2 FY27, up 18.22% YoY and 4.93% QoQ . — ₹34,946 crore: unit ₹ crore does not match %. Answer began: “Management stated in the Q2 FY27 earnings call that pricing interventions on the prepaid ladder played out during the quarter [E15], but the company's disclosur…”
- E09 SUNPHARMA “How did the innovative medicines business perform?” — gold passage not cited. Answer began: “Management reported that the Global Innovative Medicines business delivered solid multi-quarter growth, driven by key brands like Ilumya, though the US segment …”
- A02 PIDILITIND “What revenue did the company report for FY35?” — expected abstention. Answer began: “The company's available financial history extends up to FY26 [F56], so filings do not contain revenue data for FY35. * The company reported FY26 consolidated re…”
- A04 ITC “What revenue growth guidance did management give on the latest earnings call?” — validator removed: Q2FY27 consolidated revenue was reported at ₹20,269 crore, marking a +3.9% YoY change . — ₹20,269 crore: no cited source contains this figure with the same metric, period. Answer began: “Management did not provide explicit quantitative revenue growth guidance on the earnings call. * Company disclosures and transcripts do not contain forward-look…”

## Materiality

28 randomly sampled real filings, developer-labelled before viewing FINN's scores (0 missing).

Accuracy 82.1% (23/28); missed material 20.0% (1/5); false alarms 17.4% (4/23).

- BAJFINANCE: “Submission Of Half Yearly Report For FY2026-27” — labelled routine, FINN high (83.0)
- RELIANCE: “Announcement under Regulation 30 (LODR)-Credit Rating” — labelled routine, FINN medium (40.0)
- ITC: “Announcement under Regulation 30 (LODR)-Scheme of Arrangement” — labelled routine, FINN high (80.0)
- LT: “Announcement under Regulation 30 (LODR) - Bagging/receiving of order(s) or contr” — labelled prominent, FINN routine (13.0)
- HDFCBANK: “Announcement under Regulation 30 (LODR)-Change in Management” — labelled routine, FINN medium (48.0)

## Caveats

- Labels were written by the developer, not independent analysts; sample sizes are small and shown with every rate.
- Document-claim support is a lexical proxy, not semantic entailment.
- Cost is FINN's own estimate from token counts and configured prices; the provider bill is authoritative.
