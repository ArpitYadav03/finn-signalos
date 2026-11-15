"use client";
import type { Citation, Claim } from "@/lib/types";
import { CiteChip, useEvidence } from "./EvidenceSheet";

/** Minimal, safe markdown: paragraphs, "- " bullets, **bold**, and citation groups. No HTML injection.
 *  Consecutive markers form one group: document passages ([E#]) stay individual chips, while structured figures
 *  ([F#] facts, [C#] calculations) collapse into one control that opens exactly those figures and their inputs. */

function claimLabel(c: Claim): string {
  const per = c.period_type === "session" ? "" : `${c.period} `;
  const short = c.label.replace(/\s*\(.*?\)\s*/g, " ").trim();
  return `${per}${short}`.slice(0, 28);
}

function ClaimChip({ ids, claims }: { ids: string[]; claims: Record<string, Claim> }) {
  const open = useEvidence();
  const known = ids.filter((i) => claims[i]);
  if (!known.length) return null;
  const label = known.length === 1 ? claimLabel(claims[known[0]]) : `${known.length} figures`;
  const anyCalc = known.some((i) => claims[i].kind === "calc");
  return (
    <button onClick={() => open({ kind: "claims", claim_ids: known, claims, document_title: "Figures & calculations" })}
      className="inline-flex min-h-[24px] items-center gap-1 rounded-md border border-line bg-surface-2 px-1.5 py-0.5 align-baseline text-[11px] text-ink-2 hover:border-accent hover:text-ink"
      aria-label={`Open ${known.length === 1 ? "figure" : `${known.length} figures`}: ${known.map((i) => `${claims[i].label} ${claims[i].period} ${claims[i].display}`).join("; ")}`}>
      <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden>
        {anyCalc ? <path d="M5 7h14M5 12h14M5 17h8" /> : <path d="M4 19V9m6 10V5m6 14v-7" />}
      </svg>
      {label}
    </button>
  );
}

function Group({ ids, evidence, claims }: { ids: string[]; evidence: Map<string, Citation>; claims: Record<string, Claim> }) {
  const docs = ids.filter((i) => i.startsWith("E"));
  const figs = ids.filter((i) => i[0] === "F" || i[0] === "C");
  return (
    <span className="inline-flex flex-wrap items-baseline gap-1 align-baseline">
      {docs.map((d) => {
        const c = evidence.get(d);
        return c ? <CiteChip key={d} c={{ ...c, id: d }} label={`${d}${c.page_number ? ` · p.${c.page_number}` : ""}`} /> : null;
      })}
      {figs.length > 0 && <ClaimChip ids={figs} claims={claims} />}
    </span>
  );
}

function Inline({ text, evidence, claims }: { text: string; evidence: Map<string, Citation>; claims: Record<string, Claim> }) {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|(?:\s*\[(?:E\d+|F\d+|C\d+|M)\])+)/g).filter(Boolean);
  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith("**")) return <strong key={i} className="font-semibold text-ink">{p.slice(2, -2)}</strong>;
        if (p.length > 2 && p.startsWith("*") && p.endsWith("*")) return <em key={i} className="text-ink-3">{p.slice(1, -1)}</em>;
        if (/^(\s*\[(E\d+|F\d+|C\d+|M)\])+$/.test(p)) {
          const ids = Array.from(p.matchAll(/\[(E\d+|F\d+|C\d+)\]/g)).map((m) => m[1]);
          return ids.length ? <span key={i}> <Group ids={ids} evidence={evidence} claims={claims} /></span> : null;
        }
        return <span key={i}>{p}</span>;
      })}
    </>
  );
}

const BULLET = /^\s*([-*•]|\d+\.)\s+/;

/** Explicit provenance per line: Fact (document passage), Reported figure / Calculation (structured data), or Interpretation. */
function lineLabel(text: string, section: string): { label: string; cls: string } | null {
  if (/^\s*\*\*[^*]+:\*\*\s*$/.test(text)) return null;
  if (section === "open") return { label: "Open question", cls: "text-ink-3 border-line" };
  if (section === "limits") return { label: "Limit", cls: "text-ink-3 border-line" };
  const e = /\[E\d+\]/.test(text), c = /\[C\d+\]/.test(text), f = /\[F\d+\]/.test(text);
  if (e && (c || f)) return { label: "Fact + figures", cls: "text-info border-info/40" };
  if (e) return { label: "Fact", cls: "text-info border-info/40" };
  if (c) return { label: "Calculation", cls: "text-up border-up/40" };
  if (f) return { label: "Reported figure", cls: "text-up border-up/40" };
  return { label: "Interpretation", cls: "text-accent border-accent/40" };
}

