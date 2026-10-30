import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
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

export function PageHead({ title, subtitle, children }: { title: string; subtitle?: string; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div><h1>{title}</h1>{subtitle && <p className="muted">{subtitle}</p>}</div>
      {children && <div className="page-actions">{children}</div>}
    </div>
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
}

export function DataTable<T>({ rows, columns, rowKey, onRow, empty }: {
  rows: T[]; columns: Column<T>[]; rowKey: (row: T) => string | number; onRow?: (row: T) => void; empty: string;
}) {
  if (!rows.length) return <p className="table-empty muted">{empty}</p>;
  return (
    <div className="table-wrap" role="region" aria-label="Table, scrolls sideways on small screens" tabIndex={0}>
      <table>
        <thead><tr>{columns.map((c) => <th key={c.label} scope="col" className={c.className}>{c.label}</th>)}</tr></thead>
        <tbody>
          {rows.map((row) => (
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
export function Dialog({ title, onClose, children, side }: { title: string; onClose: () => void; children: ReactNode; side?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog ref={ref} className={side ? "dialog side" : "dialog"} onClose={onClose} onClick={(e) => e.target === ref.current && onClose()}>
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
