// Regressions for the replay date race and stale coverage. Run: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { coverageKey, currentCoverage, initialReplayForm, replayFormReducer as r, validRange } from "../lib/replayForm.ts";

test("a date typed before the delayed defaults arrive survives them", () => {
  let s = initialReplayForm();
  s = r(s, { type: "edit", field: "start", value: "2026-09-08" });   // user types first
  s = r(s, { type: "defaults", latest: "2026-11-13" });               // slow /status reply lands later
  assert.equal(s.start, "2026-09-08");                                // the old code overwrote this with 2026-10-14
  assert.equal(s.end, "2026-11-13");                                  // untouched field still gets its default
  assert.equal(s.maxDate, "2026-11-13");
});

test("defaults fill untouched fields, including a second late reply", () => {
  let s = r(initialReplayForm(), { type: "defaults", latest: "2026-11-13" });
  assert.deepEqual([s.start, s.end], ["2026-10-14", "2026-11-13"]);
  s = r(s, { type: "edit", field: "end", value: "2026-11-01" });
  s = r(s, { type: "defaults", latest: "2026-11-13" });
  assert.deepEqual([s.start, s.end], ["2026-10-14", "2026-11-01"]);
});

test("coverage is shown only for the exact dates, mode and plan version it was checked for", () => {
  let s = r(initialReplayForm<{ n: number }>(), { type: "defaults", latest: "2026-11-13" });
  const key = coverageKey(7, 2, s);
  s = r(s, { type: "coverage", key, data: { n: 45 } });
  assert.deepEqual(currentCoverage(s, 7, 2), { n: 45 });
  assert.equal(currentCoverage(s, 7, 3), null, "plan edited to v3 → old coverage hidden");
  const edited = r(s, { type: "edit", field: "start", value: "2026-09-08" });
  assert.equal(currentCoverage(edited, 7, 2), null, "date changed → stale");
  const moded = r(s, { type: "mode", mode: "indexed" });
  assert.equal(currentCoverage(moded, 7, 2), null, "mode changed → stale");
  const back = r(moded, { type: "mode", mode: "public" });
  assert.deepEqual(currentCoverage(back, 7, 2), { n: 45 }, "same inputs again → still valid");
});

test("range validation", () => {
  assert.equal(validRange({ start: "2026-11-02", end: "2026-11-01", maxDate: "" }), "“From” must be on or before “To”.");
  assert.match(validRange({ start: "2026-11-01", end: "2026-12-01", maxDate: "2026-11-13" }) ?? "", /only up to/);
  assert.equal(validRange({ start: "2026-11-01", end: "2026-11-13", maxDate: "2026-11-13" }), null);
});
