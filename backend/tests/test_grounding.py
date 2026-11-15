from app.agents.grounding import unverified_numbers, validate_citations


def test_citation_validation():
    text, cav = validate_citations("A [E1] B [E9] C [M]", {"E1", "E2"})
    assert text == "A [E1] B  C [M]" and cav


def test_numbers_rounding_and_trivia():
    outputs = ['{"yoy": {"value": 12.37}, "revenue": 1120.0, "close": 2408.55}']
    ans = "Revenue ₹1,120 cr, up 12.4% (Q2FY27, 2026), close ₹2,408.6; 3 filings; target 25% [E1]"
    assert unverified_numbers(ans, outputs) == ["25%"]


def test_grouped_citations_are_split():
    from app.agents.grounding import normalize_citations
    assert normalize_citations("x [E8, E14; E15] y") == "x [E8][E14][E15] y"


def test_rupee_amounts_in_json_tool_output_are_found():
    """Regression: '₹992.25' serialised as '\\u20b9992.25' was read as 9992.25 and flagged as unverified."""
    import json
    escaped = json.dumps({"s": "floor price ₹992.25; aggregating ₹5,799.99 crore"})               # ascii-escaped
    plain = json.dumps({"s": "floor price ₹992.25; aggregating ₹5,799.99 crore"}, ensure_ascii=False)
    for out in (escaped, plain):
        assert unverified_numbers("floor ₹992.25; total ₹5,799.99 crore", [out]) == []


def test_runtime_keeps_unicode_in_tool_outputs():
    from types import SimpleNamespace as NS
    from app.agents.runtime import ToolSpec, run_agent
    from app.llm.client import RunTracker

    class OneTool:
        def __init__(self):
            self.n = 0

        def chat(self, tracker, tier, messages, tools):
            self.n += 1
            if self.n == 1:
                call = NS(id="c1", function=NS(name="f", arguments="{}"))
                call.model_dump = lambda: {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
                return NS(content="", tool_calls=[call])
            return NS(content="done", tool_calls=None)

    res = run_agent(OneTool(), RunTracker("t"), "fast", "sys", [{"role": "user", "content": "q"}],
                    [ToolSpec("f", "d", {"type": "object", "properties": {}}, lambda: {"price": "₹992.25"})])
    assert "₹992.25" in res.tool_outputs[0]
