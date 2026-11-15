# ADR-005: Explainable, size-aware materiality score with guardrails

**Status:** accepted

**Decision.** `score = category baseline + content modifiers + amount-vs-revenue + novelty + market reaction on filing day + followed-category boost + bounded LLM adjustment (±10)`. Each component is stored and shown under "why this rank?". Results, M&A, management change, litigation, ratings, fund-raises and pledges can never fall below *medium*.

**Why.** Investors need to trust what is hidden. Real data showed the need for size awareness: a ₹1.6 lakh DoT penalty on Bharti Airtel initially ranked *high*; normalising amounts to crore and comparing with annual revenue fixes this deterministically.
