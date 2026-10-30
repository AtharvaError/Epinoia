import "@fontsource-variable/bricolage-grotesque";
import "@fontsource-variable/instrument-sans";
import "./styles.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { Shell } from "./components/Shell";
import { useMe } from "./lib/auth";
import { Accommodation } from "./pages/Accommodation";
import { Analytics } from "./pages/Analytics";
import { CommandCenter } from "./pages/CommandCenter";
import { Communication } from "./pages/Communication";
import { Events } from "./pages/Events";
import { Finance } from "./pages/Finance";
import { Login } from "./pages/Login";
import { Participants } from "./pages/Participants";
import { Ripple } from "./pages/Ripple";
import { Safety } from "./pages/Safety";
import { Transport } from "./pages/Transport";
import { Venues } from "./pages/Venues";
import { Volunteers } from "./pages/Volunteers";

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
          <Route path="/login" element={<Login />} />
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
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
