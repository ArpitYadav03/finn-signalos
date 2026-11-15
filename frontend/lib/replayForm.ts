/** Replay form state. Pure (no React, no imports) so it can be tested with `node --test`.
 *
 * Rules:
 * - Server defaults (latest session, 30 days before it) fill only fields the user has not touched, whenever they arrive.
 * - Coverage is stored with the exact key it was checked for (plan, version, dates, mode); it is shown only while that
 *   key still matches the inputs, and a response for an outdated key is dropped.
 */
export type Mode = "public" | "indexed";

export interface ReplayForm<C = unknown> {
  start: string;
  end: string;
  mode: Mode;
  maxDate: string;
  touched: { start: boolean; end: boolean };
  coverage: { key: string; data: C } | null;
}

export type ReplayAction<C = unknown> =
  | { type: "defaults"; latest: string }
  | { type: "edit"; field: "start" | "end"; value: string }
  | { type: "mode"; mode: Mode }
  | { type: "coverage"; key: string; data: C }
  | { type: "clearCoverage" };

export const initialReplayForm = <C,>(): ReplayForm<C> => ({
  start: "", end: "", mode: "public", maxDate: "", touched: { start: false, end: false }, coverage: null,
});

export function daysBefore(isoDate: string, days: number): string {
  const d = new Date(`${isoDate}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

export function coverageKey(planId: number, version: number, f: { start: string; end: string; mode: Mode }): string {
  return `${planId}:v${version}:${f.start}:${f.end}:${f.mode}`;
}

export function replayFormReducer<C>(s: ReplayForm<C>, a: ReplayAction<C>): ReplayForm<C> {
  switch (a.type) {
    case "defaults": {
      if (!a.latest) return s;
      return {
        ...s,
        maxDate: a.latest,
        end: s.touched.end ? s.end : a.latest,
        start: s.touched.start ? s.start : daysBefore(a.latest, 30),
      };
    }
    case "edit":
      return { ...s, [a.field]: a.value, touched: { ...s.touched, [a.field]: true } };
    case "mode":
      return { ...s, mode: a.mode };
    case "coverage":
      return { ...s, coverage: { key: a.key, data: a.data } };
    case "clearCoverage":
      return { ...s, coverage: null };
  }
}

/** Coverage that still describes the current inputs, or null when the inputs changed since it was checked. */
export function currentCoverage<C>(s: ReplayForm<C>, planId: number, version: number): C | null {
  return s.coverage && s.coverage.key === coverageKey(planId, version, s) ? s.coverage.data : null;
}

export function validRange(s: { start: string; end: string; maxDate: string }): string | null {
  if (!s.start || !s.end) return "Choose both dates.";
  if (s.start > s.end) return "“From” must be on or before “To”.";
  if (s.maxDate && s.end > s.maxDate) return `FINN has prices only up to ${s.maxDate}.`;
  return null;
}
