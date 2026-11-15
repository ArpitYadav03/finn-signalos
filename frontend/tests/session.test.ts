// Regressions for the sign-in redirect and session token. Run: npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { safeEqual, safeNext, sessionToken } from "../lib/session.ts";

test("after sign-in FINN only returns to its own pages", () => {
  assert.equal(safeNext("/research?ticker=LT"), "/research?ticker=LT");
  for (const bad of [null, "", "https://evil.example", "//evil.example", "/\\evil", "/login?next=/"]) assert.equal(safeNext(bad), "/");
});

test("the session cookie is an HMAC of the password, not the password", async () => {
  const t = await sessionToken("demo-pass");
  assert.match(t, /^[0-9a-f]{64}$/);
  assert.ok(!t.includes("demo-pass"));
  assert.equal(t, await sessionToken("demo-pass"));
  assert.notEqual(t, await sessionToken("other-pass"));
  assert.ok(safeEqual(t, t) && !safeEqual(t, t.slice(1) + "0"));
});