function Tag({ t }: { t: { label: string; cls: string } | null }) {
  if (!t) return null;
  return <span className={`mr-1.5 inline-block rounded border px-1 align-[1px] text-[10.5px] font-medium uppercase tracking-wide ${t.cls}`}>{t.label}</span>;
}

export function Answer({ md, evidence, claims = {} }: { md: string; evidence: Citation[]; claims?: Record<string, Claim> }) {
  const map = new Map(evidence.map((e) => [e.id as string, e]));
  const groups: { kind: "ul" | "p" | "h"; lines: string[]; section: string }[] = [];
  let section = "main";
  for (const raw of md.split("\n")) {
    const line = raw.trimEnd();
    if (!line.trim()) { groups.push({ kind: "p", lines: [], section }); continue; }
    if (/^\s*\*\*\s*open questions?/i.test(line)) section = "open";
    else if (/^\s*\*\*\s*(limits?|limitations)/i.test(line)) section = "limits";
    else if (/^\s*\*\*\s*evidence/i.test(line)) section = "evidence";
    const kind = BULLET.test(line) ? "ul" : /^#{1,4}\s+/.test(line) ? "h" : "p";
    const last = groups[groups.length - 1];
    if (last && last.kind === kind && kind !== "h" && last.lines.length && last.section === section) last.lines.push(line);
    else groups.push({ kind, lines: [line], section });
  }
  return (
    <div className="space-y-3 text-[13.5px] leading-relaxed text-ink-2">
      {groups.filter((g) => g.lines.length).map((g, i) => {
        if (g.kind === "h") return <p key={i} className="font-semibold text-ink">{g.lines[0].replace(/^#{1,4}\s+/, "")}</p>;
        if (g.kind === "ul") {
          return (
            <ul key={i} className="space-y-1.5 pl-1">
              {g.lines.map((l, j) => (
                <li key={j} className="flex gap-2"><span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-ink-3" /><span><Tag t={lineLabel(l, g.section)} /><Inline text={l.replace(BULLET, "")} evidence={map} claims={claims} /></span></li>
              ))}
            </ul>
          );
        }
        return <p key={i}>{g.lines.map((l, j) => <span key={j}>{j > 0 && <br />}<Tag t={lineLabel(l, g.section)} /><Inline text={l} evidence={map} claims={claims} /></span>)}</p>;
      })}
    </div>
  );
}

/** Every figure the answer relies on, one row each: what it is, period, value, basis, and where it comes from. */
export function FiguresTable({ claims, onOpen }: { claims: Record<string, Claim>; onOpen: (ids: string[]) => void }) {
  const rows = Object.values(claims);
  if (!rows.length) return null;
  return (
    <div className="no-scrollbar overflow-x-auto rounded-lg border border-line">
      <table className="w-full min-w-[460px] text-[12px]">
        <caption className="sr-only">Figures used in this answer, with period, value, basis and source</caption>
        <thead className="bg-surface-2 text-[11px] uppercase tracking-wider text-ink-3">
          <tr><th scope="col" className="px-2.5 py-1.5 text-left font-medium">Figure</th><th scope="col" className="px-2 text-left font-medium">Period</th>
            <th scope="col" className="px-2 text-right font-medium">Value</th><th scope="col" className="px-2 text-left font-medium">Basis</th>
            <th scope="col" className="px-2.5 text-left font-medium">Source</th></tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id} className="border-t border-line">
              <th scope="row" className="px-2.5 py-1.5 text-left font-normal">
                <button className="text-left text-ink-2 hover:text-ink" onClick={() => onOpen([c.id])}>{c.label}</button>
                <span className="ml-1 text-[11px] text-ink-3">{c.id}</span></th>
              <td className="num px-2 text-ink-2">{c.period}{c.base_period ? ` vs ${c.base_period}` : ""}</td>
              <td className="num px-2 text-right text-ink">{c.display}</td>
              <td className="px-2 text-ink-3">{c.basis ?? "—"}</td>
              <td className="px-2.5 text-ink-3">{c.kind === "calc" ? `calculated from ${c.inputs.join(", ")}` : c.source.primary ? `filing p.${c.source.primary.page_number}` : c.source.kind === "eod_prices" ? "EOD prices" : "standardised results"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
