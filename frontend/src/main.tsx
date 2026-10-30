import "@fontsource-variable/bricolage-grotesque";
import "@fontsource-variable/instrument-sans";
import "./styles.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ComponentType, lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { Shell } from "./components/Shell";
import { useMe } from "./lib/auth";

/** Each page loads when first opened, so charts and maps never slow the login screen. */
function page<K extends string>(load: () => Promise<Record<K, ComponentType>>, name: K) {
  return lazy(() => load().then((m) => ({ default: m[name] })));
}

const Accommodation = page(() => import("./pages/Accommodation"), "Accommodation");
const Analytics = page(() => import("./pages/Analytics"), "Analytics");
const CommandCenter = page(() => import("./pages/CommandCenter"), "CommandCenter");
const Communication = page(() => import("./pages/Communication"), "Communication");
const Events = page(() => import("./pages/Events"), "Events");
const Finance = page(() => import("./pages/Finance"), "Finance");
const Login = page(() => import("./pages/Login"), "Login");
const Participants = page(() => import("./pages/Participants"), "Participants");
const Ripple = page(() => import("./pages/Ripple"), "Ripple");
const Safety = page(() => import("./pages/Safety"), "Safety");
const Settings = page(() => import("./pages/Settings"), "Settings");
const Transport = page(() => import("./pages/Transport"), "Transport");
const Venues = page(() => import("./pages/Venues"), "Venues");
const Volunteers = page(() => import("./pages/Volunteers"), "Volunteers");

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});

function Protected() {
  const me = useMe();
  if (me.isPending) return <div className="splash" role="status">Loading FestOS Command</div>;
  if (!me.data) return <Navigate to="/login" replace />;
  return <Shell user={me.data} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Suspense fallback={<div className="splash" role="status">Loading FestOS Command</div>}><Login /></Suspense>} />
          <Route element={<Protected />}>
            <Route index element={<CommandCenter />} />
            <Route path="events" element={<Events />} />
            <Route path="participants" element={<Participants />} />
            <Route path="accommodation" element={<Accommodation />} />
            <Route path="transportation" element={<Transport />} />
            <Route path="volunteers" element={<Volunteers />} />
            <Route path="venues" element={<Venues />} />
            <Route path="ripple" element={<Ripple />} />
            <Route path="finance" element={<Finance />} />
            <Route path="communication" element={<Communication />} />
            <Route path="safety" element={<Safety />} />
            <Route path="analytics" element={<Analytics />} />
            <Route path="settings" element={<Settings />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
