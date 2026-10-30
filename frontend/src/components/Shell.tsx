import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity, Bell, ClipboardList, CircleDot, LogOut, MessageCircleQuestion, Moon, Search, Siren, Sun, UserRound, type LucideIcon,
} from "lucide-react";
import { Suspense, useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { Assistant, type Message } from "./Assistant";
import { type Command, CommandPalette } from "./CommandPalette";
import { ProfileDialog } from "./ProfileDialog";
import { Loading, Toasts } from "./ui";
import { api, type Festival, type Notifications, type User } from "../lib/api";
import { formatTime } from "../lib/format";

interface Page { label: string; to: string; permission: string }
interface Workspace { label: string; icon: LucideIcon; pages: Page[] }

// Organised by what someone is doing, not by department. See docs/DESIGN.md.
const WORKSPACES: Workspace[] = [
  { label: "Now", icon: CircleDot, pages: [
    { label: "Decisions", to: "/", permission: "dashboard.read" },
    { label: "Check-in", to: "/check-in", permission: "participants.checkin" },
    { label: "Safety", to: "/safety", permission: "incidents.read" },
    { label: "Messages", to: "/communication", permission: "communication.read" },
    { label: "Trends", to: "/trends", permission: "dashboard.read" },
  ] },
  { label: "Respond", icon: Siren, pages: [{ label: "Report a disruption", to: "/ripple", permission: "simulation.run" }] },
  { label: "Plan", icon: ClipboardList, pages: [
    { label: "Schedule", to: "/events", permission: "events.read" },
    { label: "People", to: "/participants", permission: "participants.read" },
    { label: "Stays", to: "/accommodation", permission: "accommodation.read" },
    { label: "Transport", to: "/transportation", permission: "transport.read" },
    { label: "Crew", to: "/volunteers", permission: "volunteers.read" },
    { label: "Venues and kit", to: "/venues", permission: "venues.read" },
    { label: "Food", to: "/vendors", permission: "dashboard.read" },
  ] },
  { label: "Review", icon: Activity, pages: [
    { label: "Money", to: "/finance", permission: "finance.read" },
    { label: "Reports", to: "/analytics", permission: "analytics.read" },
    { label: "Settings", to: "/settings", permission: "settings.manage" },
  ] },
];

const SIMULATIONS = [
  ["hostel_closure", "A hostel closed"], ["venue_change", "A venue changed"], ["bus_delay", "A bus is late"], ["late_contingent", "A contingent is late"],
  ["equipment_failure", "Equipment failed"], ["vendor_shutdown", "A vendor shut down"], ["volunteers_unavailable", "Volunteers dropped out"], ["budget_reduction", "The budget was cut"],
];

const THEME_KEY = "festos.theme";
const FESTIVAL_KEY = "festos.festival";

function rememberedFestival() {
  try {
    return Number(localStorage.getItem(FESTIVAL_KEY)) || undefined;
  } catch {
    return undefined;
  }
}

function rememberFestival(id: number) {
  try {
    localStorage.setItem(FESTIVAL_KEY, String(id));
  } catch {
    // Only a convenience: it falls back to the newest festival.
  }
}

/** Day reads in sunlight, so it is the default. Night suits the control room. */
function useTheme() {
  const [theme, setTheme] = useState(document.documentElement.dataset.theme ?? "day");
  const toggle = () => {
    const next = theme === "night" ? "day" : "night";
    document.documentElement.dataset.theme = next;
    setTheme(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // Only a convenience: the theme still applies for this visit.
    }
  };
  return { theme, toggle };
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
  const [editing, setEditing] = useState(false);
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
          <button className="menu-action" onClick={() => { setOpen(false); setEditing(true); }}><UserRound size={16} /> Profile and password</button>
          <button className="menu-action" onClick={() => signOut.mutate()} disabled={signOut.isPending}>
            <LogOut size={16} /> Sign out
          </button>
        </div>
      )}
      {editing && <ProfileDialog user={user} onClose={() => setEditing(false)} />}
    </div>
  );
}

