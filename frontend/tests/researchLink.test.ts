// Regressions for Handover → Research contextual navigation. Run: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { planBriefingHref, researchHref, safeReturn } from "../lib/researchLink.ts";

test("Investigate from a Handover item keeps filing, question, company and the way back", () => {
  const back = planBriefingHref(1, "e88301d28c7fd120fce1:abc");
  const href = researchHref({ ticker: "bajfinance", filingId: 2, question: "What is the balance sheet position?", from: back });
  const u = new URL(href, "http://x");
  assert.equal(u.pathname, "/research");
  assert.equal(u.searchParams.get("ticker"), "BAJFINANCE");
  assert.equal(u.searchParams.get("filing"), "2");
  assert.equal(u.searchParams.get("q"), "What is the balance sheet position?");
  assert.equal(u.searchParams.get("from"), "/?view=plans&plan=1#item-e88301d28c7fd120fce1%3Aabc");
});

test("investigation links open the For & against tab", () => {
  const u = new URL(researchHref({ ticker: "LT", tab: "evidence", from: "/?view=plans&plan=1" }), "http://x");
  assert.equal(u.searchParams.get("tab"), "evidence");
  assert.equal(u.searchParams.get("filing"), null);
});

test("the return target must stay inside the app", () => {
  assert.equal(safeReturn("/?view=plans&plan=1"), "/?view=plans&plan=1");
  for (const bad of ["https://evil.example", "//evil.example", "/\\evil", "javascript:alert(1)", "", null]) assert.equal(safeReturn(bad), null);
  assert.equal(new URL(researchHref({ ticker: "LT", from: "https://evil.example" }), "http://x").searchParams.get("from"), null);
});
