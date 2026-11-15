"use client";
import type { Level } from "@/lib/types";

export function LevelBadge({ level, score }: { level: Level; score?: number }) {
  const cls: Record<Level, string> = {
    high: "bg-accent/15 text-accent ring-1 ring-inset ring-accent/30",
    medium: "bg-info/12 text-info ring-1 ring-inset ring-info/25",
    low: "bg-surface-2 text-ink-3 ring-1 ring-inset ring-line",
    routine: "bg-surface-2 text-ink-3 ring-1 ring-inset ring-line",
  };
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold capitalize ${cls[level]}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />{level}{score !== undefined && <span className="num font-normal opacity-80">{Math.round(score)}</span>}
    </span>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} />;
}

export function EmptyState({ title, body, action }: { title: string; body?: string; action?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line px-5 py-8 text-center">
      <p className="text-sm font-medium">{title}</p>
      {body && <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-ink-3">{body}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="rounded-xl border border-down/40 bg-down/5 px-4 py-4 text-sm">
      <p className="font-medium text-down">Something went wrong</p>
      <p className="mt-1 text-xs text-ink-2">{message}</p>
      {retry && <button onClick={retry} className="mt-3 rounded-md border border-line px-3 py-1.5 text-xs">Retry</button>}
    </div>
  );
}

/** Sparkline of the last N closes. Colour encodes the change over the window (not today's move): green up,
 *  red down, neutral within ±0.5%. The label is exposed to screen readers and as a tooltip. */
export function Sparkline({ data, className = "" }: { data: number[]; className?: string }) {
  if (!data || data.length < 2) return <div className={className} />;
  const min = Math.min(...data), max = Math.max(...data);
  const w = 80, h = 24;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / (max - min || 1)) * h}`).join(" ");
  const change = ((data[data.length - 1] - data[0]) / data[0]) * 100;
  const color = Math.abs(change) < 0.5 ? "var(--ink-3)" : change > 0 ? "var(--up)" : "var(--down)";
  const label = `${data.length}-session trend ${change > 0 ? "+" : ""}${change.toFixed(1)}%`;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className={className} preserveAspectRatio="none" role="img" aria-label={label}>
      <title>{label}</title>
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function SectionTitle({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="mb-3 mt-8 flex items-baseline justify-between">
      <h2 className="text-[12px] font-semibold uppercase tracking-[0.12em] text-ink-3">{children}</h2>
      {right}
    </div>
  );
}
