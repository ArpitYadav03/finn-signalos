// Regressions for Briefing view selection, de-duplication, age labels and plan subtitles. Run: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { ageLabel, dedupeAcross, parseView, planSubtitle, viewHref } from "../lib/briefingView.ts";

test("Today is the default; ?view and ?plan select other views", () => {
  assert.equal(parseView(""), "today");
  assert.equal(parseView("?view=unreviewed"), "unreviewed");
  assert.equal(parseView("?plan=2"), "plans");
  assert.equal(parseView("?view=nonsense"), "today");
  assert.equal(viewHref("?view=plans&plan=2", "today"), "/?view=today");
  assert.equal(viewHref("?plan=2", "plans"), "/?plan=2&view=plans");
});

test("an event shown in one section of a view is not repeated in another", () => {
  const [updated, fresh, watching] = dedupeAcross([[{ k: "a" }], [{ k: "a" }, { k: "b" }], [{ k: "b" }, { k: "c" }]], (x) => x.k);
  assert.deepEqual([updated, fresh, watching].map((s) => s.map((x) => x.k)), [["a"], ["b"], ["c"]]);
});

test("first-review items older than the 24-hour digest are labelled", () => {
  const now = Date.parse("2026-11-15T10:00:00Z");
  assert.equal(ageLabel("2026-11-15T02:00:00Z", now), null);
  assert.equal(ageLabel("2026-11-14T05:00:00Z", now), "Older item · filed 1 day ago, not reviewed yet");
  assert.equal(ageLabel("2026-11-05T10:00:00Z", now), "Older item · filed 10 days ago, not reviewed yet");
});

test("plans with different thresholds get different subtitles", () => {
  const base = { preview: { scope: "All 18 holdings" } };
  const a = planSubtitle({ ...base, spec: { logical_operator: "OR", conditions: [{ kind: "price_move", direction: "down", threshold: 4 }, { kind: "volume", threshold: 2.5 }] } });
  const b = planSubtitle({ ...base, spec: { logical_operator: "AND", conditions: [{ kind: "price_move", direction: "down", threshold: 5 }, { kind: "volume", threshold: 2 }] } });
  assert.equal(a, "All 18 holdings · falls >4% or volume >2.5×");
  assert.equal(b, "All 18 holdings · falls >5% and volume >2×");
  assert.notEqual(a, b);
  assert.equal(planSubtitle({ ...base, spec: { logical_operator: "OR", conditions: [{ kind: "guidance_cut" }, { kind: "filing", categories: ["management_change"], roles: ["CFO"], change: "departure" }] } }),
    "All 18 holdings · guidance cuts or CFO exits");
});
