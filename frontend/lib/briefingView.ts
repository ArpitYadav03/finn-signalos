/** Briefing views. Pure (no imports) so it can be tested with `node --test`. */
export type View = "today" | "plans" | "unreviewed";
export const VIEWS: { id: View; label: string; hint: string }[] = [
  { id: "today", label: "Digest", hint: "Material filings and moves in the selected window (default: previous 24 hours)" },
  { id: "plans", label: "My plans", hint: "Changes since your last review, per monitoring plan" },
  { id: "unreviewed", label: "Unreviewed", hint: "Material events from the last 14 days you haven't marked reviewed" },
];

/** ?view=… wins; a ?plan= link implies My plans; anything else is Today (the 24-hour digest stays the default). */
export function parseView(search: string): View {
  const q = new URLSearchParams(search);
  const v = q.get("view");
  if (v === "today" || v === "plans" || v === "unreviewed") return v;
  if (q.get("plan")) return "plans";
  return "today";
}

/** Same URL with only the view changed (other params such as the plan are kept). */
export function viewHref(search: string, view: View): string {
  const q = new URLSearchParams(search);
  q.set("view", view);
  if (view !== "plans") q.delete("plan");
  return `/?${q.toString()}`;
}

/** Keep the first occurrence of each event across a view's sections (sections are given in display order). */
export function dedupeAcross<T>(sections: T[][], key: (x: T) => string): T[][] {
  const seen = new Set<string>();
  return sections.map((items) => items.filter((x) => {
    const k = key(x);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  }));
}

/** First review of something older than the digest window must say so. */
export function ageLabel(publishedIso: string, nowMs: number, windowHours = 24): string | null {
  const h = (nowMs - new Date(publishedIso).getTime()) / 3600e3;
  if (!(h > windowHours)) return null;
  const d = Math.floor(h / 24);
  return d >= 1 ? `Older item · filed ${d} day${d === 1 ? "" : "s"} ago, not reviewed yet` : `Older item · filed ${Math.round(h)} h ago, not reviewed yet`;
}

/** Distinguishable subtitle for a plan: scope + its conditions in short form, e.g. "All 18 holdings · falls >4% or volume >2.5×". */
export function planSubtitle(p: { preview: { scope: string }; spec: { logical_operator: string; conditions: { kind: string; threshold?: number | null;
  direction?: string | null; roles?: string[] | null; change?: string | null; categories?: string[] | null; rating_action?: string | null }[] } }): string {
  const short = p.spec.conditions.map((c) => {
    switch (c.kind) {
      case "price_move": return `${c.direction === "up" ? "rises" : c.direction === "down" ? "falls" : "moves"} >${c.threshold}%`;
      case "volume": return `volume >${c.threshold}×`;
      case "guidance_cut": return "guidance cuts";
      case "fundraise_update": return "fund raises";
      default:
        if (c.categories?.[0] === "management_change") return `${(c.roles ?? ["leadership"]).join("/")} ${c.change === "departure" ? "exits" : c.change === "appointment" ? "appointments" : "changes"}`;
        return `${(c.categories ?? []).join(", ").replace(/_/g, " ")}${c.rating_action ? ` ${c.rating_action}s` : ""}`;
    }
  });
  return `${p.preview.scope} · ${short.join(p.spec.logical_operator === "AND" ? " and " : " or ")}`;
}
