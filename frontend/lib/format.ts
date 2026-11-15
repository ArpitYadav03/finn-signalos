const IST = "Asia/Kolkata";

/** Signed percentage with an explicit sign and a true minus: +3.9%, −18.5%, 0.0%. */
export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const r = Number(v.toFixed(digits));
  if (r === 0) return `${(0).toFixed(digits)}%`;
  return `${r > 0 ? "+" : "−"}${Math.abs(r).toFixed(digits)}%`;
}

/** Signed percentage points: +1.0pp / −6.0pp. */
export function fmtPp(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const r = Number(v.toFixed(digits));
  return r === 0 ? `${(0).toFixed(digits)}pp` : `${r > 0 ? "+" : "−"}${Math.abs(r).toFixed(digits)}pp`;
}

/** Human window label for the Briefing summary. */
export function windowLabel(hours: number, custom: boolean, startIso?: string, endIso?: string): string {
  if (custom && startIso && endIso) return `${fmtTime(startIso)} → ${fmtTime(endIso)}`;
  if (hours <= 24) return "Previous 24 hours";
  if (hours % 24 === 0) return `Previous ${hours / 24} days`;
  return `Previous ${hours} hours`;
}

export function fmtCr(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const n = Math.round(v);
  return `${n < 0 ? "−" : ""}₹${Math.abs(n).toLocaleString("en-IN")} cr`;
}

export function fmtPrice(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `₹${v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", { timeZone: IST, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }) + " IST";
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export const tone = (v: number | null | undefined) =>
  v === null || v === undefined ? "text-ink-3" : v > 0 ? "text-up" : v < 0 ? "text-down" : "text-ink-2";

export function investigateQuestion(category: string, headline: string): string {
  switch (category) {
    case "results": return "What drove this quarter's results, and is profit keeping pace with revenue?";
    case "management_change": return "What has management said about leadership and strategy continuity?";
    case "M&A": return "How does this deal fit the company's stated strategy and capital allocation?";
    case "credit_rating": return "How has leverage and cash flow trended over the last few years?";
    case "fund_raise": return "What is the balance sheet position and how has management talked about funding needs?";
    case "litigation": return "What legal or regulatory risks does the company disclose?";
    case "capex": return "What has management said about capex and capacity expansion?";
    case "earnings_call": return "What did management guide, and did they deliver on earlier promises?";
    default: return `What do the primary documents say about: ${headline.slice(0, 80)}?`;
  }
}

/** "2026-11-13" -> "Fri 13 Nov" (a trading session, shown the way people say it). */
export function fmtDay(isoDate: string | null | undefined): string {
  if (!isoDate) return "—";
  const d = new Date(`${isoDate.slice(0, 10)}T12:00:00+05:30`);
  return d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", timeZone: "Asia/Kolkata" });
}

/** "2028-05-11" -> "11 May 2028": for dates that may fall outside the current year (guidance, deadlines). */
export function fmtDate(isoDate: string | null | undefined): string {
  if (!isoDate) return "—";
  const d = new Date(`${isoDate.slice(0, 10)}T12:00:00+05:30`);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
}
