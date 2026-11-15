# ADR-004: Two workflows, not a multi-agent swarm

**Status:** accepted

**Decision.** Filing Intelligence (Push) and Fundamental Research (Pull) are the only agentic workflows. Guidance extraction is a workflow inside ingestion, not an autonomous agent. No agent framework dependency: a ~40-line tool loop over the OpenAI-compatible chat API (`agents/research_agent.py`) with typed tools.

**Why.** Each workflow has a clear input/output contract and can be tested; a swarm would add latency, cost and failure modes without better answers. A framework (e.g. an agents SDK) can wrap the same tools later — the tools are plain Python functions.
