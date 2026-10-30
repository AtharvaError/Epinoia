export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : "The server could not complete that request.";
    throw new ApiError(response.status, detail);
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
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
  items: { id: number; title: string; body: string | null; read: boolean; created_at: string }[];
}
