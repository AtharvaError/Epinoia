import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ArrowUpDown, X } from "lucide-react";
import {
  type FormEvent, type ReactNode, useEffect, useMemo, useRef, useState, useSyncExternalStore,
} from "react";
import { useOutletContext } from "react-router-dom";

import { api, ApiError, type Festival, type Paged, type User } from "../lib/api";

export function useFest() {
  const { festival, user } = useOutletContext<{ festival: Festival; user: User }>();
  return useMemo(() => ({
    festival, user,
    base: `/api/festivals/${festival.id}`,
    can: (permission: string) => user.permissions.includes(permission),
  }), [festival, user]);
}

export function useDebounced<T>(value: T, ms = 250) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

export function queryString(params: Record<string, string | number | boolean | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "" && value !== false) search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export function usePaged<T>(path: string, params: Record<string, string | number | boolean | undefined>, enabled = true) {
  return useQuery({
    queryKey: [path, params],
    queryFn: () => api<Paged<T>>(`${path}${queryString(params)}`),
    placeholderData: keepPreviousData,
    enabled,
  });
}

/** Run a change, then refresh the listed data and show the outcome. */
export function useAction<V = void, R = unknown>(run: (values: V) => Promise<R>, options: { success?: string | ((r: R) => string); onDone?: (r: R) => void } = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: (result) => {
      queryClient.invalidateQueries();
      const { success, onDone } = options;
      if (success) notify(typeof success === "string" ? success : success(result));
      onDone?.(result);
    },
  });
}

let toasts: { id: number; text: string }[] = [];
const listeners = new Set<() => void>();
function publish(next: typeof toasts) {
  toasts = next;
  listeners.forEach((l) => l());
}
export function notify(text: string) {
  const id = Date.now() + Math.random();
  publish([...toasts, { id, text }]);
  setTimeout(() => publish(toasts.filter((t) => t.id !== id)), 4000);
}
export function Toasts() {
  const items = useSyncExternalStore((l) => {
    listeners.add(l);
    return () => listeners.delete(l);
  }, () => toasts);
  return <div className="toasts" role="status" aria-live="polite">{items.map((t) => <div key={t.id} className="toast">{t.text}</div>)}</div>;
}

export function PageHead({ title, subtitle, simulated, children }: { title: string; subtitle?: string; simulated?: boolean; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div><h1>{title}{simulated && <> <Simulated /></>}</h1>{subtitle && <p className="muted">{subtitle}</p>}</div>
      {children && <div className="page-actions">{children}</div>}
    </div>
  );
}

/** Heads a group of panels, so where the data came from is said once. */
export function SectionHead({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="section-head"><h2>{title}</h2>{children}</div>;
}

/** The one loud line on a screen: what just happened, or what needs a decision. */
export function Moment({ eyebrow, title, children }: { eyebrow?: ReactNode; title: ReactNode; children?: ReactNode }) {
  return (
    <section className="moment">
      {eyebrow && <div className="moment-eyebrow">{eyebrow}</div>}
      <h1>{title}</h1>
      {children && <div className="moment-body">{children}</div>}
    </section>
  );
}

export function Tabs<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: [T, string][] }) {
  return (
    <div className="tabs" role="tablist">
      {options.map(([key, label]) => (
        <button key={key} role="tab" aria-selected={value === key} className={value === key ? "active" : ""} onClick={() => onChange(key)}>{label}</button>
      ))}
    </div>
  );
}

export interface Column<T> {
  label: string;
  cell: (row: T) => ReactNode;
  className?: string;
  /** Server sort key, for paged tables where only the server has every row. */
  sort?: string;
  /** Sort value, for tables that already hold every row. */
  value?: (row: T) => string | number | null;
}