export function Shell({ user }: { user: User }) {
  const [festivalId, setFestivalId] = useState(rememberedFestival);
  const festivals = useQuery({ queryKey: ["festivals"], queryFn: () => api<Festival[]>("/api/festivals") });
  const festival = festivals.data?.find((f) => f.id === festivalId) ?? festivals.data?.[0];
  const { pathname } = useLocation();
  const workspaces = WORKSPACES.map((w) => ({ ...w, pages: w.pages.filter((p) => user.permissions.includes(p.permission)) })).filter((w) => w.pages.length);
  const current = workspaces.find((w) => w.pages.some((p) => p.to === pathname));
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [conversation, setConversation] = useState<Message[]>([]);
  const { theme, toggle: toggleTheme } = useTheme();
  const canRespond = user.permissions.includes("simulation.run");
  const nightMode = theme === "night";
  useLiveUpdates(festival?.id);

  useEffect(() => {
    const open = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
      }
    };
    document.addEventListener("keydown", open);
    return () => document.removeEventListener("keydown", open);
  }, []);

  const commands: Command[] = [
    ...workspaces.flatMap((w) => w.pages.map((p) => ({ label: p.label, hint: w.label, to: p.to }))),
    ...(canRespond ? SIMULATIONS.map(([key, label]) => ({ label, hint: "Report a disruption", to: `/ripple?scenario=${key}` })) : []),
    { label: nightMode ? "Switch to day mode" : "Switch to night mode", hint: "Display", run: toggleTheme },
  ];

  return (
    <div className="shell">
      <header className="masthead">
        <div className="masthead-row">
          <Link className="brand" to="/" aria-label="FestOS home"><span className="brand-mark" aria-hidden="true" />FestOS</Link>
          <nav className="workspaces" aria-label="Workspaces">
            {workspaces.map((w) => <Link key={w.label} to={w.pages[0].to} className={w === current ? "active" : ""} aria-current={w === current ? "true" : undefined}>{w.label}</Link>)}
          </nav>
          {festival && (
            <button className="search" onClick={() => setPaletteOpen(true)}>
              <Search size={16} aria-hidden="true" /><span>Search or jump to</span><kbd>Ctrl K</kbd>
            </button>
          )}
          <div className="masthead-end">
            {canRespond && <Link className="report" to="/ripple"><Siren size={16} aria-hidden="true" /><span>Report a disruption</span></Link>}
            <Clock />
            <button className="icon-button" aria-label={nightMode ? "Switch to day mode" : "Switch to night mode"} title={nightMode ? "Day mode" : "Night mode"} onClick={toggleTheme}>{nightMode ? <Sun size={18} /> : <Moon size={18} />}</button>
            <button className="icon-button" aria-label="Open the festival assistant" title="Festival assistant" onClick={() => setAssistantOpen(true)}><MessageCircleQuestion size={18} /></button>
            {festival && <NotificationCenter festivalId={festival.id} />}
            <Profile user={user} />
          </div>
        </div>
        <div className="context-row">
          {festivals.data && (
            <select className="festival-select" aria-label="Festival" value={festival?.id} onChange={(e) => { setFestivalId(Number(e.target.value)); rememberFestival(Number(e.target.value)); }}>
              {festivals.data.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
          )}
          {festival && <span className={`phase phase-${festival.phase.toLowerCase()}`}>{festival.phase}</span>}
          {current && current.pages.length > 1 && (
            <nav className="subnav" aria-label={`${current.label} pages`}>
              {current.pages.map((p) => <NavLink key={p.to} to={p.to} end>{p.label}</NavLink>)}
            </nav>
          )}
        </div>
      </header>
      <main className="content">
        {festivals.isError && <div className="state-error" role="alert">{festivals.error.message}</div>}
        {festival && <Suspense fallback={<Loading />}><Outlet context={{ festival, user }} /></Suspense>}
      </main>
      <nav className="tabbar" aria-label="Workspaces on a phone">
        {workspaces.map(({ label, icon: Icon, pages }) => (
          <Link key={label} to={pages[0].to} className={pages.some((p) => p.to === pathname) ? "active" : ""}><Icon size={20} aria-hidden="true" /><span>{label}</span></Link>
        ))}
      </nav>
      {assistantOpen && festival && <Assistant festivalId={festival.id} messages={conversation} setMessages={setConversation} onClose={() => setAssistantOpen(false)} />}
      {paletteOpen && festival && <CommandPalette festivalId={festival.id} commands={commands} onClose={() => setPaletteOpen(false)} />}
      <Toasts />
    </div>
  );
}
