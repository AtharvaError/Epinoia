export class ApiError extends Error {
  constructor(public status: number, message: string, public conflicts: string[] = []) {
    super(message);
  }
}

function errorFrom(status: number, body: { detail?: unknown } | null) {
  const detail = body?.detail;
  if (typeof detail === "string") return new ApiError(status, detail);
  if (Array.isArray(detail)) {
    const first = detail[0] as { loc?: string[]; msg?: string };
    return new ApiError(status, `${first.loc?.at(-1) ?? "Input"}: ${first.msg ?? "is not valid"}`);
  }
  if (detail && typeof detail === "object" && "message" in detail) {
    const { message, conflicts } = detail as { message: string; conflicts?: string[] };
    return new ApiError(status, message, conflicts);
  }
  return new ApiError(status, "The server could not complete that request.");
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const isForm = init?.body instanceof FormData;
  const response = await fetch(path, {
    ...init,
    headers: { ...(isForm ? {} : { "Content-Type": "application/json" }), ...init?.headers },
  });
  if (!response.ok) throw errorFrom(response.status, await response.json().catch(() => null));
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  pages: number;
  page_size: number;
}

export type Source = "pack" | "generated" | "simulated";

export interface User {
  id: number;
  name: string;
  email: string;
  festival_id: number | null;
  role: { key: string; name: string };
  permissions: string[];
}

export interface Festival {
  id: number;
  name: string;
  start_date: string;
  end_date: string;
  phase: string;
}

export interface Kpi {
  key: string;
  label: string;
  value: number | string;
  total: number | string | null;
  source: Source;
  note: string | null;
}

export interface Alert {
  severity: "critical" | "high" | "medium";
  module: string;
  title: string;
  detail: string;
  source: Source;
}

export interface FeedItem {
  id: number;
  action: string;
  actor_label: string;
  summary: string;
  created_at: string;
}

export interface Dashboard {
  festival: Festival;
  kpis: Kpi[];
  alerts: Alert[];
  feed: FeedItem[];
  charts: {
    arrivals: { label: string; count: number }[];
    event_participation: { code: string; name: string; registered: number; capacity: number | null }[];
    budget: { department: string; approved: number; spent: number }[];
    occupancy: { name: string; kind: string; capacity: number; occupied: number }[];
    volunteers: { department: string; volunteers: number; shifts: number }[];
    event_status: { status: string; count: number }[];
    tasks: { department: string; total: number; done: number; in_progress: number }[];
  };
}

export interface SearchResult {
  type: string;
  code: string;
  title: string;
  subtitle: string;
}

export interface Notifications {
  unread: number;
  items: { id: number; title: string; body: string | null; link: string | null; read: boolean; created_at: string }[];
}
