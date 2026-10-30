import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity, BarChart3, Bell, BedDouble, Bus, CalendarDays, ChevronsLeft, ChevronsRight, HandHelping, LayoutDashboard,
  LogOut, Megaphone, Menu, MessageCircleQuestion, Search, Settings, ShieldAlert, Users, Wallet, Warehouse, X, type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";

import { Assistant, type Message } from "./Assistant";
import { Toasts } from "./ui";
import { api, type Festival, type Notifications, type SearchResult, type User } from "../lib/api";
import { formatTime } from "../lib/format";

interface NavItem {
  label: string;
  icon: LucideIcon;
  permission?: string;
  to?: string;
  phase?: number;
}

const NAV: NavItem[] = [
  { label: "Command Center", icon: LayoutDashboard, permission: "dashboard.read", to: "/" },
  { label: "Events & Schedule", icon: CalendarDays, permission: "events.read", to: "/events" },
  { label: "Participants", icon: Users, permission: "participants.read", to: "/participants" },
  { label: "Accommodation", icon: BedDouble, permission: "accommodation.read", to: "/accommodation" },
  { label: "Transportation", icon: Bus, permission: "transport.read", to: "/transportation" },
  { label: "Volunteers", icon: HandHelping, permission: "volunteers.read", to: "/volunteers" },
  { label: "Venues & Resources", icon: Warehouse, permission: "venues.read", to: "/venues" },
  { label: "Finance", icon: Wallet, permission: "finance.read", to: "/finance" },
  { label: "Communication", icon: Megaphone, permission: "communication.read", to: "/communication" },
  { label: "Safety & Incidents", icon: ShieldAlert, permission: "incidents.read", to: "/safety" },
  { label: "Ripple Simulator", icon: Activity, permission: "simulation.run", to: "/ripple" },
  { label: "Analytics & Reports", icon: BarChart3, permission: "analytics.read", to: "/analytics" },
  { label: "Settings", icon: Settings, permission: "settings.manage", phase: 5 },
];

const COLLAPSE_KEY = "festos.sidebar";

