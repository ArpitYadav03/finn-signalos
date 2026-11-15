/** Contextual research links. Pure (no imports) so it can be tested with `node --test`. */

export interface ResearchContext {
  ticker: string;
  filingId?: number | null;      // the filing/event the investor came from (Research shows it "in context")
  question?: string | null;      // pre-filled investigation question
  tab?: "ask" | "evidence";
  from?: string | null;          // where "Back to briefing" returns
}

/** Only same-app paths are accepted as a return target ("/..." but never "//host" or a scheme). */
export function safeReturn(from: string | null | undefined): string | null {
  if (!from) return null;
  if (!from.startsWith("/") || from.startsWith("//") || from.includes("\\") || /^\/+[a-z]+:/i.test(from)) return null;
  return from.length > 300 ? null : from;
}

export function researchHref(c: ResearchContext): string {
  const q = new URLSearchParams({ ticker: c.ticker.toUpperCase() });
  if (c.filingId) q.set("filing", String(c.filingId));
  if (c.question) q.set("q", c.question);
  if (c.tab && c.tab !== "ask") q.set("tab", c.tab);
  const back = safeReturn(c.from);
  if (back) q.set("from", back);
  return `/research?${q.toString()}`;
}

/** Briefing URL for one plan view, optionally scrolled to an item. */
export function planBriefingHref(planId: number, itemId?: string): string {
  return `/?view=plans&plan=${planId}${itemId ? `#item-${encodeURIComponent(itemId)}` : ""}`;
}