export function DataTable<T>({ rows, columns, rowKey, onRow, empty, sort, onSort }: {
  rows: T[]; columns: Column<T>[]; rowKey: (row: T) => string | number; onRow?: (row: T) => void; empty: string; sort?: string; onSort?: (sort: string) => void;
}) {
  const [local, setLocal] = useState("");
  const active = sort ?? local;
  const order = active.startsWith("-") ? -1 : 1;
  const byValue = columns.find((c) => c.value && c.label === active.replace(/^-/, ""))?.value;
  const shown = useMemo(() => (byValue ? [...rows].sort((a, b) => {
    const [x, y] = [byValue(a), byValue(b)];
    if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1;
    return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * order;
  }) : rows), [rows, byValue, order]);
  if (!rows.length) return <p className="table-empty muted">{empty}</p>;
  const heading = (c: Column<T>) => {
    const key = c.sort ?? (c.value ? c.label : undefined);
    if (!key) return c.label;
    const direction = active === key ? "ascending" : active === `-${key}` ? "descending" : undefined;
    const Icon = direction === "ascending" ? ArrowUp : direction === "descending" ? ArrowDown : ArrowUpDown;
    const next = direction === "ascending" ? `-${key}` : direction === "descending" ? "" : key;
    return <button className="th-sort" onClick={() => (onSort ?? setLocal)(next)}>{c.label}<Icon size={13} aria-hidden="true" /></button>;
  };
  const ariaSort = (c: Column<T>) => {
    const key = c.sort ?? c.label;
    return active === key ? "ascending" as const : active === `-${key}` ? "descending" as const : undefined;
  };
  return (
    <div className="table-wrap" role="region" aria-label="Table, scrolls on small screens" tabIndex={0}>
      <table>
        <thead><tr>{columns.map((c) => <th key={c.label} scope="col" className={c.className} aria-sort={ariaSort(c)}>{heading(c)}</th>)}</tr></thead>
        <tbody>
          {shown.map((row) => (
            <tr key={rowKey(row)} className={onRow ? "clickable" : ""} onClick={onRow && (() => onRow(row))}
                tabIndex={onRow ? 0 : undefined} onKeyDown={onRow && ((e) => e.key === "Enter" && onRow(row))}>
              {columns.map((c) => <td key={c.label} className={c.className}>{c.cell(row)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Pagination({ page, pages, total, onPage }: { page: number; pages: number; total: number; onPage: (p: number) => void }) {
  return (
    <div className="pagination">
      <span className="muted small">{total.toLocaleString("en-IN")} total</span>
      <button className="button-ghost" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
      <span className="small">Page {page} of {pages}</span>
      <button className="button-ghost" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </div>
  );
}

/** A modal built on the native dialog element, which handles focus and Escape. */
export function Dialog({ title, onClose, children, side, className = "" }: { title: string; onClose: () => void; children: ReactNode; side?: boolean; className?: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
    ref.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus(); // showModal moves focus to the close button.
  }, []);
  return (
    <dialog ref={ref} className={`dialog ${side ? "side" : ""} ${className}`} onClose={onClose} onClick={(e) => e.target === ref.current && onClose()}>
      <header><h2>{title}</h2><button className="icon-button" aria-label="Close" onClick={onClose}><X size={18} /></button></header>
      <div className="dialog-body">{children}</div>
    </dialog>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : "Something went wrong";
  const conflicts = error instanceof ApiError ? error.conflicts : [];
  return <div className="form-error" role="alert"><p>{message}</p>{conflicts.length > 0 && <ul>{conflicts.map((c) => <li key={c}>{c}</li>)}</ul>}</div>;
}

export function Loading() {
  return <p className="muted loading" role="status">Loading</p>;
}

export function QueryState({ query, children }: { query: { isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown }; children: ReactNode }) {
  if (query.isPending) return <Loading />;
  if (query.isError) {
    return <div className="state-error" role="alert"><ErrorNote error={query.error} /><button className="button-secondary" onClick={() => query.refetch()}>Try again</button></div>;
  }
  return <>{children}</>;
}

export function Pill({ value, tone }: { value: string; tone?: "ok" | "warn" | "bad" | "info" }) {
  return <span className={`pill ${tone ? `pill-${tone}` : ""}`}>{value.replaceAll("_", " ")}</span>;
}

export function Simulated() {
  return <span className="source source-simulated" title="Demo data: the pack has no records for this area">Simulated</span>;
}

export function Facts({ items }: { items: [string, ReactNode][] }) {
  return <dl className="facts">{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? <span className="unknown">Unknown</span>}</dd></div>)}</dl>;
}

/** Ask for a reason, then submit. Used for deletions and overrides. */
export function ReasonForm({ label, action, onSubmit, pending, error, danger }: {
  label: string; action: string; onSubmit: (reason: string) => void; pending: boolean; error: unknown; danger?: boolean;
}) {
  const [reason, setReason] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit(reason.trim());
  };
  return (
    <form className="form" onSubmit={submit}>
      <Field label={label}><textarea required minLength={5} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      <ErrorNote error={error} />
      <button className={danger ? "button-danger" : "button-primary"} disabled={pending}>{action}</button>
    </form>
  );
}

/** Times are entered and shown in festival time, IST. */
export function toInput(iso: string | null | undefined) {
  if (!iso) return "";
  const local = new Date(new Date(iso).getTime() + 5.5 * 3600 * 1000);
  return local.toISOString().slice(0, 16);
}
export function fromInput(value: string) {
  return value ? `${value}:00+05:30` : null;
}