function remembered() {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

function remember(collapsed: boolean) {
  try {
    localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
  } catch {
    // Only a convenience: the sidebar still works without storage.
  }
}

/** The server sends no data over this socket, only a nudge to refetch through the normal routes. */
function useLiveUpdates(festivalId: number | undefined) {
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!festivalId) return;
    let socket: WebSocket;
    let retry: ReturnType<typeof setTimeout>;
    let refresh: ReturnType<typeof setTimeout>;
    let stopped = false;
    const connect = () => {
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${location.host}/api/ws/${festivalId}`);
      socket.onmessage = () => {
        clearTimeout(refresh);
        refresh = setTimeout(() => queryClient.invalidateQueries(), 300);
      };
      socket.onclose = (event) => {
        if (!stopped && event.code !== 4401) retry = setTimeout(connect, 3000);
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      clearTimeout(refresh);
      socket.close();
    };
  }, [festivalId, queryClient]);
}

function Clock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 30000);
    return () => clearInterval(timer);
  }, []);
  return <time className="clock">{formatTime(now.toISOString())} IST</time>;
}

function GlobalSearch({ festivalId }: { festivalId: number }) {
  const [term, setTerm] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setQuery(term.trim()), 250);
    return () => clearTimeout(timer);
  }, [term]);

  const results = useQuery({
    queryKey: ["search", festivalId, query],
    queryFn: () => api<SearchResult[]>(`/api/festivals/${festivalId}/search?q=${encodeURIComponent(query)}`),
    enabled: query.length >= 2,
  });

  return (
    <div className="search">
      <Search size={16} aria-hidden="true" />
      <input type="search" placeholder="Search participants and events" aria-label="Search participants and events"
             value={term} onChange={(e) => setTerm(e.target.value)} onBlur={() => setTimeout(() => setTerm(""), 150)} />
      {query.length >= 2 && term && (
        <div className="popover search-results">
          {results.isPending && <p className="muted small">Searching</p>}
          {results.isError && <p className="form-error">{results.error.message}</p>}
          {results.data?.length === 0 && <p className="muted small">No matches for “{query}”.</p>}
          {results.data?.map((r) => (
            <Link key={`${r.type}-${r.code}`} className="search-row" to={`/${r.type === "Event" ? "events" : "participants"}?open=${r.code}`}>
              <span className="mono">{r.code}</span>
              <span><strong>{r.title}</strong><small>{r.subtitle}</small></span>
              <span className="tag">{r.type}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function usePopover() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  return { open, setOpen, ref };
}

function NotificationCenter({ festivalId }: { festivalId: number }) {
  const { open, setOpen, ref } = usePopover();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const visit = (note: Notifications["items"][number]) => {
    setOpen(false);
    if (note.link) navigate(note.link);
    if (!note.read) api(`/api/festivals/${festivalId}/notifications/${note.id}/read`, { method: "POST" }).then(() => queryClient.invalidateQueries({ queryKey: ["notifications", festivalId] }));
  };
  const notes = useQuery({
    queryKey: ["notifications", festivalId],
    queryFn: () => api<Notifications>(`/api/festivals/${festivalId}/notifications`),
    refetchInterval: 15000,
  });
  const unread = notes.data?.unread ?? 0;
  return (
    <div className="popover-wrap" ref={ref}>
      <button className="icon-button" aria-label={`Notifications, ${unread} unread`} aria-expanded={open} onClick={() => setOpen(!open)}>
        <Bell size={18} />
        {unread > 0 && <span className="badge">{unread}</span>}
      </button>
      {open && (
        <div className="popover notifications">
          <h3>Notifications</h3>
          {notes.isError && <p className="form-error">{notes.error.message}</p>}
          {notes.data?.items.length === 0 && <p className="muted small">Nothing yet. Operational changes that affect you will appear here.</p>}
          {notes.data?.items.map((n) => (
            <button key={n.id} className={n.read ? "note" : "note unread"} onClick={() => visit(n)}><strong>{n.title}</strong>{n.body && <p>{n.body}</p>}<small>{formatTime(n.created_at)}</small></button>
          ))}
        </div>
      )}
    </div>
  );
}

function Profile({ user }: { user: User }) {
  const { open, setOpen, ref } = usePopover();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const signOut = useMutation({
    mutationFn: () => api<void>("/api/auth/logout", { method: "POST" }),
    onSuccess: () => {
      queryClient.clear();
      navigate("/login", { replace: true });
    },
  });
  const initials = user.name.split(" ").map((part) => part[0]).slice(0, 2).join("");
  return (
    <div className="popover-wrap" ref={ref}>
      <button className="profile-button" aria-label={`Account menu for ${user.name}`} aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="avatar">{initials}</span>
        <span className="profile-text"><strong>{user.name}</strong><small>{user.role.name}</small></span>
      </button>
      {open && (
        <div className="popover profile-menu">
          <strong>{user.name}</strong>
          <p className="muted small">{user.email}</p>
          <p className="muted small">{user.role.name}</p>
          <button className="menu-action" onClick={() => signOut.mutate()} disabled={signOut.isPending}>
            <LogOut size={16} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export function Shell({ user }: { user: User }) {
  const [collapsed, setCollapsed] = useState(remembered);
  const [drawer, setDrawer] = useState(false);
  const [festivalId, setFestivalId] = useState<number>();
  const festivals = useQuery({ queryKey: ["festivals"], queryFn: () => api<Festival[]>("/api/festivals") });
  const festival = festivals.data?.find((f) => f.id === festivalId) ?? festivals.data?.[0];
  const allowed = NAV.filter((item) => !item.permission || user.permissions.includes(item.permission));
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [conversation, setConversation] = useState<Message[]>([]);
  useLiveUpdates(festival?.id);

  const toggle = () => {
    remember(!collapsed);
    setCollapsed(!collapsed);
  };

  return (
    <div className={`shell ${collapsed ? "collapsed" : ""}`}>
      {drawer && <button className="scrim" aria-label="Close menu" onClick={() => setDrawer(false)} />}
      <aside className={`sidebar ${drawer ? "open" : ""}`}>
        <div className="sidebar-head">
          <div className="brand"><span className="brand-mark" aria-hidden="true" /><span className="nav-text">FestOS Command</span></div>
          <button className="icon-button drawer-close" aria-label="Close menu" onClick={() => setDrawer(false)}><X size={18} /></button>
        </div>
        <nav aria-label="Main">
          {allowed.map(({ label, icon: Icon, to, phase }) =>
            to ? (
              <NavLink key={label} to={to} end className={`nav-item ${label === "Ripple Simulator" ? "signature" : ""}`} title={label} onClick={() => setDrawer(false)}>
                <Icon size={18} aria-hidden="true" /><span className="nav-text">{label}</span>
              </NavLink>
            ) : (
              <span key={label} className="nav-item disabled" aria-disabled="true" title={`${label} arrives in Phase ${phase}`}>
                <Icon size={18} aria-hidden="true" /><span className="nav-text">{label}</span><span className="nav-phase">P{phase}</span>
              </span>
            ),
          )}
        </nav>
        <button className="collapse" onClick={toggle} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
          {collapsed ? <ChevronsRight size={18} /> : <><ChevronsLeft size={18} /><span className="nav-text">Collapse</span></>}
        </button>
      </aside>

      <div className="main">
        <header className="topbar">
          <button className="icon-button menu-open" aria-label="Open menu" onClick={() => setDrawer(true)}><Menu size={18} /></button>
          {festivals.data && (
            <select className="festival-select" aria-label="Festival" value={festival?.id} onChange={(e) => setFestivalId(Number(e.target.value))}>
              {festivals.data.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
          )}
          {festival && <span className={`phase phase-${festival.phase.toLowerCase()}`}>{festival.phase}</span>}
          {festival && <GlobalSearch festivalId={festival.id} />}
          <div className="topbar-end">
            <Clock />
            <button className="icon-button" aria-label="Open the festival assistant" title="Festival assistant" onClick={() => setAssistantOpen(true)}><MessageCircleQuestion size={18} /></button>
            {festival && <NotificationCenter festivalId={festival.id} />}
            <Profile user={user} />
          </div>
        </header>
        <main className="content">
          {festivals.isError && <div className="state-error" role="alert">{festivals.error.message}</div>}
          {festival && <Outlet context={{ festival, user }} />}
        </main>
      </div>
      {assistantOpen && festival && <Assistant festivalId={festival.id} messages={conversation} setMessages={setConversation} onClose={() => setAssistantOpen(false)} />}
      <Toasts />
    </div>
  );
}
